from __future__ import annotations

from typing import Optional
from pathlib import Path
from langgraph.graph import END, START, StateGraph
from ..config import get_resolved_config
from .state import TranslationState
from ..translate.state import ChapterTranslation
from ..translate.graph import build_chapter_graph
from ..read.graph import build_skeleton_graph

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
    if resolved_config is None:
        raise ValueError(
            "resolved_config is required in TranslationState. "
            "Use resolve_config_for_graph() or get_resolved_config() to build it "
            "before invoking the workflow graph."
        )
    global_opts = resolved_config.global_options
    return [
        Send(
            "translator_graph",
            {
                "source_path": chapter["source_path"],
                "output_path": chapter["output_path"],
                "resolved_config": resolved_config,
            },
        )
        for chapter in manifest
    ]



def build_workflow_graph():
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
            "project_source": state["project_source"],
            "temp_dir": state["temp_dir"],
            "output_dir": state["output_dir"],
            "resolved_config": resolved_config,
        })
        return {"manifest": output.get("manifest", [])}

    graph_builder.add_node("build_skeleton", call_skeleton_graph)
    graph_builder.add_node("translator_graph", chapter_graph)

    graph_builder.add_edge(START, "build_skeleton")
    graph_builder.add_conditional_edges("build_skeleton", split_manifest)
    graph_builder.add_edge("translator_graph", END)

    return graph_builder.compile()