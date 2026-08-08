"""Deterministic document conversion.

Unlike ``lyretext.pipeline`` (which handles *source format* → Markdown, e.g.
knitting .Rmd), this package handles Markdown → PreTeXt via pandoc and Oscar
Levin's ``pretext.lua`` custom writer.
"""

from .pandoc import (
    PandocNotFoundError,
    convert_markdown_to_pretext,
    find_pandoc,
)
from .split import split_pretext_by_section

__all__ = [
    "PandocNotFoundError",
    "convert_markdown_to_pretext",
    "find_pandoc",
    "split_pretext_by_section",
]
