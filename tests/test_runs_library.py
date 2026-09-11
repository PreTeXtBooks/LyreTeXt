"""Tests for the runs-library summary listing (#47).

Covers the three things the design called out for GET /api/runs:
  * a lightweight per-run summary computed from the run snapshot alone
    (no full view model, no per-chapter thread reads);
  * ``::chapter::`` sub-threads filtered out of the enumeration;
  * an enumeration failure surfaced (HTTP 500), not silently swallowed into
    an empty list the way the old ``list_threads()`` call was.

Everything runs against mocked checkpointer/graph seams — no LLM calls, no
real graph execution, file I/O only under tmp_path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lyretext.orchestration.checkpointing import (
    build_chapter_thread_id,
    is_chapter_thread_id,
    list_run_thread_ids,
)
from lyretext.service import list_run_summaries
from lyretext.viewmodel import build_run_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tuple(thread_id: str, ts: str | None):
    """A minimal stand-in for a langgraph CheckpointTuple."""
    return SimpleNamespace(
        config={"configurable": {"thread_id": thread_id}},
        checkpoint={"ts": ts} if ts is not None else {},
    )


def _fake_checkpointer(tuples):
    cp = MagicMock()
    cp.list.return_value = iter(tuples)
    return cp


def _snapshot(values, interrupts=None):
    snap = MagicMock()
    snap.values = values
    snap.interrupts = interrupts or []
    return snap


def _interrupt(itype):
    intr = MagicMock()
    intr.id = "intr-1"
    intr.value = {"type": itype}
    return intr


def _graph_returning(snapshot):
    graph = MagicMock()
    graph.get_state.return_value = snapshot
    return graph


# ---------------------------------------------------------------------------
# Thread enumeration
# ---------------------------------------------------------------------------

class TestThreadIdMarker:
    def test_chapter_thread_id_is_recognised(self):
        tid = build_chapter_thread_id("run-1", "chapter-2")
        assert is_chapter_thread_id(tid)

    def test_a_bare_run_id_is_not_a_chapter_thread(self):
        assert not is_chapter_thread_id("run-1")


class TestListRunThreadIds:
    def test_chapter_subthreads_are_filtered_out(self):
        cp = _fake_checkpointer([
            _tuple("run-1", "2026-09-10T10:00:00Z"),
            _tuple(build_chapter_thread_id("run-1", "chapter-1"), "2026-09-10T10:01:00Z"),
            _tuple(build_chapter_thread_id("run-1", "chapter-2"), "2026-09-10T10:02:00Z"),
            _tuple("run-2", "2026-09-10T09:00:00Z"),
        ])
        ids = list_run_thread_ids(cp)
        assert [run_id for run_id, _ in ids] == ["run-1", "run-2"]

    def test_first_seen_timestamp_wins_per_thread(self):
        # list() yields newest-first, so the first tuple for a thread is its
        # latest checkpoint — later (older) checkpoints must not overwrite it.
        cp = _fake_checkpointer([
            _tuple("run-1", "2026-09-10T10:05:00Z"),
            _tuple("run-1", "2026-09-10T10:00:00Z"),
        ])
        assert list_run_thread_ids(cp) == [("run-1", "2026-09-10T10:05:00Z")]

    def test_tuples_without_a_thread_id_are_ignored(self):
        cp = _fake_checkpointer([
            SimpleNamespace(config={"configurable": {}}, checkpoint={"ts": "x"}),
            _tuple("run-1", "2026-09-10T10:00:00Z"),
        ])
        assert list_run_thread_ids(cp) == [("run-1", "2026-09-10T10:00:00Z")]

    def test_enumeration_failure_propagates(self):
        cp = MagicMock()
        cp.list.side_effect = RuntimeError("db locked")
        with pytest.raises(RuntimeError):
            list_run_thread_ids(cp)


# ---------------------------------------------------------------------------
# Per-run summary
# ---------------------------------------------------------------------------

class TestBuildRunSummary:
    def _build(self, values, interrupts=None, updated_at="2026-09-10T10:00:00Z"):
        graph = _graph_returning(_snapshot(values, interrupts))
        cp = MagicMock()
        with patch("lyretext.viewmodel._active_workflow_graph", return_value=graph):
            return build_run_summary("run-1", cp, updated_at=updated_at)

    def test_empty_snapshot_is_not_a_run(self):
        assert self._build({}) is None

    def test_shape_and_name_from_source_basename(self, tmp_path):
        summary = self._build({
            "project_source": str(tmp_path / "my-book"),
            "project_type": "tex",
            "manifest": [],
            "output_dir": str(tmp_path),
        })
        assert summary["run_id"] == "run-1"
        assert summary["name"] == "my-book"
        assert summary["source_type"] == "tex"
        assert summary["updated_at"] == "2026-09-10T10:00:00Z"
        assert set(summary) == {
            "run_id", "name", "source", "source_type", "status", "stage",
            "chapters_done", "chapters_total", "read_pending", "updated_at",
        }

    def test_reading_when_no_manifest_yet(self, tmp_path):
        summary = self._build({
            "project_source": str(tmp_path / "book"),
            "manifest": [],
            "output_dir": str(tmp_path),
        })
        assert summary["status"] == "reading"
        assert summary["read_pending"] is False

    def test_read_gate_pending(self, tmp_path):
        summary = self._build(
            {"project_source": str(tmp_path / "book"), "manifest": [], "output_dir": str(tmp_path)},
            interrupts=[_interrupt("validation_gate_blocked")],
        )
        assert summary["status"] == "read_gate"
        assert summary["read_pending"] is True
        assert summary["stage"] == "Manifest review"

    def test_translating_counts_only_chapters_with_output(self, tmp_path):
        (tmp_path / "ch1.ptx").write_text("<chapter/>")
        summary = self._build({
            "project_source": str(tmp_path / "book"),
            "output_dir": str(tmp_path),
            "manifest": [
                {"output_path": str(tmp_path / "ch1.ptx")},
                {"output_path": str(tmp_path / "ch2.ptx")},
            ],
        })
        assert summary["chapters_done"] == 1
        assert summary["chapters_total"] == 2
        assert summary["status"] == "translating"
        assert summary["stage"] == "Translating 1/2"

    def test_complete_when_every_chapter_has_output(self, tmp_path):
        (tmp_path / "ch1.ptx").write_text("<chapter/>")
        (tmp_path / "ch2.ptx").write_text("<chapter/>")
        summary = self._build({
            "project_source": str(tmp_path / "book"),
            "output_dir": str(tmp_path),
            "manifest": [
                {"output_path": str(tmp_path / "ch1.ptx")},
                {"output_path": str(tmp_path / "ch2.ptx")},
            ],
        })
        assert summary["chapters_done"] == 2
        assert summary["status"] == "complete"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestListRunSummaries:
    def test_skips_a_run_that_cannot_be_summarised(self):
        def fake_build(run_id, cp, *, updated_at=None):
            if run_id == "bad":
                raise ValueError("stale checkpoint")
            return {"run_id": run_id, "updated_at": updated_at}

        cp = MagicMock()
        with (
            patch("lyretext.service.list_run_thread_ids",
                  return_value=[("bad", "t2"), ("good", "t1")]),
            patch("lyretext.service.build_run_summary", side_effect=fake_build),
        ):
            summaries = list_run_summaries(cp)
        assert [s["run_id"] for s in summaries] == ["good"]

    def test_sorted_newest_first(self):
        def fake_build(run_id, cp, *, updated_at=None):
            return {"run_id": run_id, "updated_at": updated_at}

        cp = MagicMock()
        with (
            patch("lyretext.service.list_run_thread_ids",
                  return_value=[("old", "2026-01-01T00:00:00Z"),
                                ("new", "2026-09-10T00:00:00Z")]),
            patch("lyretext.service.build_run_summary", side_effect=fake_build),
        ):
            summaries = list_run_summaries(cp)
        assert [s["run_id"] for s in summaries] == ["new", "old"]

    def test_enumeration_failure_is_not_swallowed(self):
        cp = MagicMock()
        with patch("lyretext.service.list_run_thread_ids", side_effect=RuntimeError("db")):
            with pytest.raises(RuntimeError):
                list_run_summaries(cp)


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

class TestRunsEndpoint:
    def _client(self, raise_server_exceptions=True):
        from fastapi.testclient import TestClient

        from lyretext.api import app

        return TestClient(app, raise_server_exceptions=raise_server_exceptions)

    def test_lists_summaries_and_filters_chapter_subthreads(self):
        cp = _fake_checkpointer([
            _tuple("run-1", "2026-09-10T10:00:00Z"),
            _tuple(build_chapter_thread_id("run-1", "chapter-1"), "2026-09-10T10:01:00Z"),
        ])

        def fake_build(run_id, checkpointer, *, updated_at=None):
            return {"run_id": run_id, "name": "book", "updated_at": updated_at}

        with (
            patch("lyretext.api._cp", return_value=cp),
            patch("lyretext.service.build_run_summary", side_effect=fake_build),
        ):
            with self._client() as client:
                body = client.get("/api/runs").json()

        # Only the run-level thread appears — the ::chapter:: sub-thread is gone.
        assert [r["run_id"] for r in body["runs"]] == ["run-1"]

    def test_enumeration_failure_returns_500(self):
        cp = MagicMock()
        cp.list.side_effect = RuntimeError("db locked")
        with patch("lyretext.api._cp", return_value=cp):
            with self._client(raise_server_exceptions=False) as client:
                response = client.get("/api/runs")
        assert response.status_code == 500
