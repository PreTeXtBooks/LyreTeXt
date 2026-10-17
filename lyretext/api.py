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
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import config as _config
from .config import PROVIDER_KEY_ENV, data_paths
from .environment import environment_report, rmd_preflight, run_needs_r, summarize
from .orchestration.checkpointing import build_checkpointer, ensure_run_id
from .orchestration.graph import _mock_enabled
from .pipeline.rmd import RToolchainError
from .tls import use_os_trust_store
from .service import (
    apply_chapter_command,
    apply_chapter_decision,
    apply_read_gate_decision,
    build_project_archive,
    dismiss_issue,
    dismiss_issues,
    dispatch_chapters,
    fix_issues,
    fix_single_issue,
    get_chapter_checkpoints,
    get_chapter_findings,
    get_jobs,
    get_output,
    get_run_view,
    list_run_summaries,
    remap_chapter_source,
    render_chapter_output,
    ASSET_SUFFIXES,
    restore_issues,
    revert_chapter_to_checkpoint,
    start_run,
    write_chapter_file_and_trigger,
)
from .security import (
    AUTH_HEADER,
    DevOnlyCORS,
    LocalAuthMiddleware,
    auth_token,
    install_log_redaction,
    redact,
    token_matches,
)

# ---------------------------------------------------------------------------
# Shared resources
# ---------------------------------------------------------------------------

logger = logging.getLogger("lyretext.api")

# Provider calls verify TLS against the OS certificate store (plus certifi), so
# they work behind proxies with a locally trusted root (#90). Here because every
# server mode imports this module — including uvicorn's --reload worker, which
# never runs lyretext.__main__.
use_os_trust_store()

_UI_DIR = Path(__file__).parent.parent / "frontend"
_checkpointer = None

# Two pools, deliberately. Graph execution (a chapter can take tens of seconds
# — pandoc, then LLM review) and read-only view queries must never share
# workers: "Run all" saturated the single 4-worker pool with chapter jobs, so
# every GET /api/runs/{id} — the UI's 2s poll — queued behind them and the
# chapter cards froze for the whole batch. Reads are short sqlite queries, so
# they get their own generously-sized pool and always answer promptly.
_exec_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lyretext-exec")
_read_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lyretext-read")

# Tracks run-level operations in flight (start_run, the read/manifest gate —
# there is exactly one of these per run, sharing the run's own checkpoint
# thread, so these must stay serialized per run_id).
_running: set[str] = set()
# Tracks per-CHAPTER operations in flight. Each chapter now runs on its own
# checkpoint thread (see orchestration/graph.py), so two different chapters
# of the same run are independent and must NOT share a lock — only guard
# against two concurrent operations on the *same* chapter's own thread.
_chapter_running: set[tuple[str, str]] = set()
# Last error raised by a background chapter task, keyed by (run_id, chapter_id).
# Background tasks are fire-and-forget, so without this an exception inside one
# (an LLM timeout, a network failure) vanished into "Task exception was never
# retrieved" and the UI just showed the chapter as finished.
_chapter_errors: dict[tuple[str, str], str] = {}
_run_errors: dict[str, str] = {}


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
    """Run blocking graph execution off the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_exec_pool, functools.partial(fn, *args, **kwargs))


async def _read_in_thread(fn, *args, **kwargs):
    """Run a short read-only query. Never queues behind graph execution."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_read_pool, functools.partial(fn, *args, **kwargs))


def _spawn(coro, *, run_id: str, chapter_id: str | None = None):
    """Schedule a fire-and-forget background task, recording any exception.

    Two things this guards against:
      * the task being garbage-collected mid-flight — asyncio only holds a weak
        reference, so the strong ref in _background_tasks is what keeps it alive
      * an exception disappearing silently; it is stored so the next poll can
        surface it on the chapter card instead of the card claiming success
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        # Shown on the run/chapter card: never let a key in a provider error
        # message reach the UI (the log copy is redacted by the log filter).
        message = redact(f"{type(exc).__name__}: {exc}")
        logger.exception("Background task failed (run=%s chapter=%s)", run_id, chapter_id)
        if chapter_id is None:
            _run_errors[run_id] = message
        else:
            _chapter_errors[(run_id, chapter_id)] = message

    task.add_done_callback(_done)
    return task


_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Key values and the desktop auth token never reach a log line (#67).
install_log_redaction()

app = FastAPI(title="LyreTeXt", version=__version__, docs_url="/api/docs")

# Permissive CORS for the dev workflow; switched off in desktop mode, where the
# app's own same-origin window is the only legitimate caller.
app.add_middleware(
    DevOnlyCORS,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last so it runs first: in desktop mode (LYRETEXT_AUTH_TOKEN set) every
# request needs the per-launch token and a loopback Host — see lyretext.security.
app.add_middleware(LocalAuthMiddleware)


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

class _RevalidatingStaticFiles(StaticFiles):
    """Serve the UI with must-revalidate, so an edit is never masked by cache.

    These files are actively developed against a long-lived local server, and
    a browser holding a stale app.js/api-client.js pair produces symptoms that
    look like backend bugs (a call into a function the cached client doesn't
    have yet). ETags still make revalidation a cheap 304 — this only stops the
    browser serving from cache *without asking*.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if _UI_DIR.exists():
    app.mount("/ui", _RevalidatingStaticFiles(directory=str(_UI_DIR), html=True), name="ui")


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

@app.get("/api/health")
def health():
    """Liveness check for the desktop shell and other supervisors.

    Deliberately touches nothing else — not config.yml, not the runs database —
    so it answers 200 whenever the server is up, even if those are broken.
    """
    return {"status": "ok", "version": __version__}


# ---------------------------------------------------------------------------
# API keys (desktop app, #67)
# ---------------------------------------------------------------------------

def _key_status() -> list[dict]:
    return [
        {"provider": provider, "set": bool(os.environ.get(env_var))}
        for provider, env_var in PROVIDER_KEY_ENV.items()
    ]


@app.post("/api/internal/keys")
async def set_internal_keys(request: Request):
    """Set or unset provider keys in this process (desktop app only).

    Called by the Electron main process after the user saves or removes a key,
    so the change applies without restarting the backend. Body:
    ``{"keys": {"gemini": "<key>" | null, ...}}``; null removes a key.

    Requires the token in the ``X-LyreTeXt-Token`` header specifically: the
    window's cookie is not enough, so the web layer can never push key material
    here — only the main process, which holds the token, can. The body is
    parsed by hand and never logged or echoed (FastAPI's validation errors
    would echo it back).
    """
    token = auth_token()
    if not token:
        raise HTTPException(403, "Only available in the desktop app")
    if not token_matches(request.headers.get(AUTH_HEADER), token):
        raise HTTPException(401, "Unauthorized")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Body must be JSON") from None
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, dict):
        raise HTTPException(400, 'Expected {"keys": {provider: key | null}}')
    for provider, value in keys.items():
        if provider not in PROVIDER_KEY_ENV:
            raise HTTPException(400, f"Unknown provider {provider!r}")
        if value is not None and not (isinstance(value, str) and value.strip()):
            raise HTTPException(400, f"Key for {provider!r} must be a non-empty string or null")
    for provider, value in keys.items():
        env_var = PROVIDER_KEY_ENV[provider]
        if value is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = value.strip()
    # The cached client also caches one adapter per (provider, key); drop it so
    # the next call uses the new key and the old one is forgotten.
    _config.reset_client()
    return {"keys": _key_status()}


@app.post("/api/keys/verify")
async def verify_key(body: dict = Body(...)):
    """Smoke-test the current key for ``{"provider": ...}`` with the provider's
    cheapest authenticated call. Returns ``{provider, ok, detail}``; the key is
    never echoed (the provider's error text is redacted too)."""
    provider = body.get("provider")
    if provider not in PROVIDER_KEY_ENV:
        raise HTTPException(400, f"Unknown provider {provider!r}")
    if not os.environ.get(PROVIDER_KEY_ENV[provider]):
        return {"provider": provider, "ok": False, "detail": f"No API key is set for {provider}."}

    def _verify() -> tuple[bool, str]:
        try:
            return _config._get_client().verify_key(provider)
        except Exception as exc:  # noqa: BLE001 - a bad key is an answer, not a 500
            return False, f"{type(exc).__name__}: {exc}"

    # The read pool: a key test must not queue behind running chapters.
    ok, detail = await _read_in_thread(_verify)
    return {"provider": provider, "ok": bool(ok), "detail": redact(str(detail))[:300]}


# ---------------------------------------------------------------------------
# Environment checks (#70, decision #65)
# ---------------------------------------------------------------------------

def _resolved_options(node_id: str, run_config: dict | None = None) -> dict:
    """The options *node_id* will run with (env < config.yml < run_config).
    Falls back to the defaults if config.yml is unreadable: a broken config is
    reported by the run itself, and must not hide the toolchain checks."""
    config_file = data_paths().config_file
    try:
        resolved = _config.get_resolved_config(
            config_file=config_file if config_file.exists() else None,
            # Passed exactly as start_run passes it, so this sees the run's config.
            runtime_payload=run_config or None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not resolve config for environment checks", exc_info=True)
        resolved = _config.RuntimeConfig()
    return resolved.get_node_config(node_id)


def _environment() -> dict:
    opts = _resolved_options("translate_chapter")
    report = environment_report(
        pandoc_exe=opts.get("pandoc_exe"),
        pandoc_writer=opts.get("pandoc_writer"),
    )
    logger.info("Environment: %s", summarize(report))
    return report


@app.get("/api/environment")
async def get_environment():
    """First-run checks: pandoc, the pretext.lua writer, R, knitr, API keys.

    Each tool entry is ``{ok, error, guidance, path, source, ...}``; *guidance*
    says what to install when something is missing. API keys report set/unset
    per provider — never a value. Probes run with timeouts (see
    lyretext.environment), so a hung R cannot hold the request for long.
    """
    report = await _read_in_thread(_environment)
    report["api_keys"] = _key_status()
    report["desktop"] = bool(auth_token())
    return report


async def _check_run_toolchain(source: str, run_config: dict | None = None) -> None:
    """Refuse to start an R Markdown run without R and knitr (422), with what
    to install, rather than let it fail on its first knit."""
    if _mock_enabled():
        return  # mock runs replay recorded output and run no tools

    def _check() -> None:
        pipeline = _resolved_options("process_to_markdown", run_config).get("pipeline", "rmd")
        if run_needs_r(source, pipeline):
            rmd_preflight()

    try:
        await _read_in_thread(_check)
    except RToolchainError as exc:
        raise HTTPException(422, str(exc)) from None


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




def _check_run_dirs(output_dir: str, temp_dir: str) -> None:
    """In data-dir mode, refuse run directories outside the data dir (400).

    The desktop app's local API must not be usable to write anywhere on disk:
    an absolute path or a ``..`` that resolves outside the data dir is
    rejected. Relative paths anchor in the data dir, as before. Dev mode (no
    data dir) keeps accepting any path.
    """
    paths = data_paths()
    if paths.root is None:
        return
    root = paths.root.resolve()
    for field, raw in (("output_dir", output_dir), ("temp_dir", temp_dir)):
        try:
            resolved = paths.resolve(str(raw)).resolve()
        except (OSError, ValueError, TypeError):
            raise HTTPException(400, f"Invalid {field}") from None
        if not resolved.is_relative_to(root):
            raise HTTPException(400, f"{field} must be inside the data directory")


@app.get("/api/runs")
async def list_runs():
    """List every run as a lightweight summary for the runs library.

    Each entry carries name, status, stage, chapter progress and updated-at,
    computed from the run's own checkpoint snapshot without building the full
    view model or fanning out to per-chapter threads — so the frontend renders
    the list from one call instead of a get_run per run.

    A genuine failure to enumerate the checkpointer is *not* swallowed here (it
    surfaces as a 500); only individual stale runs are skipped, inside
    list_run_summaries.
    """
    return {"runs": await _read_in_thread(list_run_summaries, _cp())}


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
    _check_run_dirs(output_dir, temp_dir)
    if run_id in _running:
        raise HTTPException(409, f"Run {run_id!r} already in progress")
    await _check_run_toolchain(source, run_config)

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

    _spawn(_run_bg(), run_id=run_id)
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
    _check_run_dirs(output_dir, temp_dir)

    resolved_run_id = ensure_run_id(run_id)
    if resolved_run_id in _running:
        raise HTTPException(409, f"Run {resolved_run_id!r} already in progress")

    stage_dir = Path(tempfile.mkdtemp(prefix=f"lyretext_upload_{resolved_run_id}_"))
    for upload, rel_path in zip(files, paths):
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise HTTPException(400, f"invalid relative path: {rel_path!r}")
        # webkitRelativePath always starts with the picked folder's own name
        # (e.g. "MyPaper/paper.tex"), but pipelines scan project_source
        # non-recursively for their source files. Drop that leading segment
        # so stage_dir itself lines up with what the picker treated as the
        # project root — otherwise every uploaded project's files sit one
        # directory deeper than the pipelines ever look, and nothing is found.
        rel_to_stage = Path(*rel.parts[1:]) if len(rel.parts) > 1 else rel
        dest = stage_dir / rel_to_stage
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await upload.read())
    try:
        await _check_run_toolchain(str(stage_dir))
    except HTTPException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise

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

    _spawn(_run_bg(), run_id=resolved_run_id)
    return {"run_id": resolved_run_id, "status": "starting", "staged_path": str(stage_dir)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Return the full view-model for a run (chapters, stages, findings, output)."""
    # Snapshot what's in flight BEFORE reading, and hand it to the view model:
    # a chapter that is executing right now has the same checkpoint shape as
    # one that died part-way, so without this every running chapter was
    # reported as stalled and the card flashed "Stopped before completing".
    # Taken first so a chapter that finishes mid-read is treated as still
    # running for this tick — erring towards "running" merely delays a genuine
    # failure by one 2s poll, where the reverse shows a false alarm.
    executing_chapters = {cid for (rid, cid) in _chapter_running if rid == run_id}
    run_executing = run_id in _running
    view = await _read_in_thread(
        get_run_view,
        run_id,
        _cp(),
        executing_chapters=executing_chapters,
        run_executing=run_executing,
    )
    if "error" in view:
        raise HTTPException(404, view["error"])
    view["_executing"] = _run_executing(run_id)
    if run_id in _run_errors:
        view["_error"] = _run_errors[run_id]
    # Per-chapter executing flags let the UI show several chapters "running"
    # at once, since they're now independent (see _chapter_running above).
    for ch in view.get("chapters", []):
        chapter_id = ch.get("id")
        ch["_executing"] = chapter_id in executing_chapters
        # Surface a crashed background task so the card can say so, rather
        # than the chapter silently appearing finished.
        error = _chapter_errors.get((run_id, chapter_id))
        if error and not ch["_executing"]:
            ch["_error"] = error
    return view


@app.get("/api/runs/{run_id}/status")
async def run_status(run_id: str):
    """Lightweight status check — chapters + gate only, no output listing."""
    executing_chapters = {cid for (rid, cid) in _chapter_running if rid == run_id}
    view = await _read_in_thread(
        get_run_view,
        run_id,
        _cp(),
        executing_chapters=executing_chapters,
        run_executing=run_id in _running,
    )
    if "error" in view:
        raise HTTPException(404, view["error"])
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

    _spawn(_resume(), run_id=run_id)
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

    _chapter_errors.pop(key, None)
    # Marked here, not inside _resume: the coroutine doesn't start until the
    # next event-loop tick, so a poll (or a second command) landing in between
    # would see the chapter as idle while its thread was about to move. The
    # batch endpoint below does the same for the same reason.
    _chapter_running.add(key)

    async def _resume():
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

    _spawn(_resume(), run_id=run_id, chapter_id=chapter_id)
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
        _chapter_errors.pop(key, None)
        _spawn(_resume_one(chapter_id), run_id=run_id, chapter_id=chapter_id)

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


@app.put("/api/runs/{run_id}/chapters/{chapter_id}/file", status_code=202)
async def write_chapter_file(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Write directly-edited source or translated-output text for a chapter.

    Body: {target: "source"|"output", content: str}

    Editing the source triggers edit_source_file_acknowledged + recompile
    (recompile_source) if the chapter is currently paused at its review gate;
    editing the translated output triggers revalidate instead of a full
    retranslate — see ux2.md's review/edit workspace requirements.

    Returns once the file is on disk; the recompile/revalidate behind it runs
    as a background task, like every other chapter command here. It used to run
    inline, which meant a PUT that had already written the file sat open for
    the whole pipeline run (tens of seconds, an LLM call or two) — the editor's
    Save button span for all of it, and any error or dropped connection along
    the way left the client with no idea the write had in fact succeeded.

    The per-chapter lock is taken before the write and released only when the
    background command finishes, so the chapter is guarded across both halves.
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

    _chapter_errors.pop(key, None)
    _chapter_running.add(key)
    try:
        result = await _in_thread(
            write_chapter_file_and_trigger,
            run_id, chapter_id, target, content, _cp(),
            trigger=False,
        )
    except BaseException:
        _chapter_running.discard(key)
        raise

    if "error" in result:
        _chapter_running.discard(key)
        raise HTTPException(400, result["error"])

    action = result.get("pending_action")
    if not action:
        # Chapter not dispatched yet — the edit simply takes effect when it is.
        _chapter_running.discard(key)
        return result

    async def _resume():
        try:
            await _in_thread(apply_chapter_command, run_id, chapter_id, action, _cp())
        finally:
            _chapter_running.discard(key)

    _spawn(_resume(), run_id=run_id, chapter_id=chapter_id)
    return {**result, "status": "processing", "triggered": action}


@app.get("/api/runs/{run_id}/chapters/{chapter_id}/findings")
async def chapter_findings(run_id: str, chapter_id: str):
    """Return the full per-issue findings (severity, message, suggestion) for a chapter.

    GET /api/runs/{id} only carries findings *counts* — this returns the
    complete sidecar so the workspace review-gate panel can list each finding.
    """
    result = await _read_in_thread(get_chapter_findings, run_id, chapter_id, _cp())
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/issues/{issue_index}/dismiss")
async def dismiss_chapter_issue(run_id: str, chapter_id: str, issue_index: int):
    """Mark a single finding as ignored, so it drops out of the findings counts."""
    result = await _in_thread(dismiss_issue, run_id, chapter_id, issue_index, _cp())
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/issues/dismiss-group")
async def dismiss_chapter_issue_group(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Ignore a whole group of findings at once.

    Body: {issue_indices: [int, ...]}
    """
    issue_indices = body.get("issue_indices")
    if not isinstance(issue_indices, list) or not issue_indices:
        raise HTTPException(400, "issue_indices must be a non-empty list")
    result = await _in_thread(dismiss_issues, run_id, chapter_id, issue_indices, _cp())
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/issues/restore")
async def restore_chapter_issues(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Un-ignore one or more findings, bringing them back into the counts.

    Body: {issue_indices: [int, ...]}

    Dismissals now survive re-review (finalize_review carries them forward by
    group_key), so this is the only way back from a mis-click.
    """
    issue_indices = body.get("issue_indices")
    if not isinstance(issue_indices, list) or not issue_indices:
        raise HTTPException(400, "issue_indices must be a non-empty list")
    result = await _in_thread(restore_issues, run_id, chapter_id, issue_indices, _cp())
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


def _spawn_targeted_fix(run_id: str, chapter_id: str, issue_indices: list[int]):
    """Take the per-chapter lock and run a targeted fix in the background.

    Guarded by the same per-(run_id, chapter_id) lock as other chapter
    commands — this reopens the chapter's own thread via apply_chapter_command,
    so it must not race a concurrent operation on that same thread.
    """
    key = (run_id, chapter_id)
    if key in _chapter_running:
        raise HTTPException(
            409, f"Chapter {chapter_id!r} of run {run_id!r} already has an operation in progress"
        )

    _chapter_errors.pop(key, None)
    _chapter_running.add(key)

    async def _resume():
        try:
            await _in_thread(fix_issues, run_id, chapter_id, issue_indices, _cp())
        finally:
            _chapter_running.discard(key)

    _spawn(_resume(), run_id=run_id, chapter_id=chapter_id)


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/issues/{issue_index}/fix", status_code=202)
async def fix_chapter_issue(run_id: str, chapter_id: str, issue_index: int):
    """Trigger a targeted edit_chapter run addressing one finding only."""
    _spawn_targeted_fix(run_id, chapter_id, [issue_index])
    return {"run_id": run_id, "chapter_id": chapter_id, "issue_index": issue_index, "status": "processing"}


@app.post("/api/runs/{run_id}/chapters/{chapter_id}/issues/fix-group", status_code=202)
async def fix_chapter_issue_group(run_id: str, chapter_id: str, body: dict = Body(...)):
    """Fix a whole group of identical findings in ONE edit_chapter run.

    Body: {issue_indices: [int, ...]}

    The group is formed client-side (findings whose text matches bar the line
    number); the point of the endpoint is that all its occurrences are handed to
    the editing agent together, so the chapter is rewritten once rather than
    once per line.
    """
    issue_indices = body.get("issue_indices")
    if not isinstance(issue_indices, list) or not issue_indices:
        raise HTTPException(400, "issue_indices must be a non-empty list")
    _spawn_targeted_fix(run_id, chapter_id, issue_indices)
    return {
        "run_id": run_id,
        "chapter_id": chapter_id,
        "issue_indices": issue_indices,
        "status": "processing",
    }


@app.get("/api/runs/{run_id}/chapters/{chapter_id}/render")
async def chapter_render(run_id: str, chapter_id: str):
    """Render a chapter's PreTeXt output to preview HTML for the workspace.

    Deterministic and read-only: it re-reads the .ptx from disk each call, so
    a hand-edit or a re-run is reflected on the next poll without any extra
    invalidation. Malformed XML comes back as `error` with a line number
    rather than a 500 — a chapter that fails to parse is exactly the case the
    render pane needs to show.
    """
    result = await _read_in_thread(render_chapter_output, run_id, chapter_id, _cp())
    if "error" in result and isinstance(result["error"], str):
        raise HTTPException(404, result["error"])
    return result


@app.get("/api/asset")
def read_asset(path: str = Query(..., description="Absolute path to an image asset")):
    """Serve a figure referenced by a rendered chapter.

    Rendered PreTeXt points at images on disk (knitr's figure output, say),
    which the browser cannot open directly. Restricted to image suffixes —
    text lives behind /api/files, and this shouldn't become a second way to
    read arbitrary files.
    """
    p = Path(path)
    if p.suffix.lower() not in ASSET_SUFFIXES:
        raise HTTPException(400, f"unsupported asset type: {p.suffix!r}")
    if not p.is_file():
        raise HTTPException(404, f"asset not found: {path}")
    return FileResponse(str(p))


@app.get("/api/runs/{run_id}/chapters/{chapter_id}/checkpoints")
async def chapter_checkpoints(run_id: str, chapter_id: str):
    """List a chapter's own checkpoint history, most recent first."""
    checkpoints = await _read_in_thread(get_chapter_checkpoints, run_id, chapter_id, _cp())
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

    _run_errors.pop(run_id, None)
    _spawn(_resume(), run_id=run_id)
    return {"run_id": run_id, "status": "processing"}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/output")
async def run_output(run_id: str):
    files = await _read_in_thread(get_output, run_id, _cp())
    return {"output": files}


@app.get("/api/runs/{run_id}/output/{filename}")
async def download_output(run_id: str, filename: str):
    files = await _read_in_thread(get_output, run_id, _cp())
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


@app.get("/api/runs/{run_id}/project.zip")
async def download_project(run_id: str, root: str = "auto", title: str | None = None):
    """Download the whole run as one compilable PreTeXt project (zip).

    Bundles every chapter/matter ``.ptx`` with a freshly-assembled unifying
    ``main.ptx``. ``root`` selects the PreTeXt root element — ``auto`` (default),
    ``book`` or ``article`` (decision #34, Option U); anything else falls back
    to ``auto``. Runs on the exec pool because it (re)writes ``main.ptx`` to
    disk.
    """
    if root not in ("auto", "book", "article"):
        root = "auto"
    result = await _in_thread(
        build_project_archive, run_id, _cp(), root_element=root, title=title
    )
    if result is None:
        raise HTTPException(404, f"run {run_id!r} has no output to bundle")
    filename, data = result
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/jobs")
async def run_jobs(run_id: str):
    jobs = await _read_in_thread(get_jobs, run_id, _cp())
    return {"jobs": jobs}


# ---------------------------------------------------------------------------
# Settings (config.yml)
# ---------------------------------------------------------------------------

def _config_file() -> Path:
    """The config.yml in force — the same file runs read (config.data_paths)."""
    return data_paths().config_file


def _read_config_yml() -> dict:
    config_file = _config_file()
    if not config_file.exists():
        return {"global_options": {}, "node_overrides": {}}
    try:
        import yaml
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read config.yml: {exc}") from exc


def _write_config_yml(data: dict) -> None:
    try:
        import yaml
        with open(_config_file(), "w") as f:
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
