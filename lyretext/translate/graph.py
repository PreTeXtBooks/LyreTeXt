from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .state import ChapterTranslation
from .agents import translate_chapter
from ..read.agents import read_chapter


def build_chapter_graph():
    graph_builder = StateGraph(ChapterTranslation)

    graph_builder.add_node("read_chapter", read_chapter)
    graph_builder.add_node("translate_chapter", translate_chapter)

    graph_builder.add_edge(START, "read_chapter")
    graph_builder.add_edge("read_chapter", "translate_chapter")
    graph_builder.add_edge("translate_chapter", END)

    return graph_builder.compile()
