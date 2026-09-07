"""Canonical formatting for PreTeXt artifacts.

Two producers now write the same ``.ptx``: pandoc's ``pretext.lua`` writer and
the editing agent. They indent differently, and an LLM cannot be reliably held
to *any* whitespace convention — it is a token-level property, so prompting
lowers the rate of drift but never removes it. Every drift event is noise in
the diff a human is trying to review, and it silently invalidates the line
numbers findings are recorded against.

Whitespace outside verbatim elements carries no meaning in PreTeXt, so
normalising it is a safe deterministic transform — the same argument that made
pandoc the right tool for conversion. Running both producers' output through
one formatter makes the artifact reproducible regardless of which touched it
last.

The safety invariant, which every design choice here serves:

    **Line breaks are only ever introduced at whitespace that already
    existed, and verbatim content is never touched at all.**

Breaking `here.<fn>note</fn>` across lines would insert a space into rendered
prose that the author did not write. So mixed content is tokenised into words
that may span a tag boundary when no whitespace separated them, and wrapping
chooses between "space" and "newline" only at real word gaps — both of which
collapse to a single space when rendered.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

INDENT = "  "
WRAP_WIDTH = 78

_XML_NS = "{http://www.w3.org/XML/1998/namespace}"

# Content preserved byte-for-byte: the whitespace *is* the content.
_VERBATIM = frozenset({
    "code", "pre", "cd", "console", "input", "output", "prompt",
    "latex-image", "asymptote", "sageplot", "sage", "tikz", "slate",
})

# Elements laid out on their own lines. Anything NOT listed is treated as
# inline — the conservative default, since flowing an unknown element keeps it
# glued to its neighbours, whereas giving it a line of its own would inject
# whitespace into prose.
_BLOCK = frozenset({
    # divisions
    "pretext", "book", "article", "frontmatter", "backmatter", "mainmatter",
    "chapter", "section", "subsection", "subsubsection", "subsubsubsection",
    "paragraphs", "appendix", "preface", "acknowledgement", "colophon",
    "dedication", "biography", "introduction", "conclusion", "exercises",
    "references", "glossary", "solutions", "worksheet", "reading-questions",
    "docinfo", "body", "task",
    # block content
    "p", "blockquote", "ul", "ol", "dl", "li", "program", "figure", "image",
    "caption", "shortdescription", "table", "tabular", "row", "cell",
    "sidebyside", "stack", "listing", "poem", "titlepage",
    "title", "subtitle", "author", "date", "statement", "hint", "answer",
    "solution",
    # display math
    "me", "men", "md", "mdn", "mrow", "intertext",
    # theorem-like
    "theorem", "lemma", "corollary", "proposition", "claim", "fact",
    "identity", "algorithm", "definition", "axiom", "conjecture", "principle",
    "heuristic", "hypothesis", "assumption", "example", "question", "problem",
    "exercise", "activity", "exploration", "investigation", "project",
    "remark", "note", "observation", "warning", "convention", "insight",
    "assemblage", "aside", "objectives", "outcomes", "proof", "case",
    "notation", "biblio",
})

# Children of these are packed without blank lines between them.
_COMPACT = frozenset({"ul", "ol", "dl", "tabular", "table", "row", "sidebyside", "stack"})


def format_pretext(xml_text: str) -> tuple[str, list[str]]:
    """Return (formatted_xml, notes).

    Never raises and never loses content: if the input cannot be parsed, or
    uses constructs this formatter can't round-trip, it is returned unchanged
    with a note saying why. Formatting is best-effort tidying, so it must
    never be able to damage an artifact.
    """
    notes: list[str] = []
    if not (xml_text or "").strip():
        return xml_text, notes

    if "<!DOCTYPE" in xml_text:
        # ElementTree discards the DTD, so reserialising would drop it.
        return xml_text, ["left unformatted: document has a DOCTYPE"]

    declaration = ""
    body = xml_text
    stripped = xml_text.lstrip()
    if stripped.startswith("<?xml"):
        end = stripped.find("?>")
        if end != -1:
            declaration = stripped[: end + 2]
            body = stripped[end + 2:]

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        parser.feed(body)
        root = parser.close()
    except ET.ParseError as exc:
        # Malformed output is a real and reportable condition (check_wellformed
        # catches it downstream) — don't let the formatter mask it by failing,
        # and don't let it write a mangled file either.
        return xml_text, [f"left unformatted: XML is not well-formed ({exc})"]

    foreign = _foreign_namespaces(root)
    if foreign:
        return xml_text, [
            "left unformatted: cannot round-trip namespace(s) "
            + ", ".join(sorted(foreign))
        ]

    lines: list[str] = []
    _write(root, 0, lines, notes)
    out = "\n".join(lines) + "\n"
    if declaration:
        out = declaration + "\n" + out
    return out, notes


# ---------------------------------------------------------------------------
# Inline flow
# ---------------------------------------------------------------------------

class _Flow:
    """Collects mixed content into words that wrapping may break between.

    A "word" may span tag boundaries: ``here.<fn>`` is one word, because no
    whitespace separated the text from the tag and introducing some would
    change the rendered prose.
    """

    def __init__(self) -> None:
        self.words: list[str] = []
        self._gap = True  # whitespace (or start of content) precedes the next atom

    def atom(self, text: str) -> None:
        if not text:
            return
        if self._gap or not self.words:
            self.words.append(text)
        else:
            self.words[-1] += text
        self._gap = False

    def text(self, raw: str | None) -> None:
        if not raw:
            return
        if raw[0].isspace():
            self._gap = True
        parts = raw.split()
        for index, part in enumerate(parts):
            if index:
                self._gap = True
            self.atom(_escape_text(part))
        if raw[-1].isspace():
            self._gap = True

    def element(self, element: ET.Element) -> None:
        if not isinstance(element.tag, str):  # comment inside mixed content
            self.atom(_comment(element))
            return
        name = _qname(element.tag)
        if name in _VERBATIM or not (list(element) or element.text):
            self.atom(_flat(element))
            return
        self.atom(f"<{name}{_attrs(element)}>")
        self.text(element.text)
        for child in element:
            self.element(child)
            self.text(child.tail)
        self.atom(f"</{name}>")


def _wrap(words: list[str], depth: int) -> list[str]:
    """Greedy fill. Breaks only between words, i.e. at pre-existing gaps."""
    indent = INDENT * depth
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and len(indent) + len(candidate) > WRAP_WIDTH:
            lines.append(indent + current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(indent + current)
    return lines


# ---------------------------------------------------------------------------
# Block layout
# ---------------------------------------------------------------------------

def _write(element: ET.Element, depth: int, out: list[str], notes: list[str]) -> None:
    indent = INDENT * depth

    if not isinstance(element.tag, str):
        out.append(indent + _comment(element))
        return

    name = _qname(element.tag)
    attrs = _attrs(element)

    if name in _VERBATIM:
        # The whitespace is the content — emit the text exactly, with the tags
        # tight against it so no character is added or removed.
        if list(element):
            notes.append(f"<{name}> contains markup; left as-is")
            out.append(indent + _flat(element))
            return
        out.append(f"{indent}<{name}{attrs}>{_escape_text(element.text or '')}</{name}>")
        return

    children = list(element)
    if not children and not (element.text or "").strip():
        out.append(f"{indent}<{name}{attrs}/>")
        return

    if not any(_is_block(child) for child in children):
        flow = _Flow()
        flow.text(element.text)
        for child in children:
            flow.element(child)
            flow.text(child.tail)
        single = f"{indent}<{name}{attrs}>{' '.join(flow.words)}</{name}>"
        if len(single) <= WRAP_WIDTH:
            out.append(single)
        else:
            out.append(f"{indent}<{name}{attrs}>")
            out.extend(_wrap(flow.words, depth + 1))
            out.append(f"{indent}</{name}>")
        return

    out.append(f"{indent}<{name}{attrs}>")
    _write_mixed(element, depth + 1, out, notes, compact=name in _COMPACT)
    out.append(f"{indent}</{name}>")


def _write_mixed(
    element: ET.Element, depth: int, out: list[str], notes: list[str], *, compact: bool
) -> None:
    """Emit children, flowing runs of inline content between block children."""
    flow = _Flow()
    emitted = False

    def flush() -> None:
        nonlocal emitted, flow
        if flow.words:
            if emitted and not compact:
                out.append("")
            out.extend(_wrap(flow.words, depth))
            emitted = True
        flow = _Flow()

    flow.text(element.text)
    for child in element:
        if _is_block(child):
            flush()
            if emitted and not compact:
                out.append("")
            _write(child, depth, out, notes)
            emitted = True
        else:
            flow.element(child)
        flow.text(child.tail)
    flush()


def _is_block(element: ET.Element) -> bool:
    if not isinstance(element.tag, str):
        return True  # comments get their own line
    return _qname(element.tag) in _BLOCK


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _qname(tag: str) -> str:
    if tag.startswith(_XML_NS):
        return "xml:" + tag[len(_XML_NS):]
    return tag


def _attrs(element: ET.Element) -> str:
    return "".join(
        f' {_qname(key)}="{_escape_attr(value)}"' for key, value in element.attrib.items()
    )


def _flat(element: ET.Element) -> str:
    """Serialise an element to a single string with no reformatting at all."""
    if not isinstance(element.tag, str):
        return _comment(element)
    name = _qname(element.tag)
    attrs = _attrs(element)
    if not list(element) and not element.text:
        return f"<{name}{attrs}/>"
    inner = _escape_text(element.text or "")
    for child in element:
        inner += _flat(child) + _escape_text(child.tail or "")
    return f"<{name}{attrs}>{inner}</{name}>"


def _comment(element: ET.Element) -> str:
    return f"<!--{element.text or ''}-->"


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _foreign_namespaces(root: ET.Element) -> set[str]:
    """Namespaces other than the built-in xml: one, which we can't re-prefix.

    ElementTree gives Clark notation ({uri}local) with the original prefix
    lost, so reserialising an xinclude or MathML document would invent prefixes
    and change the file. Better to leave such a document alone.
    """
    found: set[str] = set()
    for element in root.iter():
        names = [element.tag] if isinstance(element.tag, str) else []
        names.extend(element.attrib)
        for name in names:
            if name.startswith("{") and not name.startswith(_XML_NS):
                found.add(name[1: name.index("}")])
    return found
