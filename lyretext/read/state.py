from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class SkeletonState(TypedDict):
    project_source: str
    project_md_source: NotRequired[str]
    source_files: NotRequired[list[Any]]
    temp_dir: str
    output_dir: str
    manifest: NotRequired[list[dict[str, Any]] | Any]
    # P4: resource files discovered during read
    project_resources: NotRequired[list[dict[str, Any]]]
