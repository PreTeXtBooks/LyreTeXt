from __future__ import annotations

from typing import Optional
from pathlib import Path
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from ..config import get_resolved_config
from .state import TranslationState
from ..translate.state import ChapterTranslation
from ..translate.graph import build_chapter_graph
from ..read.graph import build_skeleton_graph
from .checkpointing import (
    build_checkpointer,
    build_checkpoint_config,
    ensure_run_id,
    extract_checkpoint_metadata,
)

""" def build_translation_graph():
    llm = create_llm("gemini")
    translate_content_agent = build_translate_content_agent(llm)

    graph_builder = StateGraph(TranslationState)

    graph_builder.add_node("read_ingest", read_ingest_agent)
    graph_builder.add_node("read_structure", read_structure_agent)
    graph_builder.add_node("translate_content", translate_content_agent)
    #graph_builder.add_node("translate_math", translate_math_agent)
    graph_builder.add_node("review", review_agent)
    graph_builder.add_node("enhance", enhance_agent)

    graph_builder.add_edge(START, "read_ingest")
    graph_builder.add_edge("read_ingest", "read_structure")
    graph_builder.add_edge("read_structure", "translate_content")
    #graph_builder.add_edge("translate_content", "translate_math")
    #graph_builder.add_edge("translate_math", "review")
    graph_builder.add_edge("translate_content", "review")
    graph_builder.add_edge("review", "enhance")
    graph_builder.add_edge("enhance", END) 

    return graph_builder.compile() """

from langgraph.types import Send


# ============================================================================
# Configuration Resolution Helper (Phase 1)
# ============================================================================

def resolve_config_for_graph(
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
) -> dict:
    """
    Resolve configuration and return state dict with resolved_config field.
    This wraps get_resolved_config() to integrate with graph invocation.
    
    Returns dict suitable for merging into graph input state.
    """
    resolved_config = get_resolved_config(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )
    return {"resolved_config": resolved_config}


# ============================================================================
# Graph Builders
# ============================================================================

def split_manifest(state: TranslationState) -> Send[ChapterTranslation]:
    manifest = state.get("manifest", [])
    resolved_config = state.get("resolved_config")
    run_id = state.get("run_id")
    last_checkpoint_id = state.get("last_checkpoint_id", "")
    checkpoint_namespace = state.get("checkpoint_namespace", "")
    pause_requested = state.get("pause_requested", False)
    pause_reason = state.get("pause_reason", "")
    if resolved_config is None:
        raise ValueError(
            "resolved_config is required in TranslationState. "
            "Use resolve_config_for_graph() or get_resolved_config() to build it "
            "before invoking the workflow graph."
        )
    return [
        Send(
            "translator_graph",
            {
                "run_id": run_id,
                "last_checkpoint_id": last_checkpoint_id,
                "checkpoint_namespace": checkpoint_namespace,
                "stage_id": "translate",
                "pause_requested": pause_requested,
                "pause_reason": pause_reason,
                "source_path": chapter["source_path"],
                "output_path": chapter["output_path"],
                "resolved_config": resolved_config,
            },
        )
        for chapter in manifest
    ]


def route_workflow_entry(state: TranslationState) -> str:
    # If a manifest is already available and stage_id is translate, resume translation
    # without re-running the read stage.
    if state.get("stage_id") == "translate" and state.get("manifest"):
        return "dispatch_manifest"
    return "build_skeleton"


def route_after_read(state: TranslationState):
    # Minimal backend pause gate: stop after read when explicitly requested.
    if state.get("pause_requested") and state.get("stage_id") == "read":
        return END
    return split_manifest(state)



def build_workflow_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph_builder = StateGraph(TranslationState)

    chapter_graph = build_chapter_graph()
    skeleton_graph = build_skeleton_graph()

    def call_skeleton_graph(state):
        resolved_config = state.get("resolved_config")
        if resolved_config is None:
            raise ValueError(
                "resolved_config is required in TranslationState. "
                "Use resolve_config_for_graph() or get_resolved_config() before invoking."
            )
        output = skeleton_graph.invoke({
            "run_id": state["run_id"],
            "last_checkpoint_id": state.get("last_checkpoint_id", ""),
            "checkpoint_namespace": state.get("checkpoint_namespace", ""),
            "stage_id": "read",
            "pause_requested": state.get("pause_requested", False),
            "pause_reason": state.get("pause_reason", ""),
            "project_source": state["project_source"],
            "temp_dir": state["temp_dir"],
            "output_dir": state["output_dir"],
            "resolved_config": resolved_config,
        })
        return {"manifest": output.get("manifest", [])}

    graph_builder.add_node("build_skeleton", call_skeleton_graph)
    graph_builder.add_node("dispatch_manifest", lambda state: {})
    graph_builder.add_node("translator_graph", chapter_graph)

    graph_builder.add_conditional_edges(START, route_workflow_entry)
    graph_builder.add_conditional_edges("build_skeleton", route_after_read)
    graph_builder.add_conditional_edges("dispatch_manifest", split_manifest)
    graph_builder.add_edge("translator_graph", END)

    return graph_builder.compile(checkpointer=checkpointer)


def build_checkpointed_workflow_graph():
    return build_workflow_graph(checkpointer=build_checkpointer())


def invoke_workflow_graph(
    state: dict,
    *,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    run_id: str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    workflow_graph = build_workflow_graph(checkpointer=checkpointer or build_checkpointer())
    workflow_state = dict(state)
    if "resolved_config" not in workflow_state:
        workflow_state.update(
            resolve_config_for_graph(
                config_file=config_file,
                runtime_payload=runtime_payload,
            )
        )
    resolved_run_id = ensure_run_id(run_id or workflow_state.get("run_id"))
    workflow_state.setdefault("stage_id", "read")
    workflow_state.setdefault("pause_requested", False)
    workflow_state.setdefault("pause_reason", "")
    workflow_state.setdefault("last_checkpoint_id", "")
    workflow_state.setdefault("checkpoint_namespace", "")
    workflow_state["run_id"] = resolved_run_id
    checkpoint_config = build_checkpoint_config(resolved_run_id)
    result = workflow_graph.invoke(workflow_state, config=checkpoint_config)
    snapshot = workflow_graph.get_state(config=checkpoint_config)
    checkpoint_metadata = extract_checkpoint_metadata(snapshot)
    if isinstance(result, dict):
        merged_result = dict(result)
        merged_result.update(checkpoint_metadata)
        if "checkpoint_id" in checkpoint_metadata:
            merged_result["last_checkpoint_id"] = checkpoint_metadata["checkpoint_id"]
        if "checkpoint_ns" in checkpoint_metadata:
            merged_result["checkpoint_namespace"] = checkpoint_metadata["checkpoint_ns"]
        if merged_result.get("pause_requested") and merged_result.get("stage_id") == "read":
            pass
        else:
            merged_result["stage_id"] = "translate"
        merged_result.setdefault("pause_requested", False)
        merged_result.setdefault("pause_reason", "")
        return merged_result
    return result


def continue_translation_from_manifest(
    paused_state: dict,
    *,
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    resume_state = dict(paused_state)
    if not resume_state.get("manifest"):
        raise ValueError(
            "continue_translation_from_manifest requires a state with a non-empty manifest."
        )
    resume_state["stage_id"] = "translate"
    resume_state["pause_requested"] = False
    resume_state["pause_reason"] = ""
    return invoke_workflow_graph(
        resume_state,
        config_file=config_file,
        runtime_payload=runtime_payload,
        run_id=resume_state.get("run_id"),
        checkpointer=checkpointer,
    )