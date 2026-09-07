"""Markdown → PreTeXt conversion via pandoc + the ``pretext.lua`` custom writer.

This replaces what used to be an LLM call.  The transform is a deterministic
tree rewrite, so it belongs in a subprocess, not a prompt.

Requires pandoc (>= 3.0, for custom writer support) and Oscar Levin's
pandoc-pretext writer installed in pandoc's user data directory under
``custom/`` — see https://github.com/oscarlevin/pandoc-pretext.

Follows the same conventions as ``lyretext.pipeline.rmd``: executable discovery
via env var → PATH → platform install globs, and a non-raising return shape so
compilation errors are data rather than exceptions.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

# The custom writer name. pandoc resolves a bare "<name>.lua" against the CWD
# first, then against <user data dir>/custom/, which is where pandoc-pretext
# installs itself.
DEFAULT_WRITER = "pretext.lua"

DEFAULT_TIMEOUT = 120


class PandocNotFoundError(FileNotFoundError):
    """Raised when no pandoc executable can be located."""


def find_pandoc(explicit: str | None = None) -> str:
    """Locate the pandoc executable.

    Precedence: *explicit* argument → ``PANDOC_EXE`` env var → PATH → common
    Windows install locations.

    Raises:
        PandocNotFoundError: if pandoc cannot be found anywhere.
    """
    candidate = explicit or os.environ.get("PANDOC_EXE")
    if candidate:
        return candidate

    found = shutil.which("pandoc")
    if found:
        return found

    # Windows-specific fallbacks — pandoc's own installer uses LOCALAPPDATA,
    # the MSI/choco route uses Program Files.
    patterns = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Pandoc", "pandoc.exe"),
        r"C:\Program Files\Pandoc\pandoc.exe",
        r"C:\Program Files (x86)\Pandoc\pandoc.exe",
    ]
    candidates: list[str] = []
    for pattern in patterns:
        if pattern:
            candidates.extend(glob.glob(pattern))

    if candidates:
        return sorted(candidates)[-1]

    raise PandocNotFoundError(
        "pandoc was not found. Install it from https://pandoc.org/installing.html, "
        "add it to PATH, or set PANDOC_EXE (or the 'pandoc_exe' config option) "
        "to its full path."
    )


def convert_markdown_to_pretext(
    source_path: str | Path,
    *,
    pandoc_exe: str | None = None,
    writer: str = DEFAULT_WRITER,
    extra_args: Sequence[str] = (),
    timeout: int = DEFAULT_TIMEOUT,
    cwd: str | Path | None = None,
) -> tuple[str, list[str]]:
    """Convert a Markdown file to PreTeXt XML.

    Args:
        source_path: Path to the Markdown source file.
        pandoc_exe: Explicit pandoc path; auto-discovered when None.
        writer: Custom writer to pass to ``-t``. Defaults to "pretext.lua".
        extra_args: Additional pandoc arguments (e.g. ``("--lua-filter", ...)``).
        timeout: Seconds before the subprocess is killed.
        cwd: Working directory for the pandoc subprocess. Needed for LaTeX
            projects so pandoc can resolve \\input/\\include relative to the
            project root rather than the caller's cwd.

    Returns:
        ``(pretext_xml, messages)``. **Failure is signalled by an empty
        pretext_xml**, not by non-empty messages — pandoc also reports
        non-fatal warnings (unsupported constructs, missing references) on
        stderr after a successful run, and those are passed through here.
        Never raises for conversion failures; only ``PandocNotFoundError`` if
        the executable itself is missing.
    """
    source = Path(source_path)
    if not source.exists():
        return "", [f"Source file not found: {source}"]

    exe = find_pandoc(pandoc_exe)

    # No -o: pandoc writes to stdout by default, which avoids a temp file.
    # No -s: chapters are fragments, and pretext.lua already emits a rooted
    # <section> element.
    cmd = [exe, str(source), "-t", writer, *extra_args]

    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            # Mandatory: without an explicit encoding, Windows decodes pandoc's
            # UTF-8 output as cp1252 and mangles every math and unicode glyph.
            encoding="utf-8",
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired:
        return "", [f"pandoc conversion timed out after {timeout}s for {source.name}"]
    except OSError as exc:
        return "", [f"Failed to run pandoc for {source.name}: {exc}"]

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return "", [f"pandoc conversion failed for {source.name}: {stderr}"]

    xml = result.stdout or ""
    if not xml.strip():
        return "", [f"pandoc produced no output for {source.name}"]

    # pandoc warnings (e.g. unsupported constructs) go to stderr on success;
    # surface them without treating them as failures.
    warnings: list[str] = []
    stderr = (result.stderr or "").strip()
    if stderr:
        warnings.append(f"pandoc warning for {source.name}: {stderr}")

    return xml, warnings


__all__ = [
    "DEFAULT_WRITER",
    "PandocNotFoundError",
    "convert_markdown_to_pretext",
    "find_pandoc",
]
