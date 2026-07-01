from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from ..config import create_llm
from .state import ChapterTranslation, TranslationState, SkeletonState

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

from ..read.agents import read_chapter, read_project, upload_project, structure_project, create_temp_directory, process_to_markdown
from ..translate.agents import translate_chapter
from langgraph.types import Send


def build_chapter_graph(provider: str = "gemini"):
    llm = create_llm(provider)
    graph_builder = StateGraph(ChapterTranslation)

    graph_builder.add_node("read_chapter", read_chapter)
    graph_builder.add_node("translate_chapter", translate_chapter)

    graph_builder.add_edge(START, "read_chapter")
    graph_builder.add_edge("read_chapter", "translate_chapter")
    graph_builder.add_edge("translate_chapter", END)
    # graph_builder.add_edge("read_chapter", END)

    return graph_builder.compile()


def split_manifest(state: TranslationState) -> Send[ChapterTranslation]:
    manifest = state.get("manifest", [])
    return [
        Send(
            "translator_graph",
            {
                "source_path": chapter["source_path"],
                "output_path": chapter["output_path"],
                "execution_mode": state.get("execution_mode", "upload"),
                "apply_mode": state.get("apply_mode", "auto_apply"),
                "create_backup": state.get("create_backup", False),
                "provider": state.get("provider", "gemini"),
            },
        )
        for chapter in manifest
    ]



def build_skeleton_agent(provider: str = "gemini"):
    llm = create_llm(provider)
    graph_builder = StateGraph(SkeletonState)


    graph_builder.add_node("process_to_markdown", process_to_markdown)
    graph_builder.add_node("structure_project", structure_project)
    graph_builder.add_node("upload_project", upload_project)
    graph_builder.add_node("create_temp_directory", create_temp_directory)
    #graph_builder.add_node("read_project", read_project)

    graph_builder.add_edge(START, "process_to_markdown")
    graph_builder.add_edge("process_to_markdown", "upload_project")
    graph_builder.add_edge("upload_project", "structure_project")
    graph_builder.add_edge("structure_project", "create_temp_directory")
    graph_builder.add_edge("create_temp_directory", END)

    return graph_builder.compile()

def build_workflow_graph(provider: str = "gemini"):
    llm = create_llm(provider)
    graph_builder = StateGraph(TranslationState)

    chapter_graph = build_chapter_graph(provider)
    skeleton_graph = build_skeleton_agent(provider)

    def call_skeleton_graph(state):
        output = skeleton_graph.invoke({
            "project_source": state["project_source"],
            "temp_dir": state["temp_dir"],
            "output_dir": state["output_dir"],
            "execution_mode": state.get("execution_mode", "upload"),
            "apply_mode": state.get("apply_mode", "auto_apply"),
            "create_backup": state.get("create_backup", False),
            "provider": state.get("provider", provider),
        })
        return {"manifest": output.get("manifest", [])}

    graph_builder.add_node("build_skeleton", call_skeleton_graph)
    graph_builder.add_node("translator_graph", chapter_graph)

    graph_builder.add_edge(START, "build_skeleton")
    graph_builder.add_conditional_edges("build_skeleton", split_manifest)
    graph_builder.add_edge("translator_graph", END)

    return graph_builder.compile()