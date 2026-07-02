from __future__ import annotations

from typing import Any, TypedDict


class SkeletonState(TypedDict):
    project_source: str
    project_md_source: str
    source_files: list[Any]
    temp_dir: str
    run_id: str
    last_checkpoint_id: str
    checkpoint_namespace: str
    stage_id: str
    pause_requested: bool
    pause_reason: str
    output_dir: str
    manifest: list[dict[str, Any]] | Any
    resolved_config: Any
