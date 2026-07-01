from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _last_value(a, b):
    """Reducer that keeps the last value. Used for configuration fields that
    are written identically by every parallel chapter node."""
    return b


class ChapterManifest(TypedDict):
    type: str
    name: str
    source_path: str

class TranslationState(TypedDict):
    project_source: str
    temp_dir: str
    output_dir: str
    manifest: list[ChapterManifest]
    # Annotated so that parallel chapter nodes can all write the same value
    # without triggering InvalidUpdateError.
    execution_mode: Annotated[str, _last_value]
    apply_mode: Annotated[str, _last_value]
    create_backup: Annotated[bool, _last_value]
    provider: Annotated[str, _last_value]

class SkeletonState(TypedDict):
    project_source: str
    project_md_source: str
    source_files: list[Any]
    temp_dir: str
    output_dir: str
    manifest: list[dict[str, Any]] | Any
    execution_mode: str
    apply_mode: str
    create_backup: bool
    provider: str

class TestState(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    pretext_output: str

class ChapterTranslation(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    output_path: str
    pretext_output: str
    execution_mode: str
    apply_mode: str
    create_backup: bool
    provider: str