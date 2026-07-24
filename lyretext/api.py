"""FastAPI HTTP layer wrapping lyretext.service.

All mutating operations (start, decide, gate) run the LangGraph in a thread-pool
so the event loop is never blocked.  Clients should poll GET /api/runs/{id} for
live state — the graph runs until the next interrupt, then stops and checkpoints.

Usage:
    python -m lyretext               # serves on http://127.0.0.1:8000
    python -m lyretext --port 8080
"""
from __future__ import annotations

import asyncio
import functools
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .orchestration.checkpointing import build_checkpointer, ensure_run_id
from .service import (
    apply_chapter_command,
    apply_chapter_decision,
    apply_read_gate_decision,
    dispatch_chapters,
    get_chapter_checkpoints,
    get_chapter_findings,
    get_jobs,
    get_output,
    get_run_view,
    remap_chapter_source,
    revert_chapter_to_checkpoint,
    start_run,
    write_chapter_file_and_trigger,
)

# ---------------------------------------------------------------------------
# Shared resources
# ---------------------------------------------------------------------------

_UI_DIR = Path(__file__).parent.parent / "development" / "ui-prototype"
_checkpointer = None
_pool = ThreadPoolExecutor(max_workers=4)
# Tracks run-level operations in flight (start_run, the read/manifest gate —
# there is exactly one of these per run, sharing the run's own checkpoint
# thread, so these must stay serialized per run_id).
_running: set[str] = set()
# Tracks per-CHAPTER operations in flight. Each chapter now runs on its own
# checkpoint thread (see orchestration/graph.py), so two different chapters
# of the same run are independent and must NOT share a lock — only guard
# against two concurrent operations on the *same* chapter's own thread.
_chapter_running: set[tuple[str, str]] = set()


def _run_executing(run_id: str) -> bool:
    """True if the run-level thread OR any of its chapters is mid-execution."""
    if run_id in _running:
        return True
    return any(rid == run_id for (rid, _cid) in _chapter_running)


def _cp():
    global _checkpointer
    if _checkpointer is None:
        try:
            _checkpointer = build_checkpointer(backend="sqlite")
        except ImportError:
            _checkpointer = build_checkpointer(backend="memory")
    return _checkpointer


async def _in_thread(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pool, functools.partial(fn, *args, **kwargs))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LyreTeXt", version="0.1.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    if _UI_DIR.exists():
        # Redirect (not FileResponse) so the browser's page URL is actually
        # /ui/ — index.html's asset tags are relative ("styles.css",
        # "app.js", ...), and those only resolve correctly against the
        # /ui/ prefix the StaticFiles mount below serves them from. Serving
        # index.html's bytes directly at "/" left the page URL as "/", so
        # every relative asset 404'd and the page rendered blank.
        return RedirectResponse(url="/ui/")
    return {"message": "LyreTeXt API — see /api/docs"}


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

@app.get("/api/browse")
def browse_folder(initial: str = Query(default="", description="Initial directory to open")):
    """Open a native OS folder-picker dialog and return the chosen path.

    Runs synchronously in the calling thread — acceptable since it blocks only
    while the user interacts with the dialog.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root_tk = tk.Tk()
        root_tk.withdraw()
        root_tk.call("wm", "attributes", ".", "-topmost", True)
        start = initial if initial and Path(initial).exists() else str(Path.home())
        chosen = filedialog.askdirectory(initialdir=start, title="Select project folder")
        root_tk.destroy()
        if chosen:
            return {"path": chosen}
        return {"path": None, "cancelled": True}
    except Exception as exc:
        raise HTTPException(500, f"Folder picker unavailable: {exc}") from exc


@app.get("/api/demo-path")
def demo_path():
    """Return the absolute path to the bundled example project."""
    p = Path(__file__).parent.parent / "examples" / "example_rmd_project" / "source"
    return {"path": str(p.resolve()), "exists": p.exists()}


@app.get("/api/debug/{run_id}")
def debug_snapshot(run_id: str):
    """Return raw LangGraph snapshot data for debugging."""
    from .orchestration.graph import build_workflow_graph
    cp = _cp()
    graph = build_workflow_graph(cp)
    from .orchestration.checkpointing import build_checkpoint_config
    config = build_checkpoint_config(run_id=run_id)
    snapshot = graph.get_state(config, subgraphs=True)
    if snapshot is None:
        return {"error": "no checkpoint found"}

    def _collect_interrupts(snap):
        result = []
        for i in (getattr(snap, "interrupts", None) or []):
            result.append({"value": getattr(i, "value", str(i)), "id": str(getattr(i, "id", ""))})
        for task in (getattr(snap, "tasks", None) or []):
            sub = getattr(task, "state", None)
            if sub is not None:
                result.extend(_collect_interrupts(sub))
        return result

    all_interrupts = _collect_interrupts(snapshot)

    # Debug: inspect task attributes
    task_debug = []
    for task in (getattr(snapshot, "tasks", None) or []):
        td = {
            "name": getattr(task, "name", "?"),
            "has_state": hasattr(task, "state"),
            "state_type": type(getattr(task, "state", None)).__name__,
        }
        sub = getattr(task, "state", None)
        if sub is not None:
            td["sub_interrupts"] = len(getattr(sub, "interrupts", []) or [])
            td["sub_next"] = list(getattr(sub, "next", []) or [])
            td["sub_values_keys"] = list((getattr(sub, "values", None) or {}).keys())
        task_debug.append(td)
    return {
        "interrupts_count": len(all_interrupts),
        "interrupts": all_interrupts[:10],
        "tasks": task_debug,
        "next": list(getattr(snapshot, "next", []) or []),
        "values_keys": list((snapshot.values or {}).keys()),
    }


@app.get("/api/files")
def read_file_content(path: str = Query(..., description="Absolute path to a project file")):
    """Return the text content of a project file (source .md or output .ptx)."""
    try:
        p = Path(path).resolve()
        if not p.is_file():
            raise HTTPException(404, f"File not found: {path}")
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "name": p.name, "content": content}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc




@app.get("/api/runs")
async def list_runs():
    """List all known run IDs from the checkpointer."""
    cp = _cp()
    try:
        # SqliteSaver exposes list_threads; MemorySaver may not
        threads = [t for t in cp.list_threads()]  # type: ignore[attr-defined]
        return {"runs": [{"run_id": str(t)} for t in threads]}
    except Exception:
        return {"runs": []}


@app.post("/api/runs", status_code=202)
async def create_run(body: dict = Body(...)):
    """Start a new translation run.

    The LangGraph executes in a background thread until the first interrupt
    (read gate).  Returns immediately with the new run_id; poll GET /api/runs/{id}
    for progress.
    """
    source: str = body.get("source", "")
    output_dir: str = body.get("output_dir", "output")
    temp_dir: str = body.get("temp_dir", "temp")
    run_config: dict = body.get("run_config", {})
    run_id: str = ensure_run_id(body.get("run_id"))

    if not source:
        raise HTTPException(400, "source path is required")
    if run_id in _running:
        raise HTTPException(409, f"Run {run_id!r} already in progress")

    async def _run_bg():
        _running.add(run_id)
        try:
            await _in_thread(
                start_run,
                source,
                output_dir,
                temp_dir=temp_dir,
                run_config=run_config,
                checkpointer=_cp(),
                run_id=run_id,
            )
        finally:
            _running.discard(run_id)

    asyncio.create_task(_run_bg())
    return {"run_id": run_id, "status": "starting"}


@app.post("/api/runs/upload", status_code=202)
async def create_run_from_upload(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    output_dir: str = Form("output"),
    temp_dir: str = Form("temp"),
    run_id: str | None = Form(None),
):
    """Start a new run from an uploaded project (ux2.md phase-1 ingestion: file
    upload from the client device, alongside the folder-path flow above).

    Each entry in `files` is paired by index with a relative path in `paths`
    (e.g. from a directory-picker's webkitRelativePath), so a whole project
    folder's structure is preserved in a staging directory before the normal
    start_run path picks it up.
    """
    if len(files) != len(paths):
        raise HTTPException(400, "files and paths must be the same length")
    if not files:
        raise HTTPException(400, "at least one file is required")

    resolved_run_id = ensure_run_id(run_id)
    if resolved_run_id in _running:
        raise HTTPException(409, f"Run {resolved_run_id!r} already in progress")

    stage_dir = Path(tempfile.mkdtemp(prefix=f"lyretext_upload_{resolved_run_id}_"))
    for upload, rel_path in zip(files, paths):
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise HTTPException(400, f"invalid relative path: {rel_path!r}")
        dest = stage_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await upload.read())

    async def _run_bg():
        _running.add(resolved_run_id)
        try:
            await _in_thread(
                start_run,
                str(stage_dir),
                output_dir,
                temp_dir=temp_dir,
                checkpointer=_cp(),
                run_id=resolved_run_id,
            )
        finally:
            _running.discard(resolved_run_id)

    asyncio.create_task(_run_bg())
    return {"run_id": resolved_run_id, "status": "starting", "staged_path": str(stage_dir)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Return the full view-model for a run (chapters, stages, findings, output)."""
    view = await _in_thread(get_run_view, run_id, _cp())
    if "error" in view:
        raise HTTPException(404, view["error"])
    view["_executing"] = _run_executing(run_id)
    # Per-chapter executing flags let the UI show several chapters "running"
    # at once, since they're now independent (see _chapter_running above).
    executing_chapters = {cid for (rid, cid) in _chapter_running if rid == run_id}
    for ch in view.get("chapters", []):
        ch["_executing"] = ch.get("id") in executing_chapters
    return view


@app.get("/api/runs/{run_id}/status")
async def run_status(run_id: str):
    """Lightweight status check — chapters + gate only, no output listing."""
    view = await _in_thread(get_run_view, run_id, _cp())
    if "error" in view:
        raise HTTPException(404, view["error"])
    executing_chapters = {cid for (rid, cid) in _chapter_running if rid == run_id}
    return {
        "run_id": run_id,
        "executing": _run_executing(run_id),
        "chapters": [
            {
                "id": c["id"],
                "stages": c["stages"],
                "gate": c["gate"],
                "executing": c["id"] in executing_chapters,
            }
            for c in view.get("chapters", [])
        ],
        "gate": view.get("gate"),
    }


# ---------------------------------------------------------------------------
# Chapter decisions
# ---------------------------------------------------------------------------

@app.post("/api/runs/{run_id}/chapters/decide", status_code=202)
async def chapter_decide(run_id: str, body: dict = Body(...)):
    """Apply per-chapter gate decisions and resume the graph.

    Body: {decisions: {chapter_id: "approve"|"retry"|"skip"|"revalidate"|"recompile_source"},
           action: str (default for chapters not listed),
           instruction: str (NL refinement, forwarded on retry)}
    """
    if run_id in _running:
        raise HTTPException(409, f"Run {run_id!r} already has an operation in progress")

    decisions: dict[str, str] = body.get("decisions", {})
    action: str = body.get("action", "approve")
    instruction: str | None = body.get("instruction")

    async def _resume():
        _running.add(run_id)
        try:
            await _in_thread(
                apply_chapter_decision,
                run_id,
                decisions,
                _cp(),
                default_action=action,
                instruction=instruction,
            )
        finally:
            _running.discard(run_id)

    asyncio.create_task(_resume())
    return {"run_id": run_id, "status": "processing"}


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/commands", status_code=202)
async def chapter_command(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Run a targeted chapter command for the requested chapter only.

    Guarded by a per-(run_id, chapter_id) lock, NOT the run-wide lock — each
    chapter has its own checkpoint thread (see orchestration/graph.py), so
    this is safe to call concurrently for different chapter_ids on the same
    run. Only a second call for the SAME chapter while one is still in
    flight is rejected.
    """
    key = (run_id, chapter_id)
    if key in _chapter_running:
        raise HTTPException(
            409, f"Chapter {chapter_id!r} of run {run_id!r} already has an operation in progress"
        )

    action = body.get("action", "translate")
    instruction = body.get("instruction")

    async def _resume():
        _chapter_running.add(key)
        try:
            await _in_thread(
                apply_chapter_command,
                run_id,
                chapter_id,
                action,
                _cp(),
                instruction=instruction,
            )
        finally:
            _chapter_running.discard(key)

    asyncio.create_task(_resume())
    return {"run_id": run_id, "chapter_id": chapter_id, "status": "processing", "action": action}


@app.post("/api/runs/{run_id}/chapters/commands", status_code=202)
async def chapters_command_batch(run_id: str, body: dict = Body(...)):
    """Run the same action across several chapters at once ("run selected"/"run all").

    Body: {chapter_ids: [str, ...], action: str (default "translate"), instruction: str|None}

    Each chapter runs on its own checkpoint thread, so this submits one
    independent background task per chapter — they execute concurrently,
    not queued behind one another. Chapters already mid-operation are
    reported as skipped rather than failing the whole batch.
    """
    chapter_ids: list[str] = body.get("chapter_ids", [])
    if not chapter_ids:
        raise HTTPException(400, "chapter_ids must be a non-empty list")
    action: str = body.get("action", "translate")
    instruction: str | None = body.get("instruction")

    accepted: list[str] = []
    skipped: list[str] = []

    async def _resume_one(chapter_id: str):
        key = (run_id, chapter_id)
        try:
            await _in_thread(
                apply_chapter_command,
                run_id,
                chapter_id,
                action,
                _cp(),
                instruction=instruction,
            )
        finally:
            _chapter_running.discard(key)

    for chapter_id in chapter_ids:
        key = (run_id, chapter_id)
        if key in _chapter_running:
            skipped.append(chapter_id)
            continue
        accepted.append(chapter_id)
        # Mark in-flight synchronously (before scheduling the task) so a
        # duplicate chapter_id later in this same batch is also skipped.
        _chapter_running.add(key)
        asyncio.create_task(_resume_one(chapter_id))

    return {
        "run_id": run_id,
        "status": "processing",
        "action": action,
        "accepted": accepted,
        "skipped": skipped,
    }


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/remap")
async def remap_chapter(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Point a chapter's source file at a different path.

    Body: {source_path: str}
    Only valid before the chapter has been dispatched (see remap_chapter_source).
    """
    new_path = body.get("source_path")
    if not new_path:
        raise HTTPException(400, "source_path is required")
    result = await _in_thread(remap_chapter_source, run_id, chapter_id, new_path, _cp())
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.put("/api/runs/{run_id}/chapters/{chapter_id}/file")
async def write_chapter_file(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Write directly-edited source or translated-output text for a chapter.

    Body: {target: "source"|"output", content: str}

    Editing the source triggers edit_source_file_acknowledged + recompile
    (recompile_source) if the chapter is currently paused at its review gate;
    editing the translated output triggers revalidate instead of a full
    retranslate — see ux2.md's review/edit workspace requirements.
    """
    key = (run_id, chapter_id)
    if key in _chapter_running:
        raise HTTPException(
            409, f"Chapter {chapter_id!r} of run {run_id!r} already has an operation in progress"
        )

    target = body.get("target")
    content = body.get("content")
    if target not in ("source", "output"):
        raise HTTPException(400, "target must be 'source' or 'output'")
    if content is None:
        raise HTTPException(400, "content is required")

    _chapter_running.add(key)
    try:
        result = await _in_thread(
            write_chapter_file_and_trigger, run_id, chapter_id, target, content, _cp()
        )
    finally:
        _chapter_running.discard(key)

    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/runs/{run_id}/chapters/{chapter_id}/findings")
async def chapter_findings(run_id: str, chapter_id: str):
    """Return the full per-issue findings (severity, message, suggestion) for a chapter.

    GET /api/runs/{id} only carries findings *counts* — this returns the
    complete sidecar so the workspace review-gate panel can list each finding.
    """
    result = await _in_thread(get_chapter_findings, run_id, chapter_id, _cp())
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/api/runs/{run_id}/chapters/{chapter_id}/checkpoints")
async def chapter_checkpoints(run_id: str, chapter_id: str):
    """List a chapter's own checkpoint history, most recent first."""
    checkpoints = await _in_thread(get_chapter_checkpoints, run_id, chapter_id, _cp())
    return {"checkpoints": checkpoints}


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/revert")
async def revert_chapter(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Revert a chapter's thread to an earlier checkpoint.

    Body: {checkpoint_id: str}
    """
    key = (run_id, chapter_id)
    if key in _chapter_running:
        raise HTTPException(
            409, f"Chapter {chapter_id!r} of run {run_id!r} already has an operation in progress"
        )
    checkpoint_id = body.get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(400, "checkpoint_id is required")
    result = await _in_thread(
        revert_chapter_to_checkpoint, run_id, chapter_id, checkpoint_id, _cp()
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ---------------------------------------------------------------------------
# Read gate
# ---------------------------------------------------------------------------

@app.post("/api/runs/{run_id}/gate", status_code=202)
async def gate_decide(run_id: str, body: dict = Body(...)):
    """Apply a read-gate decision.

    Body: {action: "approve_continue"|"select_chapters"|"skip_chapters"|...,
           **kwargs forwarded to the gate handler}
    """
    if run_id in _running:
        raise HTTPException(409, f"Run {run_id!r} already has an operation in progress")

    action: str = body.get("action", "approve_continue")
    extra = {k: v for k, v in body.items() if k != "action"}

    async def _resume():
        _running.add(run_id)
        try:
            await _in_thread(
                apply_read_gate_decision,
                run_id,
                action,
                _cp(),
                **extra,
            )
        finally:
            _running.discard(run_id)

    asyncio.create_task(_resume())
    return {"run_id": run_id, "status": "processing"}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/output")
async def run_output(run_id: str):
    files = await _in_thread(get_output, run_id, _cp())
    return {"output": files}


@app.get("/api/runs/{run_id}/output/{filename}")
async def download_output(run_id: str, filename: str):
    files = await _in_thread(get_output, run_id, _cp())
    for f in files:
        if f["file"] == filename:
            path = Path(f["path"])
            if path.exists():
                return FileResponse(
                    str(path),
                    filename=filename,
                    media_type="application/xml",
                )
    raise HTTPException(404, f"{filename!r} not found in run {run_id!r}")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/jobs")
async def run_jobs(run_id: str):
    jobs = await _in_thread(get_jobs, run_id, _cp())
    return {"jobs": jobs}


# ---------------------------------------------------------------------------
# Settings (config.yml)
# ---------------------------------------------------------------------------

_CONFIG_FILE = Path(__file__).parent.parent / "config.yml"


def _read_config_yml() -> dict:
    if not _CONFIG_FILE.exists():
        return {"global_options": {}, "node_overrides": {}}
    try:
        import yaml
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read config.yml: {exc}") from exc


def _write_config_yml(data: dict) -> None:
    try:
        import yaml
        with open(_CONFIG_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        raise HTTPException(500, f"Failed to write config.yml: {exc}") from exc


@app.get("/api/settings")
def get_settings():
    """Return the current config.yml contents as JSON."""
    raw = _read_config_yml()
    return {
        "global_options": raw.get("global_options", {}),
        "node_overrides": raw.get("node_overrides", {}),
    }


@app.patch("/api/settings")
def update_settings(body: dict = Body(...)):
    """Merge the supplied fields into config.yml and write it back.

    Accepts any subset of the config structure:
      {global_options: {provider: "anthropic", ...},
       node_overrides: {translate_chapter: {provider: "gemini"}, ...}}
    Node overrides are merged at the node level (not deep-merged within a node).
    """
    raw = _read_config_yml()

    if "global_options" in body and isinstance(body["global_options"], dict):
        existing = raw.get("global_options") or {}
        existing.update(body["global_options"])
        raw["global_options"] = existing

    if "node_overrides" in body and isinstance(body["node_overrides"], dict):
        existing_overrides = raw.get("node_overrides") or {}
        for node_id, overrides in body["node_overrides"].items():
            if overrides is None:
                existing_overrides.pop(node_id, None)
            elif isinstance(overrides, dict):
                node = existing_overrides.get(node_id) or {}
                node.update(overrides)
                existing_overrides[node_id] = node
        raw["node_overrides"] = existing_overrides

    _write_config_yml(raw)
    return {
        "global_options": raw.get("global_options", {}),
        "node_overrides": raw.get("node_overrides", {}),
    }
