"""Environment checks: is the external toolchain usable from here? (#70)

LyreTeXt shells out to three tools it does not implement itself:

* **pandoc** (>= 3.0), for every LaTeX/Markdown → PreTeXt conversion;
* the **pretext.lua** custom writer (Oscar Levin's pandoc-pretext), which
  pandoc runs to produce PreTeXt;
* **R** with the **knitr** package, for R Markdown projects only.

The desktop app ships pandoc and pretext.lua (it points ``LYRETEXT_PANDOC_EXE``
/ ``LYRETEXT_PANDOC_WRITER`` at its copies and sets ``LYRETEXT_TOOLCHAIN_DIR``
to the folder holding them). R is never bundled (decision #65): it is detected,
and a missing R or knitr is explained in terms of what to install.

Every probe runs with a timeout and kills the tool's whole process tree if it
expires, so a slow or hung R cannot stall a request.

``environment_report`` backs ``GET /api/environment`` (the first-run checks
view); ``rmd_preflight`` is the start-of-run check for R Markdown projects.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .convert.pandoc import (
    DEFAULT_WRITER,
    PANDOC_WRITER_ENV,
    PandocNotFoundError,
    convert_markdown_to_pretext,
    locate_pandoc,
    resolve_writer,
)
from .pipeline.rmd import (
    RmdPipeline,
    RToolchainError,
    knitr_missing_guidance,
    r_missing_guidance,
    rmd_requirement_message,
)

logger = logging.getLogger("lyretext.environment")

# The desktop app's bundled-toolchain folder (resources/toolchain). A tool
# found inside it is reported as "bundled".
TOOLCHAIN_DIR_ENV = "LYRETEXT_TOOLCHAIN_DIR"

PANDOC_TIMEOUT = 15
# R's first start on a cold machine can take several seconds; a hung one must
# still not hold a request (or a run start) for long.
R_TIMEOUT = 20

MIN_PANDOC = (3, 0)
PANDOC_INSTALL_URL = "https://pandoc.org/installing.html"
PRETEXT_WRITER_URL = "https://github.com/oscarlevin/pandoc-pretext"

# One R start answers both questions. No double quotes, so it survives Windows
# command-line quoting unchanged; no --vanilla, because knitr may live in a
# user library that R's normal startup adds (and the knit itself runs without
# it too).
R_PROBE = (
    "cat('R_VERSION=', as.character(getRversion()), '\\n', "
    "'KNITR=', if (requireNamespace('knitr', quietly = TRUE)) "
    "as.character(utils::packageVersion('knitr')) else '', '\\n', sep = '')"
)

# A tiny LaTeX fixture for the writer check: converting it exercises pandoc's
# LaTeX reader and pretext.lua end to end, which is the real question (a
# writer file that exists but fails to load is no better than none).
_WRITER_FIXTURE = (
    "\\section{Toolchain check}\\label{sec:check}\n"
    "If \\(x = 1\\) then \\(x^2 = 1\\).\n"
    "\\begin{theorem}A check.\\end{theorem}\n"
)


# ---------------------------------------------------------------------------
# Running probes
# ---------------------------------------------------------------------------

def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and its children (Rscript.exe runs R as a child process)."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_probe(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a short diagnostic command and capture its output.

    Raises ``subprocess.TimeoutExpired`` after killing the process tree when
    *timeout* expires, and ``OSError`` if the command cannot be started.
    """
    proc = subprocess.Popen(
        list(cmd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        raise
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


def _tail(text: str | None, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _toolchain_dir() -> Path | None:
    raw = os.environ.get(TOOLCHAIN_DIR_ENV)
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except (OSError, ValueError):
        return None


def _is_bundled(path: str | Path | None) -> bool:
    root = _toolchain_dir()
    if root is None or not path:
        return False
    try:
        return Path(path).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    out = {"ok": ok, "error": None, "guidance": None}
    out.update(fields)
    return out


# ---------------------------------------------------------------------------
# pandoc + pretext.lua
# ---------------------------------------------------------------------------

_PANDOC_VERSION_RE = re.compile(r"^pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_USER_DATA_DIR_RE = re.compile(r"^User data directory:\s*(.+?)\s*$", re.MULTILINE)


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def detect_pandoc(explicit: str | None = None, *, timeout: float = PANDOC_TIMEOUT) -> dict[str, Any]:
    """Locate pandoc and ask it for its version.

    Returns ``{ok, path, source, bundled, version, data_dir, error, guidance}``
    where *source* is "bundled", "config", an env var name, "PATH" or
    "install location", and *data_dir* is pandoc's user data directory (where
    it looks for a bare ``pretext.lua``).
    """
    guidance = (
        f"Install pandoc 3.0 or later from {PANDOC_INSTALL_URL} and make sure it "
        "is on PATH, or set LYRETEXT_PANDOC_EXE to its full path."
    )
    try:
        path, source = locate_pandoc(explicit)
    except PandocNotFoundError:
        return _result(False, path=None, source=None, bundled=False, version=None,
                       data_dir=None, error="pandoc was not found.", guidance=guidance)

    bundled = _is_bundled(path)
    if bundled:
        source = "bundled"
        guidance = "The pandoc copy shipped with LyreTeXt is missing or broken; reinstall LyreTeXt."
    base = dict(path=path, source=source, bundled=bundled, version=None, data_dir=None)

    try:
        proc = run_probe([path, "--version"], timeout)
    except subprocess.TimeoutExpired:
        return _result(False, **base, error=f"pandoc did not respond within {timeout:g}s.",
                       guidance=guidance)
    except OSError as exc:
        return _result(False, **base, error=f"pandoc at {path} could not be run ({exc}).",
                       guidance=guidance)
    if proc.returncode != 0:
        return _result(False, **base, error=f"pandoc --version failed: {_tail(proc.stderr)}",
                       guidance=guidance)

    out = proc.stdout or ""
    match = _PANDOC_VERSION_RE.search(out.lstrip())
    data_dir = _USER_DATA_DIR_RE.search(out)
    base["version"] = match.group(1) if match else None
    base["data_dir"] = data_dir.group(1) if data_dir else None
    if base["version"] and _version_tuple(base["version"]) < MIN_PANDOC:
        return _result(False, **base,
                       error=f"pandoc {base['version']} is too old; LyreTeXt needs 3.0 or later.",
                       guidance=guidance)
    return _result(True, **base)


def _locate_writer(name: str, data_dir: str | None) -> tuple[Path | None, str]:
    """Where pandoc will load *name* from, mirroring pandoc's own lookup: a
    path as given (relative to the working directory), and for a bare file name
    also ``<user data dir>/custom/``."""
    from_env = name == os.environ.get(PANDOC_WRITER_ENV)
    given = Path(name)
    if given.is_file():
        if _is_bundled(given):
            return given.resolve(), "bundled"
        if from_env:
            return given.resolve(), PANDOC_WRITER_ENV
        return given.resolve(), ("working directory" if name == DEFAULT_WRITER else "config")
    if not given.is_absolute() and len(given.parts) == 1 and data_dir:
        candidate = Path(data_dir) / "custom" / name
        if candidate.is_file():
            return candidate, "pandoc user data directory"
    return None, (PANDOC_WRITER_ENV if from_env else "config")


def detect_writer(
    pandoc: dict[str, Any],
    writer: str | None = DEFAULT_WRITER,
    *,
    timeout: float = PANDOC_TIMEOUT,
) -> dict[str, Any]:
    """Find the pretext.lua writer and run one tiny LaTeX → PreTeXt conversion.

    *pandoc* is ``detect_pandoc``'s result. Returns ``{ok, path, source,
    bundled, error, guidance}``; ok means the conversion produced PreTeXt.
    """
    name = resolve_writer(writer)
    path, source = _locate_writer(name, pandoc.get("data_dir"))
    bundled = source == "bundled"
    base = dict(path=str(path) if path else None, source=source if path else None,
                bundled=bundled)
    guidance = (
        f"Install the pandoc-pretext writer ({PRETEXT_WRITER_URL}): copy pretext.lua "
        "and pretext-environments.lua into the custom/ folder of pandoc's user data "
        f"directory{' (' + pandoc['data_dir'] + ')' if pandoc.get('data_dir') else ''}, "
        f"or set {PANDOC_WRITER_ENV} to the full path of pretext.lua."
    )
    if bundled:
        guidance = "The pretext.lua copy shipped with LyreTeXt is missing or broken; reinstall LyreTeXt."

    if path is None:
        return _result(False, **base, error=f"The {name} writer was not found.", guidance=guidance)
    if not pandoc.get("ok"):
        return _result(False, **base, error="Not checked: pandoc is not available.",
                       guidance=None)

    with tempfile.TemporaryDirectory(prefix="lyretext-check-") as tmp:
        fixture = Path(tmp) / "check.tex"
        fixture.write_text(_WRITER_FIXTURE, encoding="utf-8")
        xml, messages = convert_markdown_to_pretext(
            fixture, pandoc_exe=pandoc["path"], writer=str(path), timeout=int(timeout),
        )
    if "<section" not in xml:
        detail = _tail(" ".join(messages)) or "no PreTeXt output"
        return _result(False, **base, error=f"A test conversion failed: {detail}",
                       guidance=guidance)
    return _result(True, **base)


# ---------------------------------------------------------------------------
# R + knitr
# ---------------------------------------------------------------------------

_R_VERSION_RE = re.compile(r"^R_VERSION=(\S*)\s*$", re.MULTILINE)
_KNITR_RE = re.compile(r"^KNITR=(\S*)\s*$", re.MULTILINE)


def detect_r(
    explicit: str | None = None,
    *,
    timeout: float = R_TIMEOUT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Locate Rscript and ask R for its version and whether knitr is installed.

    Returns ``(r, knitr)``: ``r = {ok, path, source, version, error,
    guidance}`` and ``knitr = {ok, version, error, guidance}``. A missing R is
    not an error for LaTeX projects; callers decide what it blocks.
    """
    not_checked = _result(False, version=None, error="Not checked: R is not available.")
    try:
        path, source = RmdPipeline.locate_rscript(explicit)
    except FileNotFoundError:
        return (_result(False, path=None, source=None, version=None,
                        error="R was not found on this computer.",
                        guidance=r_missing_guidance()),
                not_checked)
    base = dict(path=path, source=source, version=None)

    try:
        proc = run_probe([path, "-e", R_PROBE], timeout)
    except subprocess.TimeoutExpired:
        return (_result(False, **base, error=f"R did not respond within {timeout:g}s.",
                        guidance="Check that R starts normally on this computer, then try again."),
                not_checked)
    except OSError:
        where = f" (set by {source})" if source in ("config", "LYRETEXT_RSCRIPT_EXE", "RSCRIPT_EXE") else ""
        return (_result(False, **base, error=f"Rscript was not found at {path}{where}.",
                        guidance=r_missing_guidance()),
                not_checked)

    version = _R_VERSION_RE.search(proc.stdout or "")
    if proc.returncode != 0 or version is None:
        return (_result(False, **base, error=f"R failed to start: {_tail(proc.stderr) or 'no output'}",
                        guidance="Check that R starts normally on this computer, then try again."),
                not_checked)

    base["version"] = version.group(1) or None
    r = _result(True, **base)
    knitr_version = _KNITR_RE.search(proc.stdout or "")
    if knitr_version and knitr_version.group(1):
        return r, _result(True, version=knitr_version.group(1))
    return r, _result(False, version=None, error="The knitr package is not installed.",
                      guidance=knitr_missing_guidance())


def rmd_preflight(explicit: str | None = None, *, timeout: float = R_TIMEOUT) -> None:
    """Raise ``RToolchainError`` with what to install if R or knitr is missing.

    Called before an R Markdown run starts, so the user gets the fix rather
    than a run that fails on its first knit.
    """
    r, knitr = detect_r(explicit, timeout=timeout)
    if not r["ok"]:
        raise RToolchainError(rmd_requirement_message(r["error"], r["guidance"]))
    if not knitr["ok"]:
        raise RToolchainError(rmd_requirement_message(
            "R is installed, but the knitr package is not.", knitr_missing_guidance()))


def run_needs_r(project_source: str | Path, pipeline_name: str) -> bool:
    """True if a run of *project_source* would knit R Markdown.

    Uses the same pipeline selection as the run itself, and only says yes when
    there are R Markdown files to knit: a folder with none fails for that
    reason, whatever R's state.
    """
    from .read.agents import _pipeline_source_files, select_pipeline

    try:
        name, pipeline, _warnings = select_pipeline(project_source, pipeline_name)
    except ValueError:
        return False
    return name == "rmd" and bool(_pipeline_source_files(Path(project_source), pipeline))


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def environment_report(
    *,
    pandoc_exe: str | None = None,
    pandoc_writer: str | None = DEFAULT_WRITER,
    rscript_exe: str | None = None,
) -> dict[str, Any]:
    """Everything the first-run checks view shows, except API keys (the API
    layer adds those). Never raises for a missing or broken tool."""
    pandoc = detect_pandoc(pandoc_exe)
    writer = detect_writer(pandoc, pandoc_writer)
    r, knitr = detect_r(rscript_exe)
    toolchain = _toolchain_dir()
    return {
        "pandoc": pandoc,
        "writer": writer,
        "r": r,
        "knitr": knitr,
        "toolchain_dir": str(toolchain) if toolchain else None,
    }


def summarize(report: dict[str, Any]) -> str:
    """One log line: which tools are usable, and from where."""
    def part(name: str, item: dict[str, Any]) -> str:
        if not item.get("ok"):
            return f"{name}=MISSING"
        detail = item.get("version") or ""
        source = item.get("source")
        return f"{name}=ok" + (f" {detail}" if detail else "") + (f" ({source})" if source else "")

    return ", ".join(part(k, report[k]) for k in ("pandoc", "writer", "r", "knitr") if k in report)


__all__ = [
    "PANDOC_TIMEOUT",
    "R_TIMEOUT",
    "TOOLCHAIN_DIR_ENV",
    "detect_pandoc",
    "detect_r",
    "detect_writer",
    "environment_report",
    "rmd_preflight",
    "run_needs_r",
    "run_probe",
    "summarize",
]
