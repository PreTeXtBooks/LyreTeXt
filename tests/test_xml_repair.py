"""Tests for entity repair, the edit guard, and well-formedness diagnosis.

All three exist because of one observed failure. An editing agent handed
``<m>\\lambda&lt;1</m>`` reads the entity as the mathematics it stands for and
writes the character back literally::

    <m>\\lambda<1</m>

which is correct mathematics and invalid XML. The rewritten chapter then failed
to parse and refused to render, reporting only "not well-formed (invalid token):
line 819, column 25" — a line at which nothing looks wrong, because nothing *is*
wrong except the encoding.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyretext.edit.agents import MalformedEditError, edit_chapter
from lyretext.format import repair_xml_entities
from lyretext.review.checks import check_wellformed

# The real shape of the failure, taken from a chapter this actually happened to.
BROKEN = "<p>whether <m>\\lambda<1</m> or <m>\\lambda>1</m>.</p>"
FIXED = "<p>whether <m>\\lambda&lt;1</m> or <m>\\lambda>1</m>.</p>"


class TestRepairXmlEntities:
    def test_it_fixes_the_unescaped_comparison(self):
        text, repaired, _notes = repair_xml_entities(BROKEN)
        assert repaired
        assert text == FIXED
        ET.fromstring(text)  # parses now

    def test_it_fixes_a_bare_ampersand(self):
        text, repaired, _notes = repair_xml_entities("<p>Tom & Jerry</p>")
        assert repaired
        assert text == "<p>Tom &amp; Jerry</p>"

    def test_it_fixes_unescaped_code(self):
        text, repaired, _notes = repair_xml_entities("<c>x <- 1</c>")
        assert repaired and text == "<c>x &lt;- 1</c>"

    def test_real_markup_is_never_touched(self):
        """The invariant that makes this safe to run unattended."""
        text, repaired, _notes = repair_xml_entities(
            "<p>a <m>x<1</m> <em>b</em><!-- c --></p>"
        )
        assert repaired
        assert "<em>b</em>" in text
        assert "<!-- c -->" in text
        assert "</p>" in text

    def test_existing_entities_are_left_alone(self):
        text, _repaired, _notes = repair_xml_entities("<p>&amp; &#8212; &#x2014; a<1</p>")
        assert "&amp;amp;" not in text
        assert text.count("&amp;") == 1
        assert "&#8212;" in text and "&#x2014;" in text

    def test_well_formed_input_is_returned_untouched(self):
        text, repaired, notes = repair_xml_entities(FIXED)
        assert (text, repaired, notes) == (FIXED, False, [])

    def test_an_unrelated_fault_is_reported_not_papered_over(self):
        """A partial improvement must never be mistaken for a working artifact."""
        text, repaired, notes = repair_xml_entities("<p>unclosed")
        assert not repaired
        assert text == "<p>unclosed"
        assert notes

    def test_empty_input_is_not_an_error(self):
        assert repair_xml_entities("") == ("", False, [])


class TestEditGuard:
    """The editing agent must never replace a valid chapter with an invalid one."""

    def _state(self, tmp_path):
        out = tmp_path / "ch1.ptx"
        out.write_text(FIXED, encoding="utf-8")
        return {
            "source_path": str(tmp_path / "ch1.md"),
            "output_path": str(out),
            "chapter_id": "ch1",
            "instruction": "tidy the maths",
        }

    def _run(self, state, returned_xml):
        llm = MagicMock()
        llm.with_structured_output.return_value.invoke.return_value = {"xml": returned_xml}
        with patch("lyretext.edit.agents.create_llm", return_value=llm):
            return edit_chapter(state, None)

    def test_an_unescaped_entity_is_repaired_and_written(self, tmp_path):
        state = self._state(tmp_path)
        self._run(state, BROKEN.replace("whether", "whenever"))

        on_disk = Path(state["output_path"]).read_text(encoding="utf-8")
        ET.fromstring(on_disk)  # the whole point: it still parses
        assert "whenever" in on_disk  # …and the edit was kept

    def test_unrepairable_output_leaves_the_file_untouched(self, tmp_path):
        state = self._state(tmp_path)
        with pytest.raises(MalformedEditError) as caught:
            self._run(state, "<p>unbalanced <em>markup</p>")

        assert Path(state["output_path"]).read_text(encoding="utf-8") == FIXED
        assert "left unchanged" in str(caught.value)

    def test_the_error_says_which_line(self, tmp_path):
        state = self._state(tmp_path)
        with pytest.raises(MalformedEditError) as caught:
            self._run(state, "<chapter>\n<title>T</title>\n<p>oops</em>\n</chapter>")
        assert "Offending line" in str(caught.value)

    def test_valid_output_still_goes_through(self, tmp_path):
        state = self._state(tmp_path)
        self._run(state, "<p>rewritten</p>")
        assert "rewritten" in Path(state["output_path"]).read_text(encoding="utf-8")


class TestWellformedDiagnosis:
    """"line 819, column 25" is accurate and useless; these pin what replaced it."""

    def _issue(self, artifact):
        issues = check_wellformed({"artifact": artifact})["issues"]
        return issues[0] if issues else None

    def test_an_escaping_fault_names_the_character_and_quotes_the_line(self):
        issue = self._issue(BROKEN)
        assert "literal '<'" in issue.message
        assert "\\lambda<1" in issue.message  # the line the reader has to look at
        assert "&lt;" in issue.suggestion

    def test_multiple_roots_are_not_reported_as_an_escaping_fault(self):
        """Both faults put a '<' at the error column; only one is about escaping."""
        issue = self._issue("<section><title>A</title></section><section><title>B</title></section>")
        assert "more than one top-level element" in issue.message
        assert "one root" in issue.suggestion
        assert "&lt;" not in issue.suggestion

    def test_unbalanced_tags_say_so(self):
        issue = self._issue("<p><em>unclosed</p>")
        assert "unclosed or closed out of order" in issue.message

    def test_well_formed_input_reports_nothing(self):
        assert self._issue(FIXED) is None

    def test_empty_input_reports_nothing(self):
        assert self._issue("   ") is None

    def test_the_finding_is_an_error_and_offers_a_fix(self):
        issue = self._issue(BROKEN)
        assert issue.severity == "error"
        assert issue.line == 1
        assert issue.suggestion
