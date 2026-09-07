"""Regression tests for the 'Run all' failure mode.

Three linked defects, all reproduced against a live server before fixing:

  1. Read requests shared one 4-worker pool with graph execution, so a batch
     dispatch starved the UI's 2s poll and every chapter card froze.
  2. Background chapter tasks swallowed exceptions ("Task exception was never
     retrieved") — a failed chapter reported nothing at all.
  3. A chapter whose thread died mid-run rendered as lifecycle="approved" /
     validate="done", because "no findings sidecar" was treated as a clean pass.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from lyretext.viewmodel import (
    _chapter_lifecycle,
    _chapter_stage_states,
    build_view_model,
)


def _stalled_graphs(tmp_path, *, next_nodes=("review_chapter",)):
    """A run graph + chapter graph whose chapter thread has a node queued and
    no interrupt — the shape shared by "died part-way" and "running right now".
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    ptx = out_dir / "ch1.ptx"
    ptx.write_text("<section/>", encoding="utf-8")

    manifest = [{"type": "chapter", "name": "Ch1",
                 "source_path": str(tmp_path / "ch1.md"),
                 "output_path": str(ptx)}]

    run_snap = MagicMock()
    run_snap.values = {"manifest": manifest, "output_dir": str(out_dir),
                       "project_source": str(tmp_path), "chapter_status": {},
                       "human_signoffs": {"read->translate": True}}
    run_snap.interrupts = ()
    run_snap.metadata = {}
    run_snap.config = {"configurable": {}}
    run_graph = MagicMock()
    run_graph.get_state.return_value = run_snap
    run_graph.get_state_history.return_value = []

    ch_snap = MagicMock()
    ch_snap.values = {"output_path": str(ptx), "chapter_id": "ch1"}
    ch_snap.interrupts = ()
    ch_snap.next = next_nodes
    ch_snap.metadata = {}
    ch_snap.config = {"configurable": {}}
    ch_graph = MagicMock()
    ch_graph.get_state.return_value = ch_snap
    ch_graph.get_state_history.return_value = []

    return run_graph, ch_graph


class TestExecutorIsolation:
    """The UI poll must never queue behind chapter execution."""

    def test_read_and_exec_pools_are_distinct(self):
        from lyretext import api

        assert api._read_pool is not api._exec_pool

    def test_read_endpoints_use_the_read_pool(self):
        """get_run / status / findings / checkpoints / output / jobs are reads."""
        import inspect

        from lyretext import api

        for name in ("get_run", "run_status", "chapter_findings", "chapter_checkpoints"):
            fn = getattr(api, name, None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            assert "_read_in_thread(" in src, f"{name} still shares the exec pool"

    def test_execution_endpoints_use_the_exec_pool(self):
        import inspect

        from lyretext import api

        src = inspect.getsource(api.chapters_command_batch)
        assert "_in_thread(" in src and "_read_in_thread(" not in src

    def test_reads_stay_responsive_while_exec_pool_is_saturated(self):
        """The actual 'Run all froze the cards' bug, made deterministic.

        Fill every exec worker with slow work — as a batch dispatch does — and
        assert a read still returns promptly. Against the old shared pool this
        read waited for a chapter job to finish.
        """
        import asyncio
        import threading
        import time

        from lyretext import api

        workers = api._exec_pool._max_workers
        release = threading.Event()

        def _slow():
            release.wait(timeout=30)

        async def _drive():
            # Occupy every execution worker, plus one queued behind them.
            busy = [asyncio.ensure_future(api._in_thread(_slow)) for _ in range(workers + 1)]
            await asyncio.sleep(0.2)  # let them be picked up

            t0 = time.perf_counter()
            result = await api._read_in_thread(lambda: "read-ok")
            elapsed = time.perf_counter() - t0

            release.set()
            await asyncio.gather(*busy)
            return result, elapsed

        result, elapsed = asyncio.run(_drive())

        assert result == "read-ok"
        assert elapsed < 1.0, (
            f"read blocked {elapsed:.1f}s behind execution work — the UI poll "
            "is being starved again"
        )


class TestBackgroundTaskErrors:
    def test_spawn_records_chapter_failure(self):
        import asyncio

        from lyretext import api

        async def _boom():
            raise RuntimeError("LLM exploded")

        async def _drive():
            api._spawn(_boom(), run_id="run-1", chapter_id="ch1")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        api._chapter_errors.pop(("run-1", "ch1"), None)
        asyncio.run(_drive())

        assert "LLM exploded" in api._chapter_errors[("run-1", "ch1")]
        api._chapter_errors.pop(("run-1", "ch1"), None)

    def test_spawn_keeps_a_strong_reference(self):
        """asyncio only weakly references tasks; a batch of them can be GC'd."""
        import asyncio

        from lyretext import api

        async def _drive():
            async def _work():
                await asyncio.sleep(0.01)

            task = api._spawn(_work(), run_id="run-2", chapter_id="ch2")
            assert task in api._background_tasks
            await task
            assert task not in api._background_tasks

        asyncio.run(_drive())


class TestStalledChapterIsNotApproved:
    """A thread with a node queued but no interrupt stopped part-way."""

    def test_lifecycle_is_not_approved_when_stalled(self, tmp_path):
        out = tmp_path / "ch1.ptx"
        out.write_text("<section/>", encoding="utf-8")

        assert _chapter_lifecycle(
            output_path=str(out), findings=None, pending_interrupt=None
        ) == "approved"
        assert _chapter_lifecycle(
            output_path=str(out), findings=None, pending_interrupt=None, stalled=True
        ) == "review_required"

    def test_validate_stage_is_failed_when_stalled(self, tmp_path):
        out = tmp_path / "ch1.ptx"
        out.write_text("<section/>", encoding="utf-8")

        kwargs = dict(
            chapter_id="ch1",
            output_path=str(out),
            output_dir=str(tmp_path),
            chapter_status={},
            pending_interrupt=None,
            findings=None,
        )
        assert _chapter_stage_states(**kwargs)["validate"] == "done"
        assert _chapter_stage_states(**kwargs, stalled=True)["validate"] == "failed"

    def test_build_view_model_flags_a_stalled_chapter(self, tmp_path):
        """The end-to-end shape the cards render from."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ptx = out_dir / "ch1.ptx"
        ptx.write_text("<section/>", encoding="utf-8")

        manifest = [{"type": "chapter", "name": "Ch1",
                     "source_path": str(tmp_path / "ch1.md"),
                     "output_path": str(ptx)}]

        run_snap = MagicMock()
        run_snap.values = {"manifest": manifest, "output_dir": str(out_dir),
                           "project_source": str(tmp_path), "chapter_status": {},
                           "human_signoffs": {"read->translate": True}}
        run_snap.interrupts = ()
        run_snap.metadata = {}
        run_snap.config = {"configurable": {}}
        run_graph = MagicMock()
        run_graph.get_state.return_value = run_snap
        run_graph.get_state_history.return_value = []

        # Stalled: review_chapter still queued, nothing to resume into.
        ch_snap = MagicMock()
        ch_snap.values = {"output_path": str(ptx), "chapter_id": "ch1"}
        ch_snap.interrupts = ()
        ch_snap.next = ("review_chapter",)
        ch_snap.metadata = {}
        ch_snap.config = {"configurable": {}}
        ch_graph = MagicMock()
        ch_graph.get_state.return_value = ch_snap
        ch_graph.get_state_history.return_value = []

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=run_graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=ch_graph),
        ):
            vm = build_view_model("run-x", MagicMock())

        chapter = vm["chapters"][0]
        assert chapter["lifecycle"] == "review_required", "a crashed chapter must not read as approved"
        assert chapter["stages"]["validate"] == "failed"
        assert chapter["gate"]["failed"] is True
        assert "retry" in chapter["available_actions"]

    def test_a_running_chapter_is_not_reported_as_stalled(self, tmp_path):
        """The follow-up regression: mid-execution looks exactly like stalled.

        While review_chapter is actually running, the snapshot is
        next=("review_chapter",) with no interrupt — indistinguishable from a
        thread that died there. The card flashed "Stopped before completing"
        for the whole validate stage until the caller started saying which
        chapters were in flight.
        """
        run_graph, ch_graph = _stalled_graphs(tmp_path)

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=run_graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=ch_graph),
        ):
            running = build_view_model(
                "run-x", MagicMock(), executing_chapters={"ch1"}
            )["chapters"][0]
            run_level = build_view_model(
                "run-x", MagicMock(), run_executing=True
            )["chapters"][0]
            unattended = build_view_model("run-x", MagicMock())["chapters"][0]

        for chapter in (running, run_level):
            assert chapter["stages"]["validate"] != "failed"
            assert chapter["gate"] is None
            assert chapter["lifecycle"] != "review_required"

        # …and the genuine case still reports, so this didn't just mute it.
        assert unattended["stages"]["validate"] == "failed"
        assert unattended["gate"]["failed"] is True

    def test_a_different_chapter_running_does_not_mask_this_one(self, tmp_path):
        """Suppression is per-chapter, not a global mute."""
        run_graph, ch_graph = _stalled_graphs(tmp_path)

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=run_graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=ch_graph),
        ):
            vm = build_view_model("run-x", MagicMock(), executing_chapters={"ch-other"})

        assert vm["chapters"][0]["stages"]["validate"] == "failed"

    def test_completed_chapter_still_reads_as_approved(self, tmp_path):
        """Guard the fix against over-reach: a thread at END is not stalled."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ptx = out_dir / "ch1.ptx"
        ptx.write_text("<section/>", encoding="utf-8")

        manifest = [{"type": "chapter", "name": "Ch1",
                     "source_path": str(tmp_path / "ch1.md"),
                     "output_path": str(ptx)}]

        run_snap = MagicMock()
        run_snap.values = {"manifest": manifest, "output_dir": str(out_dir),
                           "project_source": str(tmp_path), "chapter_status": {},
                           "human_signoffs": {"read->translate": True}}
        run_snap.interrupts = ()
        run_snap.metadata = {}
        run_snap.config = {"configurable": {}}
        run_graph = MagicMock()
        run_graph.get_state.return_value = run_snap
        run_graph.get_state_history.return_value = []

        ch_snap = MagicMock()
        ch_snap.values = {"output_path": str(ptx), "chapter_id": "ch1"}
        ch_snap.interrupts = ()
        ch_snap.next = ()          # reached END
        ch_snap.metadata = {}
        ch_snap.config = {"configurable": {}}
        ch_graph = MagicMock()
        ch_graph.get_state.return_value = ch_snap
        ch_graph.get_state_history.return_value = []

        with (
            patch("lyretext.viewmodel._active_workflow_graph", return_value=run_graph),
            patch("lyretext.viewmodel._active_chapter_graph", return_value=ch_graph),
        ):
            vm = build_view_model("run-x", MagicMock())

        assert vm["chapters"][0]["lifecycle"] == "approved"
        assert vm["chapters"][0]["stages"]["validate"] == "done"
