"""Read-model builder for LyreTeXt runs.

``build_view_model(run_id, checkpointer)`` is the single source-of-truth for all
UI state.  It derives everything from:
  - graph.get_state snapshot (manifest, chapter_status, human_signoffs, …)
  - pending interrupts on the snapshot
  - output_dir/*.ptx presence
  - .lyretext/<chapter_id>.findings.json sidecars
  - graph.get_state_history (jobs view)

The returned dict mirrors the prototype's LYRE data structure (data.js).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from .orchestration.checkpointing import build_checkpoint_config, build_chapter_checkpoint_config
from .orchestration.graph import _active_chapter_graph, _active_workflow_graph, _chapter_id_from_path


# ---------------------------------------------------------------------------
# Findings helpers
# ---------------------------------------------------------------------------

def _findings_path(output_dir: str, chapter_id: str) -> Path:
    return Path(output_dir) / ".lyretext" / f"{chapter_id}.findings.json"


def _load_findings(output_dir: str, chapter_id: str) -> dict[str, Any] | None:
    """Return the raw findings dict from the sidecar, or None if absent/unreadable."""
    p = _findings_path(output_dir, chapter_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def load_chapter_findings(output_dir: str, chapter_id: str) -> dict[str, Any] | None:
    """Public wrapper around the findings sidecar loader, for reuse by service.py."""
    return _load_findings(output_dir, chapter_id)


def _findings_counts(findings: dict[str, Any] | None) -> dict[str, int]:
    if findings is None:
        return {"error": 0, "warn": 0, "info": 0}
    counts = findings.get("counts", {})
    return {
        "error": counts.get("error", 0),
        "warn": counts.get("warn", 0),
        "info": counts.get("info", 0),
    }


# ---------------------------------------------------------------------------
# Per-chapter stage-state derivation
# ---------------------------------------------------------------------------

def _chapter_stage_states(
    *,
    chapter_id: str,
    output_path: str,
    output_dir: str,
    chapter_status: dict[str, str],
    pending_interrupt: dict[str, Any] | None,
    findings: dict[str, Any] | None,
    at_read_gate: bool = False,
) -> dict[str, str]:
    """Return {read, translate, validate, enhance} stage states for one chapter."""
    status = chapter_status.get(chapter_id, "pending")

    if status == "skipped":
        return {"read": "done", "translate": "skipped", "validate": "skipped", "enhance": "disabled"}

    # When the run is at the read gate (reading is done but translation hasn't started
    # yet for this run), treat all chapters as translate=pending regardless of any
    # .ptx files that may exist from a prior run on the same output directory.
    if at_read_gate and not pending_interrupt:
        return {"read": "done", "translate": "pending", "validate": "pending", "enhance": "disabled"}

    ptx_exists = bool(output_path) and Path(output_path).exists()
    findings_exist = findings is not None
    has_blocking = (
        findings is not None
        and any(
            i.get("severity") == "error" and not i.get("auto_fixed")
            for i in findings.get("issues", [])
        )
    )
    escalation = pending_interrupt.get("escalation_required", False) if pending_interrupt else False
    is_dispatch = pending_interrupt and pending_interrupt.get("type") == "chapter_dispatch"
    # By the time chapter_review_gate fires, review_chapter has already run
    # to completion (that's the graph order: translate -> review -> gate) —
    # the human decision it's waiting on is a *validate*-stage concern, not
    # unfinished translate work, so this is the primary signal for validate
    # below rather than "running": nothing is actually executing while a
    # human is being asked to decide.
    is_review_gate = bool(pending_interrupt and pending_interrupt.get("type") == "chapter_review")

    # --- translate ---
    if is_dispatch:
        # Chapter is queued but not yet started — show as pending
        translate_state = "pending"
    elif ptx_exists:
        # Translation work itself is complete once output exists, even while
        # a review gate is open on it — the pending human decision belongs
        # to validate, not translate.
        translate_state = "done"
    elif pending_interrupt:
        translate_state = "needs-review"
    else:
        translate_state = "pending"

    # --- validate ---
    if is_review_gate:
        validate_state = "failed" if escalation else "needs-review"
    elif not ptx_exists:
        validate_state = "pending"
    elif findings_exist and has_blocking:
        validate_state = "failed" if escalation else "needs-review"
    else:
        # ptx exists and there's no open gate: the chapter has nothing left
        # pending, whether that's because findings were recorded and came
        # back clean, or because no findings sidecar exists at all (e.g. the
        # mock pipeline never writes one) — either way, treat it as done
        # rather than stalling on "pending" forever.
        validate_state = "done"

    return {
        "read": "done",
        "translate": translate_state,
        "validate": validate_state,
        "enhance": "disabled",
    }


def _chapter_lifecycle(
    *,
    output_path: str,
    findings: dict[str, Any] | None,
    pending_interrupt: dict[str, Any] | None,
) -> str:
    """Return a stable lifecycle label for a chapter summary."""
    if pending_interrupt and pending_interrupt.get("type") == "chapter_review":
        return "review_required"
    if output_path and Path(output_path).exists():
        if findings is not None and any(
            i.get("severity") == "error" and not i.get("auto_fixed")
            for i in findings.get("issues", [])
        ):
            return "review_required"
        return "approved"
    return "translation_ready"


def _available_actions(*, lifecycle: str, pending_interrupt: dict[str, Any] | None) -> list[str]:
    """Return the actions available to the UI for this chapter."""
    if lifecycle == "review_required":
        return ["approve", "retry", "revalidate"]
    if lifecycle == "approved":
        return ["revalidate", "retry"]
    return ["translate"]


# ---------------------------------------------------------------------------
# Jobs view — derived from checkpoint history
# ---------------------------------------------------------------------------

# Human-readable labels for known node names
_NODE_LABELS: dict[str, str] = {
    "build_skeleton":        "Read project",
    "process_to_markdown":   "Compile to Markdown",
    "upload_project":        "Upload source files",
    "structure_project":     "Structure project",
    "create_temp_directory": "Create temp directory",
    "scan_project_resources":"Scan resources",
    "evaluate_read_gate":    "Read gate",
    "recompile_chapters":    "Recompile chapters",
    "chapter_dispatch_gate": "Ready to translate",
    "read_chapter":          "Read chapter",
    "translate_chapter":     "Translate chapter",
    "review_chapter":        "Validate chapter",
    "chapter_review_gate":   "Chapter gate",
}


def _meta_get(metadata: Any, key: str, default: Any = None) -> Any:
    """Safe accessor for checkpoint metadata that may be a TypedDict, dataclass, or plain dict."""
    if metadata is None:
        return default
    if isinstance(metadata, dict):
        return metadata.get(key, default)
    # LangGraph CheckpointMetadata may be a dataclass/named-tuple in some versions
    return getattr(metadata, key, default)


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        from datetime import datetime
        # LangGraph timestamps are ISO-8601, typically with a trailing "Z".
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_duration(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s"


def _history_entries(graph: Any, config: dict[str, Any]) -> list[tuple[int, str, str | None]]:
    """Return deduplicated (step, node_name, duration_label) triples from checkpoint history.

    In LangGraph, each StateSnapshot has ``snapshot.tasks`` containing nodes
    that are PENDING (about to run). The snapshot at step N shows which node
    runs next, so collecting unique task names from all non-final snapshots
    gives us the complete set of nodes that executed on this thread. Each
    snapshot's ``created_at`` timestamps the *start* of that step, so a node's
    duration is the gap between the snapshot it was pending on and the next
    snapshot recorded (when it finished and the following step began).
    """
    try:
        history = list(graph.get_state_history(config))
    except Exception:
        return []

    step_times: dict[int, Any] = {}
    raw: list[tuple[int, str]] = []

    for snapshot in history:
        md = getattr(snapshot, "metadata", None)
        step = _meta_get(md, "step", 0)
        created_at = _parse_iso(getattr(snapshot, "created_at", None))
        if created_at is not None:
            step_times[step] = created_at

        for task in (getattr(snapshot, "tasks", None) or []):
            name = getattr(task, "name", None)
            if not name or name in ("__start__", "__end__"):
                continue
            raw.append((step, name))

    entries: list[tuple[int, str, str | None]] = []
    seen: set[str] = set()
    for step, name in sorted(raw, key=lambda e: e[0]):
        if name in seen:
            continue
        seen.add(name)
        start = step_times.get(step)
        end = step_times.get(step + 1)
        duration = None
        if start is not None and end is not None:
            duration = _format_duration((end - start).total_seconds())
        entries.append((step, name, duration))

    return entries


def _build_jobs_view(
    *,
    run_graph: Any,
    run_config: dict[str, Any],
    chapter_threads: list[tuple[str, Any, dict[str, Any], dict[str, Any] | None]],
) -> list[dict[str, Any]]:
    """Build the jobs list from the run-level thread plus each chapter's own thread.

    chapter_threads is a list of (chapter_id, chapter_graph, chapter_config,
    pending_interrupt) tuples — one entry per chapter — so each chapter's own
    checkpoint history (read/translate/review nodes) is reported separately,
    with a real status derived from whether that chapter is currently paused
    at a gate, still has nodes ahead of it, or has finished running.
    """
    jobs: list[dict[str, Any]] = []

    for step, name, duration in _history_entries(run_graph, run_config):
        jobs.append({
            "node": name,
            "label": _NODE_LABELS.get(name, name.replace("_", " ").title()),
            "status": "done",
            "step": step,
            "duration": duration,
            "chapter_id": None,
        })

    for chapter_id, chapter_graph, chapter_config, pending_interrupt in chapter_threads:
        entries = _history_entries(chapter_graph, chapter_config)
        for step, name, duration in entries:
            jobs.append({
                "node": name,
                "label": _NODE_LABELS.get(name, name.replace("_", " ").title()),
                "status": "done",
                "step": step,
                "duration": duration,
                "chapter_id": chapter_id,
            })
        if pending_interrupt:
            gate_node = (
                "chapter_review_gate"
                if pending_interrupt.get("type") == "chapter_review"
                else "chapter_dispatch_gate"
            )
            escalation = pending_interrupt.get("escalation_required", False)
            jobs.append({
                "node": gate_node,
                "label": _NODE_LABELS.get(gate_node, gate_node.replace("_", " ").title()),
                "status": "failed" if escalation else "needs-review",
                "step": (entries[-1][0] + 1) if entries else 0,
                "duration": None,
                "chapter_id": chapter_id,
            })

    return jobs



# ---------------------------------------------------------------------------
# Main view-model builder
# ---------------------------------------------------------------------------

def build_view_model(run_id: str, checkpointer: BaseCheckpointSaver) -> dict[str, Any]:
    """Build the read-model dict for *run_id*.

    Returns a dict with keys: run_id, project, chapters, resources, jobs, output, gate.
    Returns ``{"error": "..."}`` if no checkpoint exists for the run.

    Each chapter now runs on its own checkpoint thread (see
    orchestration/graph.py's invoke_chapter_graph/resume_chapter_graph), so
    this reads the run's own thread for project-level state (manifest,
    human_signoffs, output_dir, …) and then reads each chapter's own thread
    separately for its stage/gate state, rather than descending into nested
    Send-subgraph task states (chapters are no longer Send branches of this
    graph at all).
    """
    graph = _active_workflow_graph(checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = graph.get_state(config)

    if snapshot is None or not snapshot.values:
        return {"error": f"No checkpoint found for run_id={run_id!r}"}

    values: dict[str, Any] = snapshot.values
    manifest: list[dict] = values.get("manifest", [])
    chapter_status: dict[str, str] = values.get("chapter_status", {})
    human_signoffs: dict[str, bool] = values.get("human_signoffs", {})
    output_dir: str = values.get("output_dir", "")
    project_source: str = values.get("project_source", "")
    project_type: str = values.get("project_type", "rmd")
    project_resources: list[dict] = values.get("project_resources", [])

    def _thread_interrupts(snap: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for intr in (getattr(snap, "interrupts", None) or []):
            value = getattr(intr, "value", None)
            if isinstance(value, dict):
                result.append({"interrupt_id": getattr(intr, "id", ""), **value})
        return result

    read_gate_interrupt = next(
        (i for i in _thread_interrupts(snapshot) if i.get("type") == "validation_gate_blocked"),
        None,
    )

    # Each chapter has its own thread; fetch its snapshot independently.
    chapter_graph = _active_chapter_graph(checkpointer)
    chapter_snapshots: dict[str, Any] = {}
    chapter_configs: dict[str, dict[str, Any]] = {}
    chapter_interrupts: dict[str, dict] = {}
    for ch in manifest:
        chapter_id = _chapter_id_from_path(ch["output_path"])
        ch_config = build_chapter_checkpoint_config(run_id, chapter_id)
        chapter_configs[chapter_id] = ch_config
        ch_snapshot = chapter_graph.get_state(ch_config)
        if ch_snapshot is not None and ch_snapshot.values:
            chapter_snapshots[chapter_id] = ch_snapshot
            intrs = _thread_interrupts(ch_snapshot)
            if intrs:
                chapter_interrupts[chapter_id] = intrs[0]

    # Build per-chapter view entries
    chapters_view: list[dict[str, Any]] = []
    for ch in manifest:
        chapter_id = Path(ch["output_path"]).stem
        findings = _load_findings(output_dir, chapter_id)
        pending_intr = chapter_interrupts.get(chapter_id)

        stages = _chapter_stage_states(
            chapter_id=chapter_id,
            output_path=ch["output_path"],
            output_dir=output_dir,
            chapter_status=chapter_status,
            pending_interrupt=pending_intr,
            findings=findings,
            at_read_gate=read_gate_interrupt is not None,
        )

        # Gate entry
        gate: dict[str, Any] | None = None
        if pending_intr:
            intr_type = pending_intr.get("type", "")
            if intr_type == "chapter_dispatch":
                gate = {
                    "stage": "dispatch",
                    "message": "Ready to translate",
                    "failed": False,
                    "interrupt_id": pending_intr.get("interrupt_id", ""),
                }
            else:  # chapter_review
                escalation = pending_intr.get("escalation_required", False)
                gate = {
                    "stage": "translate",
                    "message": (
                        "Escalation required — retries exhausted"
                        if escalation
                        else "Needs review"
                    ),
                    "failed": escalation,
                    "interrupt_id": pending_intr.get("interrupt_id", ""),
                    "iteration_count": pending_intr.get("iteration_count"),
                    "escalation_required": escalation,
                }
        elif read_gate_interrupt:
            # chapter_status is only populated once the read gate is approved
            # (see _build_approved_read_gate in orchestration/graph.py), so
            # every chapter is uniformly "awaiting approval" here — there's
            # no per-chapter distinction to make yet.
            gate = {
                "stage": "read",
                "message": "Awaiting read gate approval",
                "failed": False,
                "interrupt_id": read_gate_interrupt.get("interrupt_id", ""),
            }

        lifecycle = _chapter_lifecycle(
            output_path=ch.get("output_path", ""),
            findings=findings,
            pending_interrupt=pending_intr,
        )
        chapters_view.append({
            "id": chapter_id,
            "title": ch.get("name", chapter_id),
            "file": ch.get("source_path", ""),
            "output_file": ch.get("output_path", ""),
            "type": ch.get("type", "chapter"),
            "stages": stages,
            "findings": _findings_counts(findings),
            "gate": gate,
            "lifecycle": lifecycle,
            "available_actions": _available_actions(lifecycle=lifecycle, pending_interrupt=pending_intr),
        })

    # Output .ptx listing
    output_files: list[dict[str, Any]] = []
    if output_dir:
        out_path = Path(output_dir)
        if out_path.exists():
            for ptx in sorted(out_path.glob("*.ptx")):
                output_files.append({
                    "file": ptx.name,
                    "path": str(ptx),
                    "size": ptx.stat().st_size,
                })

    project_name = Path(project_source).name if project_source else "Untitled project"

    chapter_threads = [
        (
            chapter_id,
            chapter_graph,
            chapter_configs[chapter_id],
            chapter_interrupts.get(chapter_id),
        )
        for chapter_id in chapter_configs
        if chapter_id in chapter_snapshots
    ]

    return {
        "run_id": run_id,
        "project": {
            "name": project_name,
            "source": project_source,
            "source_type": project_type,
        },
        "chapters": chapters_view,
        "resources": project_resources,
        "jobs": _build_jobs_view(
            run_graph=graph, run_config=config, chapter_threads=chapter_threads
        ),
        "output": output_files,
        "gate": {
            "read_pending": read_gate_interrupt is not None,
            "decision": values.get("stage_gate_decision"),
            "interrupt_id": read_gate_interrupt.get("interrupt_id", "") if read_gate_interrupt else None,
            "warnings": values.get("read_stage_warnings", []),
        },
    }
