"""Tests for the PreTeXt -> HTML preview renderer.

The fixtures here are real pandoc `pretext.lua` output, not hand-written
PreTeXt: the renderer's job is to cover what the writer actually emits, so
drifting away from that vocabulary is the failure worth catching.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from lyretext.render import render_pretext

# Verbatim pandoc output for a chapter with a heading, prose, math, a table
# and a code block (see tests/README or run: pandoc x.md -t pretext.lua).
PANDOC_CHAPTER = """<section xml:id="measuring-spread">
  <title>Measuring Spread</title>

  <introduction>
    <p>
      An average doesn't tell the whole story.
    </p>
  </introduction>

  <subsection xml:id="variance">
    <title>Variance and Standard Deviation</title>

    <p>
      Sample variance <m>s^2</m> computes the average squared deviation:
    </p>

    <p>
      <md>s^2 = \\frac{1}{n-1} \\sum_{i=1}^{n} (X_i - \\bar{X})^2</md>
    </p>

    <program language="r">
    <code>
coffee_cups &lt;- c(3, 4, 2, 5, 4)
var(coffee_cups)
    </code>
    </program>

    <tabular>
      <row header="yes">
        <cell>Metric</cell>
        <cell>Value</cell>
      </row>
      <row>
        <cell>Mean</cell>
        <cell>3.6</cell>
      </row>
    </tabular>
  </subsection>
</section>
"""


class TestStructure:
    def test_renders_divisions_as_nested_headings(self):
        html = render_pretext(PANDOC_CHAPTER).html
        assert "<h2" in html and "Measuring Spread" in html
        assert "<h3" in html and "Variance and Standard Deviation" in html

    def test_xml_id_becomes_an_html_id(self):
        html = render_pretext(PANDOC_CHAPTER).html
        assert 'id="measuring-spread"' in html
        assert 'id="variance"' in html

    def test_output_is_well_formed(self):
        """The pane injects this HTML directly, so it must parse as a tree."""
        html = render_pretext(PANDOC_CHAPTER).html
        ET.fromstring(f"<root>{html}</root>")

    def test_no_block_elements_inside_a_p_tag(self):
        """pandoc wraps lists and display math in <p>; HTML forbids it.

        A browser auto-closes <p> at the first block child and orphans the
        rest, so emitting it verbatim silently wrecks the layout.
        """
        source = """<section><title>T</title>
          <p><ul><li>a</li></ul></p>
          <p><md>x = 1</md></p>
          <p>plain text only</p>
        </section>"""
        html = render_pretext(source).html
        for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", html, re.S):
            assert not re.search(r"<(div|ul|ol|dl|table|figure|blockquote|pre)\b", match.group(1))
        # …and the plain paragraph is still a real <p>.
        assert re.search(r"<p\b[^>]*>\s*plain text only\s*</p>", html)


class TestMath:
    def test_inline_math_uses_mathjax_inline_delimiters(self):
        html = render_pretext("<p>see <m>s^2</m> here</p>").html
        assert "\\(s^2\\)" in html

    def test_display_math_uses_display_delimiters(self):
        html = render_pretext("<p><me>x = 1</me></p>").html
        assert "\\[x = 1\\]" in html

    def test_aligned_md_is_wrapped_but_plain_md_is_not(self):
        aligned = render_pretext("<p><md>a &amp;= b</md></p>").html
        plain = render_pretext("<p><md>x = 1</md></p>").html
        assert "\\begin{aligned}" in aligned
        assert "\\begin{aligned}" not in plain

    def test_mrow_children_are_joined_as_alignment_lines(self):
        html = render_pretext("<md><mrow>a &amp;= b</mrow><mrow>c &amp;= d</mrow></md>").html
        assert "\\\\" in html and "\\begin{aligned}" in html

    def test_tex_special_characters_survive_escaping(self):
        """MathJax reads the DOM text, so & and < must round-trip."""
        html = render_pretext("<p><m>a &lt; b &amp; c</m></p>").html
        text = "".join(ET.fromstring(f"<root>{html}</root>").itertext())
        assert "a < b & c" in text


class TestBlocks:
    @pytest.mark.parametrize(
        "tag,label",
        [("theorem", "Theorem"), ("definition", "Definition"),
         ("example", "Example"), ("proof", "Proof"), ("remark", "Remark")],
    )
    def test_theorem_like_blocks_are_labelled(self, tag, label):
        html = render_pretext(f"<{tag}><p>body</p></{tag}>").html
        assert label in html
        assert f"ptx-block-{tag}" in html

    def test_a_block_title_is_shown_as_its_name(self):
        html = render_pretext(
            "<theorem><title>Pythagoras</title><p>body</p></theorem>"
        ).html
        assert "Pythagoras" in html
        # …and not also rendered as a heading.
        assert "<h" not in html

    def test_statement_wrapper_is_transparent(self):
        html = render_pretext(
            "<definition><statement><p>a group</p></statement></definition>"
        ).html
        assert "a group" in html


class TestVerbatim:
    def test_program_keeps_code_and_strips_pretty_printer_indentation(self):
        html = render_pretext(PANDOC_CHAPTER).html
        assert "coffee_cups &lt;- c(3, 4, 2, 5, 4)" in html
        # The writer indents <code> bodies to match the surrounding markup;
        # rendering that verbatim would indent every block by its nesting depth.
        assert "\n    coffee_cups" not in html

    def test_program_language_is_surfaced(self):
        html = render_pretext(PANDOC_CHAPTER).html
        assert "ptx-lang" in html and ">r<" in html


class TestTables:
    def test_header_rows_become_th(self):
        html = render_pretext(PANDOC_CHAPTER).html
        assert "<th>Metric</th>" in html
        assert "<td>Mean</td>" in html


    def test_malformed_tabular_is_reported_not_silently_dropped(self):
        """Found on a real LLM-edited chapter: HTML <tr>/<td> inside <tabular>.

        Matching only the expected <row>/<cell> children rendered an empty
        table and reported nothing wrong — the worst outcome for a pane whose
        job is to show what the markup actually says.
        """
        source = (
            "<tabular><tr><td>Metric</td><td>Value</td></tr>"
            "<tr><td>Mean</td><td>3.6</td></tr></tabular>"
        )
        result = render_pretext(source)
        assert "Metric" in result.html and "3.6" in result.html
        assert "tr" in result.unsupported
        assert result.warnings


class TestImages:
    def test_resolved_image_becomes_an_img(self):
        source = '<figure><image source="p.png"><shortdescription>A plot</shortdescription></image><caption>Cap</caption></figure>'
        result = render_pretext(source, asset_resolver=lambda s: f"/api/asset?path=/abs/{s}")
        assert '<img class="ptx-image"' in result.html
        assert 'alt="A plot"' in result.html
        assert "Cap" in result.html
        assert not result.warnings

    def test_missing_image_is_a_visible_placeholder_and_a_warning(self):
        result = render_pretext(
            '<figure><image source="p.png"/></figure>', asset_resolver=lambda s: None
        )
        assert "ptx-image-missing" in result.html
        assert any("p.png" in w for w in result.warnings)


class TestFailureIsVisible:
    def test_malformed_xml_reports_a_line_number_rather_than_raising(self):
        result = render_pretext("<section><p>unclosed</section>")
        assert result.error is not None
        assert result.error["line"] is not None
        assert result.html == ""

    def test_unknown_elements_are_reported_and_still_rendered(self):
        """Dropping unrecognised markup would let a conversion bug read clean."""
        result = render_pretext("<p>before <weirdtag>inner text</weirdtag> after</p>")
        assert "weirdtag" in result.unsupported
        assert "inner text" in result.html

    def test_dangling_xref_is_flagged(self):
        """pandoc-pretext emits bare <xref> for citations — a known gap."""
        result = render_pretext("<p>see <xref/></p>")
        assert "ptx-xref-dangling" in result.html
        assert any("xref" in w for w in result.warnings)

    def test_empty_input_is_not_an_error(self):
        result = render_pretext("")
        assert result.error is None
        assert "ptx-empty" in result.html


class TestEscaping:
    def test_text_content_is_escaped(self):
        html = render_pretext("<p>a &lt;script&gt;alert(1)&lt;/script&gt; b</p>").html
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_attribute_values_are_escaped(self):
        html = render_pretext('<p><url href="x&quot; onerror=&quot;alert(1)">t</url></p>').html
        assert 'onerror="' not in html


class TestUiIsNotCached:
    """The UI must revalidate, not serve from cache without asking.

    A browser holding a stale api-client.js next to a fresh app.js calls into
    a function that doesn't exist, which aborts the re-render mid-template and
    freezes the page — a failure that looks like a backend bug and isn't.
    """

    def test_static_assets_send_no_cache(self):
        from fastapi.testclient import TestClient

        from lyretext.api import app

        with TestClient(app) as client:
            for asset in ("/ui/app.js", "/ui/api-client.js", "/ui/index.html"):
                response = client.get(asset)
                assert response.status_code == 200, asset
                assert "no-cache" in response.headers.get("cache-control", ""), asset

    def test_revalidation_still_returns_304(self):
        """no-cache must not mean re-downloading the file every time."""
        from fastapi.testclient import TestClient

        from lyretext.api import app

        with TestClient(app) as client:
            first = client.get("/ui/app.js")
            etag = first.headers["etag"]
            second = client.get("/ui/app.js", headers={"If-None-Match": etag})
            assert second.status_code == 304


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
class TestAgainstRealPandocOutput:
    def test_a_converted_chapter_renders_with_no_unsupported_elements(self, tmp_path):
        """End-to-end: if the writer gains an element, this is what notices."""
        md = tmp_path / "ch.md"
        md.write_text(
            "# Title\n\nProse with *em*, **strong**, `code`, $x^2$ and a [link](http://e.com).\n\n"
            "- one\n- two\n\n1. first\n2. second\n\n"
            "$$\\int_0^1 f(x)\\,dx = 1$$\n\n"
            "::: {.theorem}\nA statement.\n:::\n\n"
            "::: {.proof}\nObvious.\n:::\n\n"
            "> quoted\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "```r\nx <- 1\n```\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["pandoc", str(md), "-t", "pretext.lua"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        ptx = completed.stdout
        assert ptx.strip(), "pandoc produced no output"

        result = render_pretext(ptx)
        assert result.error is None
        assert result.unsupported == [], (
            f"pandoc emitted element(s) the renderer doesn't handle: {result.unsupported}"
        )
        ET.fromstring(f"<root>{result.html}</root>")
        for expected in ("Title", "one", "first", "quoted", "x &lt;- 1", "\\[", "\\("):
            assert expected in result.html, f"missing {expected!r} from render"
