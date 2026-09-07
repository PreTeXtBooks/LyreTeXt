"""State TypedDict for the review subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired, TypedDict

from .structure import Issue


class ChapterReview(TypedDict):
    """Per-chapter review subgraph state."""

    output_path: str
    chapter_id: str
    # Full text of the artifact being reviewed (.ptx content).
    artifact: str
    # Reduced channel: parallel check-agents append their issues lists here.
    # operator.add is the native LangGraph reducer for list concatenation.
    issues: Annotated[list[Issue], operator.add]
    # Set by finalize_review: "passing" | "blocking"
    review_status: NotRequired[str]
    # Serialisable summary written to .lyretext/<chapter_id>.findings.json
    findings: NotRequired[dict[str, Any]]
