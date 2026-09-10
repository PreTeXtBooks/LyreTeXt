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

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

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
    get_run_source_context,
    invoke_chapter_graph,
    invoke_workflow_graph,
    resume_chapter_graph,
    resume_workflow_graph,
)
from .render import build_main_ptx, render_pretext
from .review.grouping import group_key
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

def get_run_view(
    run_id: str,
    checkpointer: BaseCheckpointSaver,
    *,
    executing_chapters: Iterable[str] | None = None,
    run_executing: bool = False,
) -> dict[str, Any]:
    """Return the read-model view dict for *run_id*.

    *executing_chapters*/*run_executing* are forwarded to build_view_model so a
    chapter that is mid-execution isn't mistaken for one that stopped part-way
    — see the note there.
    """
    return build_view_model(
        run_id,
        checkpointer,
        executing_chapters=executing_chapters,
        run_executing=run_executing,
    )


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


_REOPEN_TARGET_NODE = {
    "retry": "translate_chapter",
    "approve_after_escalation": "translate_chapter",
    "revalidate": "review_chapter",
    # translate_chapter converts straight from source_path via pandoc, so
    # re-running it is exactly what "recompile from source" means now.
    "recompile_source": "translate_chapter",
}

# With an instruction attached, these actions go to the editing agent instead —
# conversion is deterministic and cannot act on natural language.
_INSTRUCTION_ACTIONS = ("retry", "approve_after_escalation")


def reopen_chapter_for_action(
    run_id: str,
    chapter_id: str,
    action: str,
    checkpointer: BaseCheckpointSaver,
    *,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Reopen an already-resolved chapter thread (approved/skipped, at END)
    for a follow-up action.

    Once chapter_review_gate has resolved and the thread reaches END, there
    is no pending interrupt left to Command(resume=...) into — LangGraph has
    nothing to resume, so a plain resume_chapter_graph call silently no-ops
    (this is why "Revalidate"/"Retry translate" on an approved chapter used
    to do nothing). This forges a fresh transition as if chapter_review_gate
    had just produced the given decision — the same values it sets itself in
    chapter_review_gate() — via update_state(as_node="chapter_review_gate"),
    which redirects the graph's next node, then actually runs it forward.
    """
    target_node = _REOPEN_TARGET_NODE.get(action)
    if target_node is None:
        return {"error": f"{action!r} cannot be used to reopen an already-resolved chapter"}

    # Mirrors chapter_review_gate's own dispatch: a natural-language
    # instruction is only actionable by the editing agent.
    if instruction and action in _INSTRUCTION_ACTIONS:
        target_node = "edit_chapter"

    chapter_graph = _active_chapter_graph(checkpointer)
    base_config = build_chapter_checkpoint_config(run_id, chapter_id)
    snapshot = chapter_graph.get_state(base_config)
    if snapshot is None or not snapshot.values:
        return {"error": f"chapter {chapter_id!r} of run {run_id!r} has no checkpoint to reopen"}

    iteration_count = snapshot.values.get("iteration_count", 1)
    values: dict[str, Any] = {
        "chapter_next": target_node,
        "retry_requested": target_node in ("translate_chapter", "edit_chapter"),
        "iteration_count": iteration_count if target_node == "review_chapter" else iteration_count + 1,
    }
    if instruction:
        values["instruction"] = instruction
        # A fresh human instruction earns a fresh edit budget.
        values["edit_iterations"] = 0

    chapter_graph.update_state(base_config, values, as_node="chapter_review_gate")
    result = _run_with_checkpoint_metadata(chapter_graph, None, checkpoint_config=base_config)
    result.setdefault("run_id", run_id)
    result.setdefault("chapter_id", chapter_id)
    return result


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

        source_context = get_run_source_context(run_id, checkpointer=checkpointer)
        result = invoke_chapter_graph(
            run_id=run_id,
            chapter_id=chapter_id,
            state={
                "source_path": chapter["source_path"],
                "output_path": chapter["output_path"],
                "chapter_id": chapter_id,
                **source_context,
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

    # A thread with nothing pending (next == () and no interrupts) has
    # already reached END — Command(resume=...) has nothing to attach to and
    # silently no-ops. Reopen it explicitly for actions that make sense to
    # replay after approval; anything else (e.g. "approve" again) falls
    # through to the normal resume, which harmlessly no-ops as before.
    is_terminal = not snapshot.next and not getattr(snapshot, "interrupts", None)
    if is_terminal and action in _REOPEN_TARGET_NODE:
        return reopen_chapter_for_action(
            run_id, chapter_id, action, checkpointer, instruction=instruction
        )

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
    *,
    trigger: bool = True,
) -> dict[str, Any]:
    """Write directly-edited source or translated-output text for a chapter.

    ux2.md: editing the source directly triggers edit_source_file_acknowledged
    and requires recompilation; editing the translated output re-runs
    compilation/validation instead of a full retranslate.

    target="source": writes the chapter's source file. If the chapter is
        already dispatched — paused at its review gate, or already resolved
        (approved/skipped) — immediately triggers recompile_source (re-read
        the edited source, retranslate). If not yet dispatched, the edit
        simply takes effect whenever the chapter is later dispatched —
        nothing further to trigger now.
    target="output": writes the chapter's translated .ptx file directly, then
        triggers revalidate (re-run review against the edited artifact) —
        again, whether the chapter is currently paused at its review gate or
        already resolved (apply_chapter_command reopens resolved threads;
        see reopen_chapter_for_action).

    trigger=False writes the file and reports the action that *would* have been
    triggered as "pending_action", without running it. The write is near
    instant; the recompile/revalidate behind it is a full pipeline run taking
    tens of seconds, so the HTTP layer uses this to answer as soon as the edit
    is safely on disk and run the command in the background (see
    api.write_chapter_file). Callers outside the API keep the default and get
    the whole thing synchronously.
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
    dispatched = snapshot is not None

    if not dispatched:
        return {
            "run_id": run_id,
            "chapter_id": chapter_id,
            "target": target,
            "status": "written",
            "triggered": None,
            "pending_action": None,
        }

    action = "recompile_source" if target == "source" else "revalidate"
    if not trigger:
        return {
            "run_id": run_id,
            "chapter_id": chapter_id,
            "target": target,
            "status": "written",
            "triggered": None,
            "pending_action": action,
        }

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


def _findings_sidecar_path(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> Path | None:
    """Resolve the .lyretext/<chapter_id>.findings.json path for a chapter, or None."""
    chapter = _find_chapter_in_manifest(run_id, chapter_id, checkpointer)
    if chapter is None:
        return None
    output_path = chapter.get("output_path", "")
    if not output_path:
        return None
    return Path(output_path).parent / ".lyretext" / f"{chapter_id}.findings.json"


def _read_findings_sidecar(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> tuple[Path | None, dict[str, Any]]:
    """Return (sidecar_path, findings), or (None, {"error": ...}) if unavailable."""
    sidecar_path = _findings_sidecar_path(run_id, chapter_id, checkpointer)
    if sidecar_path is None:
        return None, {"error": f"chapter {chapter_id!r} not found in run {run_id!r}"}
    if not sidecar_path.exists():
        return None, {"error": f"no findings sidecar for chapter {chapter_id!r}"}
    return sidecar_path, json.loads(sidecar_path.read_text(encoding="utf-8"))


def _select_issues(
    issues: list[dict[str, Any]], issue_indices: Iterable[int], chapter_id: str
) -> tuple[list[int], dict[str, Any] | None]:
    """Validate and de-duplicate issue indices, preserving sidecar order.

    Ordering matters for the grouped operations below: the instruction handed to
    the editing agent lists line numbers, and those read far more naturally in
    document order than in whatever order the UI happened to collect them.
    """
    wanted = {int(i) for i in issue_indices}
    if not wanted:
        return [], {"error": "at least one issue index is required"}
    out_of_range = sorted(i for i in wanted if i < 0 or i >= len(issues))
    if out_of_range:
        return [], {
            "error": (
                f"issue index {out_of_range[0]} out of range for chapter {chapter_id!r}"
            )
        }
    return [i for i in range(len(issues)) if i in wanted], None


def _issue_group_key(issue: dict[str, Any]) -> str:
    """The stored group_key, recomputed for sidecars written before it existed."""
    return issue.get("group_key") or group_key(
        check_id=issue.get("check_id"),
        message=issue.get("message"),
        suggestion=issue.get("suggestion"),
    )


def _fix_instruction(selected: list[dict[str, Any]]) -> str:
    """Build the editing-agent instruction for an arbitrary set of findings.

    The set may be one finding, one group of occurrences of a single systematic
    problem, or a queue the user assembled across several unrelated ones — all
    three arrive here, because the whole point is that they cost one edit_chapter
    run between them rather than one each.

    Findings are re-grouped by group_key so a repeated problem is stated once
    with its lines enumerated, rather than restated per occurrence: that is both
    clearer to the model and markedly cheaper in tokens on the twenty-occurrence
    case this exists for.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for issue in selected:
        grouped.setdefault(_issue_group_key(issue), []).append(issue)

    def _at(issue: dict[str, Any]) -> str:
        return f"Line {issue['line']}: " if issue.get("line") is not None else ""

    def _detail(issue: dict[str, Any]) -> str:
        text = (issue.get("message") or "").rstrip()
        if issue.get("suggestion"):
            text = f"{text.rstrip('.')}. Fix: {issue['suggestion']}"
        return text

    def _describe(occurrences: list[dict[str, Any]]) -> str:
        # Findings group by the *shape* of their fix, so a group's occurrences
        # may still carry per-instance detail — one xml:id per definition, say.
        # Restating only the first occurrence's text would tell the agent to
        # apply that instance's fix everywhere, so enumerate when they differ
        # and collapse only when they genuinely are the same words. The
        # collapsed form is what keeps the twenty-occurrence case cheap.
        distinct = {(i.get("message"), i.get("suggestion")) for i in occurrences}
        if len(distinct) > 1:
            bullets = "\n".join(f"   - {_at(i)}{_detail(i)}" for i in occurrences)
            return (
                f"{len(occurrences)} related findings, each needing its own version "
                f"of the same fix:\n{bullets}"
            )

        first = occurrences[0]
        lines = [str(i["line"]) for i in occurrences if i.get("line") is not None]
        if len(occurrences) == 1:
            return f"{_at(first)}{_detail(first)}"
        where = f"Lines {', '.join(lines)}: " if lines else ""
        return (
            f"{where}{_detail(first)}\n"
            f"   This is one systematic problem occurring {len(occurrences)} times; "
            "apply the identical fix at every listed location."
        )

    if len(grouped) == 1:
        body = f"Fix the following in this chapter.\n{_describe(next(iter(grouped.values())))}"
    else:
        items = "\n".join(
            f"{n}. {_describe(occurrences)}"
            for n, occurrences in enumerate(grouped.values(), start=1)
        )
        body = f"Apply all {len(grouped)} of the following fixes to this chapter:\n{items}"

    return f"{body}\nChange nothing else."


def _set_ignored(
    run_id: str,
    chapter_id: str,
    issue_indices: Iterable[int],
    checkpointer: BaseCheckpointSaver,
    *,
    ignored: bool,
) -> dict[str, Any]:
    """Flip the ``ignored`` flag on one or more issues in the findings sidecar."""
    sidecar_path, findings = _read_findings_sidecar(run_id, chapter_id, checkpointer)
    if sidecar_path is None:
        return findings

    issues = findings.get("issues", [])
    indices, error = _select_issues(issues, issue_indices, chapter_id)
    if error is not None:
        return error

    for index in indices:
        issues[index]["ignored"] = ignored
    sidecar_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return findings


def dismiss_issues(
    run_id: str,
    chapter_id: str,
    issue_indices: Iterable[int],
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Mark one or more issues as ignored in the findings sidecar."""
    return _set_ignored(run_id, chapter_id, issue_indices, checkpointer, ignored=True)


def restore_issues(
    run_id: str,
    chapter_id: str,
    issue_indices: Iterable[int],
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Un-ignore one or more issues, bringing them back into the counts.

    The counterpart to dismiss_issues, and load-bearing rather than a nicety:
    finalize_review now carries dismissals across review passes (keyed on
    group_key), so without this a mis-click would suppress a finding for the
    rest of the chapter's life.
    """
    return _set_ignored(run_id, chapter_id, issue_indices, checkpointer, ignored=False)


def fix_issues(
    run_id: str,
    chapter_id: str,
    issue_indices: Iterable[int],
    checkpointer: BaseCheckpointSaver,
) -> dict[str, Any]:
    """Trigger ONE edit_chapter run addressing an arbitrary set of findings.

    The set may be a single finding, a group of occurrences of one systematic
    problem, or a queue the user assembled across several unrelated findings.
    All three fold into one edit → review → gate cycle: a cycle costs an editing
    call plus every registered check, so paying that per finding is both slow to
    sit through and wasteful of tokens.
    """
    sidecar_path, findings = _read_findings_sidecar(run_id, chapter_id, checkpointer)
    if sidecar_path is None:
        return findings

    issues = findings.get("issues", [])
    indices, error = _select_issues(issues, issue_indices, chapter_id)
    if error is not None:
        return error

    instruction = _fix_instruction([issues[i] for i in indices])
    apply_chapter_command(run_id, chapter_id, "retry", checkpointer, instruction=instruction)
    return {"status": "triggered", "instruction": instruction, "issue_indices": indices}


def dismiss_issue(
    run_id: str, chapter_id: str, issue_index: int, checkpointer: BaseCheckpointSaver
) -> dict[str, Any]:
    """Mark a single issue as ignored in the findings sidecar."""
    return dismiss_issues(run_id, chapter_id, [issue_index], checkpointer)


def fix_single_issue(
    run_id: str, chapter_id: str, issue_index: int, checkpointer: BaseCheckpointSaver
) -> dict[str, Any]:
    """Trigger edit_chapter with a targeted instruction for one issue."""
    return fix_issues(run_id, chapter_id, [issue_index], checkpointer)


ASSET_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".pdf"}
)


def _asset_resolver(
    source_path: str, output_path: str, project_source: str
) -> Callable[[str], str | None]:
    """Build a resolver mapping an <image source="..."> to an absolute path.

    A chapter's image references come through pandoc verbatim from the
    Markdown, so they are relative to wherever that Markdown lived — for a
    knitr-generated chapter that's the compiled Markdown's directory
    (``<chapter>_files/figure-html/…``), but a hand-authored path may be
    relative to the original project instead. Try each, nearest first.
    """
    roots = [Path(p).parent for p in (output_path, source_path) if p]
    if project_source:
        roots.append(Path(project_source))

    def resolve(source: str) -> str | None:
        if not source:
            return None
        candidate = Path(source)
        if candidate.is_absolute():
            return str(candidate) if candidate.is_file() else None
        for root in roots:
            found = root / candidate
            if found.is_file():
                return str(found.resolve())
        return None

    return resolve


def render_chapter_output(
    run_id: str, chapter_id: str, checkpointer: BaseCheckpointSaver
) -> dict[str, Any]:
    """Render a chapter's .ptx to preview HTML for the workspace render pane.

    Reads the artifact straight off disk rather than from chapter state, so a
    hand-edit made through PUT .../file shows up immediately without the
    chapter having to be re-run.
    """
    graph = _active_workflow_graph(checkpointer)
    config = build_checkpoint_config(run_id=run_id)
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return {"error": f"no checkpoint found for run_id={run_id!r}"}

    chapter = _find_chapter_in_manifest(run_id, chapter_id, checkpointer)
    if chapter is None:
        return {"error": f"chapter {chapter_id!r} not found in run {run_id!r}"}

    output_path = chapter.get("output_path", "")
    if not output_path or not Path(output_path).exists():
        return {
            "run_id": run_id,
            "chapter_id": chapter_id,
            "path": output_path,
            "html": "",
            "warnings": [],
            "unsupported": [],
            "error": None,
            "rendered": False,
        }

    xml_text = Path(output_path).read_text(encoding="utf-8", errors="replace")
    result = render_pretext(
        xml_text,
        asset_resolver=_asset_resolver(
            chapter.get("source_path", ""),
            output_path,
            snapshot.values.get("project_source", ""),
        ),
    )
    return {
        "run_id": run_id,
        "chapter_id": chapter_id,
        "path": output_path,
        "html": result.html,
        "warnings": result.warnings,
        "unsupported": result.unsupported,
        "error": result.error,
        "rendered": True,
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


# ---------------------------------------------------------------------------
# Project manifest (main.ptx)
# ---------------------------------------------------------------------------

MAIN_PTX_FILENAME = "main.ptx"


def _run_values(
    run_id: str, checkpointer: BaseCheckpointSaver
) -> dict[str, Any] | None:
    """Return the run thread's checkpointed values, or None if absent."""
    graph = _active_workflow_graph(checkpointer)
    snapshot = graph.get_state(build_checkpoint_config(run_id=run_id))
    if snapshot is None or not snapshot.values:
        return None
    return snapshot.values


def assemble_main_ptx(
    run_id: str,
    checkpointer: BaseCheckpointSaver,
    *,
    root_element: str = "auto",
    title: str | None = None,
) -> str | None:
    """Build the unifying ``main.ptx`` XML for a run from its approved manifest.

    Deterministic string building (see render.assemble) — no files are read or
    written here. Returns None when the run has no manifest yet. *title*
    defaults to the project source's name; *root_element* is ``"auto"`` /
    ``"book"`` / ``"article"`` (decision #34, Option U).
    """
    values = _run_values(run_id, checkpointer)
    if values is None:
        return None
    manifest = values.get("manifest") or []
    if not manifest:
        return None
    if title is None:
        source = values.get("project_source", "")
        title = Path(source).name if source else "Untitled"
    return build_main_ptx(manifest, title=title, root_element=root_element)


def write_main_ptx(
    run_id: str,
    checkpointer: BaseCheckpointSaver,
    *,
    root_element: str = "auto",
    title: str | None = None,
) -> Path | None:
    """Write ``main.ptx`` into the run's output directory, returning its path.

    Regenerated from the current manifest on every call so it always reflects
    the chapter set on disk; assembly is deterministic, so rewriting it is
    idempotent. Returns None when there is no manifest or no output directory.
    """
    values = _run_values(run_id, checkpointer)
    if values is None:
        return None
    output_dir = values.get("output_dir", "")
    if not output_dir:
        return None
    xml = assemble_main_ptx(
        run_id, checkpointer, root_element=root_element, title=title
    )
    if xml is None:
        return None
    path = Path(output_dir) / MAIN_PTX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return path


def build_project_archive(
    run_id: str,
    checkpointer: BaseCheckpointSaver,
    *,
    root_element: str = "auto",
    title: str | None = None,
) -> tuple[str, bytes] | None:
    """Bundle a run's output into a complete, compilable PreTeXt project zip.

    Writes a fresh ``main.ptx`` into the output directory (so the on-disk
    project is complete too, for anyone building it directly), then packs it
    together with every chapter/matter ``.ptx`` from the manifest into a zip.

    Returns ``(filename, zip_bytes)`` or None when the run has no output.
    """
    main_path = write_main_ptx(
        run_id, checkpointer, root_element=root_element, title=title
    )
    if main_path is None:
        return None

    values = _run_values(run_id, checkpointer) or {}
    manifest = values.get("manifest") or []

    # main.ptx first, then fragments in manifest order; de-dupe by filename so
    # two manifest entries that somehow share a name can't double-add.
    paths = [main_path] + [Path(ch["output_path"]) for ch in manifest]
    buffer = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.name in seen or not path.exists():
                continue
            archive.write(path, arcname=path.name)
            seen.add(path.name)
    buffer.seek(0)

    source = values.get("project_source", "")
    project = (Path(source).stem if source else "") or "project"
    return f"{project}.zip", buffer.getvalue()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def get_output(run_id: str, checkpointer: BaseCheckpointSaver) -> list[dict[str, Any]]:
    """Return the list of generated .ptx files for *run_id*.

    Refreshes the unifying ``main.ptx`` first so the output directory is a
    complete, buildable project and the manifest shows up in the listing
    alongside the chapters. The refresh is best-effort: a listing must never
    fail because assembly did.
    """
    try:
        write_main_ptx(run_id, checkpointer)
    except Exception:  # pragma: no cover - defensive; listing must still work
        pass
    view = get_run_view(run_id, checkpointer)
    return view.get("output", [])


def get_jobs(run_id: str, checkpointer: BaseCheckpointSaver) -> list[dict[str, Any]]:
    """Return the jobs list for *run_id* (derived from checkpoint history)."""
    view = get_run_view(run_id, checkpointer)
    return view.get("jobs", [])
