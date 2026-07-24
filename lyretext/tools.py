"""Backward-compatible re-export shim for lyretext utility functions.

All utilities have moved to lyretext.utils.*:
  - load_prompts          -> lyretext.utils.prompts
  - split_into_sections   -> lyretext.utils.text
  - merge_pretext_document-> lyretext.utils.text
  - split_markdown_by_h1  -> lyretext.utils.text
  - load_r_markdown       -> lyretext.utils.filesystem
  - create_files_from_json-> lyretext.utils.filesystem

Rmd-specific helpers (find_rscript, get_before_chapter_scripts) have moved
to lyretext.pipeline.rmd.RmdPipeline.
"""
from __future__ import annotations

from .utils import (
    load_prompts,
    split_into_sections,
    merge_pretext_document,
    split_markdown_by_h1,
    load_r_markdown,
    create_files_from_json,
)

__all__ = [
    "load_prompts",
    "split_into_sections",
    "merge_pretext_document",
    "split_markdown_by_h1",
    "load_r_markdown",
    "create_files_from_json",
]
