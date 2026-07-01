from __future__ import annotations

from typing import Any, TypedDict


class ChapterTranslation(TypedDict):
    source_path: str
    chapter_structure: list[dict[str, Any]] | Any
    output_path: str
    pretext_output: str
    resolved_config: Any
