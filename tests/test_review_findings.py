"""Tests for finding identity, dismissal persistence, and check attribution.

Three things the review layer now owns that used to be scattered or absent:

  * ``group_key`` — one rule for "these findings are the same problem", shared by
    the gate UI (which collapses them into one Fix) and by finalize_review
    (which carries a dismissal across review passes)
  * dismissals surviving re-review — the sidecar is rewritten every pass, and
    human-driven fixing makes passes frequent
  * ``auto_fixable`` coming from the CheckSpec rather than from prose in the
    prompt, so a per-check constant stops masquerading as a per-finding judgement
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyretext.review.agents import finalize_review, run_check
from lyretext.review.checks import CheckSpec
from lyretext.review.grouping import group_key, normalise
from lyretext.review.structure import Issue


def _issue(line, **overrides):
    fields = {
        "check_id": "pretext_structure",
        "severity": "warn",
        "line": line,
        "message": "remove <md> from <p>",
        "suggestion": "lift the <md> out of the enclosing <p>",
    }
    fields.update(overrides)
    return Issue(**fields)


def _state(tmp_path, issues):
    return {
        "output_path": str(tmp_path / "chapter-1.ptx"),
        "chapter_id": "chapter-1",
        "issues": issues,
    }


def _sidecar(tmp_path):
    return tmp_path / ".lyretext" / "chapter-1.findings.json"


class TestGroupKey:
    """Findings group by the fix they call for, not by how they were worded."""

    def test_line_numbers_do_not_change_identity(self):
        assert group_key(
            check_id="c", message="bad <md> at line 7", suggestion="lift it"
        ) == group_key(
            check_id="c", message="bad <md> at line 214", suggestion="lift it"
        )

    def test_per_occurrence_wording_does_not_split_a_group(self):
        """The case that made grouping too strict to be useful.

        A check that names the offending element writes a different message for
        every occurrence while describing one systematic problem — and the
        suggestion names it too, in an xml:id.
        """
        def key(ordinal, xml_id):
            return group_key(
                check_id="pretext_structure",
                message=f"The definition '{ordinal} definition' lacks the required <statement> structure.",
                suggestion=f"Wrap the definition content in <definition xml:id='{xml_id}'><statement>...</statement></definition>.",
            )

        assert key("1.11", "def-fn-addition") == key("1.13", "def-fn-product")

    def test_different_fixes_stay_apart(self):
        assert group_key(
            check_id="c", message="m", suggestion="wrap in <m>"
        ) != group_key(
            check_id="c", message="m", suggestion="wrap in <me>"
        )

    def test_the_same_fix_groups_across_checks(self):
        """One edit resolves both, which is the only thing a group promises."""
        assert group_key(
            check_id="a", message="one wording", suggestion="wrap in <m>"
        ) == group_key(
            check_id="b", message="another wording", suggestion="wrap in <m>"
        )

    def test_suggestionless_findings_fall_back_to_their_message(self):
        assert group_key(check_id="c", message="same", suggestion=None) == group_key(
            check_id="c", message="same", suggestion=""
        )
        assert group_key(check_id="c", message="a", suggestion=None) != group_key(
            check_id="c", message="b", suggestion=None
        )

    def test_suggestionless_findings_do_not_cross_checks(self):
        """Without a suggestion to key on, they must not all collapse into one row."""
        assert group_key(check_id="a", message="same", suggestion=None) != group_key(
            check_id="b", message="same", suggestion=None
        )

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("line 7", "line #"),
            ("lines 7, 34 and 117", "lines #, # and #"),
            ("section 1.11", "section #"),
            ("the '1.11 definition' element", "the '#' element"),
            ('xml:id="def-fn-addition"', "xml:id='#'"),
            # Digits glued to other characters are content, not identity.
            ("wrap 2x in <m>", "wrap 2x in <m>"),
            ("use <h2>", "use <h2>"),
        ],
    )
    def test_normalisation_absorbs_per_instance_detail_only(self, text, expected):
        assert normalise(text) == expected

    def test_none_is_tolerated(self):
        assert normalise(None) == ""


class TestFinalizeReviewStampsGroupKeys:
    def test_identical_findings_share_a_key(self, tmp_path):
        result = finalize_review(_state(tmp_path, [_issue(7), _issue(34), _issue(117)]))
        keys = {i["group_key"] for i in result["findings"]["issues"]}
        assert len(keys) == 1

    def test_the_key_reaches_the_sidecar(self, tmp_path):
        finalize_review(_state(tmp_path, [_issue(7)]))
        on_disk = json.loads(_sidecar(tmp_path).read_text(encoding="utf-8"))
        assert on_disk["issues"][0]["group_key"]


class TestDismissalsSurviveReReview:
    def test_ignored_findings_stay_ignored(self, tmp_path):
        finalize_review(_state(tmp_path, [_issue(7), _issue(34)]))

        # The user dismisses the group through service.dismiss_issues.
        findings = json.loads(_sidecar(tmp_path).read_text(encoding="utf-8"))
        for issue in findings["issues"]:
            issue["ignored"] = True
        _sidecar(tmp_path).write_text(json.dumps(findings), encoding="utf-8")

        # A later pass re-derives the same findings from scratch, at moved lines.
        result = finalize_review(_state(tmp_path, [_issue(9), _issue(41)]))
        assert all(i["ignored"] for i in result["findings"]["issues"])
        assert result["findings"]["counts"] == {"error": 0, "warn": 0, "info": 0}

    def test_undismissed_findings_are_untouched(self, tmp_path):
        finalize_review(_state(tmp_path, [_issue(7)]))
        result = finalize_review(_state(tmp_path, [_issue(7), _issue(34, message="something else")]))
        assert [i["ignored"] for i in result["findings"]["issues"]] == [False, False]

    def test_a_dismissed_error_stops_blocking(self, tmp_path):
        finalize_review(_state(tmp_path, [_issue(7, severity="error")]))
        findings = json.loads(_sidecar(tmp_path).read_text(encoding="utf-8"))
        findings["issues"][0]["ignored"] = True
        _sidecar(tmp_path).write_text(json.dumps(findings), encoding="utf-8")

        result = finalize_review(_state(tmp_path, [_issue(7, severity="error")]))
        assert result["review_status"] == "passing"

    def test_a_corrupt_previous_sidecar_is_survivable(self, tmp_path):
        _sidecar(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        _sidecar(tmp_path).write_text("{not json", encoding="utf-8")
        result = finalize_review(_state(tmp_path, [_issue(7)]))
        assert result["findings"]["counts"]["warn"] == 1


class TestCheckOwnsAutoFixable:
    """auto_fixable is a property of the check, not something the LLM reports."""

    def _run(self, *, spec_auto_fixable):
        spec = CheckSpec(
            id="math_notation",
            name="Math",
            target_stage="translate",
            prompt_key="math_notation",
            auto_fixable=spec_auto_fixable,
        )
        llm = MagicMock()
        llm.with_structured_output.return_value.invoke.return_value = {
            "issues": [{"severity": "warn", "line": 7, "message": "bare $"}]
        }
        with patch("lyretext.review.agents.create_llm", return_value=llm):
            return run_check(spec)({"artifact": "<chapter/>", "chapter_id": "ch1"}, None)

    @pytest.mark.parametrize("flag", [True, False])
    def test_the_spec_decides(self, flag):
        issues = self._run(spec_auto_fixable=flag)["issues"]
        assert [i.auto_fixable for i in issues] == [flag]

    def test_check_id_is_attributed(self):
        issues = self._run(spec_auto_fixable=True)["issues"]
        assert issues[0].check_id == "math_notation"
