"""
Pipeline abstraction for lyretext.
Defines interfaces and base classes for source format pipelines (Rmd, qmd, tex, etc).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any, Dict


@dataclass
class PipelineConfig:
    """Configuration specific to a pipeline format."""
    name: str  # e.g., "rmd", "qmd", "tex"
    supported_extensions: list[str]  # e.g., [".rmd", ".Rmd"]
    options: Dict[str, Any]  # Pipeline-specific options


class PipelineInterface(ABC):
    """Base interface for document format pipelines."""
    
    @abstractmethod
    def get_name(self) -> str:
        """Return the pipeline name (e.g., 'rmd', 'qmd')."""
        pass
    
    @abstractmethod
    def get_supported_extensions(self) -> list[str]:
        """Return list of file extensions this pipeline supports."""
        pass
    
    @abstractmethod
    def compile_to_markdown(
        self,
        project_path: str | Path,
        source_files: list[str],
        output_dir: str | Path,
        temp_dir: Optional[str | Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Compile project source files to intermediate markdown format.
        
        Returns dict with keys:
            - "manifest": list of compiled file info
            - "markdown_files": dict mapping source -> markdown path
            - "errors": list of any errors encountered (empty if successful)
        """
        pass
    
    @abstractmethod
    def get_before_chapter_setup(
        self,
        project_path: str | Path,
        chapter_index: int,
    ) -> str:
        """
        Get any setup code to run before processing each chapter.
        Returns empty string if no setup needed.
        """
        pass
    
    @abstractmethod
    def detect_and_resolve_config(
        self,
        project_path: str | Path,
    ) -> Dict[str, Any]:
        """
        Auto-detect project configuration (e.g., chapter order, metadata).
        Returns dict with project metadata and configuration.
        """
        pass


class PipelineRegistry:
    """Registry for available document format pipelines."""
    
    _registry: Dict[str, PipelineInterface] = {}
    
    @classmethod
    def register(cls, pipeline: PipelineInterface) -> None:
        """Register a pipeline by name."""
        name = pipeline.get_name()
        cls._registry[name] = pipeline
    
    @classmethod
    def get(cls, name: str) -> Optional[PipelineInterface]:
        """Get a pipeline by name."""
        return cls._registry.get(name)
    
    @classmethod
    def list_available(cls) -> list[str]:
        """Return list of registered pipeline names."""
        return list(cls._registry.keys())
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered pipelines (useful for testing)."""
        cls._registry.clear()


# Auto-register built-in pipelines
def _register_builtin_pipelines():
    """Register all built-in pipelines."""
    from .rmd import RmdPipeline
    from .qmd import QmdPipeline
    
    PipelineRegistry.register(RmdPipeline())
    PipelineRegistry.register(QmdPipeline())


# Register on module import
_register_builtin_pipelines()


__all__ = [
    "PipelineConfig",
    "PipelineInterface",
    "PipelineRegistry",
]
