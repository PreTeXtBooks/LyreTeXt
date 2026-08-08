"""Repair the one way an LLM reliably breaks a PreTeXt artifact.

An editing agent handed ``<m>\\lambda&lt;1</m>`` reads the entity as the
mathematics it stands for and writes the character back literally:

    <m>\\lambda<1</m>

which is no longer XML — ``<1`` cannot start a tag, so the whole chapter stops
parsing and stops rendering. The same happens to ``&`` in alignment markup and
to ``<`` in code (``x <- 1``). It is a stereotyped, purely mechanical failure:
the model is right about the mathematics and wrong about the encoding.

The repair is correspondingly narrow. Only characters that *cannot* be markup
are escaped:

  * ``<`` not followed by a name start, ``/``, ``!`` or ``?`` — so ``<p>``,
    ``</m>``, ``<!--`` and ``<?xml`` are all left alone, while ``<1`` and
    ``< `` are escaped
  * ``&`` that does not begin a well-formed entity reference — so ``&amp;``
    and ``&#x2014;`` survive untouched

Anything ambiguous is left as-is and reported as unrepairable, because turning
real markup into text would silently destroy the document. This never runs on
input that already parses.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# A '<' that opens something: an element, a closing tag, a comment/CDATA, or a
# processing instruction. Everything else is a literal less-than sign.
_MARKUP_OPEN = re.compile(r"<(?=[A-Za-z_/!?])")
# A '&' that opens a named, decimal or hex character reference.
_ENTITY = re.compile(r"&(?:[A-Za-z_][\w.\-]*|#\d+|#[xX][0-9A-Fa-f]+);")


def _escape_stray(text: str) -> str:
    """Escape only the '<' and '&' that cannot be the start of markup."""
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "<":
            if _MARKUP_OPEN.match(text, i):
                out.append(char)
            else:
                out.append("&lt;")
        elif char == "&":
            if _ENTITY.match(text, i):
                out.append(char)
            else:
                out.append("&amp;")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def repair_xml_entities(xml_text: str) -> tuple[str, bool, list[str]]:
    """Return (text, repaired, notes) for an artifact that failed to parse.

    ``repaired`` is True only when the returned text actually parses — a repair
    that doesn't fix the document is reported as a failure rather than written,
    so a partial improvement can never be mistaken for a working artifact.
    """
    if not (xml_text or "").strip():
        return xml_text, False, []

    try:
        ET.fromstring(xml_text)
    except ET.ParseError:
        pass
    else:
        return xml_text, False, []  # nothing to do

    candidate = _escape_stray(xml_text)
    if candidate == xml_text:
        return xml_text, False, ["no unescaped '<' or '&' found; not an escaping fault"]

    try:
        ET.fromstring(candidate)
    except ET.ParseError as exc:
        return xml_text, False, [
            f"escaping stray '<'/'&' did not make the document parse ({exc})"
        ]

    escaped = (candidate.count("&lt;") - xml_text.count("&lt;")) + (
        candidate.count("&amp;") - xml_text.count("&amp;")
    )
    return candidate, True, [
        f"re-escaped {escaped} literal '<'/'&' character(s) that had been "
        "written as text rather than entities"
    ]
