from __future__ import annotations

from typing import Any, Literal, Optional
from pathlib import Path
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from ..config import get_resolved_config
from .state import TranslationState
from ..translate.state import ChapterTranslation
from ..translate.graph import build_chapter_graph
from ..read.graph import build_skeleton_graph
from ..read.agents import process_to_markdown
from .checkpointing import (
    build_checkpointer,
    build_checkpoint_config,
    build_chapter_checkpoint_config,
    ensure_run_id,
    extract_checkpoint_metadata,
)
from .validation import (
    default_validation_summary,
    evaluate_stage_transition,
)


# ============================================================================
# Helpers
# ============================================================================

def _chapter_id_from_path(output_path: str) -> str:
    return Path(output_path).stem


def _build_approved_read_gate(
    state: TranslationState,
    decision: dict,
    *,
    signoffs: dict | None = None,
    manifest_override: list | None = None,
    chapter_status_override: dict | None = None,
) -> dict:
    """Build the state update when the read gate is approved."""
    manifest = manifest_override if manifest_override is not None else list(state.get("manifest", []))
    if signoffs is None:
        signoffs = dict(state.get("human_signoffs", {}))
        signoffs["read->translate"] = True

    status = dict(chapter_status_override or state.get("chapter_status", {}))
    for ch in manifest:
        ch_id = _chapter_id_from_path(ch["output_path"])
        if ch_id not in status:
            status[ch_id] = "pending"

    result: dict[str, Any] = {
        "human_signoffs": signoffs,
        "stage_gate_decision": decision,
        "chapter_status": status,
    }
    if manifest_override is not None:
        result["manifest"] = manifest_override
    return result


# ============================================================================
# Node functions
# ============================================================================

def chapters_from_manifest(manifest: list[dict]) -> list[dict]:
    """Return {chapter_id, source_path, output_path} for each manifest entry.

    Chapters are no longer dispatched from this graph via a Send fan-out —
    each chapter runs as an independent graph invocation on its own
    checkpoint thread (see invoke_chapter_graph/resume_chapter_graph below).
    This helper is what the service layer uses to enumerate the chapters
    available to dispatch once the manifest is approved.
    """
    return [
        {
            "chapter_id": _chapter_id_from_path(chapter["output_path"]),
            "source_path": chapter["source_path"],
            "output_path": chapter["output_path"],
        }
        for chapter in manifest
    ]


def route_workflow_entry(state: TranslationState) -> str:
    return "build_skeleton"


def route_after_read_gate(state: TranslationState):
    recompile_queue = state.get("recompile_queue")
    if isinstance(recompile_queue, list) and len(recompile_queue) > 0:
        return "recompile_chapters"
    # Whether or not the gate approved the manifest, this graph's job is done —
    # once approved, chapters are dispatched independently (see module docstring
    # note above); the service layer reads the finalized manifest/chapter_status
    # from this run's own checkpoint to know what's available to run.
    return END


def evaluate_read_stage_gate(state: TranslationState) -> dict:
    """Read-to-translate stage gate.

    Fires interrupt() to collect a human decision before translation begins.
    Dispatches on resume_value["action"]:
      - approve_continue              : pass gate, proceed to translation
      - edit_manifest + manifest      : overwrite manifest, then pass gate
      - select_chapters + chapter_ids : filter manifest to subset, pass gate
      - skip_chapters + chapter_ids   : mark chapters skipped, pass with remainder
      - recompile_chapters + chapter_ids : trigger recompile loop
      - abort_run                     : route to END
      - manual_file_edit_acknowledged : re-interrupt after recording file edit
    """
    decision = evaluate_stage_transition(
        state,
        from_stage="read",
        to_stage="translate",
    )
    if decision["allowed"]:
        return _build_approved_read_gate(state, decision)

    resume_value = interrupt({
        "type": "validation_gate_blocked",
        "decision": decision,
    })

    # Legacy bare-bool support
    if isinstance(resume_value, bool):
        if resume_value:
            approved = {**decision, "allowed": True, "reason": "approved_by_resume"}
            return _build_approved_read_gate(state, approved)
        return {"stage_gate_decision": decision}

    if not isinstance(resume_value, dict):
        return {"stage_gate_decision": decision}

    action = resume_value.get("action", "")

    # Legacy dict-key support
    if not action and (resume_value.get("approve") or resume_value.get("approved")):
        action = "approve_continue"

    if action == "approve_continue":
        signoffs = dict(state.get("human_signoffs", {}))
        signoffs["read->translate"] = True
        approved = {**decision, "allowed": True, "reason": "approved_by_resume"}
        return _build_approved_read_gate(state, approved, signoffs=signoffs)

    if action == "edit_manifest":
        new_manifest = resume_value.get("manifest", state.get("manifest", []))
        signoffs = dict(state.get("human_signoffs", {}))
        signoffs["read->translate"] = True
        approved = {**decision, "allowed": True, "reason": "approved_by_resume"}
        return _build_approved_read_gate(
            state, approved, signoffs=signoffs, manifest_override=new_manifest
        )

    if action == "select_chapters":
        chapter_ids = set(resume_value.get("chapter_ids", []))
        filtered = [
            ch for ch in state.get("manifest", [])
            if _chapter_id_from_path(ch["output_path"]) in chapter_ids
            or ch.get("name") in chapter_ids
        ]
        signoffs = dict(state.get("human_signoffs", {}))
        signoffs["read->translate"] = True
        approved = {**decision, "allowed": True, "reason": "approved_by_resume"}
        return _build_approved_read_gate(
            state, approved, signoffs=signoffs, manifest_override=filtered
        )

    if action == "skip_chapters":
        chapter_ids = set(resume_value.get("chapter_ids", []))
        remaining = [
            ch for ch in state.get("manifest", [])
            if _chapter_id_from_path(ch["output_path"]) not in chapter_ids
            and ch.get("name") not in chapter_ids
        ]
        skipped_status = {
            _chapter_id_from_path(ch["output_path"]): "skipped"
            for ch in state.get("manifest", [])
            if _chapter_id_from_path(ch["output_path"]) in chapter_ids
            or ch.get("name") in chapter_ids
        }
        existing_status = dict(state.get("chapter_status", {}))
        existing_status.update(skipped_status)
        signoffs = dict(state.get("human_signoffs", {}))
        signoffs["read->translate"] = True
        approved = {**decision, "allowed": True, "reason": "approved_by_resume"}
        return _build_approved_read_gate(
            state, approved,
            signoffs=signoffs,
            manifest_override=remaining,
            chapter_status_override=existing_status,
        )

    if action == "recompile_chapters":
        chapter_ids = resume_value.get("chapter_ids", [])
        # Non-empty list triggers recompile_chapters node via route_after_read_gate.
        # "__all__" is a sentinel meaning recompile everything (MVP behaviour).
        return {"recompile_queue": chapter_ids or ["__all__"]}

    if action == "abort_run":
        return {
            "stage_gate_decision": {
                "allowed": False,
                "reason": "user_aborted",
                "from_stage": "read",
                "to_stage": "translate",
                "requires_human_signoff": False,
            }
        }

    if action == "manual_file_edit_acknowledged":
        # File edited externally. Re-interrupt with after_file_edit context so
        # the UI/CLI knows files have changed and can prompt a fresh decision.
        # MVP: second interrupt only accepts approve_continue; full action
        # dispatch can be added in a later iteration.
        file_path = resume_value.get("file_path", "")
        file_type = resume_value.get("file_type", "")
        resume_value_2 = interrupt({
            "type": "validation_gate_blocked",
            "decision": decision,
            "after_file_edit": True,
            "acknowledged_file": {"path": file_path, "type": file_type},
        })
        if isinstance(resume_value_2, dict) and resume_value_2.get("action") == "approve_continue":
            signoffs = dict(state.get("human_signoffs", {}))
            signoffs["read->translate"] = True
            approved = {**decision, "allowed": True, "reason": "approved_after_file_edit"}
            return _build_approved_read_gate(state, approved, signoffs=signoffs)
        return {"stage_gate_decision": decision}

    # Unknown action — keep gate blocked
    return {"stage_gate_decision": decision}


def recompile_chapters(
    state: TranslationState,
    config: RunnableConfig = None,
) -> dict:
    """Re-run the markdown compilation step for the project.

    MVP: recompiles all project files regardless of which chapters were queued.
    Per-file selective compilation is deferred.
    Clears recompile_queue after completion so route_after_read_gate proceeds normally.
    """
    process_to_markdown(state, config)
    return {"recompile_queue": []}


# ============================================================================
# Graph builders
# ============================================================================

def build_workflow_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Project-level graph: pre-processing/read through the read/manifest gate.

    This graph owns the run's own checkpoint thread only. It no longer fans
    out per-chapter work via Send — once the manifest is approved, chapters
    are invoked independently (see invoke_chapter_graph/resume_chapter_graph)
    so they can run concurrently without racing on this thread.
    """
    graph_builder = StateGraph(TranslationState)

    skeleton_graph = build_skeleton_graph()

    graph_builder.add_node("build_skeleton", skeleton_graph)
    graph_builder.add_node("evaluate_read_gate", evaluate_read_stage_gate)
    graph_builder.add_node("recompile_chapters", recompile_chapters)

    graph_builder.add_conditional_edges(START, route_workflow_entry)
    graph_builder.add_edge("build_skeleton", "evaluate_read_gate")
    graph_builder.add_conditional_edges("evaluate_read_gate", route_after_read_gate)
    graph_builder.add_edge("recompile_chapters", "evaluate_read_gate")

    return graph_builder.compile(checkpointer=checkpointer)


def build_checkpointed_workflow_graph():
    return build_workflow_graph(checkpointer=build_checkpointer())


# ============================================================================
# Runtime helpers
# ============================================================================

def resolve_runtime_options_for_graph(
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
) -> dict:
    resolved_config = get_resolved_config(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    return resolved_config.model_dump()


def _classify_interrupts(
    raw_interrupts: list[dict],
) -> tuple[Literal["read", "translate", "unknown"], Literal["interrupted"]]:
    """Determine stage_id from interrupt payload types."""
    for intr in raw_interrupts:
        value = intr.get("value")
        if isinstance(value, dict):
            if value.get("type") in ("chapter_review", "chapter_dispatch"):
                return "translate", "interrupted"
            if value.get("type") == "validation_gate_blocked":
                return "read", "interrupted"
    return "unknown", "interrupted"


def _run_with_checkpoint_metadata(
    workflow_graph,
    graph_input: dict[str, Any] | Command,
    *,
    checkpoint_config: dict,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_interrupts: list[dict[str, Any]] = []

    for chunk in workflow_graph.stream(graph_input, config=checkpoint_config):
        if "__interrupt__" in chunk:
            for intr in chunk.get("__interrupt__", ()):
                raw_interrupts.append(
                    {
                        "id": getattr(intr, "id", ""),
                        "value": getattr(intr, "value", None),
                    }
                )

    snapshot = workflow_graph.get_state(config=checkpoint_config)
    state_values = getattr(snapshot, "values", None)
    if isinstance(state_values, dict):
        result.update(state_values)

    checkpoint_metadata = extract_checkpoint_metadata(snapshot)
    result.update(checkpoint_metadata)
    if "checkpoint_id" in checkpoint_metadata:
        result["last_checkpoint_id"] = checkpoint_metadata["checkpoint_id"]
    if "checkpoint_ns" in checkpoint_metadata:
        result["checkpoint_namespace"] = checkpoint_metadata["checkpoint_ns"]

    if raw_interrupts:
        stage_id, status = _classify_interrupts(raw_interrupts)
        result["interrupted"] = True
        result["status"] = status
        result["stage_id"] = stage_id
        result["pending_interrupts"] = [
            {
                "interrupt_id": intr["id"],
                "type": intr["value"].get("type") if isinstance(intr["value"], dict) else None,
                "chapter_id": intr["value"].get("chapter_id") if isinstance(intr["value"], dict) else None,
                "value": intr["value"],
            }
            for intr in raw_interrupts
        ]
    else:
        result["interrupted"] = False
        result["status"] = "completed"
        result["stage_id"] = "complete"
        result["pending_interrupts"] = []

    return result


def _mock_enabled() -> bool:
    """True only for an explicit truthy LYRETEXT_MOCK value.

    os.environ.get() returns a *string*, and "0" is truthy in Python — a
    bare `if os.environ.get("LYRETEXT_MOCK")` treated LYRETEXT_MOCK=0 the
    same as LYRETEXT_MOCK=1, so it was impossible to turn mock mode back off
    once the variable was set to anything at all short of unsetting it.
    """
    import os
    return os.environ.get("LYRETEXT_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def _active_workflow_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Return mock or real workflow graph based on LYRETEXT_MOCK env var."""
    if _mock_enabled():
        from .mock_graph import build_mock_workflow_graph
        return build_mock_workflow_graph(checkpointer=checkpointer or build_checkpointer())
    return build_workflow_graph(checkpointer=checkpointer or build_checkpointer())


def invoke_workflow_graph(
    state: dict,
    *,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    run_id: str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    workflow_graph = _active_workflow_graph(checkpointer=checkpointer)
    workflow_state = dict(state)
    runtime_options = resolve_runtime_options_for_graph(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    resolved_run_id = ensure_run_id(run_id or workflow_state.get("run_id"))
    workflow_state.setdefault("validation_summary", default_validation_summary())
    checkpoint_config = build_checkpoint_config(
        resolved_run_id,
        runtime_options=runtime_options,
    )
    result = _run_with_checkpoint_metadata(
        workflow_graph,
        workflow_state,
        checkpoint_config=checkpoint_config,
    )
    result.setdefault("run_id", resolved_run_id)
    return result


def resume_workflow_graph(
    *,
    run_id: str,
    resume_value: Any,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    workflow_graph = _active_workflow_graph(checkpointer=checkpointer)
    runtime_options = resolve_runtime_options_for_graph(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    checkpoint_config = build_checkpoint_config(
        run_id,
        runtime_options=runtime_options,
    )
    result = _run_with_checkpoint_metadata(
        workflow_graph,
        Command(resume=resume_value),
        checkpoint_config=checkpoint_config,
    )
    result.setdefault("run_id", run_id)
    return result


# ============================================================================
# Per-chapter graph — independent checkpoint thread per chapter
# ============================================================================
#
# Chapters used to be dispatched as Send() branches inside the run's own
# graph/thread. That made every chapter operation for a run serialize behind
# a single lock (LangGraph's Pregel model isn't safe for two independent
# .stream()/Command(resume=...) calls to race against the same thread_id).
# Instead, each chapter is invoked here as its own graph against its own
# thread (build_chapter_checkpoint_config), so N chapters of the same run can
# genuinely execute concurrently.

def _active_chapter_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Return the mock or real per-chapter graph, mirroring _active_workflow_graph."""
    cp = checkpointer or build_checkpointer()
    if _mock_enabled():
        from .mock_graph import build_mock_chapter_graph
        return build_mock_chapter_graph(checkpointer=cp)
    return build_chapter_graph(checkpointer=cp)


def invoke_chapter_graph(
    *,
    run_id: str,
    chapter_id: str,
    state: dict,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    """Start a chapter's graph for the first time, on its own checkpoint thread."""
    chapter_graph = _active_chapter_graph(checkpointer=checkpointer)
    runtime_options = resolve_runtime_options_for_graph(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    checkpoint_config = build_chapter_checkpoint_config(
        run_id, chapter_id, runtime_options=runtime_options
    )
    result = _run_with_checkpoint_metadata(
        chapter_graph,
        dict(state),
        checkpoint_config=checkpoint_config,
    )
    result.setdefault("run_id", run_id)
    result.setdefault("chapter_id", chapter_id)
    return result


def resume_chapter_graph(
    *,
    run_id: str,
    chapter_id: str,
    resume_value: Any,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    """Resume a chapter's own interrupt — independent of any other chapter's thread."""
    chapter_graph = _active_chapter_graph(checkpointer=checkpointer)
    runtime_options = resolve_runtime_options_for_graph(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    checkpoint_config = build_chapter_checkpoint_config(
        run_id, chapter_id, runtime_options=runtime_options
    )
    result = _run_with_checkpoint_metadata(
        chapter_graph,
        Command(resume=resume_value),
        checkpoint_config=checkpoint_config,
    )
    result.setdefault("run_id", run_id)
    result.setdefault("chapter_id", chapter_id)
    return result


def get_chapter_snapshot(
    run_id: str,
    chapter_id: str,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Return the raw LangGraph state snapshot for one chapter's thread, or None."""
    chapter_graph = _active_chapter_graph(checkpointer=checkpointer)
    config = build_chapter_checkpoint_config(run_id, chapter_id)
    snapshot = chapter_graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return None
    return snapshot


def get_run_manifest(
    run_id: str,
    checkpointer: BaseCheckpointSaver | None = None,
) -> list[dict] | None:
    """Return the approved manifest for a run's own thread, or None if absent."""
    workflow_graph = _active_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = workflow_graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return None
    return snapshot.values.get("manifest")


def get_run_source_context(
    run_id: str,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    """Return LaTeX-pipeline dispatch context for a run: main_file,
    project_root (when the tex pipeline populated them during read), and
    chapter_ids (every chapter_id in the run's approved manifest, in order).

    Chapters are dispatched onto their own independent checkpoint threads
    (see invoke_chapter_graph), so this is how each chapter's initial state
    picks up the project-wide context it needs to run pandoc once and split
    the monolithic output across all chapters -- see
    lyretext.translate.agents._translate_chapter_tex.

    Returns {} if the run's thread has no state yet.
    """
    workflow_graph = _active_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = workflow_graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return {}

    values = snapshot.values
    manifest = values.get("manifest") or []
    context: dict[str, Any] = {
        "chapter_ids": [_chapter_id_from_path(ch["output_path"]) for ch in manifest]
    }
    if values.get("main_file"):
        context["main_file"] = values["main_file"]
    if values.get("project_root"):
        context["project_root"] = values["project_root"]
    return context
