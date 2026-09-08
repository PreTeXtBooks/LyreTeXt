"""
LaTeX (.tex) pipeline implementation.

Unlike the Rmd pipeline (knitr -> .md -> pandoc -> .ptx), LaTeX projects go
straight through pandoc with the pretext.lua custom writer -- no intermediate
markdown stage. pandoc resolves \\input/\\include natively via cwd, converting
the whole project in one invocation. The manifest, however, is still
per-chapter, derived here from top-level \\input/\\include directives in the
main .tex file's body so downstream stages (review, editing, per-chapter UI)
keep working per-chapter.
The actual pandoc invocation and splitting of the monolithic output into
per-chapter fragments happens later, in translate_chapter / split.py.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Optional, Dict

from . import PipelineInterface

# Matches \input{name} and \include{name}, but not one immediately preceded by
# a '%' (a toggled-off directive). Callers additionally strip fully-commented
# and inline-commented text before matching; see _iter_active_inputs. Only the
# brace form is matched -- bare TeX \input file (no braces) is out of scope for
# this deterministic layer.
_INPUT_INCLUDE_RE = re.compile(r'(?<!%)\\(?:input|include)\{([^}]+)\}')
_BEGIN_DOCUMENT_RE = re.compile(r'\\begin\{document\}')
_END_DOCUMENT_RE = re.compile(r'\\end\{document\}')
_DOCUMENTCLASS_RE = re.compile(r'\\documentclass(?:\[[^\]]*\])?\{[^}]+\}')
_BIBLIOGRAPHY_RE = re.compile(r'\\bibliography\{([^}]+)\}')
_ADDBIBRESOURCE_RE = re.compile(r'\\addbibresource\{([^}]+)\}')


def _find_main_tex_file(project_path: Path) -> Optional[Path]:
    """Scan .tex files in project_path for \\documentclass to find the main file."""
    for candidate in sorted(project_path.glob("*.tex")):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if _DOCUMENTCLASS_RE.search(text):
            return candidate
    return None


def _document_body(text: str) -> str:
    """Return the document body -- the slice between \\begin{document} and
    \\end{document}.

    Restricting to the body ensures preamble directives (e.g. a
    \\input{macros} or a package load) are never mistaken for chapters. If
    \\begin{document} is absent (a malformed main file), fall back to the full
    text rather than silently dropping all chapters.
    """
    begin = _BEGIN_DOCUMENT_RE.search(text)
    if begin is None:
        return text
    body = text[begin.end():]
    end = _END_DOCUMENT_RE.search(body)
    if end is not None:
        body = body[:end.start()]
    return body


def _iter_active_inputs(text: str) -> list[str]:
    """Collect top-level \\input{name} and \\include{name} directives in the
    document body, in source order, skipping commented-out ones.

    Both directives are treated the same for chapter detection: pandoc resolves
    each natively during conversion, so each top-level one corresponds to one
    top-level structural element in pandoc's output. Only the body is scanned
    (see _document_body), and a directive is skipped if a '%' precedes it on its
    line (fully-commented lines are dropped; inline comments are truncated).
    Nested directives inside the included files are not followed -- they are
    chapter *content* that pandoc inlines.
    """
    names: list[str] = []
    for line in _document_body(text).splitlines():
        stripped = line.lstrip()
        if stripped.startswith("%"):
            continue
        percent_idx = line.find("%")
        search_text = line if percent_idx == -1 else line[:percent_idx]
        for match in _INPUT_INCLUDE_RE.finditer(search_text):
            names.append(match.group(1).strip())
    return names


def _resolve_include_path(project_path: Path, name: str) -> Path:
    """Resolve an \\input{name}/\\include{name} target to a .tex file on disk.

    Handles subdirectory targets (name may contain '/') and the implicit .tex
    extension.
    """
    candidate = project_path / name
    if candidate.suffix != ".tex":
        candidate_with_ext = project_path / f"{name}.tex"
        if candidate_with_ext.exists():
            return candidate_with_ext
    return candidate


class TexPipeline(PipelineInterface):
    """Pipeline for LaTeX (.tex) document format."""

    def get_name(self) -> str:
        """Return pipeline name."""
        return "tex"

    def get_supported_extensions(self) -> list[str]:
        """Return supported file extensions."""
        return [".tex"]

    def compile_to_markdown(
        self,
        project_path: str | Path,
        source_files: Optional[list[str]] = None,
        output_dir: Optional[str | Path] = None,
        temp_dir: Optional[str | Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        "Compile" a LaTeX project by identifying the main file and its
        chapters (\\include'd files), then copying those chapter files to
        output_dir so the rest of the app can display/manage per-chapter
        sources. The actual LaTeX -> PreTeXt conversion happens later via
        pandoc on the whole project (see lyretext.translate.agents), not here.

        Returns:
            dict with:
                - "markdown_files": dict mapping chapter name -> copied .tex path
                  (named for interface parity with the other pipelines; these
                  are .tex files, not markdown)
                - "errors": list of error messages (empty if successful)
                - "output_dir": str
                - "main_file": str path to the main .tex file
                - "project_root": str path to the project directory
        """
        project_path = Path(project_path)
        temp_dir = Path(temp_dir or "temp_output")
        output_dir = Path(output_dir or temp_dir / "markdown")

        main_path = _find_main_tex_file(project_path)
        if main_path is None:
            return {
                "markdown_files": {},
                "errors": [f"No .tex file with \\documentclass found in {project_path}"],
                "output_dir": str(output_dir),
            }

        if not output_dir.exists():
            output_dir.mkdir(parents=True)

        main_text = main_path.read_text(encoding="utf-8", errors="replace")
        include_names = _iter_active_inputs(main_text)

        markdown_files: dict[str, str] = {}
        errors: list[str] = []

        if not include_names:
            # Single-file project: one chapter entry for the main file itself.
            dst = output_dir / main_path.name
            shutil.copy2(main_path, dst)
            markdown_files[main_path.stem] = str(dst)
        else:
            for name in include_names:
                src = _resolve_include_path(project_path, name)
                if not src.exists():
                    errors.append(f"Included file not found: {src}")
                    continue
                dst = output_dir / src.name
                shutil.copy2(src, dst)
                markdown_files[src.stem] = str(dst)

        return {
            "markdown_files": markdown_files,
            "errors": errors,
            "output_dir": str(output_dir),
            "main_file": str(main_path),
            "project_root": str(project_path),
        }

    def get_before_chapter_setup(
        self,
        project_path: str | Path,
        chapter_index: int,
    ) -> str:
        """LaTeX needs no per-chapter setup code -- pandoc handles the whole
        project in one invocation."""
        return ""

    def detect_and_resolve_config(
        self,
        project_path: str | Path,
    ) -> Dict[str, Any]:
        """
        Auto-detect LaTeX project configuration: main file, bibliography
        files, and chapters (from body-level \\input/\\include directives).

        Returns:
            dict with "main_file", "bib_files", "chapters"
        """
        project_path = Path(project_path)
        main_path = _find_main_tex_file(project_path)

        config: dict[str, Any] = {
            "main_file": str(main_path) if main_path else None,
            "bib_files": [],
            "chapters": [],
        }

        if main_path is None:
            return config

        text = main_path.read_text(encoding="utf-8", errors="replace")

        bib_files: list[str] = []
        for match in _BIBLIOGRAPHY_RE.finditer(text):
            bib_files.extend(name.strip() for name in match.group(1).split(","))
        for match in _ADDBIBRESOURCE_RE.finditer(text):
            bib_files.append(match.group(1).strip())
        config["bib_files"] = bib_files

        config["chapters"] = _iter_active_inputs(text)

        return config


__all__ = ["TexPipeline"]
