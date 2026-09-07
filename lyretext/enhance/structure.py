"""Pydantic contracts for the enhance stage (stub)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EnhanceOutput(BaseModel):
    """Placeholder structured output for the enhance stage."""

    xml: str = Field(default="", description="Enhanced PreTeXt XML.")
    notes: list[str] = Field(default_factory=list)
