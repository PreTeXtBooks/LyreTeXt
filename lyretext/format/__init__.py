"""Canonical PreTeXt formatting, applied to every artifact write.

See pretext_fmt.py for the safety invariant this upholds.
"""
from .pretext_fmt import format_pretext
from .repair import repair_xml_entities

__all__ = ["format_pretext", "repair_xml_entities"]
