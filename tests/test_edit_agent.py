"""Tests for the editing agent and the routes that reach it.

The editing agent is the only LLM step left on the chapter happy-path, and it
is opt-in. These tests mock the LLM entirely — what matters here is *when* it
runs, what it is given, and what it writes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyretext.edit.agents import edit_chapter
from lyretext.format import format_pretext
from lyretext.service import _INSTRUCTION_ACTIONS, _REOPEN_TARGET_NODE
from lyretext.translate.graph import chapter_review_gate, route_after_chapter_gate

EDITED = "<section><title>Edited</title></section>"
ORIGINAL = "<section><title>Original</title></section>"

# What lands on disk: every write goes through the canonical formatter, so the
# artifact's whitespace no longer depends on how the LLM happened to lay it out.
EDITED_ON_DISK = format_pretext(EDITED)[0]


def _run_config(**overrides):
    global_options = {
        "execution_mode": "direct",
        "apply_mode": "auto_apply",
        "create_backup": False,
        "provider": "gemini",
        "verbosity": "normal",
        "pipeline": "rmd",
        "pandoc_exe": None,
        "pandoc_writer": "pretext.lua",
        "auto_edit": True,
        "max_edit_iterations": 2,
        "format_output": True,
    }
    global_options.update(overrides)
    return {"configurable": {"runtime_options": {"global_options": global_options}}}


def _mock_llm(xml=EDITED):
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = {"xml": xml}
    return llm


def _findings(**overrides):
    issue = {
        "check_id": "math_notation",
        "severity": "warn",
        "line": 7,
        "message": "bare $ delimiters",
        "suggestion": "wrap in <m>",
        "auto_fixable": True,
        "ignored": False,
    }
    issue.update(overrides)
    return {"issues": [issue]}


class TestEditChapter:
    def _state(self, tmp_path, **overrides):
        out = tmp_path / "ch1.ptx"
        out.write_text(ORIGINAL, encoding="utf-8")
        state = {
            "source_path": str(tmp_path / "ch1.md"),
            "output_path": str(out),
            "chapter_id": "ch1",
            "chapter_findings": _findings(),
            "edit_iterations": 0,
        }
        state.update(overrides)
        return state

    def test_no_llm_call_without_work(self, tmp_path):
        state = self._state(tmp_path, chapter_findings={"issues": []}, instruction=None)
        with patch("lyretext.edit.agents.create_llm") as create_llm:
            assert edit_chapter(state, _run_config()) == {}
        create_llm.assert_not_called()

    def test_applies_fixes_and_increments_budget(self, tmp_path):
        state = self._state(tmp_path)
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()):
            result = edit_chapter(state, _run_config())

        assert result["pretext_output"] == EDITED_ON_DISK
        assert result["edit_iterations"] == 1
        assert Path(state["output_path"]).read_text(encoding="utf-8") == EDITED_ON_DISK

    def test_clears_instruction_so_it_does_not_refire(self, tmp_path):
        state = self._state(tmp_path, instruction="use display math")
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()):
            result = edit_chapter(state, _run_config())

        assert result["instruction"] is None

    def test_instruction_alone_is_enough_to_run(self, tmp_path):
        state = self._state(
            tmp_path, chapter_findings={"issues": []}, instruction="split the intro"
        )
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()) as create_llm:
            result = edit_chapter(state, _run_config())

        assert result["pretext_output"] == EDITED_ON_DISK
        sent = create_llm.return_value.with_structured_output.return_value.invoke.call_args
        prompt_text = " ".join(b["text"] for b in sent.args[0][0].content)
        assert "split the intro" in prompt_text

    def test_prompt_carries_issue_detail(self, tmp_path):
        state = self._state(tmp_path)
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()) as create_llm:
            edit_chapter(state, _run_config())

        sent = create_llm.return_value.with_structured_output.return_value.invoke.call_args
        prompt_text = " ".join(b["text"] for b in sent.args[0][0].content)
        assert "bare $ delimiters" in prompt_text
        assert "wrap in <m>" in prompt_text
        assert ORIGINAL in prompt_text

    def test_ignores_dismissed_issues(self, tmp_path):
        state = self._state(tmp_path, chapter_findings=_findings(ignored=True))
        with patch("lyretext.edit.agents.create_llm") as create_llm:
            assert edit_chapter(state, _run_config()) == {}
        create_llm.assert_not_called()

    def test_ignores_non_fixable_issues(self, tmp_path):
        state = self._state(tmp_path, chapter_findings=_findings(auto_fixable=False))
        with patch("lyretext.edit.agents.create_llm") as create_llm:
            assert edit_chapter(state, _run_config()) == {}
        create_llm.assert_not_called()

    def test_an_instruction_suppresses_the_auto_issue_list(self, tmp_path):
        """A targeted fix must stay targeted.

        service.fix_issues builds an instruction naming exactly the findings the
        user picked. Folding the auto-loop's own issue list in on top of it meant
        clicking Fix on one row silently rewrote every other auto_fixable finding
        as well — which made the Individual/Grouped toggle meaningless.
        """
        state = self._state(tmp_path, instruction="Fix the issue on line 7: bare $ delimiters.")
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()) as create_llm:
            edit_chapter(state, _run_config())

        sent = create_llm.return_value.with_structured_output.return_value.invoke.call_args
        prompt_text = " ".join(b["text"] for b in sent.args[0][0].content)
        assert "Fix the issue on line 7" in prompt_text
        # The findings block — which _actionable_issues would have added — is absent.
        assert "Issues to fix:" not in prompt_text

    def test_dry_run_writes_nothing(self, tmp_path):
        state = self._state(tmp_path)
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()):
            result = edit_chapter(state, _run_config(apply_mode="dry_run"))

        assert result["pretext_output"] == EDITED_ON_DISK
        assert Path(state["output_path"]).read_text(encoding="utf-8") == ORIGINAL

    def test_create_backup_preserves_pre_edit_artifact(self, tmp_path):
        state = self._state(tmp_path)
        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()):
            edit_chapter(state, _run_config(create_backup=True))

        out = Path(state["output_path"])
        assert out.read_text(encoding="utf-8") == EDITED_ON_DISK
        assert out.with_suffix(".ptx.bak").read_text(encoding="utf-8") == ORIGINAL

    def test_falls_back_to_state_when_file_missing(self, tmp_path):
        state = self._state(tmp_path)
        Path(state["output_path"]).unlink()
        state["pretext_output"] = ORIGINAL

        with patch("lyretext.edit.agents.create_llm", return_value=_mock_llm()):
            result = edit_chapter(state, _run_config())

        assert result["pretext_output"] == EDITED_ON_DISK

    def test_no_artifact_anywhere_is_a_noop(self, tmp_path):
        state = self._state(tmp_path)
        Path(state["output_path"]).unlink()
        with patch("lyretext.edit.agents.create_llm") as create_llm:
            assert edit_chapter(state, _run_config()) == {}
        create_llm.assert_not_called()


class TestGateRoutesInstructionToEditor:
    """An instruction is only actionable by the editing agent — conversion is
    deterministic and would silently ignore it."""

    def _resume(self, action, instruction=None):
        payload = {"action": action}
        if instruction:
            payload["instruction"] = instruction
        state = {"output_path": "out/ch1.ptx", "chapter_id": "ch1", "iteration_count": 1}
        with patch("lyretext.translate.graph.interrupt", return_value=payload):
            return chapter_review_gate(state)

    def test_retry_with_instruction_goes_to_editor(self):
        result = self._resume("retry", "use display math")
        assert result["chapter_next"] == "edit_chapter"
        assert result["instruction"] == "use display math"
        assert result["edit_iterations"] == 0

    def test_retry_without_instruction_reconverts(self):
        result = self._resume("retry")
        assert result["chapter_next"] == "translate_chapter"
        assert "edit_iterations" not in result

    def test_escalation_with_instruction_goes_to_editor(self):
        assert self._resume("approve_after_escalation", "fix it")["chapter_next"] == "edit_chapter"

    def test_recompile_source_reconverts(self):
        """read_chapter is gone; pandoc reads source_path itself."""
        assert self._resume("recompile_source")["chapter_next"] == "translate_chapter"

    def test_revalidate_unchanged(self):
        assert self._resume("revalidate")["chapter_next"] == "review_chapter"

    def test_approve_ends(self):
        assert self._resume("approve")["chapter_next"] == "__end__"


class TestRouteAfterChapterGate:
    def test_routes_to_editor(self):
        assert route_after_chapter_gate({"chapter_next": "edit_chapter"}) == "edit_chapter"

    def test_legacy_read_chapter_maps_to_conversion(self):
        """Checkpoints written before read_chapter was unwired must still resume."""
        assert route_after_chapter_gate({"chapter_next": "read_chapter"}) == "translate_chapter"

    def test_legacy_retry_flag_still_honoured(self):
        assert route_after_chapter_gate({"retry_requested": True}) == "translate_chapter"


class TestServiceReopenTargets:
    def test_recompile_source_no_longer_targets_read_chapter(self):
        assert _REOPEN_TARGET_NODE["recompile_source"] == "translate_chapter"

    @pytest.mark.parametrize("action", _INSTRUCTION_ACTIONS)
    def test_instruction_actions_redirect_to_editor(self, action, tmp_path):
        from lyretext.service import reopen_chapter_for_action

        snapshot = MagicMock(values={"iteration_count": 1})
        graph = MagicMock()
        graph.get_state.return_value = snapshot

        with (
            patch("lyretext.service._active_chapter_graph", return_value=graph),
            patch("lyretext.service._run_with_checkpoint_metadata", return_value={}),
        ):
            reopen_chapter_for_action(
                "run-1", "ch1", action, MagicMock(), instruction="tighten the proof"
            )

        values = graph.update_state.call_args.args[1]
        assert values["chapter_next"] == "edit_chapter"
        assert values["edit_iterations"] == 0

    def test_without_instruction_retry_still_reconverts(self):
        from lyretext.service import reopen_chapter_for_action

        snapshot = MagicMock(values={"iteration_count": 1})
        graph = MagicMock()
        graph.get_state.return_value = snapshot

        with (
            patch("lyretext.service._active_chapter_graph", return_value=graph),
            patch("lyretext.service._run_with_checkpoint_metadata", return_value={}),
        ):
            reopen_chapter_for_action("run-1", "ch1", "retry", MagicMock())

        assert graph.update_state.call_args.args[1]["chapter_next"] == "translate_chapter"
