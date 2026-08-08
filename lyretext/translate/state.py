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
    # P3: natural-language refinement instruction. Now routed to the editing
    # agent (conversion itself is deterministic and ignores instructions).
    instruction: NotRequired[str | None]
    # P2: serialisable findings summary from the review subgraph
    chapter_findings: NotRequired[dict[str, Any]]
    # Editing-agent passes since the last fresh conversion. Bounded by
    # max_edit_iterations so the review→edit→review loop always terminates.
    edit_iterations: NotRequired[int]
    # Internal routing signal set by chapter_review_gate:
    # "translate_chapter" | "edit_chapter" | "review_chapter" | END
    chapter_next: NotRequired[str]
    # LaTeX pipeline: populated by the orchestration layer from the tex
    # pipeline's compile_to_markdown result so translate_chapter can run
    # pandoc on the whole project (main_file, from project_root) and split
    # the monolithic output across all chapters (chapter_ids).
    main_file: NotRequired[str]
    project_root: NotRequired[str]
    chapter_ids: NotRequired[list[str]]
