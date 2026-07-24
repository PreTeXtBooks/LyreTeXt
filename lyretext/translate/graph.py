from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .state import ChapterTranslation
from .agents import translate_chapter
from ..read.agents import read_chapter
from ..review.graph import build_review_graph


def _chapter_id_from_path(output_path: str) -> str:
    """Derive a stable chapter_id slug from the output file path."""
    return Path(output_path).stem


def review_chapter(
    state: ChapterTranslation,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Run the review subgraph on the translated .ptx file.

    Reads the artifact from disk, runs all registered translate-stage
    check-agents in parallel, and returns the findings summary.  Results
    are also written to .lyretext/<chapter_id>.findings.json by finalize_review.
    """
    output_path = state.get("output_path", "")
    chapter_id = state.get("chapter_id") or _chapter_id_from_path(output_path)

    artifact = ""
    path_obj = Path(output_path)
    if path_obj.exists():
        try:
            artifact = path_obj.read_text(encoding="utf-8")
        except OSError:
            pass

    if not artifact:
        # Nothing to review (e.g. dry_run produced no file)
        return {}

    review_graph = build_review_graph("translate")
    initial: dict = {
        "output_path": output_path,
        "chapter_id": chapter_id,
        "artifact": artifact,
        "issues": [],
    }
    result = review_graph.invoke(initial, config=run_config)
    return {"chapter_findings": result.get("findings")}


def chapter_review_gate(state: ChapterTranslation) -> dict:
    """Per-chapter review gate.

    Fires interrupt() so the UI/CLI can inspect the generated PreTeXt output
    before the chapter is accepted. Dispatches on resume_value["action"]:
      - approve                  : accept output, chapter completes
      - skip                     : skip this chapter (treated same as approve)
      - retry                    : re-run translate_chapter (if iteration_count < 2)
      - approve_after_escalation : force retry beyond the iteration limit
      - revalidate               : re-run review_chapter on the existing .ptx (no retranslate)
      - recompile_source         : re-read from source (picks up edited .rmd/.qmd) then retranslate
    """
    iteration_count = state.get("iteration_count", 1)
    chapter_id = state.get("chapter_id") or _chapter_id_from_path(state["output_path"])

    resume_value = interrupt({
        "type": "chapter_review",
        "chapter_id": chapter_id,
        "output_path": state["output_path"],
        "iteration_count": iteration_count,
        "escalation_required": iteration_count >= 2,
        "findings": state.get("chapter_findings"),
    })

    action = "approve"
    if isinstance(resume_value, dict):
        action = resume_value.get("action", "approve")
    elif isinstance(resume_value, bool):
        action = "approve" if resume_value else "skip"

    instruction = resume_value.get("instruction") if isinstance(resume_value, dict) else None

    if action in ("retry", "approve_after_escalation"):
        updates: dict[str, Any] = {
            "chapter_next": "translate_chapter",
            "retry_requested": True,
            "chapter_id": chapter_id,
            "iteration_count": iteration_count + 1,
        }
        if instruction:
            updates["instruction"] = instruction
        return updates

    if action == "revalidate":
        return {
            "chapter_next": "review_chapter",
            "retry_requested": False,
            "chapter_id": chapter_id,
            "iteration_count": iteration_count,
        }

    if action == "recompile_source":
        return {
            "chapter_next": "read_chapter",
            "retry_requested": False,
            "chapter_id": chapter_id,
            "iteration_count": iteration_count + 1,
            **({"instruction": instruction} if instruction else {}),
        }

    # approve / skip / unknown — complete this chapter
    return {
        "chapter_next": "__end__",
        "retry_requested": False,
        "chapter_id": chapter_id,
        "iteration_count": iteration_count,
    }


def route_after_chapter_gate(state: ChapterTranslation) -> str:
    """Route based on chapter_next set by the gate."""
    nxt = state.get("chapter_next", "__end__")
    if nxt in ("translate_chapter", "review_chapter", "read_chapter"):
        return nxt
    # Legacy: honour retry_requested flag for existing checkpoints
    if state.get("retry_requested"):
        return "translate_chapter"
    return END


def chapter_dispatch_gate(state: ChapterTranslation) -> dict:
    """Per-chapter dispatch gate.

    Fires an interrupt so the UI can launch chapters one at a time.
    Any resume value means "proceed" — the gate is purely a pause point.
    """
    chapter_id = state.get("chapter_id") or _chapter_id_from_path(state["output_path"])
    interrupt({
        "type": "chapter_dispatch",
        "chapter_id": chapter_id,
        "output_path": state["output_path"],
    })
    return {}


def build_chapter_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compile the per-chapter subgraph.

    Each chapter is invoked independently against its own checkpoint thread
    (see orchestration.checkpointing.build_chapter_checkpoint_config), so this
    accepts an explicit checkpointer for standalone use — pass None only if
    embedding this graph as a node inside another compiled graph that already
    provides one.
    """
    graph_builder = StateGraph(ChapterTranslation)

    graph_builder.add_node("chapter_dispatch_gate", chapter_dispatch_gate)
    graph_builder.add_node("read_chapter", read_chapter)
    graph_builder.add_node("translate_chapter", translate_chapter)
    graph_builder.add_node("review_chapter", review_chapter)
    graph_builder.add_node("chapter_review_gate", chapter_review_gate)

    graph_builder.add_edge(START, "chapter_dispatch_gate")
    graph_builder.add_edge("chapter_dispatch_gate", "read_chapter")
    graph_builder.add_edge("read_chapter", "translate_chapter")
    graph_builder.add_edge("translate_chapter", "review_chapter")
    graph_builder.add_edge("review_chapter", "chapter_review_gate")
    graph_builder.add_conditional_edges(
        "chapter_review_gate",
        route_after_chapter_gate,
        {
            "translate_chapter": "translate_chapter",
            "review_chapter": "review_chapter",
            "read_chapter": "read_chapter",
            END: END,
        },
    )

    return graph_builder.compile(checkpointer=checkpointer)
