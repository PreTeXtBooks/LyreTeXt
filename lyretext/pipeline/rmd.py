"""
Rmd (R Markdown) pipeline implementation.
Handles .rmd/.Rmd file compilation using knitr.
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional, Dict

import yaml

from . import PipelineInterface

# Rscript-location override, set by whoever installed the toolchain (the
# desktop app). RSCRIPT_EXE is the older, unprefixed name and still honoured.
# Precedence: rscript_exe kwarg → LYRETEXT_RSCRIPT_EXE → RSCRIPT_EXE → PATH →
# common Windows install locations.
RSCRIPT_EXE_ENV = "LYRETEXT_RSCRIPT_EXE"

# R is not bundled with the desktop app (decision #65): the user installs it,
# and anything that finds it missing says what to install.
CRAN_WINDOWS_URL = "https://cran.r-project.org/bin/windows/base/"
KNITR_INSTALL_CMD = 'install.packages("knitr")'

# R's error when a package is absent: "there is no package called 'knitr'"
# (curly quotes in a UTF-8 locale).
_KNITR_MISSING_RE = re.compile(r"there is no package called\s*\W?knitr\W", re.IGNORECASE)


class RToolchainError(RuntimeError):
    """R or knitr is missing for an R Markdown run.

    The message is meant for the user as-is: what is wrong and what to install.
    """


def r_download_url() -> str:
    """Where to get R for this platform."""
    if sys.platform == "win32":
        return CRAN_WINDOWS_URL
    if sys.platform == "darwin":
        return "https://cran.r-project.org/bin/macosx/"
    return "https://cran.r-project.org/"


def r_missing_guidance() -> str:
    exe = "Rscript.exe" if sys.platform == "win32" else "Rscript"
    return (
        f"Install R from {r_download_url()}, then open R and run "
        f"{KNITR_INSTALL_CMD}. If R is already installed somewhere LyreTeXt "
        f"does not look, set {RSCRIPT_EXE_ENV} to the full path of {exe}."
    )


def knitr_missing_guidance() -> str:
    return f"Open R and run {KNITR_INSTALL_CMD}, then try again."


def rmd_requirement_message(problem: str, guidance: str) -> str:
    """The user-facing error for an R Markdown run that cannot start."""
    return f"R Markdown projects need R with the knitr package. {problem} {guidance}"


def _runnable(exe: str) -> bool:
    """True if *exe* names an existing file or a command on PATH."""
    return os.path.isfile(exe) or shutil.which(exe) is not None


class RmdPipeline(PipelineInterface):
    """Pipeline for R Markdown (.rmd/.Rmd) document format."""
    
    def get_name(self) -> str:
        """Return pipeline name."""
        return "rmd"
    
    def get_supported_extensions(self) -> list[str]:
        """Return supported file extensions."""
        return [".rmd", ".Rmd"]
    
    def compile_to_markdown(
        self,
        project_path: str | Path,
        source_files: Optional[list[str]] = None,
        output_dir: Optional[str | Path] = None,
        temp_dir: Optional[str | Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Compile Rmd files to Markdown using knitr.
        
        Args:
            project_path: Root directory of the project
            source_files: Optional list of specific files to compile. If None, all .rmd/.Rmd files in project_path
            output_dir: Where to write markdown output (default: temp_dir/markdown)
            temp_dir: Temporary working directory (default: temp_output)
            **kwargs: Additional options (e.g., rscript_exe)
        
        Returns:
            dict with:
                - "markdown_files": dict mapping source path -> markdown path
                - "errors": list of error messages (empty if successful)
        """
        project_path = Path(project_path)
        temp_dir = Path(temp_dir or "temp_output")
        output_dir = Path(output_dir or temp_dir / "markdown")
        
        if not output_dir.exists():
            output_dir.mkdir(parents=True)
        
        # Find Rscript. A missing R is reported as what to install, not as the
        # subprocess error each file would otherwise fail with.
        try:
            rscript = self.resolve_rscript(kwargs.get("rscript_exe"))
        except FileNotFoundError:
            raise RToolchainError(rmd_requirement_message(
                "R was not found on this computer.", r_missing_guidance())) from None
        if not _runnable(rscript):
            raise RToolchainError(rmd_requirement_message(
                f"Rscript was not found at {rscript}.", r_missing_guidance()))

        markdown_files = {}
        errors = []
        
        # Discover source files if not provided
        if source_files is None:
            source_files = [
                str(f) for f in project_path.iterdir()
                if f.is_file() and f.suffix in self.get_supported_extensions()
            ]
        print("SOURCE FILES", source_files)
        
        # Compile each Rmd file
        for source_file in source_files:
            source_path = project_path / Path(source_file).name

            if not source_path.exists():
                errors.append(f"Source file not found: {source_path}")
                continue
            
            if source_path.suffix not in self.get_supported_extensions():
                continue
            
            print(f"[RMD] Compiling {source_path.name} to markdown...")
            
            markdown_output = output_dir / source_path.with_suffix(".md").name
            
            # Build knitr expression with before_chapter_script setup
            before_scripts = self.get_before_chapter_setup(project_path, 0)
            knit_expr = before_scripts
            knit_expr += f"knitr::knit('{source_path.as_posix()}', output='{markdown_output.as_posix()}')"
            
            try:
                result = subprocess.run(
                    [rscript, "-e", knit_expr],
                    text=True,
                    capture_output=True,
                    timeout=300,  # 5 minute timeout
                )
                
                if result.returncode != 0:
                    if _KNITR_MISSING_RE.search(result.stderr or ""):
                        # Every other file would fail the same way.
                        raise RToolchainError(rmd_requirement_message(
                            "R is installed, but the knitr package is not.",
                            knitr_missing_guidance()))
                    errors.append(f"knitr compilation failed for {source_path.name}: {result.stderr}")
                    print(f"[RMD] Error: {result.stderr}")
                else:
                    markdown_files[str(source_path)] = str(markdown_output)
                    print(f"[RMD] Success: {markdown_output}")

            except subprocess.TimeoutExpired:
                errors.append(f"knitr compilation timeout for {source_path.name}")
            except RToolchainError:
                raise
            except OSError as e:
                # Rscript itself could not be started.
                raise RToolchainError(rmd_requirement_message(
                    f"Rscript at {rscript} could not be run ({e}).", r_missing_guidance())) from e
            except Exception as e:
                errors.append(f"Error compiling {source_path.name}: {str(e)}")
        
        return {
            "markdown_files": markdown_files,
            "errors": errors,
            "output_dir": str(output_dir),
        }
    
    def get_before_chapter_setup(
        self,
        project_path: str | Path,
        chapter_index: int,
    ) -> str:
        """
        Get R code to source before processing each chapter.
        Parses _bookdown.yml for 'before_chapter_script' field.
        
        Returns:
            R code string (empty if no setup needed)
        """
        project_path = Path(project_path)
        yaml_path = project_path / "_bookdown.yml"
        
        if not yaml_path.exists():
            return ""
        
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            
            scripts = config.get("before_chapter_script", [])
            if isinstance(scripts, str):
                scripts = [scripts]
            elif not isinstance(scripts, list):
                scripts = []
            
            knit_expr = ""
            for script in scripts:
                script_path = project_path / script
                if script_path.exists():
                    knit_expr += f"source('{script_path.as_posix()}'); "
            
            return knit_expr
            
        except yaml.YAMLError as e:
            print(f"[RMD] Warning: Failed to parse _bookdown.yml: {e}")
            return ""
        except Exception as e:
            print(f"[RMD] Warning: Error reading before_chapter_script: {e}")
            return ""
    
    def detect_and_resolve_config(
        self,
        project_path: str | Path,
    ) -> Dict[str, Any]:
        """
        Auto-detect Rmd project configuration from _bookdown.yml.
        
        Returns:
            dict with:
                - "chapters": list of chapter file patterns
                - "before_chapter_script": list of setup scripts
                - "output_formats": list of output formats
        """
        project_path = Path(project_path)
        yaml_path = project_path / "_bookdown.yml"
        
        config = {
            "chapters": [],
            "before_chapter_script": [],
            "output_formats": [],
        }
        
        if not yaml_path.exists():
            return config
        
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                bookdown_config = yaml.safe_load(f) or {}
            
            # Extract relevant bookdown settings
            config["chapters"] = bookdown_config.get("rmd_files", [])
            scripts = bookdown_config.get("before_chapter_script", [])
            config["before_chapter_script"] = scripts if isinstance(scripts, list) else [scripts]
            config["output_formats"] = bookdown_config.get("output_formats", [])
            
        except Exception as e:
            print(f"[RMD] Warning: Could not parse _bookdown.yml: {e}")
        
        return config
    
    @classmethod
    def resolve_rscript(cls, explicit: Optional[str] = None) -> str:
        """Rscript to run: *explicit* → LYRETEXT_RSCRIPT_EXE → RSCRIPT_EXE →
        PATH / install-location discovery."""
        return cls.locate_rscript(explicit)[0]

    @classmethod
    def locate_rscript(cls, explicit: Optional[str] = None) -> tuple[str, str]:
        """``resolve_rscript``, plus where the answer came from: one of
        ``"config"``, ``"LYRETEXT_RSCRIPT_EXE"``, ``"RSCRIPT_EXE"``, ``"PATH"``
        or ``"install location"``.

        Raises FileNotFoundError if Rscript cannot be found anywhere. A path
        given explicitly or by env var is returned as given, existing or not.
        """
        for candidate, source in (
            (explicit, "config"),
            (os.environ.get(RSCRIPT_EXE_ENV), RSCRIPT_EXE_ENV),
            (os.environ.get("RSCRIPT_EXE"), "RSCRIPT_EXE"),
        ):
            if candidate:
                return candidate, source
        path = cls._find_rscript()
        return path, ("PATH" if shutil.which("Rscript") else "install location")

    @staticmethod
    def _find_rscript() -> str:
        """
        Locate Rscript executable.
        Prefers PATH, then checks common Windows install locations.
        """
        found = shutil.which("Rscript")
        if found:
            return found
        
        # Windows-specific fallback paths
        candidates = []
        for base in [
            r"C:\Program Files\R",
            r"C:\Program Files\R\R-*",
            r"C:\Program Files\R\R-*\bin",
            r"C:\Program Files\R\R-*\bin\x64",
            r"C:\Program Files\R\R-*\bin\x86",
        ]:
            candidates.extend(glob.glob(os.path.join(base, "Rscript.exe")))
        
        if candidates:
            return sorted(candidates)[-1]
        
        raise FileNotFoundError(
            "Rscript.exe was not found. Add R to PATH or set LYRETEXT_RSCRIPT_EXE "
            "to its full path."
        )


__all__ = [
    "CRAN_WINDOWS_URL",
    "KNITR_INSTALL_CMD",
    "RToolchainError",
    "RmdPipeline",
    "knitr_missing_guidance",
    "r_download_url",
    "r_missing_guidance",
    "rmd_requirement_message",
]
