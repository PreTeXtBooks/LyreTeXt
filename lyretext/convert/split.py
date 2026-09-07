"""Split a monolithic PreTeXt output into per-chapter fragments."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Sequence

_STRUCTURAL_TAGS = {"section", "chapter", "part", "appendix"}


def split_pretext_by_section(
    xml_text: str,
    chapter_ids: Sequence[str],
) -> tuple[dict[str, str], list[str]]:
    """Split monolithic PreTeXt into per-chapter fragments by top-level children.

    pandoc/pretext.lua emits one top-level <section> (or <chapter>) per
    \\include'd file. We correlate by order: chapter_ids[i] <-> ith top-level
    section element.

    Returns (mapping, warnings) where mapping is {chapter_id: xml_fragment}.
    If counts don't match, warns and assigns entire output to the first
    chapter id.
    """
    warnings: list[str] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        warnings.append(f"Failed to parse PreTeXt XML for splitting: {exc}")
        fallback_id = chapter_ids[0] if chapter_ids else ""
        return ({fallback_id: xml_text} if fallback_id else {}), warnings

    structural_children = [
        child for child in list(root) if _local_name(child.tag) in _STRUCTURAL_TAGS
    ]

    if len(structural_children) != len(chapter_ids):
        warnings.append(
            f"Chapter count mismatch: found {len(structural_children)} structural "
            f"elements but expected {len(chapter_ids)} chapter_ids "
            f"({list(chapter_ids)}); assigning entire output to the first chapter."
        )
        fallback_id = chapter_ids[0] if chapter_ids else ""
        return ({fallback_id: xml_text} if fallback_id else {}), warnings

    mapping: dict[str, str] = {}
    for chapter_id, child in zip(chapter_ids, structural_children):
        mapping[chapter_id] = ET.tostring(child, encoding="unicode")

    return mapping, warnings


def _local_name(tag: str) -> str:
    """Strip any XML namespace prefix from an element tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


__all__ = ["split_pretext_by_section"]
