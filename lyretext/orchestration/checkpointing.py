from __future__ import annotations

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
