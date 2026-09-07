from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from lyretext.orchestration.checkpointing import build_checkpointer, build_checkpoint_config
from lyretext.orchestration.graph import (
    invoke_workflow_graph,
    resume_workflow_graph,
    build_workflow_graph,
    resolve_runtime_options_for_graph,
    extract_checkpoint_metadata,
)
from langgraph.types import Command


# ============================================================================
# Output helpers
# ============================================================================

def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True, default=str)


def _run_response(state: dict[str, Any]) -> dict[str, Any]:
    """Build the standard RunResponse envelope from a graph result dict."""
    manifest = state.get("manifest", [])
    return {
        "run_id": state.get("run_id", ""),
        "stage_id": state.get("stage_id", ""),
        "status": state.get("status", "unknown"),
        "interrupted": bool(state.get("interrupted", False)),
        "pending_interrupts": state.get("pending_interrupts", []),
        "manifest_count": len(manifest) if isinstance(manifest, list) else 0,
        "chapter_status": state.get("chapter_status", {}),
        "checkpoint_id": state.get("last_checkpoint_id", ""),
        "checkpoint_ns": state.get("checkpoint_namespace", ""),
    }


def _print_response(state: dict[str, Any]) -> None:
    print(_pretty_json(_run_response(state)))


# ============================================================================
# Checkpointer factory for CLI
# ============================================================================

def _make_checkpointer(args: argparse.Namespace):
    backend = getattr(args, "checkpointer", "sqlite")
    db_path = getattr(args, "db_path", None)
    return build_checkpointer(backend=backend, db_path=db_path)


# ============================================================================
# Resume value construction
# ============================================================================

def _build_resume_value(
    action: str,
    payload: dict[str, Any],
    chapter_decisions: dict[str, str] | None,
    pending_interrupts: list[dict[str, Any]],
) -> Any:
    """Build the correct Command.resume value for the current gate context.

    For a read gate (single validation_gate_blocked interrupt):
        Returns a plain dict {"action": action, **payload}.

    For chapter gates (one or more chapter_review interrupts):
        - If chapter_decisions provided: maps chapter_id -> interrupt_id,
          returns {interrupt_id: {"action": decision}, ...}.
        - If single interrupt pending: returns plain {"action": action}.
        - If multiple interrupts and no per-chapter decisions: applies the
          same action to all, returning the required dict form.
    """
    chapter_interrupts = [
        i for i in pending_interrupts if i.get("type") == "chapter_review"
    ]
    gate_interrupts = [
        i for i in pending_interrupts if i.get("type") == "validation_gate_blocked"
    ]

    if gate_interrupts:
        # Read gate — single resume value
        return {"action": action, **payload}

    if not chapter_interrupts:
        # No classified interrupts — fall back to plain value
        return {"action": action, **payload}

    if chapter_decisions:
        # Build per-chapter interrupt_id map
        ch_id_to_interrupt_id = {
            intr["chapter_id"]: intr["interrupt_id"]
            for intr in chapter_interrupts
            if intr.get("chapter_id")
        }
        resume_map: dict[str, Any] = {}
        for ch_id, decision in chapter_decisions.items():
            intr_id = ch_id_to_interrupt_id.get(ch_id)
            if intr_id:
                resume_map[intr_id] = {"action": decision}
        # Any pending chapters not in decisions default to approve
        for intr in chapter_interrupts:
            ch_id = intr.get("chapter_id", "")
            intr_id = intr["interrupt_id"]
            if intr_id not in resume_map:
                resume_map[intr_id] = {"action": "approve"}
        return resume_map

    if len(chapter_interrupts) == 1:
        # Single pending interrupt — plain value is fine
        return {"action": action, **payload}

    # Multiple chapter interrupts, no per-chapter decisions — apply same action to all.
    # LangGraph requires the interrupt_id map form when multiple are pending.
    return {
        intr["interrupt_id"]: {"action": action}
        for intr in chapter_interrupts
    }


def _get_pending_interrupts(run_id: str, checkpointer) -> list[dict[str, Any]]:
    """Fetch pending interrupt list from the checkpoint without running the graph."""
    graph = build_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id)
    snapshot = graph.get_state(config=config)
    interrupts = getattr(snapshot, "interrupts", ()) or ()
    return [
        {
            "interrupt_id": getattr(intr, "id", ""),
            "type": getattr(intr, "value", {}).get("type") if isinstance(getattr(intr, "value", None), dict) else None,
            "chapter_id": getattr(intr, "value", {}).get("chapter_id") if isinstance(getattr(intr, "value", None), dict) else None,
            "value": getattr(intr, "value", None),
        }
        for intr in interrupts
    ]


# ============================================================================
# Subcommand handlers
# ============================================================================

def cmd_start(args: argparse.Namespace) -> int:
    checkpointer = _make_checkpointer(args)
    initial_state = {
        "project_source": args.source,
        "temp_dir": args.temp,
        "output_dir": args.output,
    }
    result = invoke_workflow_graph(
        initial_state,
        config_file=args.config,
        checkpointer=checkpointer,
    )
    _print_response(result)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    run_id: str = args.run_id

    payload: dict[str, Any] = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"Invalid --payload JSON: {exc}", file=sys.stderr)
            print(f"  Provided: {args.payload}", file=sys.stderr)
            return 1

    chapter_decisions: dict[str, str] | None = None
    if args.chapter_decisions:
        try:
            chapter_decisions = json.loads(args.chapter_decisions)
        except json.JSONDecodeError as exc:
            print(f"Invalid --chapter-decisions JSON: {exc}", file=sys.stderr)
            print(f"  Provided: {args.chapter_decisions}", file=sys.stderr)
            print(f"\nPowerShell quoting help:", file=sys.stderr)
            print(f'  Use backticks to escape quotes: --chapter-decisions "{{\\"ch1\\": \\"approve\\"}}"', file=sys.stderr)
            print(f"  Or use Get-Content with a JSON file: --chapter-decisions (Get-Content file.json -Raw)", file=sys.stderr)
            return 1

    checkpointer = _make_checkpointer(args)
    pending = _get_pending_interrupts(run_id, checkpointer)

    resume_value = _build_resume_value(
        action=args.action,
        payload=payload,
        chapter_decisions=chapter_decisions,
        pending_interrupts=pending,
    )

    result = resume_workflow_graph(
        run_id=run_id,
        resume_value=resume_value,
        config_file=args.config,
        checkpointer=checkpointer,
    )
    _print_response(result)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_id: str = args.run_id
    checkpointer = _make_checkpointer(args)
    graph = build_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id)
    snapshot = graph.get_state(config=config)
    if snapshot is None or not getattr(snapshot, "values", None):
        print(json.dumps({"error": f"No run found for run_id: {run_id}"}))
        return 1

    state_values = dict(getattr(snapshot, "values", {}))
    meta = extract_checkpoint_metadata(snapshot)
    state_values.update(meta)
    if "checkpoint_id" in meta:
        state_values["last_checkpoint_id"] = meta["checkpoint_id"]
    if "checkpoint_ns" in meta:
        state_values["checkpoint_namespace"] = meta["checkpoint_ns"]

    interrupts = getattr(snapshot, "interrupts", ()) or ()
    pending = [
        {
            "interrupt_id": getattr(i, "id", ""),
            "type": getattr(i, "value", {}).get("type") if isinstance(getattr(i, "value", None), dict) else None,
            "chapter_id": getattr(i, "value", {}).get("chapter_id") if isinstance(getattr(i, "value", None), dict) else None,
            "value": getattr(i, "value", None),
        }
        for i in interrupts
    ]
    state_values["interrupted"] = bool(pending)
    state_values["pending_interrupts"] = pending
    state_values["run_id"] = run_id

    print(_pretty_json(_run_response(state_values)))
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    run_id: str = args.run_id
    checkpointer = _make_checkpointer(args)
    graph = build_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id)
    snapshot = graph.get_state(config=config)
    if snapshot is None:
        print(json.dumps({"error": f"No run found for run_id: {run_id}"}))
        return 1

    manifest = (getattr(snapshot, "values", {}) or {}).get("manifest", [])
    if args.raw:
        print(_pretty_json(manifest))
    else:
        print(f"Manifest entries: {len(manifest)}")
        for idx, ch in enumerate(manifest, start=1):
            if isinstance(ch, dict):
                print(f"  {idx}. {ch.get('name', '')} [{ch.get('type', '')}]")
                print(f"       source: {ch.get('source_path', '')}")
                print(f"       output: {ch.get('output_path', '')}")
            else:
                print(f"  {idx}. {ch}")
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    # NOTE: placeholder, not real event streaming. Prints one snapshot of the
    # run's current state (pending interrupts / next nodes) and exits; it does
    # not tail live events as the run progresses. Kept for status inspection
    # until real streaming is implemented.
    run_id: str = args.run_id
    checkpointer = _make_checkpointer(args)
    graph = build_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id)
    snapshot = graph.get_state(config=config)
    if snapshot is None:
        print(json.dumps({"error": f"No run found for run_id: {run_id}"}))
        return 1

    interrupts = getattr(snapshot, "interrupts", ()) or ()
    for intr in interrupts:
        value = getattr(intr, "value", None)
        intr_type = value.get("type") if isinstance(value, dict) else None
        event_type = "chapter_gate_reached" if intr_type == "chapter_review" else "read_gate_reached"
        print(_pretty_json({
            "event": event_type,
            "interrupt_id": getattr(intr, "id", ""),
            "value": value,
        }))

    if not interrupts:
        next_nodes = getattr(snapshot, "next", ())
        if not next_nodes:
            print(_pretty_json({"event": "run_completed", "run_id": run_id}))
        else:
            print(_pretty_json({"event": "run_paused", "run_id": run_id, "next": list(next_nodes)}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    backend = getattr(args, "checkpointer", "sqlite")
    if backend == "memory":
        print(json.dumps({"error": "list is not supported for the memory backend. Use --checkpointer sqlite."}))
        return 1
    checkpointer = _make_checkpointer(args)
    try:
        threads = list(checkpointer.list_threads()) if hasattr(checkpointer, "list_threads") else []
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(_pretty_json([{"run_id": t} for t in threads]))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    run_id: str = args.run_id
    checkpointer = _make_checkpointer(args)
    graph = build_workflow_graph(checkpointer=checkpointer)
    config = build_checkpoint_config(run_id)
    snapshot = graph.get_state(config=config)
    if snapshot is None:
        print(json.dumps({"error": f"No run found for run_id: {run_id}"}))
        return 1
    print(_pretty_json(dict(getattr(snapshot, "values", {}))))
    return 0


# ============================================================================
# Parser
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LyreTeXt orchestration CLI.",
    )

    # Global flags
    parser.add_argument("--config", default="config.yml", help="Runtime config file")
    parser.add_argument(
        "--source",
        default="examples\\example_rmd_project\\source",
        help="Project source directory"
    )
    parser.add_argument(
        "--temp",
        default="examples\\example_rmd_project\\temp",
        help="Temporary working directory"
    )
    parser.add_argument(
        "--output",
        default="examples\\example_rmd_project\\output",
        help="PreTeXt output directory"
    )
    parser.add_argument(
        "--checkpointer",
        choices=["memory", "sqlite"],
        default="sqlite",
        help="Checkpoint backend (default: sqlite)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: lyretext_runs.db)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Start a new run")

    # resume
    p_resume = sub.add_parser("resume", help="Resume a paused run at a gate")
    p_resume.add_argument("run_id", help="Run ID to resume")
    p_resume.add_argument("--action", required=True, help="Gate action (e.g. approve_continue, abort_run)")
    p_resume.add_argument("--payload", default=None, help="Extra JSON payload for the action")
    p_resume.add_argument(
        "--chapter-decisions",
        default=None,
        dest="chapter_decisions",
        help='Per-chapter decisions JSON, e.g. \'{"ch1":"approve","ch2":"retry"}\'',
    )

    # status
    p_status = sub.add_parser("status", help="Show current status of a run")
    p_status.add_argument("run_id", help="Run ID")

    # manifest
    p_manifest = sub.add_parser("manifest", help="Show the manifest for a run")
    p_manifest.add_argument("run_id", help="Run ID")
    p_manifest.add_argument("--raw", action="store_true", help="Output raw JSON")

    # stream
    p_stream = sub.add_parser(
        "stream",
        help="Print a one-off state snapshot for a run (NOT real event streaming — placeholder)",
    )
    p_stream.add_argument("run_id", help="Run ID")

    # list
    sub.add_parser("list", help="List all run IDs (requires sqlite backend)")

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect full state snapshot of a run")
    p_inspect.add_argument("run_id", help="Run ID")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "start": cmd_start,
        "resume": cmd_resume,
        "status": cmd_status,
        "manifest": cmd_manifest,
        "stream": cmd_stream,
        "list": cmd_list,
        "inspect": cmd_inspect,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
