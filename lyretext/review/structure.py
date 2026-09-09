"""Pydantic contracts for the review layer: IssueDraft, Issue and ReviewReport.

Two models, deliberately. ``IssueDraft`` is what a check-agent is *asked* to
produce, and holds only what an LLM can actually judge about the document in
front of it. ``Issue`` is what the system stores and the UI renders, and adds
the fields the check registry and the review layer own. Handing the LLM the
full ``Issue`` schema is what led to ``auto_fixable`` being prose in three
prompt files rather than a property of the check.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class IssueDraft(BaseModel):
    """A finding as reported by a check-agent — the LLM-facing schema."""

    severity: Literal["error", "warn", "info"]
    line: Optional[int] = None
    end_line: Optional[int] = None
    message: str
    suggestion: Optional[str] = None


class Issue(IssueDraft):
    """A finding after the review layer has attributed it to its check."""

    check_id: str
    # Whether the OPTIONAL automatic review→edit→review loop may attempt this
    # unasked. Set from CheckSpec.auto_fixable by run_check — never by the LLM.
    # It says nothing about whether a human may request the fix at the review
    # gate: there, every finding is actionable.
    auto_fixable: bool = False
    rule_id: Optional[str] = None
    # Reserved for future block-level translation (issue-draft 005)
    block_id: Optional[str] = None
    # Identity of the *kind* of finding, shared by every occurrence of one
    # systematic problem. Set by finalize_review; see review.grouping.
    group_key: Optional[str] = None
    # Set to True when the user dismisses this issue from the UI
    ignored: bool = False
    # Review-derived lifecycle, reconciled across passes by finding_identity
    # (see review.grouping / finalize_review). "open" = still applies to the
    # current output; "fixed" = a prior finding the latest review no longer
    # sees, auto-resolved. Orthogonal to ``ignored`` (user suppression): a
    # finding is *active* iff status == "open" and not ignored.
    status: Literal["open", "fixed"] = "open"


class ReviewReport(BaseModel):
    """Structured output schema every check-agent must return."""

    issues: list[IssueDraft] = Field(default_factory=list)
