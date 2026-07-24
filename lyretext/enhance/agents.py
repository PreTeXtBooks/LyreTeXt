"""Enhance stage agent stubs — not yet implemented."""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from .state import ChapterEnhance


def enhance_chapter(
    state: ChapterEnhance,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Placeholder — enhance stage is not yet implemented."""
    raise NotImplementedError("Enhance stage is coming soon.")
