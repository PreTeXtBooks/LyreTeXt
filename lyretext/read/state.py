from __future__ import annotations

from typing import Any, TypedDict


class SkeletonState(TypedDict):
    project_source: str
    project_md_source: str
    source_files: list[Any]
    temp_dir: str
    output_dir: str
    manifest: list[dict[str, Any]] | Any
    resolved_config: Any
