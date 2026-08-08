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
    # Surfaced on the manifest gate: pipeline mismatches, compile errors, and
    # anything else that would otherwise leave an empty manifest unexplained.
    read_stage_warnings: NotRequired[list[dict[str, Any]]]
    # LaTeX pipeline: main .tex file and project root, populated by
    # process_to_markdown when the tex pipeline runs.
    main_file: NotRequired[str]
    project_root: NotRequired[str]
