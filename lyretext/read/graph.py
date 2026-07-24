from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .state import SkeletonState
from .agents import process_to_markdown, upload_project, structure_project, create_temp_directory


def build_skeleton_graph():
    graph_builder = StateGraph(SkeletonState)

    graph_builder.add_node("process_to_markdown", process_to_markdown)
    graph_builder.add_node("upload_project", upload_project)
    graph_builder.add_node("structure_project", structure_project)
    graph_builder.add_node("create_temp_directory", create_temp_directory)

    graph_builder.add_edge(START, "process_to_markdown")
    graph_builder.add_edge("process_to_markdown", "upload_project")
    graph_builder.add_edge("upload_project", "structure_project")
    graph_builder.add_edge("structure_project", "create_temp_directory")
    graph_builder.add_edge("create_temp_directory", END)

    return graph_builder.compile()
