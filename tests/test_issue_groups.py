"""Tests for grouped findings operations in the service layer.

The check-agents report one systematic problem once per occurrence, so the UI
collapses findings whose text matches bar the line number into a single row.
What the backend owes that row is a *single* edit_chapter run covering every
occurrence — these tests pin the index selection, the instruction it builds,
and the fact that ignored occurrences never get resent.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lyretext.service import (
    _fix_instruction,
    _select_issues,
    dismiss_issue,
    dismiss_issues,
    fix_issues,
    fix_single_issue,
    restore_issues,
)


def _issue(line, *, message="remove <md> from <p>", suggestion="lift the <md> out of the <p>", **overrides):
    issue = {
        "check_id": "pretext_structure",
        "severity": "warn",
        "line": line,
        "message": message,
        "suggestion": suggestion,
        "auto_fixable": True,
        "ignored": False,
    }
    issue.update(overrides)
    return issue


@pytest.fixture
def sidecar(tmp_path):
    """A findings sidecar on disk, with _findings_sidecar_path pointed at it."""
    path = tmp_path / "chapter-1.findings.json"
    path.write_text(
        json.dumps({
            "chapter_id": "chapter-1",
            "review_status": "passing",
            "issues": [_issue(7), _issue(34), _issue(56), _issue(117)],
        }),
        encoding="utf-8",
    )
    with patch("lyretext.service._findings_sidecar_path", return_value=path):
        yield path


class TestSelectIssues:
    ISSUES = [_issue(7), _issue(34), _issue(56)]

    def test_returns_document_order_regardless_of_input_order(self):
        indices, error = _select_issues(self.ISSUES, [2, 0], "chapter-1")
        assert error is None
        assert indices == [0, 2]

    def test_deduplicates(self):
        indices, error = _select_issues(self.ISSUES, [1, 1, 1], "chapter-1")
        assert error is None
        assert indices == [1]

    def test_empty_selection_is_an_error(self):
        _indices, error = _select_issues(self.ISSUES, [], "chapter-1")
        assert error is not None and "at least one" in error["error"]

    @pytest.mark.parametrize("bad", [-1, 3, 99])
    def test_out_of_range_is_an_error(self, bad):
        _indices, error = _select_issues(self.ISSUES, [bad], "chapter-1")
        assert error is not None and "out of range" in error["error"]


class TestFixInstruction:
    def test_single_issue_names_its_line(self):
        text = _fix_instruction([_issue(7)])
        assert "Line 7:" in text
        assert "remove <md> from <p>" in text
        assert "lift the <md> out of the <p>" in text

    def test_one_systematic_problem_is_stated_once(self):
        text = _fix_instruction([_issue(7), _issue(34), _issue(56)])
        assert "Lines 7, 34, 56:" in text
        assert "occurring 3 times" in text
        # Stated once, not repeated per occurrence — that's what makes twenty
        # occurrences cheap to send rather than twenty times the tokens.
        assert text.count("remove <md> from <p>") == 1

    def test_a_mixed_queue_enumerates_each_distinct_fix(self):
        """The batching case: unrelated findings applied in one pass."""
        text = _fix_instruction([
            _issue(7), _issue(34),
            _issue(12, message="bare $ delimiters", suggestion="wrap in <m>"),
        ])
        assert "all 2 of the following fixes" in text
        assert "1." in text and "2." in text
        assert "Lines 7, 34:" in text
        assert "Line 12:" in text
        assert "wrap in <m>" in text

    def test_a_group_with_per_instance_detail_is_enumerated_not_collapsed(self):
        """One row, but the agent still gets each occurrence's own text.

        Findings group by the *shape* of their fix, so a group can hold
        occurrences whose suggestions name different targets — one xml:id per
        definition. Restating only the first would tell the agent to apply that
        definition's fix to all of them.
        """
        text = _fix_instruction([
            _issue(7,
                   message="The definition '1.11 definition' lacks <statement>.",
                   suggestion="Wrap it in <definition xml:id='def-addition'><statement>…"),
            _issue(34,
                   message="The definition '1.13 definition' lacks <statement>.",
                   suggestion="Wrap it in <definition xml:id='def-product'><statement>…"),
        ])
        assert "2 related findings" in text
        assert "occurring 2 times" not in text
        # Both targets survive — this is the assertion that matters.
        assert "def-addition" in text and "def-product" in text
        assert "Line 7:" in text and "Line 34:" in text

    def test_identical_occurrences_still_collapse(self):
        """The saving is only safe when the words really are the same."""
        text = _fix_instruction([_issue(n) for n in (7, 34, 56, 117, 228)])
        assert "occurring 5 times" in text
        assert text.count("lift the <md> out of the <p>") == 1

    def test_it_tells_the_agent_not_to_touch_anything_else(self):
        assert "Change nothing else" in _fix_instruction([_issue(7), _issue(34)])

    def test_lineless_issues_do_not_produce_a_dangling_line_list(self):
        text = _fix_instruction([_issue(None), _issue(None)])
        assert "lines" not in text
        assert "occurring 2 times" in text


class TestDismissIssues:
    def test_marks_every_selected_issue_ignored(self, sidecar):
        findings = dismiss_issues("run", "chapter-1", [0, 2], None)
        assert [i["ignored"] for i in findings["issues"]] == [True, False, True, False]
        # …and persists, so the next poll sees it
        on_disk = json.loads(sidecar.read_text(encoding="utf-8"))
        assert [i["ignored"] for i in on_disk["issues"]] == [True, False, True, False]

    def test_singular_wrapper_still_works(self, sidecar):
        findings = dismiss_issue("run", "chapter-1", 1, None)
        assert [i["ignored"] for i in findings["issues"]] == [False, True, False, False]

    def test_out_of_range_leaves_the_sidecar_untouched(self, sidecar):
        result = dismiss_issues("run", "chapter-1", [0, 99], None)
        assert "error" in result
        on_disk = json.loads(sidecar.read_text(encoding="utf-8"))
        assert not any(i["ignored"] for i in on_disk["issues"])


class TestRestoreIssues:
    """The way back from a mis-click.

    finalize_review now carries dismissals across review passes (keyed on
    group_key), so without this a dismissed finding would stay suppressed for
    the rest of the chapter's life.
    """

    def test_brings_a_dismissed_group_back(self, sidecar):
        dismiss_issues("run", "chapter-1", [0, 1, 2, 3], None)
        findings = restore_issues("run", "chapter-1", [0, 1, 2, 3], None)
        assert not any(i["ignored"] for i in findings["issues"])

    def test_restores_only_what_was_asked_for(self, sidecar):
        dismiss_issues("run", "chapter-1", [0, 1, 2, 3], None)
        findings = restore_issues("run", "chapter-1", [1], None)
        assert [i["ignored"] for i in findings["issues"]] == [True, False, True, True]


class TestGroupEndpoints:
    """The HTTP surface the gate's grouped rows call."""

    def _client(self):
        from fastapi.testclient import TestClient

        from lyretext.api import app

        return TestClient(app)

    BASE = "/api/runs/run/chapters/chapter-1/issues"

    def test_fix_group_hands_the_whole_set_to_one_run(self, sidecar):
        # The fix itself is a background task, so patch at the seam that
        # schedules it: asserting on apply_chapter_command from here would race
        # the event loop, and a half-run task leaks the per-chapter lock into
        # whatever test comes next. The folding is covered by TestFixIssues.
        with patch("lyretext.api._spawn_targeted_fix") as spawn:
            with self._client() as client:
                response = client.post(f"{self.BASE}/fix-group", json={"issue_indices": [0, 1, 2]})

        assert response.status_code == 202
        assert response.json()["issue_indices"] == [0, 1, 2]
        spawn.assert_called_once_with("run", "chapter-1", [0, 1, 2])

    def test_dismiss_then_restore_round_trips(self, sidecar):
        with self._client() as client:
            dismissed = client.post(f"{self.BASE}/dismiss-group", json={"issue_indices": [0, 2]})
            assert [i["ignored"] for i in dismissed.json()["issues"]] == [True, False, True, False]

            restored = client.post(f"{self.BASE}/restore", json={"issue_indices": [0, 2]})
            assert not any(i["ignored"] for i in restored.json()["issues"])

    @pytest.mark.parametrize("path", ["fix-group", "dismiss-group", "restore"])
    def test_an_empty_selection_is_rejected(self, sidecar, path):
        with patch("lyretext.api._spawn_targeted_fix"):
            with self._client() as client:
                response = client.post(f"{self.BASE}/{path}", json={"issue_indices": []})
        assert response.status_code == 400


class TestFixIssues:
    def test_group_triggers_exactly_one_chapter_command(self, sidecar):
        with patch("lyretext.service.apply_chapter_command") as command:
            result = fix_issues("run", "chapter-1", [0, 1, 2, 3], None)

        assert command.call_count == 1
        _args, kwargs = command.call_args
        assert "Lines 7, 34, 56, 117:" in kwargs["instruction"]
        assert result["issue_indices"] == [0, 1, 2, 3]

    def test_command_is_a_retry_carrying_the_instruction(self, sidecar):
        with patch("lyretext.service.apply_chapter_command") as command:
            result = fix_issues("run", "chapter-1", [0], None)

        args, kwargs = command.call_args
        assert args[:3] == ("run", "chapter-1", "retry")
        assert kwargs["instruction"] == result["instruction"]
        assert "Line 7:" in kwargs["instruction"]

    def test_singular_wrapper_still_works(self, sidecar):
        with patch("lyretext.service.apply_chapter_command") as command:
            fix_single_issue("run", "chapter-1", 2, None)

        assert "Line 56:" in command.call_args.kwargs["instruction"]

    def test_out_of_range_never_reaches_the_graph(self, sidecar):
        with patch("lyretext.service.apply_chapter_command") as command:
            result = fix_issues("run", "chapter-1", [99], None)

        assert "error" in result
        command.assert_not_called()
