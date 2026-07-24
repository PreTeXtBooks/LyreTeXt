"""Service layer — Python API contract for the LyreTeXt backend.

This module is THE contract.  Future GUI layers (HTTP, Electron, etc.) import
these functions directly.  The CLI is a dev harness on top of them.

Functions
---------
start_run          – kick off a new translation run
get_run_view       – return the read-model view for a run (calls viewmodel)
apply_chapter_decision – resume the graph with per-chapter actions
apply_read_gate_decision – resume the graph at the read gate
get_output         – list generated .ptx files for a run
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .orchestration.checkpointing import (
    build_checkpointer,
    build_checkpoint_config,
    build_chapter_checkpoint_config,
    ensure_run_id,
)
from .orchestration.graph import (
    _active_chapter_graph,
    _active_workflow_graph,
    _chapter_id_from_path,
    _run_with_checkpoint_metadata,
    get_chapter_snapshot,
    get_run_manifest,
    invoke_chapter_graph,
    invoke_workflow_graph,
    resume_chapter_graph,
    resume_workflow_graph,
)
from .viewmodel import build_view_model, load_chapter_findings

_DEFAULT_CONFIG_FILE = Path("config.yml")


def _config_file() -> Path | None:
    """Return config.yml path if it exists, else None."""
    return _DEFAULT_CONFIG_FILE if _DEFAULT_CONFIG_FILE.exists() else None


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def start_run(
    source: str,
    output_dir: str,
    temp_dir: str = "temp",
    *,
    run_config: dict[str, Any] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Start a new translation run and return a status envelope.

    Args:
        source:       path to the project source directory / file.
        output_dir:   directory where .ptx files will be written.
        temp_dir:     working directory for intermediate artefacts.
        run_config:   flat runtime options dict (merged into GlobalRuntimeOptions).
        checkpointer: supply an existing checkpointer; defaults to SQLite.
        run_id:       stable run identifier; generated if omitted.

    Returns:
        dict with keys: run_id, status, interrupted, pending_interrupts, …
    """
    cp = checkpointer or build_checkpointer(backend="sqlite")
    rid = ensure_run_id(run_id)
    initial_state: dict[str, Any] = {
        "project_source": source,
        "temp_dir": temp_dir,
        "output_dir": output_dir,
    }
    result = invoke_workflow_graph(
        initial_state,
        config_file=_config_file(),
        runtime_payload=run_config or {},
        run_id=rid,
        checkpointer=cp,
    )
    result.setdefault("run_id", rid)
    return result


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_run_view(run_id: str, checkpointer: BaseCheckpointSaver) -> dict[str, Any]:
    """Return the read-model view dict for *run_id*."""
    return build_view_model(run_id, checkpointer)


# ---------------------------------------------------------------------------
# Apply decisions
# ---------------------------------------------------------------------------

def _find_chapter_in_manifest(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> dict[str, Any] | None:
    """Look up a chapter's source_path/output_path from the run's approved manifest."""
    manifest = get_run_manifest(run_id, checkpointer=checkpointer) or []
    for ch in manifest:
        if _chapter_id_from_path(ch["output_path"]) == chapter_id:
            return ch
    return None


def _read_gate_approved(run_id: str, checkpointer: BaseCheckpointSaver) -> bool:
    """True once the run's read/manifest gate has been signed off.

    Chapters used to be structurally impossible to dispatch before this —
    they only ran as Send branches emitted by the run graph's own routing
    after the gate passed. Now that each chapter is an independently
    invokable graph/thread, this check re-establishes that same ordering
    guarantee: a chapter can't be started fresh until a human has approved
    the manifest at the read gate.
    """
    graph = _active_workflow_graph(checkpointer)
    snapshot = graph.get_state(build_checkpoint_config(run_id=run_id))
    if snapshot is None or not snapshot.values:
        return False
    if getattr(snapshot, "interrupts", None):
        return False
    return bool(snapshot.values.get("human_signoffs", {}).get("read->translate"))


def apply_chapter_command(
    run_id: str,
    chapter_id: str,
    action: str,
    checkpointer: BaseCheckpointSaver,
    *,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Run a targeted action against ONE chapter's own graph thread.

    Each chapter has its own checkpoint thread (see orchestration.graph's
    invoke_chapter_graph/resume_chapter_graph), so this call is fully
    independent of any other chapter's in-flight execution on the same run —
    it is safe to call this concurrently for different chapter_ids.

    If the chapter hasn't been dispatched yet (no checkpoint thread exists),
    this starts it fresh (which immediately reaches chapter_dispatch_gate)
    and, for a "translate"/"start"-style action, auto-advances past that gate
    so the caller's intent ("run this chapter") is fulfilled in one call.
    """
    snapshot = get_chapter_snapshot(run_id, chapter_id, checkpointer=checkpointer)

    if snapshot is None:
        if not _read_gate_approved(run_id, checkpointer):
            return {
                "error": (
                    f"run {run_id!r} has not passed its read/manifest gate yet — "
                    "cannot dispatch chapters before it is approved"
                )
            }
        chapter = _find_chapter_in_manifest(run_id, chapter_id, checkpointer)
        if chapter is None:
            return {"error": f"chapter {chapter_id!r} not found in run {run_id!r}"}

        result = invoke_chapter_graph(
            run_id=run_id,
            chapter_id=chapter_id,
            state={
                "source_path": chapter["source_path"],
                "output_path": chapter["output_path"],
                "chapter_id": chapter_id,
            },
            config_file=_config_file(),
            checkpointer=checkpointer,
        )
        # A brand-new chapter thread always pauses at chapter_dispatch_gate
        # first; any action here means "run/advance this chapter", so
        # immediately resume past the dispatch gate.
        pending = result.get("pending_interrupts") or []
        at_dispatch = any(
            p.get("type") == "chapter_dispatch" for p in pending
        )
        if at_dispatch:
            result = resume_chapter_graph(
                run_id=run_id,
                chapter_id=chapter_id,
                resume_value={"action": "start"},
                config_file=_config_file(),
                checkpointer=checkpointer,
            )
        return result

    resume_value: dict[str, Any] = {"action": action}
    if instruction:
        resume_value["instruction"] = instruction

    return resume_chapter_graph(
        run_id=run_id,
        chapter_id=chapter_id,
        resume_value=resume_value,
        config_file=_config_file(),
        checkpointer=checkpointer,
    )


def dispatch_chapters(
    run_id: str,
    chapter_ids: list[str],
    checkpointer: BaseCheckpointSaver,
    *,
    action: str = "translate",
    instruction: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Run an action for several chapters.

    Each chapter runs on its own checkpoint thread, so this simply loops
    apply_chapter_command per chapter — true concurrency comes from the
    caller (e.g. the API layer) invoking this per-chapter, or scheduling each
    apply_chapter_command call on its own worker thread, since none of them
    contend for a shared thread_id anymore.
    """
    return {
        chapter_id: apply_chapter_command(
            run_id, chapter_id, action, checkpointer, instruction=instruction
        )
        for chapter_id in chapter_ids
    }


def apply_chapter_decision(
    run_id: str,
    decisions: dict[str, str],
    checkpointer: BaseCheckpointSaver,
    *,
    default_action: str = "approve",
    instruction: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper around targeted chapter commands.

    This preserves the historic API while routing each chapter to the new
    targeted command logic.
    """
    results: list[dict[str, Any]] = []
    for chapter_id, action in decisions.items():
        results.append(
            apply_chapter_command(
                run_id,
                chapter_id,
                action,
                checkpointer,
                instruction=instruction,
            )
        )
    return results[-1] if results else {"status": "no-op"}


def apply_read_gate_decision(
    run_id: str,
    action: str,
    checkpointer: BaseCheckpointSaver,
    **kwargs: Any,
) -> dict[str, Any]:
    """Resume the graph at the read gate.

    Common actions: approve_continue, select_chapters, skip_chapters,
    recompile_chapters, abort_run, manual_file_edit_acknowledged.
    Extra keyword args are forwarded in the resume payload (e.g. chapter_ids=[…]).
    """
    payload: dict[str, Any] = {"action": action, **kwargs}
    return resume_workflow_graph(
        run_id=run_id,
        resume_value=payload,
        config_file=_config_file(),
        checkpointer=checkpointer,
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def remap_chapter_source(
    run_id: str,
    chapter_id: str,
    new_source_path: str,
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Point a chapter's source file at a different path.

    ux2.md: "Remap file paths (e.g. point to a file in a subdirectory)".
    Only valid before the chapter has been dispatched — once its own thread
    exists its initial state (including source_path) is already fixed, so a
    remap at that point wouldn't do anything; the chapter would need to be
    recompiled from source instead (see write_chapter_file_and_trigger).
    """
    if get_chapter_snapshot(run_id, chapter_id, checkpointer=checkpointer) is not None:
        return {
            "error": (
                f"chapter {chapter_id!r} has already started — remap must happen "
                "before it is dispatched"
            )
        }

    graph = _active_workflow_graph(checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return {"error": f"no checkpoint found for run_id={run_id!r}"}

    manifest = list(snapshot.values.get("manifest", []))
    new_manifest = []
    found = False
    for ch in manifest:
        if _chapter_id_from_path(ch["output_path"]) == chapter_id:
            ch = {**ch, "source_path": new_source_path}
            found = True
        new_manifest.append(ch)
    if not found:
        return {"error": f"chapter {chapter_id!r} not found in run {run_id!r}"}

    graph.update_state(config, {"manifest": new_manifest})
    return {
        "run_id": run_id,
        "chapter_id": chapter_id,
        "source_path": new_source_path,
        "status": "remapped",
    }


def write_chapter_file_and_trigger(
    run_id: str,
    chapter_id: str,
    target: str,
    content: str,
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Write directly-edited source or translated-output text for a chapter.

    ux2.md: editing the source directly triggers edit_source_file_acknowledged
    and requires recompilation; editing the translated output re-runs
    compilation/validation instead of a full retranslate.

    target="source": writes the chapter's source file. If the chapter is
        already dispatched and paused at its review gate, immediately
        triggers recompile_source (re-read the edited source, retranslate).
        If not yet dispatched, the edit simply takes effect whenever the
        chapter is later dispatched — nothing further to trigger now.
    target="output": writes the chapter's translated .ptx file directly, then
        triggers revalidate (re-run review against the edited artifact) if
        the chapter is currently paused at its review gate.
    """
    if target not in ("source", "output"):
        return {"error": "target must be 'source' or 'output'"}

    chapter = _find_chapter_in_manifest(run_id, chapter_id, checkpointer)
    if chapter is None:
        return {"error": f"chapter {chapter_id!r} not found in run {run_id!r}"}

    path = Path(chapter["source_path"] if target == "source" else chapter["output_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    snapshot = get_chapter_snapshot(run_id, chapter_id, checkpointer=checkpointer)
    at_gate = bool(snapshot is not None and getattr(snapshot, "interrupts", None))

    if not at_gate:
        return {
            "run_id": run_id,
            "chapter_id": chapter_id,
            "target": target,
            "status": "written",
            "triggered": None,
        }

    action = "recompile_source" if target == "source" else "revalidate"
    result = apply_chapter_command(run_id, chapter_id, action, checkpointer)
    result["target"] = target
    result["triggered"] = action
    return result


def get_chapter_findings(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> dict[str, Any]:
    """Return the full findings sidecar (per-issue messages, not just counts) for one chapter."""
    graph = _active_workflow_graph(checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return {"error": f"no checkpoint found for run_id={run_id!r}"}
    output_dir = snapshot.values.get("output_dir", "")
    return {
        "run_id": run_id,
        "chapter_id": chapter_id,
        "findings": load_chapter_findings(output_dir, chapter_id),
    }


# ---------------------------------------------------------------------------
# Checkpoint history / revert
# ---------------------------------------------------------------------------

def get_chapter_checkpoints(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> list[dict[str, Any]]:
    """List a chapter's own checkpoint history, most recent first.

    ux2.md: "editors should keep awareness of checkpoints and saved progress
    so that things can always be reverted." Each entry names the node that
    was about to run at that point (or "end" once the chapter finished), so
    the UI can label checkpoints meaningfully (e.g. "Before Translate").
    """
    chapter_graph = _active_chapter_graph(checkpointer)
    config = build_chapter_checkpoint_config(run_id, chapter_id)
    try:
        history = list(chapter_graph.get_state_history(config))
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for snap in history:
        cfg = getattr(snap, "config", None) or {}
        checkpoint_id = (cfg.get("configurable") or {}).get("checkpoint_id")
        if not checkpoint_id:
            continue
        md = getattr(snap, "metadata", None)
        step = md.get("step") if isinstance(md, dict) else getattr(md, "step", None)
        next_nodes = list(getattr(snap, "next", []) or [])
        out.append({
            "checkpoint_id": checkpoint_id,
            "created_at": getattr(snap, "created_at", None),
            "step": step,
            "next": next_nodes[0] if next_nodes else "end",
        })
    return out


def revert_chapter_to_checkpoint(
    run_id: str,
    chapter_id: str,
    checkpoint_id: str,
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Revert a chapter's thread to an earlier checkpoint (LangGraph time-travel).

    Fetches the historical snapshot and re-writes its values as a new
    checkpoint on top of the thread's history, so it becomes the current
    state that future resume/run calls continue from — nothing is deleted,
    the prior (now-superseded) checkpoints remain in history.
    """
    chapter_graph = _active_chapter_graph(checkpointer)
    base_config = build_chapter_checkpoint_config(run_id, chapter_id)
    # checkpoint_ns must be explicit — get_state() doesn't fill it in, it just
    # echoes back whatever config was passed, and the sqlite/memory savers'
    # put_writes KeyErrors without it. The per-chapter graph has no nested
    # subgraphs, so the namespace at this level is always "".
    lookup_config = {
        "configurable": {
            **base_config["configurable"],
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint_id,
        }
    }
    snapshot = chapter_graph.get_state(lookup_config)
    if snapshot is None or not snapshot.values:
        return {"error": f"checkpoint {checkpoint_id!r} not found for chapter {chapter_id!r}"}

    chapter_graph.update_state(lookup_config, snapshot.values)

    # update_state only rewrites the checkpointed values — it doesn't replay
    # execution, so the node that was pending at that point (e.g.
    # chapter_review_gate) hasn't actually run again yet and there is no
    # fresh interrupt(). Advance once on the plain thread config (no pinned
    # checkpoint_id, so it resolves to the new latest checkpoint just
    # written) so that node re-executes and re-fires its interrupt with the
    # reverted values — otherwise the chapter would look gate-less until
    # some other unrelated action nudged it forward.
    _run_with_checkpoint_metadata(chapter_graph, None, checkpoint_config=base_config)

    return {
        "run_id": run_id,
        "chapter_id": chapter_id,
        "reverted_to": checkpoint_id,
        "status": "reverted",
    }


def get_output(run_id: str, checkpointer: BaseCheckpointSaver) -> list[dict[str, Any]]:
    """Return the list of generated .ptx files for *run_id*."""
    view = get_run_view(run_id, checkpointer)
    return view.get("output", [])


def get_jobs(run_id: str, checkpointer: BaseCheckpointSaver) -> list[dict[str, Any]]:
    """Return the jobs list for *run_id* (derived from checkpoint history)."""
    view = get_run_view(run_id, checkpointer)
    return view.get("jobs", [])
