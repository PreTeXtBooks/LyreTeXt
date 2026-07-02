from __future__ import annotations

from typing import Any
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def ensure_run_id(run_id: str | None = None) -> str:
    return run_id or str(uuid4())


def build_checkpointer() -> BaseCheckpointSaver:
    return MemorySaver()


def build_checkpoint_config(run_id: str | None = None) -> dict:
    resolved_run_id = ensure_run_id(run_id)
    return {"configurable": {"thread_id": resolved_run_id}}


def build_resume_config(
    run_id: str,
    checkpoint_id: str,
    checkpoint_ns: str = "",
) -> dict:
    return {
        "configurable": {
            "thread_id": run_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_ns": checkpoint_ns,
        }
    }


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
