"""Enhance stage graph stub — not yet implemented."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .state import ChapterEnhance


def build_enhance_graph():
    """Placeholder — returns a passthrough graph until the stage is built."""
    graph_builder = StateGraph(ChapterEnhance)
    graph_builder.add_edge(START, END)
    return graph_builder.compile()
