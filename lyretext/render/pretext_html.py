"""Deterministic PreTeXt -> HTML preview renderer.

This is a *preview* renderer, not an implementation of the PreTeXt spec. It
exists so the workspace's "Review render" pane can show what a chapter's
``.ptx`` actually says, next to the markup, while a human decides whether the
conversion is right.

Why hand-rolled rather than the real toolchain: authoritative PreTeXt HTML
comes from the PreTeXt-CLI's XSL stylesheets, which need a complete assembled
document (``<pretext><book>…``), a project directory, and resolvable ``xref``
targets. Chapters here are standalone fragments rooted at ``<section>``, with
the dangling ``xref``s pandoc-pretext is documented to emit — exactly the input
that toolchain rejects. It is also seconds-per-build, where this pane needs to
repaint on every edit. So the trade is deliberate: cover the vocabulary the
pandoc writer actually emits, and make anything outside it *visible* rather
than silently dropped (see ``unsupported``), so the gap is never mistaken for
correct output.

Math is emitted as TeX delimiters for MathJax to typeset client-side; this
module never tries to render math itself.
"""
from __future__ import annotations

import html
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# Divisions that carry a <title> and open a new heading level.
_HEADING_LEVEL: dict[str, int] = {
    "pretext": 1, "book": 1, "article": 1, "frontmatter": 1, "backmatter": 1,
    "chapter": 1,
    "section": 2, "appendix": 2, "preface": 2, "acknowledgement": 2,
    "colophon": 2, "dedication": 2, "biography": 2,
    "subsection": 3, "exercises": 3, "references": 3, "reading-questions": 3,
    "glossary": 3, "solutions": 3, "worksheet": 3,
    "subsubsection": 4,
    "paragraphs": 5, "subsubsubsection": 5,
}

# Untitled structural wrappers — render their children, add nothing visible.
_TRANSPARENT = {
    "introduction", "conclusion", "statement", "mainmatter", "body",
    "sidebyside", "stack", "task", "docinfo",
}

# Theorem-like and other captioned blocks: tag -> display label.
_BLOCKS: dict[str, str] = {
    "theorem": "Theorem", "lemma": "Lemma", "corollary": "Corollary",
    "proposition": "Proposition", "claim": "Claim", "fact": "Fact",
    "identity": "Identity", "algorithm": "Algorithm",
    "definition": "Definition", "axiom": "Axiom", "conjecture": "Conjecture",
    "principle": "Principle", "heuristic": "Heuristic", "hypothesis": "Hypothesis",
    "assumption": "Assumption",
    "example": "Example", "question": "Question", "problem": "Problem",
    "exercise": "Exercise", "activity": "Activity", "exploration": "Exploration",
    "investigation": "Investigation", "project": "Project",
    "remark": "Remark", "note": "Note", "observation": "Observation",
    "warning": "Warning", "convention": "Convention", "insight": "Insight",
    "assemblage": "", "aside": "Aside", "objectives": "Objectives",
    "outcomes": "Outcomes", "proof": "Proof",
}

# PreTeXt elements that render as block-level HTML, and so cannot legally sit
# inside a <p> (see the <p> handler).
_BLOCK_IN_P = {
    "ul", "ol", "dl", "me", "men", "md", "mdn", "blockquote", "program",
    "pre", "console", "cd", "tabular", "table", "figure", "sidebyside",
    "image", "listing", *_BLOCKS,
}

# Inline elements -> (open, close) HTML.
_INLINE: dict[str, tuple[str, str]] = {
    "em": ("<em>", "</em>"),
    "term": ('<b class="ptx-term">', "</b>"),
    "alert": ('<strong class="ptx-alert">', "</strong>"),
    "c": ("<code>", "</code>"),
    "delete": ("<del>", "</del>"),
    "insert": ("<ins>", "</ins>"),
    "stale": ("<s>", "</s>"),
    "foreign": ('<i class="ptx-foreign">', "</i>"),
    "pubtitle": ("<i>", "</i>"),
    "articletitle": ("“", "”"),
    "q": ("“", "”"),
    "sq": ("‘", "’"),
    "abbr": ("<abbr>", "</abbr>"),
    "acro": ("<abbr>", "</abbr>"),
    "init": ("<abbr>", "</abbr>"),
    "taxon": ("<i>", "</i>"),
    "quantity": ('<span class="ptx-quantity">', "</span>"),
}

# Empty elements standing in for a character.
_CHARS: dict[str, str] = {
    "nbsp": " ", "ellipsis": "…", "ndash": "–", "mdash": "—",
    "times": "×", "copyright": "©", "degree": "°",
    "trademark": "™", "registered": "®", "plusminus": "±",
    "midpoint": "·", "swungdash": "⁓", "lq": "“", "rq": "”",
    "lsq": "‘", "rsq": "’", "solidus": "/", "obelus": "÷",
    "less": "<", "greater": ">", "ampersand": "&",
}

# Rendered as literal wordmarks by PreTeXt.
_WORDMARKS = {
    "pretext": "PreTeXt", "latex": "LaTeX", "tex": "TeX", "html": "HTML",
    "webwork": "WeBWorK", "xml": "XML",
}


@dataclass
class RenderResult:
    """Outcome of one render attempt.

    ``error`` is set only when the XML could not be parsed at all; in that
    case ``html`` is empty. Everything else is best-effort: unknown markup is
    still rendered (visibly marked) and reported in ``unsupported``.
    """

    html: str = ""
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    error: dict | None = None


def render_pretext(
    xml_text: str,
    *,
    asset_resolver: Callable[[str], str | None] | None = None,
) -> RenderResult:
    """Render a PreTeXt fragment to a self-contained HTML body string.

    *asset_resolver* maps an ``<image source="...">`` value to a URL the
    browser can fetch, or None when the file can't be found — images then
    render as a labelled placeholder rather than a broken image.
    """
    if not (xml_text or "").strip():
        return RenderResult(html='<div class="ptx-empty">No PreTeXt output yet.</div>')

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        line, column = (exc.position if getattr(exc, "position", None) else (None, None))
        return RenderResult(
            error={"message": str(exc), "line": line, "column": column},
            warnings=[f"XML is not well-formed: {exc}"],
        )

    renderer = _Renderer(asset_resolver=asset_resolver)
    body = renderer.render(root)
    return RenderResult(
        html=body + renderer.footnotes_html(),
        warnings=renderer.warnings,
        unsupported=sorted(renderer.unsupported),
    )


class _Renderer:
    def __init__(self, *, asset_resolver: Callable[[str], str | None] | None = None) -> None:
        self._resolve_asset = asset_resolver
        self.warnings: list[str] = []
        self.unsupported: set[str] = set()
        self._footnotes: list[str] = []

    # -- entry point ------------------------------------------------------

    def render(self, element: ET.Element) -> str:
        return self._element(element, level=_HEADING_LEVEL.get(element.tag, 2))

    def footnotes_html(self) -> str:
        if not self._footnotes:
            return ""
        items = "".join(
            f'<li id="ptx-fn-{i}">{body}</li>' for i, body in enumerate(self._footnotes, 1)
        )
        return f'<section class="ptx-footnotes"><h6>Notes</h6><ol>{items}</ol></section>'

    # -- dispatch ---------------------------------------------------------

    def _children(self, element: ET.Element, level: int) -> str:
        return "".join(self._element(child, level) for child in element)

    def _content(self, element: ET.Element, level: int, *, skip: set[str] = frozenset()) -> str:
        """Render an element's mixed text/child content, in document order."""
        out = [_esc(element.text)]
        for child in element:
            if child.tag not in skip:
                out.append(self._element(child, level))
            out.append(_esc(child.tail))
        return "".join(out)

    def _element(self, element: ET.Element, level: int) -> str:
        tag = element.tag
        if not isinstance(tag, str):  # comments / processing instructions
            return ""

        for handler in (
            self._structural, self._math, self._block, self._list,
            self._code, self._media, self._inline,
        ):
            result = handler(element, level)
            if result is not None:
                return result

        self.unsupported.add(tag)
        inner = self._content(element, level)
        return (
            f'<span class="ptx-unknown" data-tag="{_esc(tag)}" '
            f'title="Unrecognised PreTeXt element &lt;{_esc(tag)}&gt;">{inner}</span>'
        )

    # -- handlers ---------------------------------------------------------

    def _structural(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag

        if tag in _TRANSPARENT:
            return f'<div class="ptx-{_esc(tag)}">{self._content(element, level)}</div>'

        if tag in _HEADING_LEVEL:
            child_level = _HEADING_LEVEL[tag]
            title = element.find("title")
            heading = ""
            if title is not None:
                depth = min(max(child_level, 1), 6)
                heading = (
                    f'<h{depth} class="ptx-heading ptx-heading-{_esc(tag)}">'
                    f"{self._content(title, child_level)}</h{depth}>"
                )
            attrs = self._id_attr(element)
            inner = self._content(element, child_level + 1, skip={"title", "subtitle"})
            return f'<section class="ptx-division ptx-{_esc(tag)}"{attrs}>{heading}{inner}</section>'

        if tag == "p":
            # The pandoc writer routinely wraps lists, display math and
            # verbatim blocks in <p>, which PreTeXt allows and HTML does not:
            # a browser silently auto-closes <p> at the first block child and
            # leaves the rest orphaned, wrecking the layout. Emit a <div>
            # instead whenever that would happen, keeping the same class so
            # it styles identically.
            wrapper = "div" if any(c.tag in _BLOCK_IN_P for c in element) else "p"
            inner = self._content(element, level)
            return f'<{wrapper} class="ptx-p"{self._id_attr(element)}>{inner}</{wrapper}>'

        if tag == "blockquote":
            return f"<blockquote>{self._content(element, level)}</blockquote>"

        if tag == "title":
            # A stray title outside the division handling above.
            return f'<div class="ptx-title">{self._content(element, level)}</div>'

        if tag == "fn":
            self._footnotes.append(self._content(element, level))
            n = len(self._footnotes)
            return f'<sup class="ptx-fn"><a href="#ptx-fn-{n}">{n}</a></sup>'

        return None

    def _math(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag
        if tag == "m":
            return f'<span class="ptx-m">\\({_esc(_flatten(element))}\\)</span>'

        if tag in ("me", "men", "md", "mdn"):
            numbered = tag.endswith("n")
            rows = [_flatten(r) for r in element.findall("mrow")]
            tex = " \\\\\n".join(rows) if rows else _flatten(element)
            # <md> is aligned display math; only wrap when there is actually
            # alignment to honour, so a plain $$…$$ stays a plain equation.
            if tag.startswith("md") and ("&" in tex or "\\\\" in tex):
                tex = f"\\begin{{aligned}}{tex}\\end{{aligned}}"
            cls = "ptx-display ptx-numbered" if numbered else "ptx-display"
            return f'<div class="{cls}">\\[{_esc(tex)}\\]</div>'

        return None

    def _block(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag
        if tag not in _BLOCKS:
            return None

        label = _BLOCKS[tag]
        title = element.find("title")
        title_html = (
            f' <span class="ptx-block-title">({self._content(title, level)})</span>'
            if title is not None else ""
        )
        head = (
            f'<div class="ptx-block-head">{_esc(label)}{title_html}</div>'
            if (label or title_html) else ""
        )
        inner = self._content(element, level, skip={"title"})
        return (
            f'<article class="ptx-block ptx-block-{_esc(tag)}"{self._id_attr(element)}>'
            f'{head}<div class="ptx-block-body">{inner}</div></article>'
        )

    def _list(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag

        if tag in ("ul", "ol"):
            return f"<{tag} class=\"ptx-{tag}\">{self._children(element, level)}</{tag}>"

        if tag == "dl":
            return f'<dl class="ptx-dl">{self._children(element, level)}</dl>'

        if tag == "li":
            title = element.find("title")
            if title is not None:
                # <dl> items carry their term in <title>; PreTeXt uses <li>
                # for both list flavours, so the title is what distinguishes
                # a description item from a plain bullet.
                term = self._content(title, level)
                body = self._content(element, level, skip={"title"})
                return f"<dt>{term}</dt><dd>{body}</dd>"
            return f"<li>{self._content(element, level)}</li>"

        if tag in ("table", "tabular"):
            return self._table(element, level)

        return None

    def _table(self, element: ET.Element, level: int) -> str | None:
        if element.tag == "table":
            title = element.find("title")
            caption = (
                f'<caption class="ptx-table-caption">{self._content(title, level)}</caption>'
                if title is not None else ""
            )
            inner = "".join(
                self._element(child, level) for child in element if child.tag != "title"
            )
            return f'<div class="ptx-table-wrap">{caption}{inner}</div>'

        # Anything that isn't <row>/<cell> is dispatched normally rather than
        # skipped. Matching only the expected children would silently swallow
        # a whole table when the markup is wrong — which is precisely the case
        # a review pane exists to catch (LLM-edited chapters have been seen
        # with HTML <tr>/<td> inside <tabular>).
        rows: list[str] = []
        stray: list[str] = []
        for child in element:
            if child.tag != "row":
                stray.append(self._element(child, level))
                continue
            header = (child.get("header") or "").lower() in ("yes", "true", "1")
            cell_tag = "th" if header else "td"
            cells: list[str] = []
            for cell in child:
                if cell.tag == "cell":
                    cells.append(f"<{cell_tag}>{self._content(cell, level)}</{cell_tag}>")
                else:
                    cells.append(f"<{cell_tag}>{self._element(cell, level)}</{cell_tag}>")
            rows.append(f"<tr>{''.join(cells)}</tr>")

        if stray and not rows:
            self.warnings.append(
                "A <tabular> contains no <row> elements — its contents are shown "
                "unformatted below."
            )
        table = (
            f'<div class="ptx-table-scroll"><table class="ptx-tabular">{"".join(rows)}</table></div>'
            if rows else ""
        )
        return table + "".join(stray)

    def _code(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag

        if tag == "program":
            language = element.get("language") or ""
            code = element.find("code")
            body = _dedent(_flatten(code if code is not None else element))
            lang_badge = (
                f'<span class="ptx-lang">{_esc(language)}</span>' if language else ""
            )
            return (
                f'<div class="ptx-program">{lang_badge}'
                f"<pre><code>{_esc(body)}</code></pre></div>"
            )

        if tag in ("pre", "console", "output", "input"):
            return f'<pre class="ptx-{_esc(tag)}">{_esc(_dedent(_flatten(element)))}</pre>'

        if tag == "code":
            # A bare <code> outside <program>.
            return f"<code>{_esc(_flatten(element))}</code>"

        if tag == "cd":
            return f'<pre class="ptx-cd">{_esc(_dedent(_flatten(element)))}</pre>'

        return None

    def _media(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag

        if tag == "figure":
            caption = element.find("caption")
            caption_html = (
                f'<figcaption class="ptx-caption">{self._content(caption, level)}</figcaption>'
                if caption is not None else ""
            )
            inner = "".join(
                self._element(child, level) for child in element if child.tag != "caption"
            )
            return f'<figure class="ptx-figure"{self._id_attr(element)}>{inner}{caption_html}</figure>'

        if tag == "image":
            return self._image(element, level)

        if tag == "caption":
            return f'<figcaption class="ptx-caption">{self._content(element, level)}</figcaption>'

        if tag == "shortdescription":
            return ""  # surfaced as the image's alt text instead

        if tag == "url":
            href = element.get("href", "")
            text = self._content(element, level) or _esc(href)
            return (
                f'<a class="ptx-url" href="{_esc(href)}" target="_blank" '
                f'rel="noopener noreferrer">{text}</a>'
            )

        if tag == "xref":
            ref = element.get("ref", "")
            text = self._content(element, level) or _esc(element.get("text") or ref or "?")
            if not ref:
                self.warnings.append(
                    "An <xref> has no ref attribute — pandoc-pretext emits these for "
                    "citations, and they need a <biblio> target adding."
                )
                return f'<span class="ptx-xref ptx-xref-dangling" title="unresolved cross-reference">{text}</span>'
            return f'<a class="ptx-xref" href="#{_esc(ref)}">{text}</a>'

        return None

    def _image(self, element: ET.Element, level: int) -> str:
        source = element.get("source", "")
        described = element.find("shortdescription")
        alt = _flatten(described) if described is not None else (element.get("alt") or source)
        width = element.get("width")
        style = f' style="width:{_esc(width)}"' if width else ""

        url = self._resolve_asset(source) if (self._resolve_asset and source) else None
        if url:
            return (
                f'<img class="ptx-image" src="{_esc(url)}" alt="{_esc(alt)}"{style} />'
            )

        if source:
            self.warnings.append(f"Image not found on disk: {source}")
        return (
            f'<div class="ptx-image-missing" title="{_esc(alt)}">'
            f'<span>image</span><code>{_esc(source or "(no source)")}</code></div>'
        )

    def _inline(self, element: ET.Element, level: int) -> str | None:
        tag = element.tag

        if tag in _INLINE:
            open_tag, close_tag = _INLINE[tag]
            return f"{open_tag}{self._content(element, level)}{close_tag}"

        if tag in _CHARS and not list(element) and not (element.text or "").strip():
            return _esc(_CHARS[tag])

        if tag in _WORDMARKS and not list(element) and not (element.text or "").strip():
            return f'<span class="ptx-wordmark">{_esc(_WORDMARKS[tag])}</span>'

        if tag == "br":
            return "<br />"

        return None

    # -- helpers ----------------------------------------------------------

    def _id_attr(self, element: ET.Element) -> str:
        ident = element.get(XML_ID) or element.get("id")
        return f' id="{_esc(ident)}"' if ident else ""


def _esc(text: str | None) -> str:
    return html.escape(text, quote=True) if text else ""


def _flatten(element: ET.Element | None) -> str:
    """All descendant text of *element*, markup discarded.

    Used for content that is TeX or source code rather than PreTeXt markup,
    where any child elements would be a mistake in the input.
    """
    if element is None:
        return ""
    return "".join(element.itertext())


def _dedent(text: str) -> str:
    """Strip the pretty-printer's indentation off a verbatim block.

    The pandoc writer indents <code>/<pre> bodies to match the surrounding
    markup, so the raw text carries leading whitespace that is presentation,
    not content — rendering it verbatim would indent every code block by the
    depth of its enclosing division.
    """
    return textwrap.dedent(text.strip("\n").rstrip()).strip("\n")
