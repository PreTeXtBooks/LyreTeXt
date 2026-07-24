from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ChapterManifest(TypedDict):
    type: str
    name: str
    source_path: str
    output_path: str

class TranslationState(TypedDict):
    project_source: str
    temp_dir: str
    output_dir: str
    manifest: list[ChapterManifest]
    transition_policy: NotRequired[dict[str, Any]]
    human_signoffs: NotRequired[dict[str, bool]]
    validation_summary: NotRequired[dict[str, Any]]
    stage_gate_decision: NotRequired[dict[str, Any]]
    chapter_status: NotRequired[dict[str, str]]
    recompile_queue: NotRequired[list[str]]
    project_type: NotRequired[str]
    read_stage_warnings: NotRequired[list[dict[str, Any]]]
    # P4: project-level resource files discovered during read
    project_resources: NotRequired[list[dict[str, Any]]]

class TestState(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    pretext_output: str
    run_id: str


# Domain-local state types — owned by their respective modules.
from ..translate.state import ChapterTranslation  # noqa: E402
from ..read.state import SkeletonState  # noqa: E402