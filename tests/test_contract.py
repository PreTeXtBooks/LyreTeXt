"""
Contract tests for lyretext.viewmodel and lyretext.service.

All tests use an in-memory checkpointer and a fake graph snapshot so no LLM
calls, file I/O beyond tmp dirs, or real graph execution is needed.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lyretext.viewmodel import (
    _findings_counts,
    _chapter_stage_states,
    _build_jobs_view,
    build_view_model,
)
from lyretext.service import (
    apply_chapter_command,
    apply_chapter_decision,
    apply_read_gate_decision,
    get_jobs,
    get_output,
    get_run_view,
    remap_chapter_source,
    reopen_chapter_for_action,
    write_chapter_file_and_trigger,
)
from lyretext.review.structure import Issue, ReviewReport
from lyretext.review.checks import get_registry
from lyretext.enhance.graph import build_enhance_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(values: dict[str, Any], interrupts: list = None) -> MagicMock:
    snap = MagicMock()
    snap.values = values
    snap.interrupts = interrupts or []
    snap.metadata = {}
    snap.config = {"configurable": {}}
    return snap


def _make_interrupt(interrupt_id: str, itype: str, chapter_id: str = "", extra: dict = None) -> MagicMock:
    value = {"type": itype, "chapter_id": chapter_id, **(extra or {})}
    intr = MagicMock()
    intr.id = interrupt_id
    intr.value = value
    return intr


# ---------------------------------------------------------------------------
# Unit: _findings_counts
# ---------------------------------------------------------------------------

class TestFindingsCounts:
    def test_none_returns_zeros(self):
        assert _findings_counts(None) == {"error": 0, "warn": 0, "info": 0}

    def test_counts_from_findings_dict(self):
        findings = {"counts": {"error": 2, "warn": 3, "info": 1}}
        assert _findings_counts(findings) == {"error": 2, "warn": 3, "info": 1}

    def test_partial_counts(self):
        findings = {"counts": {"error": 1}}
        result = _findings_counts(findings)
        assert result["error"] == 1
        assert result["warn"] == 0
        assert result["info"] == 0


# ---------------------------------------------------------------------------
# Unit: _chapter_stage_states
# ---------------------------------------------------------------------------

class TestChapterStageStates:
    def _call(self, *, chapter_id="ch1", output_path="", output_dir="",
               chapter_status=None, pending_interrupt=None, findings=None):
        return _chapter_stage_states(
            chapter_id=chapter_id,
            output_path=output_path,
            output_dir=output_dir,
            chapter_status=chapter_status or {},
            pending_interrupt=pending_interrupt,
            findings=findings,
        )

    def test_skipped_chapter(self):
        result = self._call(chapter_status={"ch1": "skipped"})
        assert result["translate"] == "skipped"
        assert result["validate"] == "skipped"
        assert result["enhance"] == "disabled"

    def test_pending_no_ptx(self):
        result = self._call()
        assert result["read"] == "done"
        assert result["translate"] == "pending"
        assert result["validate"] == "pending"
        assert result["enhance"] == "disabled"

    def test_ptx_exists_no_interrupt(self, tmp_path):
        """A fully-resolved chapter (output exists, no open gate) reads as
        done end-to-end, even when no findings sidecar was ever recorded
        (e.g. the mock pipeline) — it must not stall on "pending" forever."""
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        result = self._call(output_path=str(ptx))
        assert result["translate"] == "done"
        assert result["validate"] == "done"

    def test_ptx_exists_with_interrupt(self, tmp_path):
        """Translate work is complete once output exists — the open review
        gate is a validate-stage concern, not unfinished translate work."""
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        intr = {"type": "chapter_review", "escalation_required": False}
        result = self._call(output_path=str(ptx), pending_interrupt=intr)
        assert result["translate"] == "done"
        assert result["validate"] == "needs-review"

    def test_review_gate_pending_without_findings_sidecar_yet(self, tmp_path):
        """review_chapter always runs before chapter_review_gate can fire, so
        a pending chapter_review interrupt means review already finished —
        validate must read as "needs-review" (a human decision is pending),
        never "running", even if no findings sidecar has been loaded (e.g.
        the mock pipeline, which doesn't write one)."""
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        intr = {"type": "chapter_review", "escalation_required": False}
        result = self._call(output_path=str(ptx), pending_interrupt=intr, findings=None)
        assert result["validate"] == "needs-review"

    def test_escalation_required(self, tmp_path):
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        findings = {"issues": [{"severity": "error", "auto_fixed": False}]}
        intr = {"type": "chapter_review", "escalation_required": True}
        result = self._call(output_path=str(ptx), pending_interrupt=intr, findings=findings)
        assert result["validate"] == "failed"

    def test_findings_passing(self, tmp_path):
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        # Write a findings sidecar (no errors)
        sidecar_dir = tmp_path / ".lyretext"
        sidecar_dir.mkdir()
        sidecar = sidecar_dir / "ch1.findings.json"
        sidecar.write_text(json.dumps({"issues": [{"severity": "warn", "auto_fixed": False}]}))
        result = self._call(
            chapter_id="ch1",
            output_path=str(ptx),
            output_dir=str(tmp_path),
            findings={"issues": [{"severity": "warn", "auto_fixed": False}]},
        )
        assert result["validate"] == "done"


# ---------------------------------------------------------------------------
# Unit: Issue / ReviewReport pydantic contracts
# ---------------------------------------------------------------------------

class TestIssueContract:
    def test_issue_required_fields(self):
        issue = Issue(check_id="xml_wellformed", severity="error", message="Unclosed tag")
        assert issue.severity == "error"
        assert issue.auto_fixable is False
        assert issue.auto_fixed is False
        assert issue.block_id is None  # future block-level field reserved

    def test_review_report_empty(self):
        report = ReviewReport()
        assert report.issues == []

    def test_review_report_with_issues(self):
        report = ReviewReport(issues=[
            Issue(check_id="math_notation", severity="warn", message="Bare $ found", auto_fixable=True),
        ])
        assert len(report.issues) == 1
        assert report.issues[0].auto_fixable is True

    def test_issue_model_copy_auto_fixed(self):
        issue = Issue(check_id="math_notation", severity="warn", message="Bare $", auto_fixable=True)
        fixed = issue.model_copy(update={"auto_fixed": True})
        assert fixed.auto_fixed is True
        assert issue.auto_fixed is False  # original unchanged


# ---------------------------------------------------------------------------
# Unit: check registry
# ---------------------------------------------------------------------------

class TestCheckRegistry:
    def test_built_in_translate_checks(self):
        specs = get_registry().get_for_stage("translate")
        ids = [s.id for s in specs]
        assert "xml_wellformed" in ids
        assert "math_notation" in ids
        assert "pretext_structure" in ids

    def test_no_checks_for_unknown_stage(self):
        assert get_registry().get_for_stage("nonexistent") == []

    def test_get_by_id(self):
        spec = get_registry().get("xml_wellformed")
        assert spec is not None
        assert spec.default_severity == "error"
        assert spec.auto_fixable is False


# ---------------------------------------------------------------------------
# Unit: enhance stub
# ---------------------------------------------------------------------------

class TestEnhanceStub:
    def test_enhance_graph_compiles(self):
        g = build_enhance_graph()
        assert g is not None

    def test_enhance_graph_nodes(self):
        g = build_enhance_graph()
        # Passthrough graph — only start/end
        assert "__start__" in g.nodes


# ---------------------------------------------------------------------------
# Integration: build_view_model (mocked graph)
# ---------------------------------------------------------------------------

class TestBuildViewModel:
    """Each chapter now lives on its own checkpoint thread (see
    orchestration/graph.py's invoke_chapter_graph/resume_chapter_graph), so
    build_view_model reads the run-level graph (_active_workflow_graph) for
    project/manifest state and a separate chapter graph (_active_chapter_graph)
    per chapter for its own stage/gate state — no more nested Send-subgraph
    task traversal. Tests patch both accordingly.
    """

    def _make_graph_mock(self, snapshot, history=None):
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        graph.get_state_history.return_value = iter(history or [])
        return graph

    def _undispatched_chapter_graph(self):
        """Chapter graph mock representing a chapter with no thread yet (not dispatched)."""
        snap = MagicMock()
        snap.values = {}
        snap.interrupts = []
        return self._make_graph_mock(snap)

    def _chapter_graph_with_interrupt(self, intr, history=None):
        snap = MagicMock()
        snap.values = {"chapter_id": intr.value.get("chapter_id", "")}
        snap.interrupts = [intr]
        return self._make_graph_mock(snap, history=history)

    def test_missing_run_returns_error(self, tmp_path):
        snap = MagicMock()
        snap.values = {}
        snap.interrupts = []
        graph = self._make_graph_mock(snap)
        with patch("lyretext.viewmodel._active_workflow_graph", return_value=graph):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("nonexistent-run", cp)
            assert "error" in result

    def test_basic_view_model_shape(self, tmp_path):
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")

        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [{"name": "Chapter 1", "source_path": "ch1.rmd", "output_path": str(ptx), "type": "chapter"}],
            "chapter_status": {"ch1": "pending"},
            "human_signoffs": {"read->translate": True},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._make_graph_mock(snap)
        chapter_graph = self._undispatched_chapter_graph()

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        assert result["run_id"] == "run-abc"
        assert "project" in result
        assert "chapters" in result
        assert "resources" in result
        assert "output" in result
        assert "jobs" in result
        assert "gate" in result

        ch = result["chapters"][0]
        assert ch["id"] == "ch1"
        assert ch["title"] == "Chapter 1"
        assert set(ch["stages"].keys()) == {"read", "translate", "validate", "enhance"}
        assert ch["stages"]["enhance"] == "disabled"

    def test_chapter_with_pending_interrupt(self, tmp_path):
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")

        # The interrupt now lives on ch1's own chapter thread, not the run thread.
        intr = _make_interrupt("intr-1", "chapter_review", "ch1", {"escalation_required": False})
        snap = _make_snapshot(
            {
                "project_source": str(tmp_path),
                "output_dir": str(tmp_path),
                "manifest": [{"name": "Ch1", "source_path": "ch1.rmd", "output_path": str(ptx), "type": "chapter"}],
                "chapter_status": {},
                "human_signoffs": {},
                "project_type": "rmd",
                "project_resources": [],
            },
        )
        graph = self._make_graph_mock(snap)
        chapter_graph = self._chapter_graph_with_interrupt(intr)

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        ch = result["chapters"][0]
        assert ch["stages"]["translate"] == "done"
        assert ch["stages"]["validate"] == "needs-review"
        assert ch["gate"] is not None
        assert ch["gate"]["stage"] == "translate"
        assert ch["gate"]["failed"] is False
        assert ch["lifecycle"] == "review_required"
        assert "approve" in ch["available_actions"]
        assert "retry" in ch["available_actions"]

    def test_chapter_without_artifacts_has_translation_ready_lifecycle(self, tmp_path):
        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [{"name": "Chapter 1", "source_path": "ch1.rmd", "output_path": str(tmp_path / "ch1.ptx"), "type": "chapter"}],
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._make_graph_mock(snap)
        chapter_graph = self._undispatched_chapter_graph()

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        ch = result["chapters"][0]
        assert ch["lifecycle"] == "translation_ready"
        assert "translate" in ch["available_actions"]

    def test_output_files_listed(self, tmp_path):
        ptx = tmp_path / "chapter-1.ptx"
        ptx.write_text("<chapter/>")

        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [],
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._make_graph_mock(snap)
        chapter_graph = self._undispatched_chapter_graph()

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        assert len(result["output"]) == 1
        assert result["output"][0]["file"] == "chapter-1.ptx"

    def test_output_files_ordered_by_manifest_not_alphabetically(self, tmp_path):
        """"app-appendix.ptx" sorts before "chapter-1-intro.ptx"
        alphabetically, but the appendix comes after the chapter in the
        manifest — the output listing must follow manifest order."""
        names = ["fm-foreword.ptx", "chapter-1-intro.ptx", "app-appendix.ptx", "bm-references.ptx"]
        for name in names:
            (tmp_path / name).write_text("<chapter/>")

        manifest = [
            {"name": "Foreword", "source_path": "fm-foreword.rmd", "output_path": str(tmp_path / "fm-foreword.ptx"), "type": "frontmatter"},
            {"name": "Intro", "source_path": "ch1.rmd", "output_path": str(tmp_path / "chapter-1-intro.ptx"), "type": "chapter"},
            {"name": "Appendix", "source_path": "app.rmd", "output_path": str(tmp_path / "app-appendix.ptx"), "type": "appendix"},
            {"name": "References", "source_path": "refs.rmd", "output_path": str(tmp_path / "bm-references.ptx"), "type": "backmatter"},
        ]
        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": manifest,
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._make_graph_mock(snap)
        chapter_graph = self._undispatched_chapter_graph()

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        assert [f["file"] for f in result["output"]] == names

    def test_two_chapters_have_independent_gate_state(self, tmp_path):
        """Regression guard for the concurrency fix: one chapter's pending
        interrupt must never leak onto another chapter's view — each is read
        from its own thread's snapshot."""
        ptx1 = tmp_path / "ch1.ptx"
        ptx1.write_text("<chapter/>")
        ptx2 = tmp_path / "ch2.ptx"
        ptx2.write_text("<chapter/>")

        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [
                {"name": "Ch1", "source_path": "ch1.rmd", "output_path": str(ptx1), "type": "chapter"},
                {"name": "Ch2", "source_path": "ch2.rmd", "output_path": str(ptx2), "type": "chapter"},
            ],
            "chapter_status": {},
            "human_signoffs": {"read->translate": True},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._make_graph_mock(snap)

        intr = _make_interrupt("intr-1", "chapter_review", "ch1", {"escalation_required": True})
        ch1_snap = MagicMock()
        ch1_snap.values = {"chapter_id": "ch1"}
        ch1_snap.interrupts = [intr]
        ch2_snap = MagicMock()
        ch2_snap.values = {}
        ch2_snap.interrupts = []

        chapter_graph = MagicMock()
        chapter_graph.get_state.side_effect = lambda config: (
            ch1_snap if config["configurable"]["thread_id"].endswith("::ch1") else ch2_snap
        )
        chapter_graph.get_state_history.return_value = iter([])

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=chapter_graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = build_view_model("run-abc", cp)

        by_id = {c["id"]: c for c in result["chapters"]}
        assert by_id["ch1"]["gate"]["failed"] is True
        assert by_id["ch2"]["gate"] is None


# ---------------------------------------------------------------------------
# Integration: service layer thin wrappers
# ---------------------------------------------------------------------------

class TestServiceLayer:
    def _patched_graph(self, snap, history=None):
        graph = MagicMock()
        graph.get_state.return_value = snap
        graph.get_state_history.return_value = iter(history or [])
        return graph

    def _empty_chapter_graph(self):
        snap = MagicMock()
        snap.values = {}
        snap.interrupts = []
        return self._patched_graph(snap)

    def test_get_run_view_delegates_to_viewmodel(self, tmp_path):
        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [],
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._patched_graph(snap)
        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=self._empty_chapter_graph()),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = get_run_view("run-xyz", cp)
        assert "project" in result
        assert "chapters" in result

    def test_get_output_returns_list(self, tmp_path):
        ptx = tmp_path / "ch1.ptx"
        ptx.write_text("<chapter/>")
        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [],
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._patched_graph(snap)
        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=self._empty_chapter_graph()),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            output = get_output("run-xyz", cp)
        assert isinstance(output, list)
        assert output[0]["file"] == "ch1.ptx"

    def test_get_jobs_returns_list(self, tmp_path):
        snap = _make_snapshot({
            "project_source": str(tmp_path),
            "output_dir": str(tmp_path),
            "manifest": [],
            "chapter_status": {},
            "human_signoffs": {},
            "project_type": "rmd",
            "project_resources": [],
        })
        graph = self._patched_graph(snap)
        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=self._empty_chapter_graph()),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            jobs = get_jobs("run-xyz", cp)
        assert isinstance(jobs, list)

    def test_apply_chapter_decision_calls_resume(self, tmp_path):
        """apply_chapter_decision should resume the chapter's own thread directly —
        no more interrupt_id-keyed resume_map, since each chapter has its own thread."""
        already_dispatched = MagicMock()
        already_dispatched.values = {"chapter_id": "ch1"}
        fake_result = {"status": "done", "interrupted": False, "pending_interrupts": []}
        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=already_dispatched),
            patch("lyretext.service.resume_chapter_graph", return_value=fake_result) as mock_resume,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = apply_chapter_decision("run-1", {"ch1": "approve"}, cp)

        mock_resume.assert_called_once()
        call_kwargs = mock_resume.call_args.kwargs
        assert call_kwargs["run_id"] == "run-1"
        assert call_kwargs["chapter_id"] == "ch1"
        assert call_kwargs["resume_value"]["action"] == "approve"

    def test_apply_read_gate_decision_calls_resume(self, tmp_path):
        fake_result = {"status": "done", "interrupted": False}
        with patch("lyretext.service.resume_workflow_graph", return_value=fake_result) as mock_resume:
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            apply_read_gate_decision("run-2", "approve_continue", cp)

        mock_resume.assert_called_once()
        assert mock_resume.call_args.kwargs["resume_value"] == {"action": "approve_continue"}

    def test_apply_chapter_command_targets_only_requested_chapter(self, tmp_path):
        """Resuming ch1 must only touch ch1's own thread — ch2's thread is
        never looked up or resumed, demonstrating the two are independent."""
        already_dispatched = MagicMock()
        already_dispatched.values = {"chapter_id": "ch1"}
        fake_result = {"status": "done", "interrupted": False, "pending_interrupts": []}
        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=already_dispatched) as mock_get_snapshot,
            patch("lyretext.service.resume_chapter_graph", return_value=fake_result) as mock_resume,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            apply_chapter_command("run-1", "ch1", "approve", cp)

        mock_get_snapshot.assert_called_once_with("run-1", "ch1", checkpointer=cp)
        mock_resume.assert_called_once()
        assert mock_resume.call_args.kwargs["chapter_id"] == "ch1"
        assert mock_resume.call_args.kwargs["resume_value"]["action"] == "approve"

    def test_apply_chapter_command_blocks_dispatch_before_read_gate_approved(self, tmp_path):
        """A chapter with no thread yet can't be started before the run's
        read/manifest gate has been signed off — this used to be structurally
        impossible (chapters only ran as Send branches emitted after the gate
        passed); now that each chapter is independently invokable, the guard
        re-establishes that ordering explicitly."""
        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=None),
            patch("lyretext.service._read_gate_approved", return_value=False),
            patch("lyretext.service.invoke_chapter_graph") as mock_invoke,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = apply_chapter_command("run-1", "ch1", "translate", cp)

        assert "error" in result
        mock_invoke.assert_not_called()

    def test_apply_chapter_command_dispatches_fresh_chapter_past_dispatch_gate(self, tmp_path):
        """Starting a chapter for the first time should create its thread and
        auto-advance past chapter_dispatch_gate in one call."""
        invoke_result = {
            "status": "interrupted",
            "interrupted": True,
            "pending_interrupts": [{"type": "chapter_dispatch", "chapter_id": "ch1"}],
        }
        resume_result = {"status": "interrupted", "interrupted": True, "pending_interrupts": []}
        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=None),
            patch("lyretext.service._read_gate_approved", return_value=True),
            patch(
                "lyretext.service._find_chapter_in_manifest",
                return_value={"source_path": "ch1.rmd", "output_path": "ch1.ptx"},
            ),
            patch("lyretext.service.invoke_chapter_graph", return_value=invoke_result) as mock_invoke,
            patch("lyretext.service.resume_chapter_graph", return_value=resume_result) as mock_resume,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = apply_chapter_command("run-1", "ch1", "translate", cp)

        mock_invoke.assert_called_once()
        assert mock_invoke.call_args.kwargs["chapter_id"] == "ch1"
        mock_resume.assert_called_once()
        assert mock_resume.call_args.kwargs["resume_value"] == {"action": "start"}
        assert result == resume_result

    def test_dispatch_chapters_runs_each_chapter_independently(self, tmp_path):
        """The batch run-selected/run-all helper just fans out per-chapter
        commands — each is independent since chapters no longer share a thread."""
        from lyretext.service import dispatch_chapters

        calls: list[str] = []

        def _fake_apply(run_id, chapter_id, action, checkpointer, *, instruction=None):
            calls.append(chapter_id)
            return {"chapter_id": chapter_id, "status": "processing"}

        with patch("lyretext.service.apply_chapter_command", side_effect=_fake_apply):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            results = dispatch_chapters("run-1", ["ch1", "ch2", "ch3"], cp)

        assert calls == ["ch1", "ch2", "ch3"]
        assert set(results.keys()) == {"ch1", "ch2", "ch3"}
        assert all(r["status"] == "processing" for r in results.values())


# ---------------------------------------------------------------------------
# Integration: remap_chapter_source / write_chapter_file_and_trigger
# ---------------------------------------------------------------------------

class TestRemapChapterSource:
    def test_rejects_remap_after_dispatch(self, tmp_path):
        already_dispatched = MagicMock()
        already_dispatched.values = {"chapter_id": "ch1"}
        with patch("lyretext.service.get_chapter_snapshot", return_value=already_dispatched):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = remap_chapter_source("run-1", "ch1", "/new/path.md", cp)
        assert "error" in result

    def test_remaps_undispatched_chapter(self, tmp_path):
        manifest = [{"name": "Ch1", "source_path": "old.rmd", "output_path": str(tmp_path / "ch1.ptx"), "type": "chapter"}]
        snap = MagicMock()
        snap.values = {"manifest": manifest}
        graph = MagicMock()
        graph.get_state.return_value = snap

        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=None),
            patch("lyretext.service._active_workflow_graph", return_value=graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = remap_chapter_source("run-1", "ch1", "/new/path.md", cp)

        assert result["status"] == "remapped"
        graph.update_state.assert_called_once()
        new_manifest = graph.update_state.call_args[0][1]["manifest"]
        assert new_manifest[0]["source_path"] == "/new/path.md"

    def test_unknown_chapter_errors(self, tmp_path):
        snap = MagicMock()
        snap.values = {"manifest": []}
        graph = MagicMock()
        graph.get_state.return_value = snap
        with (
            patch("lyretext.service.get_chapter_snapshot", return_value=None),
            patch("lyretext.service._active_workflow_graph", return_value=graph),
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = remap_chapter_source("run-1", "nonexistent", "/new/path.md", cp)
        assert "error" in result


class TestWriteChapterFileAndTrigger:
    def test_writes_source_and_triggers_recompile_when_at_gate(self, tmp_path):
        src = tmp_path / "ch1.rmd"
        chapter = {"source_path": str(src), "output_path": str(tmp_path / "ch1.ptx")}

        at_gate_snapshot = MagicMock()
        at_gate_snapshot.interrupts = [MagicMock()]
        fake_result = {"status": "interrupted", "interrupted": True}

        with (
            patch("lyretext.service._find_chapter_in_manifest", return_value=chapter),
            patch("lyretext.service.get_chapter_snapshot", return_value=at_gate_snapshot),
            patch("lyretext.service.apply_chapter_command", return_value=fake_result) as mock_apply,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = write_chapter_file_and_trigger("run-1", "ch1", "source", "# new content", cp)

        assert src.read_text(encoding="utf-8") == "# new content"
        mock_apply.assert_called_once_with("run-1", "ch1", "recompile_source", cp)
        assert result["triggered"] == "recompile_source"

    def test_writes_output_and_triggers_revalidate(self, tmp_path):
        out = tmp_path / "ch1.ptx"
        chapter = {"source_path": str(tmp_path / "ch1.rmd"), "output_path": str(out)}

        at_gate_snapshot = MagicMock()
        at_gate_snapshot.interrupts = [MagicMock()]
        fake_result = {"status": "interrupted", "interrupted": True}

        with (
            patch("lyretext.service._find_chapter_in_manifest", return_value=chapter),
            patch("lyretext.service.get_chapter_snapshot", return_value=at_gate_snapshot),
            patch("lyretext.service.apply_chapter_command", return_value=fake_result) as mock_apply,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = write_chapter_file_and_trigger("run-1", "ch1", "output", "<chapter/>", cp)

        assert out.read_text(encoding="utf-8") == "<chapter/>"
        mock_apply.assert_called_once_with("run-1", "ch1", "revalidate", cp)
        assert result["triggered"] == "revalidate"

    def test_writes_without_triggering_when_not_at_gate(self, tmp_path):
        src = tmp_path / "ch1.rmd"
        chapter = {"source_path": str(src), "output_path": str(tmp_path / "ch1.ptx")}

        with (
            patch("lyretext.service._find_chapter_in_manifest", return_value=chapter),
            patch("lyretext.service.get_chapter_snapshot", return_value=None),
            patch("lyretext.service.apply_chapter_command") as mock_apply,
        ):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = write_chapter_file_and_trigger("run-1", "ch1", "source", "# edited", cp)

        assert src.read_text(encoding="utf-8") == "# edited"
        mock_apply.assert_not_called()
        assert result["status"] == "written"
        assert result["triggered"] is None

    def test_unknown_chapter_errors(self, tmp_path):
        with patch("lyretext.service._find_chapter_in_manifest", return_value=None):
            from lyretext.orchestration.checkpointing import build_checkpointer
            cp = build_checkpointer("memory")
            result = write_chapter_file_and_trigger("run-1", "nonexistent", "source", "x", cp)
        assert "error" in result

    def test_invalid_target_errors(self, tmp_path):
        from lyretext.orchestration.checkpointing import build_checkpointer
        cp = build_checkpointer("memory")
        result = write_chapter_file_and_trigger("run-1", "ch1", "bogus", "x", cp)
        assert "error" in result


# ---------------------------------------------------------------------------
# Unit: LYRETEXT_MOCK env var parsing
# ---------------------------------------------------------------------------

class TestMockEnabled:
    """os.environ.get() returns a *string* — "0" is truthy in Python, so a
    bare `if os.environ.get("LYRETEXT_MOCK")` treated LYRETEXT_MOCK=0 the
    same as LYRETEXT_MOCK=1, making it impossible to turn mock mode back off
    short of unsetting the variable entirely."""

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on", " 1 "])
    def test_truthy_values_enable_mock(self, value, monkeypatch):
        from lyretext.orchestration.graph import _mock_enabled
        monkeypatch.setenv("LYRETEXT_MOCK", value)
        assert _mock_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", ""])
    def test_falsy_values_disable_mock(self, value, monkeypatch):
        from lyretext.orchestration.graph import _mock_enabled
        monkeypatch.setenv("LYRETEXT_MOCK", value)
        assert _mock_enabled() is False

    def test_unset_disables_mock(self, monkeypatch):
        from lyretext.orchestration.graph import _mock_enabled
        monkeypatch.delenv("LYRETEXT_MOCK", raising=False)
        assert _mock_enabled() is False


# ---------------------------------------------------------------------------
# Integration: reopening an already-resolved chapter thread
# ---------------------------------------------------------------------------

class TestReopenChapterForAction:
    """Real (non-mocked) integration coverage — this exercises actual
    LangGraph update_state(as_node=...) + stream(None, ...) mechanics, which
    a fully-mocked test wouldn't catch (the checkpoint-revert feature needed
    a real run to surface its checkpoint_ns bug; this is the same class of
    risk). Drives a chapter all the way to approved, then reopens it."""

    def _dispatch_and_approve(self, tmp_path, monkeypatch, run_id="run-reopen", chapter_id="ch1"):
        monkeypatch.setenv("LYRETEXT_MOCK", "1")
        monkeypatch.chdir(tmp_path)
        from lyretext.orchestration.checkpointing import build_checkpointer
        cp = build_checkpointer("memory")
        chapter = {"source_path": "ch1.rmd", "output_path": "ch1.ptx"}
        with (
            patch("lyretext.service._read_gate_approved", return_value=True),
            patch("lyretext.service._find_chapter_in_manifest", return_value=chapter),
        ):
            apply_chapter_command(run_id, chapter_id, "translate", cp)
            apply_chapter_command(run_id, chapter_id, "approve", cp)
        return cp, run_id, chapter_id

    def test_chapter_is_genuinely_terminal_after_approve(self, tmp_path, monkeypatch):
        cp, run_id, chapter_id = self._dispatch_and_approve(tmp_path, monkeypatch)
        from lyretext.orchestration.graph import get_chapter_snapshot
        snap = get_chapter_snapshot(run_id, chapter_id, checkpointer=cp)
        assert not snap.interrupts
        assert not snap.next

    def test_revalidate_reopens_a_terminal_chapter(self, tmp_path, monkeypatch):
        cp, run_id, chapter_id = self._dispatch_and_approve(tmp_path, monkeypatch)

        result = apply_chapter_command(run_id, chapter_id, "revalidate", cp)

        assert result.get("interrupted") is True
        pending = result.get("pending_interrupts") or []
        assert any(p.get("type") == "chapter_review" for p in pending)

    def test_retry_reopens_a_terminal_chapter_and_bumps_iteration(self, tmp_path, monkeypatch):
        cp, run_id, chapter_id = self._dispatch_and_approve(tmp_path, monkeypatch)

        result = apply_chapter_command(run_id, chapter_id, "retry", cp)

        pending = result.get("pending_interrupts") or []
        review = next(p for p in pending if p.get("type") == "chapter_review")
        assert review["value"]["iteration_count"] == 2

    def test_unsupported_action_on_terminal_chapter_returns_error(self, tmp_path, monkeypatch):
        cp, run_id, chapter_id = self._dispatch_and_approve(tmp_path, monkeypatch)

        result = reopen_chapter_for_action(run_id, chapter_id, "bogus", cp)

        assert "error" in result
