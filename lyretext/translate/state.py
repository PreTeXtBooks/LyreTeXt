from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ChapterTranslation(TypedDict):
    source_path: str
    chapter_structure: NotRequired[list[dict[str, Any]] | Any]
    output_path: str
    pretext_output: NotRequired[str]
    chapter_id: NotRequired[str]
    iteration_count: NotRequired[int]
    retry_requested: NotRequired[bool]
    # P3: natural-language refinement instruction injected at translate time
    instruction: NotRequired[str]
    # P2: serialisable findings summary from the review subgraph
    chapter_findings: NotRequired[dict[str, Any]]
    # Internal routing signal set by chapter_review_gate:
    # "translate_chapter" | "review_chapter" | "read_chapter" | END
    chapter_next: NotRequired[str]
