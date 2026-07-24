"""Pydantic contracts for the review layer: Issue and ReviewReport."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class Issue(BaseModel):
    """A single finding raised by a check-agent."""

    check_id: str
    severity: Literal["error", "warn", "info"]
    line: Optional[int] = None
    end_line: Optional[int] = None
    message: str
    suggestion: Optional[str] = None
    auto_fixable: bool = False
    rule_id: Optional[str] = None
    # Reserved for future block-level translation (issue-draft 005)
    block_id: Optional[str] = None
    # Set to True after apply_fixes resolves this issue; shown collapsed in UI
    auto_fixed: bool = False


class ReviewReport(BaseModel):
    """Structured output schema every check-agent must return."""

    issues: list[Issue] = Field(default_factory=list)
