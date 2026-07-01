"""
Rmd (R Markdown) pipeline implementation.
Handles .rmd/.Rmd file compilation using knitr.
"""

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Dict

import yaml

from . import PipelineInterface


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
        
        # Find Rscript executable
        rscript = kwargs.get("rscript_exe") or os.environ.get("RSCRIPT_EXE") or self._find_rscript()
        
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
                    errors.append(f"knitr compilation failed for {source_path.name}: {result.stderr}")
                    print(f"[RMD] Error: {result.stderr}")
                else:
                    markdown_files[str(source_path)] = str(markdown_output)
                    print(f"[RMD] Success: {markdown_output}")
                    
            except subprocess.TimeoutExpired:
                errors.append(f"knitr compilation timeout for {source_path.name}")
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
            "Rscript.exe was not found. Add R to PATH or set RSCRIPT_EXE to its full path."
        )


__all__ = ["RmdPipeline"]
