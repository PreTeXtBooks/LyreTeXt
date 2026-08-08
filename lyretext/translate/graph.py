from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .state import ChapterTranslation
from .agents import translate_chapter
from ..config import resolve_node_opts
from ..edit.agents import edit_chapter
from ..review.graph import build_review_graph


def _chapter_id_from_path(output_path: str) -> str:
    """Derive a stable chapter_id slug from the output file path."""
    return Path(output_path).stem


def review_chapter(
    state: ChapterTranslation,
    config: RunnableConfig = None,
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
    result = review_graph.invoke(initial, config=config)
    return {"chapter_findings": result.get("findings")}


def route_after_review(
    state: ChapterTranslation,
    config: RunnableConfig = None,
) -> str:
    """Decide whether the editing agent runs *unasked* before the human gate.

    Opt-in and off by default (see ``auto_edit``): the normal path is
    pandoc → review → gate, and the user chooses what gets rewritten. Turned on,
    the loop becomes pandoc → review → edit → review → …, bounded by
    max_edit_iterations so it always terminates, and limited to findings from
    checks whose CheckSpec marks them auto_fixable.
    """
    opts = resolve_node_opts(state, "edit_chapter", config)

    if not opts["auto_edit"]:
        return "chapter_review_gate"
    if state.get("edit_iterations", 0) >= opts["max_edit_iterations"]:
        return "chapter_review_gate"

    findings = state.get("chapter_findings") or {}
    issues = findings.get("issues", []) if isinstance(findings, dict) else []
    actionable = [
        i
        for i in issues
        if isinstance(i, dict) and i.get("auto_fixable") and not i.get("ignored")
    ]
    return "edit_chapter" if actionable else "chapter_review_gate"


def chapter_review_gate(state: ChapterTranslation) -> dict:
    """Per-chapter review gate.

    Fires interrupt() so the UI/CLI can inspect the generated PreTeXt output
    before the chapter is accepted. Dispatches on resume_value["action"]:
      - approve                  : accept output, chapter completes
      - skip                     : skip this chapter (treated same as approve)
      - retry                    : with an instruction, hand the .ptx to the
                                   editing agent; without one, re-run the
                                   deterministic pandoc conversion (which picks
                                   up any source edits)
      - approve_after_escalation : accepted as an alias of retry, for older
                                   checkpoints and clients (see below)
      - revalidate               : re-run review_chapter on the existing .ptx
      - recompile_source         : re-convert from source (picks up edited .rmd/.qmd)

    ``iteration_count`` is a plain revision number. It used to raise an
    "escalation required — retries exhausted" failure state at 2, which made
    sense while retries were the automatic loop giving up; now that every pass
    through here is a human asking for something, a user's second fix is not a
    failure and must not paint the chapter red.
    """
    iteration_count = state.get("iteration_count", 1)
    chapter_id = state.get("chapter_id") or _chapter_id_from_path(state["output_path"])

    resume_value = interrupt({
        "type": "chapter_review",
        "chapter_id": chapter_id,
        "output_path": state["output_path"],
        "iteration_count": iteration_count,
        "findings": state.get("chapter_findings"),
    })

    action = "approve"
    if isinstance(resume_value, dict):
        action = resume_value.get("action", "approve")
    elif isinstance(resume_value, bool):
        action = "approve" if resume_value else "skip"

    instruction = resume_value.get("instruction") if isinstance(resume_value, dict) else None

    if action in ("retry", "approve_after_escalation"):
        # An instruction is something only the editing agent can act on —
        # conversion is deterministic and would ignore it. Without one, "retry"
        # means re-run the conversion, which is still meaningful after a source
        # edit.
        updates: dict[str, Any] = {
            "chapter_next": "edit_chapter" if instruction else "translate_chapter",
            "retry_requested": True,
            "chapter_id": chapter_id,
            "iteration_count": iteration_count + 1,
        }
        if instruction:
            updates["instruction"] = instruction
            # A fresh human instruction earns a fresh edit budget.
            updates["edit_iterations"] = 0
        return updates

    if action == "revalidate":
        return {
            "chapter_next": "review_chapter",
            "retry_requested": False,
            "chapter_id": chapter_id,
            "iteration_count": iteration_count,
        }

    if action == "recompile_source":
        # translate_chapter reads source_path directly via pandoc, so this
        # picks up source edits without a separate read step.
        return {
            "chapter_next": "translate_chapter",
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
    # Checkpoints written before read_chapter was unwired may still carry it;
    # translate_chapter now reads the source itself, so it is the equivalent.
    if nxt == "read_chapter":
        return "translate_chapter"
    if nxt in ("translate_chapter", "edit_chapter", "review_chapter"):
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
    graph_builder.add_node("translate_chapter", translate_chapter)
    graph_builder.add_node("review_chapter", review_chapter)
    graph_builder.add_node("edit_chapter", edit_chapter)
    graph_builder.add_node("chapter_review_gate", chapter_review_gate)

    graph_builder.add_edge(START, "chapter_dispatch_gate")
    graph_builder.add_edge("chapter_dispatch_gate", "translate_chapter")
    graph_builder.add_edge("translate_chapter", "review_chapter")
    graph_builder.add_conditional_edges(
        "review_chapter",
        route_after_review,
        {
            "edit_chapter": "edit_chapter",
            "chapter_review_gate": "chapter_review_gate",
        },
    )
    graph_builder.add_edge("edit_chapter", "review_chapter")
    graph_builder.add_conditional_edges(
        "chapter_review_gate",
        route_after_chapter_gate,
        {
            "translate_chapter": "translate_chapter",
            "edit_chapter": "edit_chapter",
            "review_chapter": "review_chapter",
            END: END,
        },
    )

    return graph_builder.compile(checkpointer=checkpointer)
