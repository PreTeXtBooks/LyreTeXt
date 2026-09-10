from __future__ import annotations

import sqlite3
from typing import Any, Literal
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def ensure_run_id(run_id: str | None = None) -> str:
    return run_id or str(uuid4())


def build_checkpointer(
    backend: Literal["memory", "sqlite"] = "memory",
    db_path: str | None = None,
) -> BaseCheckpointSaver:
    """Build a checkpointer.

    Args:
        backend: "memory" (in-process, lost on exit) or "sqlite" (persistent file).
        db_path: Path to the SQLite database file. Only used when backend="sqlite".
                 Defaults to "lyretext_runs.db" in the working directory.
    """
    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise ImportError(
                "SQLite checkpointer requires langgraph-checkpoint-sqlite. "
                "Install it with: pip install langgraph-checkpoint-sqlite"
            ) from exc
        path = db_path or "lyretext_runs.db"
        # check_same_thread=False allows SQLite connection to be used across threads.
        # This is necessary for LangGraph's executor which may run in different threads.
        # See: https://docs.python.org/3/library/sqlite3.html#sqlite3.connect
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn)
    return MemorySaver()


def build_checkpoint_config(
    run_id: str | None = None,
    runtime_options: dict[str, Any] | None = None,
) -> dict:
    resolved_run_id = ensure_run_id(run_id)
    configurable: dict[str, Any] = {"thread_id": resolved_run_id}
    if runtime_options is not None:
        configurable["runtime_options"] = runtime_options
    return {"configurable": configurable}


# Marker separating a run's own thread id from a chapter sub-thread id. It is
# the single source of truth for both minting chapter thread ids and telling
# the two kinds of thread apart when enumerating (see is_chapter_thread_id /
# list_run_thread_ids), so the runs library never lists a chapter as a run.
CHAPTER_THREAD_MARKER = "::chapter::"


def build_chapter_thread_id(run_id: str, chapter_id: str) -> str:
    """Thread id for one chapter's independent graph invocation.

    Each chapter gets its own checkpoint thread (rather than sharing the run's
    thread via a Send fan-out) so that chapters can be resumed/executed
    concurrently — LangGraph's Pregel model isn't safe for two independent
    `.stream()`/`Command(resume=...)` calls to race against the same
    thread_id, so a shared thread would force all chapter operations for a
    run to serialize behind a single lock.
    """
    return f"{run_id}{CHAPTER_THREAD_MARKER}{chapter_id}"


def is_chapter_thread_id(thread_id: str) -> bool:
    """True for a per-chapter sub-thread (see build_chapter_thread_id).

    The runs library enumerates every checkpoint thread and must show only the
    run-level ones; a chapter sub-thread is an implementation detail, not a
    run, so it is filtered out here.
    """
    return CHAPTER_THREAD_MARKER in thread_id


def list_run_thread_ids(
    checkpointer: BaseCheckpointSaver,
) -> list[tuple[str, str | None]]:
    """Return ``(run_id, updated_at)`` for every run-level checkpoint thread.

    Enumerates the checkpointer with ``list(None)`` — which yields one tuple per
    checkpoint across *all* threads, newest first — and reduces it to distinct
    run threads, dropping the ``::chapter::`` sub-threads. The first checkpoint
    seen for a thread is its most recent (the ordering guarantee), so its
    timestamp becomes the run's ``updated_at``. Result is newest-first.

    Deliberately does not catch enumeration errors: a checkpointer that cannot
    be listed is a real failure the caller must see, not an empty list. This
    replaces the earlier ``checkpointer.list_threads()`` call, which no backend
    actually implements — it raised ``AttributeError`` on every request and was
    silently swallowed into an always-empty runs list.
    """
    seen: dict[str, str | None] = {}
    for tup in checkpointer.list(None):
        configurable = (getattr(tup, "config", None) or {}).get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id or is_chapter_thread_id(thread_id):
            continue
        if thread_id not in seen:
            checkpoint = getattr(tup, "checkpoint", None) or {}
            seen[thread_id] = checkpoint.get("ts")
    return list(seen.items())


def build_chapter_checkpoint_config(
    run_id: str,
    chapter_id: str,
    runtime_options: dict[str, Any] | None = None,
) -> dict:
    configurable: dict[str, Any] = {"thread_id": build_chapter_thread_id(run_id, chapter_id)}
    if runtime_options is not None:
        configurable["runtime_options"] = runtime_options
    return {"configurable": configurable}


def extract_checkpoint_metadata(snapshot: Any) -> dict[str, str]:
    if snapshot is None:
        return {}
    snapshot_config = getattr(snapshot, "config", None) or {}
    configurable = snapshot_config.get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id")
    checkpoint_ns = configurable.get("checkpoint_ns")
    run_id = configurable.get("thread_id")
    metadata: dict[str, str] = {}
    if run_id:
        metadata["run_id"] = run_id
    if checkpoint_id:
        metadata["checkpoint_id"] = checkpoint_id
    if checkpoint_ns is not None:
        metadata["checkpoint_ns"] = checkpoint_ns
    return metadata
