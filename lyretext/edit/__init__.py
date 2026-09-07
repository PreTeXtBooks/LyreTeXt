"""Editing agents.

Since Markdown → PreTeXt conversion became deterministic (see
``lyretext.convert``), the agentic role in this pipeline is editing rather than
translating: an LLM that takes an existing .ptx artifact plus either review
findings or a free-text user instruction, and returns a corrected document.

Not part of the default path — see ``auto_edit`` in the runtime config.
"""

from .agents import edit_chapter

__all__ = ["edit_chapter"]
