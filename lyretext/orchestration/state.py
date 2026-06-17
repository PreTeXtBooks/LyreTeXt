from __future__ import annotations

from typing import Any, TypedDict

class ChapterManifest(TypedDict):
    type: str
    name: str
    source_path: str

class TranslationState(TypedDict):
    project_source: str
    output_dir: str
    manifest: list[ChapterManifest]

class SkeletonState(TypedDict):
    project_source: str
    source_files: list[Any]
    temporary_dir: str
    output_dir: str
    manifest: list[dict[str, Any]] | Any

class TestState(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    pretext_output: str

class ChapterTranslation(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    output_path: str
    pretext_output: str