"""
LaTeX (.tex) pipeline implementation.

Unlike the Rmd pipeline (knitr -> .md -> pandoc -> .ptx), LaTeX projects go
straight through pandoc with the pretext.lua custom writer -- no intermediate
markdown stage. pandoc resolves \\input/\\include natively via cwd, converting
the whole project in one invocation. The manifest, however, is still
per-chapter, derived here from \\include directives in the main .tex file so
downstream stages (review, editing, per-chapter UI) keep working per-chapter.
The actual pandoc invocation and splitting of the monolithic output into
per-chapter fragments happens later, in translate_chapter / split.py.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Optional, Dict

from . import PipelineInterface

# Matches \include{name} but not a commented-out line (a % immediately
# preceding \include, ignoring nothing in between -- callers are expected to
# have already filtered out fully-commented lines; see _iter_active_includes).
_INCLUDE_RE = re.compile(r'(?<!%)\\include\{([^}]+)\}')
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


def _iter_active_includes(text: str) -> list[str]:
    """Parse \\include{name} directives, skipping commented-out lines.

    A line is considered "commented out" if a '%' appears before the
    \\include on that line (the user toggled it off).
    """
    includes: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("%"):
            continue
        percent_idx = line.find("%")
        search_text = line if percent_idx == -1 else line[:percent_idx]
        for match in _INCLUDE_RE.finditer(search_text):
            includes.append(match.group(1))
    return includes


def _resolve_include_path(project_path: Path, name: str) -> Path:
    """Resolve an \\include{name} target to an actual .tex file on disk."""
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
        include_names = _iter_active_includes(main_text)

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
        files, and chapters (from \\include directives).

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

        config["chapters"] = _iter_active_includes(text)

        return config


__all__ = ["TexPipeline"]
