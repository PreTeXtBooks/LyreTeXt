"""Enhance stage state — placeholder for future implementation."""
from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ChapterEnhance(TypedDict):
    """Per-chapter enhance state (stub)."""

    output_path: str
    chapter_id: NotRequired[str]
    enhanced_output: NotRequired[str]
    enhance_findings: NotRequired[dict[str, Any]]
