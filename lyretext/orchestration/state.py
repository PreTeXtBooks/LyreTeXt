from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _last_value(a, b):
    """Reducer that keeps the last value. Used for resolved_config written
    identically by every parallel chapter node."""
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
    resolved_config: Annotated[Any, _last_value]

class TestState(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    pretext_output: str


# Domain-local state types — owned by their respective modules.
from ..translate.state import ChapterTranslation  # noqa: E402
from ..read.state import SkeletonState  # noqa: E402