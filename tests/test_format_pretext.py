"""Tests for canonical PreTeXt formatting.

The formatter exists to make artifact whitespace reproducible across its two
producers (pandoc and the editing agent). It is only worth having if it can
never damage a file, so most of this is about what it must *not* change.
"""
from __future__ import annotations

import glob
import re
import xml.etree.ElementTree as ET

import pytest

from lyretext.format import format_pretext
from lyretext.format.pretext_fmt import _BLOCK, _VERBATIM, _qname

# ---------------------------------------------------------------------------
# Structural comparison helpers
# ---------------------------------------------------------------------------


def _parse(text: str) -> ET.Element:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    parser.feed(text)
    return parser.close()


def _shape(element: ET.Element) -> list:
    """Tags and attributes in document order — must survive formatting."""
    if not isinstance(element.tag, str):
        return ["#comment"]
    out: list = [(_qname(element.tag), tuple(element.attrib.items()))]
    for child in element:
        out.extend(_shape(child))
    return out


def _flow_text(element: ET.Element) -> str:
    """Text of this element's *inline* content, whitespace collapsed.

    Stops at block children, because a block boundary already renders as a
    break — injecting a newline there changes nothing. Within a run of inline
    content it changes everything, which is what this captures.
    """
    parts = [element.text or ""]
    for child in element:
        if isinstance(child.tag, str) and _qname(child.tag) in _BLOCK:
            parts.append(" ")  # block boundary: normalise to one gap
        else:
            parts.append(_flow_text(child))
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts))


def _flows(element: ET.Element) -> list[str]:
    """Inline flow of every element.

    Leading/trailing whitespace *inside a block element* is inert — `<p>x</p>`
    and `<p>\\n  x\\n</p>` render identically — so it is normalised away. Inside
    an inline element it is not (`a<em>b</em>` vs `a<em> b</em>` differ), so
    those are compared exactly.
    """
    out: list[str] = []
    for e in element.iter():
        if not isinstance(e.tag, str):
            continue
        text = _flow_text(e)
        out.append(text.strip() if _qname(e.tag) in _BLOCK else text)
    return out


def _verbatim_bodies(element: ET.Element) -> list[str]:
    return [
        e.text or ""
        for e in element.iter()
        if isinstance(e.tag, str) and _qname(e.tag) in _VERBATIM
    ]


# Block-level tags in the renderer's HTML output. Whitespace immediately
# inside one of these is inert — a browser collapses it at the boundary — so
# it is normalised away before comparing two renders.
_HTML_BLOCK = {
    "root", "section", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "tr", "td", "th", "pre",
    "figure", "figcaption", "article", "blockquote", "caption",
}


def _html_flow(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        parts.append(" " if child.tag in _HTML_BLOCK else _html_flow(child))
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts))


def _render_signature(html: str) -> list:
    root = ET.fromstring(f"<root>{html}</root>")
    return [
        (
            e.tag,
            tuple(sorted(e.attrib.items())),
            _html_flow(e).strip() if e.tag in _HTML_BLOCK else _html_flow(e),
        )
        for e in root.iter()
    ]


def assert_preserves(source: str) -> str:
    """Format *source* and assert every invariant, returning the result."""
    formatted, notes = format_pretext(source)
    assert not notes, notes

    before, after = _parse(source), _parse(formatted)
    assert _shape(before) == _shape(after), "element/attribute structure changed"
    assert _flows(before) == _flows(after), "inline whitespace changed"
    assert _verbatim_bodies(before) == _verbatim_bodies(after), "verbatim content changed"

    again, _ = format_pretext(formatted)
    assert again == formatted, "formatting is not idempotent"
    return formatted


# ---------------------------------------------------------------------------


class TestTheGlueInvariant:
    """Line breaks may only land where whitespace already was."""

    def test_a_tag_touching_text_is_never_split_from_it(self):
        """`here.<fn>` must not become `here.\\n<fn>` — that adds a space."""
        source = (
            "<p>" + "padding words " * 12 + "sentence ends here.<fn>The footnote "
            "body which is itself quite long and will need wrapping.</fn> and it "
            "continues afterwards with more text.</p>"
        )
        formatted = assert_preserves(source)
        assert "\n" in formatted, "test is vacuous unless wrapping occurred"
        assert re.search(r"here\.<fn>", formatted), "tag was split from its text"

    def test_no_space_appears_around_inline_tags(self):
        source = "<p>H<m>{}_{2}</m>O and x<m>{}^{2}</m> in a sentence.</p>"
        formatted = assert_preserves(source)
        assert "H<m>{}_{2}</m>O" in formatted

    def test_attribute_values_containing_spaces_are_not_wrapped(self):
        source = (
            "<p>" + "filler " * 14
            + '<url href="https://example.com/a path/with spaces.html">link text</url>'
            + " trailing words here.</p>"
        )
        formatted = assert_preserves(source)
        assert 'href="https://example.com/a path/with spaces.html"' in formatted

    def test_existing_gaps_are_where_breaks_land(self):
        source = "<p>" + " ".join(f"word{i}" for i in range(60)) + "</p>"
        formatted = assert_preserves(source)
        for line in formatted.splitlines():
            assert len(line) <= 80, line


class TestVerbatimIsUntouched:
    def test_code_body_is_byte_identical(self):
        body = "\nif x:\n        deeply_indented()\n   odd_indent()\n"
        source = f'<program language="python"><code>{body}</code></program>'
        formatted = assert_preserves(source)
        assert body in formatted

    def test_pre_body_is_byte_identical(self):
        body = "\n## Variance: 1.3 \n##   Std Dev: 1.14\n"
        formatted = assert_preserves(f"<pre>{body}</pre>")
        assert body in formatted

    def test_verbatim_escaping_round_trips(self):
        source = "<pre>a &lt;- c(1, 2) &amp; b</pre>"
        formatted = assert_preserves(source)
        assert "&lt;-" in formatted and "&amp;" in formatted


class TestStructurePreserved:
    def test_xml_id_keeps_its_prefix(self):
        """ElementTree stores xml:id in Clark notation; ns0:id would be wrong."""
        formatted = assert_preserves('<section xml:id="ch1"><title>T</title></section>')
        assert 'xml:id="ch1"' in formatted
        assert "ns0:" not in formatted

    def test_comments_survive(self):
        source = "<section><!-- horizontal rule omitted --><p>text</p></section>"
        formatted = assert_preserves(source)
        assert "<!-- horizontal rule omitted -->" in formatted

    def test_empty_elements_stay_empty(self):
        formatted = assert_preserves('<p>a<nbsp/>b <xref ref="fig-1"/></p>')
        assert "<nbsp/>" in formatted and '<xref ref="fig-1"/>' in formatted

    def test_attribute_order_is_stable(self):
        formatted = assert_preserves('<image source="a.png" width="60%"/>')
        assert formatted.index("source=") < formatted.index("width=")


class TestRefusesRatherThanDamages:
    """Anything it can't round-trip must come back untouched."""

    def test_malformed_xml_is_returned_unchanged(self):
        source = "<section><p>unclosed</section>"
        out, notes = format_pretext(source)
        assert out == source
        assert any("not well-formed" in n for n in notes)

    def test_doctype_is_returned_unchanged(self):
        source = '<!DOCTYPE pretext><pretext><book><title>T</title></book></pretext>'
        out, notes = format_pretext(source)
        assert out == source
        assert any("DOCTYPE" in n for n in notes)

    def test_foreign_namespaces_are_returned_unchanged(self):
        source = (
            '<section xmlns:xi="http://www.w3.org/2001/XInclude">'
            '<xi:include href="ch1.ptx"/></section>'
        )
        out, notes = format_pretext(source)
        assert out == source
        assert any("namespace" in n for n in notes)

    def test_empty_input_is_returned_unchanged(self):
        assert format_pretext("")[0] == ""
        assert format_pretext("   ")[0] == "   "

    def test_xml_declaration_is_kept(self):
        source = '<?xml version="1.0" encoding="UTF-8"?>\n<section><p>x</p></section>'
        out, notes = format_pretext(source)
        assert not notes
        assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')


class TestLayout:
    def test_short_elements_stay_on_one_line(self):
        formatted = assert_preserves("<section><title>Measuring Spread</title></section>")
        assert "<title>Measuring Spread</title>" in formatted

    def test_list_items_are_packed_without_blank_lines(self):
        source = "<ul><li>one</li><li>two</li><li>three</li></ul>"
        formatted = assert_preserves(source)
        assert "\n\n" not in formatted

    def test_sibling_blocks_are_separated_by_a_blank_line(self):
        source = "<section><p>first</p><p>second</p></section>"
        formatted = assert_preserves(source)
        assert "\n\n" in formatted

    def test_nesting_is_indented_by_two_spaces(self):
        formatted = assert_preserves("<section><subsection><p>x</p></subsection></section>")
        assert "\n  <subsection>" in formatted
        assert "\n    <p>x</p>" in formatted


class TestConvergesTheTwoProducers:
    def test_differently_indented_inputs_format_identically(self):
        """The whole point: pandoc's layout and an agent's layout converge."""
        pandoc_style = (
            "<section>\n  <title>T</title>\n\n  <p>\n    Some prose here.\n  </p>\n</section>"
        )
        agent_style = "<section><title>T</title><p>Some prose here.</p></section>"
        sprawling = (
            "<section>\n\n\n    <title>T</title>\n        <p>Some\n\n prose\n here.</p>\n\n</section>"
        )
        results = {format_pretext(s)[0] for s in (pandoc_style, agent_style, sprawling)}
        assert len(results) == 1, results


class TestWiredIntoBothProducers:
    """Formatting has to be applied at *every* write or it achieves nothing."""

    SPRAWL = "<section>\n\n\n        <title>T</title>\n<p>prose here</p>\n\n</section>"

    def _config(self, **overrides):
        options = {
            "execution_mode": "direct", "apply_mode": "auto_apply",
            "create_backup": False, "provider": "gemini", "verbosity": "normal",
            "pipeline": "rmd", "pandoc_exe": None, "pandoc_writer": "pretext.lua",
            "auto_edit": True, "max_edit_iterations": 2, "format_output": True,
        }
        options.update(overrides)
        return {"configurable": {"runtime_options": {"global_options": options}}}

    def test_pandoc_output_is_formatted_on_write(self, tmp_path):
        from unittest.mock import patch

        from lyretext.translate.agents import translate_chapter

        source = tmp_path / "ch.md"
        source.write_text("# T\n", encoding="utf-8")
        state = {"source_path": str(source), "output_path": str(tmp_path / "ch.ptx"),
                 "chapter_id": "ch"}

        with patch("lyretext.translate.agents.convert_markdown_to_pretext",
                   return_value=(self.SPRAWL, [])):
            result = translate_chapter(state, self._config())

        assert result["pretext_output"] == format_pretext(self.SPRAWL)[0]
        assert (tmp_path / "ch.ptx").read_text(encoding="utf-8") == result["pretext_output"]

    def test_agent_output_is_formatted_on_write(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from lyretext.edit.agents import edit_chapter

        out = tmp_path / "ch.ptx"
        out.write_text("<section><title>Before</title></section>", encoding="utf-8")
        state = {"chapter_id": "ch", "output_path": str(out),
                 "instruction": "retitle it", "edit_iterations": 0}

        llm = MagicMock()
        llm.with_structured_output.return_value.invoke.return_value = {"xml": self.SPRAWL}
        with patch("lyretext.edit.agents.create_llm", return_value=llm):
            result = edit_chapter(state, self._config())

        assert result["pretext_output"] == format_pretext(self.SPRAWL)[0]
        assert out.read_text(encoding="utf-8") == result["pretext_output"]

    def test_a_pure_reflow_by_the_agent_writes_nothing(self, tmp_path):
        """The property that keeps recorded line numbers valid.

        If the agent returns the same content laid out differently, formatting
        collapses the difference to nothing, so the file is left alone and any
        findings anchored to its lines still point at the right places.
        """
        from unittest.mock import MagicMock, patch

        from lyretext.edit.agents import edit_chapter

        canonical = format_pretext("<section><title>T</title><p>prose here</p></section>")[0]
        out = tmp_path / "ch.ptx"
        out.write_text(canonical, encoding="utf-8")
        before = out.stat().st_mtime_ns

        llm = MagicMock()
        llm.with_structured_output.return_value.invoke.return_value = {"xml": self.SPRAWL}
        with patch("lyretext.edit.agents.create_llm", return_value=llm):
            edit_chapter({"chapter_id": "ch", "output_path": str(out),
                          "instruction": "tidy up"}, self._config())

        assert out.read_text(encoding="utf-8") == canonical
        assert out.stat().st_mtime_ns == before, "file was rewritten despite no change"

    def test_format_output_false_leaves_the_artifact_alone(self, tmp_path):
        from unittest.mock import patch

        from lyretext.translate.agents import translate_chapter

        source = tmp_path / "ch.md"
        source.write_text("# T\n", encoding="utf-8")
        state = {"source_path": str(source), "output_path": str(tmp_path / "ch.ptx"),
                 "chapter_id": "ch"}

        with patch("lyretext.translate.agents.convert_markdown_to_pretext",
                   return_value=(self.SPRAWL, [])):
            result = translate_chapter(state, self._config(format_output=False))

        assert result["pretext_output"] == self.SPRAWL


class TestRealCorpus:
    """Every .ptx this project has produced, held to the same invariants."""

    CORPUS = sorted(
        glob.glob("output/*/*.ptx")
        + glob.glob("demo/**/*.ptx", recursive=True)
        + glob.glob("examples/**/*.ptx", recursive=True)
    )

    def test_corpus_is_not_empty(self):
        assert self.CORPUS, "no .ptx files found to check"

    @pytest.mark.parametrize("path", CORPUS)
    def test_formatting_preserves_every_real_artifact(self, path):
        source = open(path, encoding="utf-8").read()
        formatted, notes = format_pretext(source)
        if notes:
            # The only acceptable refusals are the documented ones.
            assert any(k in notes[0] for k in ("not well-formed", "DOCTYPE", "namespace"))
            assert formatted == source
            return
        assert_preserves(source)

    @pytest.mark.parametrize("path", CORPUS)
    def test_formatting_does_not_change_the_rendered_document(self, path):
        """The end-to-end statement of safety, via an independent code path.

        assert_preserves reasons about the source tree; this checks the thing a
        reader actually sees. If formatting ever moved a space that mattered,
        the rendered output would differ even where the source comparison was
        satisfied.
        """
        from lyretext.render import render_pretext

        source = open(path, encoding="utf-8").read()
        formatted, notes = format_pretext(source)
        if notes:
            return
        before, after = render_pretext(source), render_pretext(formatted)
        if before.error:
            assert after.error, "formatting must not mask a parse error"
            return
        assert _render_signature(before.html) == _render_signature(after.html)
