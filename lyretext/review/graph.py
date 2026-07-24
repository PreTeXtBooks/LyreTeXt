"""Build the review subgraph for a given target stage.

Architecture
============
START → fan_out_checks (Send → run_check_<id> per spec, parallel)
      → [all run_check nodes] → collect_issues
      → policy_route → apply_fixes (optional) → finalize_review → END

The ``issues`` channel on ChapterReview carries an operator.add reducer so
parallel check-agent branches merge their findings automatically — a native
LangGraph feature, no custom wiring needed.

Subgraphs compile WITHOUT a checkpointer (per framework convention).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import apply_fixes, finalize_review, run_check
from .checks import get_registry
from .state import ChapterReview


def _fan_out_checks(specs):
    """Return a router that fans out to all check nodes via Send."""

    def _route(state: ChapterReview):
        return [Send(f"run_check_{spec.id}", state) for spec in specs]

    return _route


def _policy_route(state: ChapterReview) -> str:
    """Route to apply_fixes if any auto_fixable, unresolved issues exist."""
    fixable = [i for i in state.get("issues", []) if i.auto_fixable and not i.auto_fixed]
    return "apply_fixes" if fixable else "finalize_review"


def build_review_graph(target_stage: str = "translate"):
    """Build and compile the review subgraph for *target_stage*.

    When the check registry for *target_stage* is empty the graph is a simple
    passthrough: START → finalize_review (writes an empty findings file) → END.
    This keeps the chapter graph integration unconditional.
    """
    specs = get_registry().get_for_stage(target_stage)

    graph_builder = StateGraph(ChapterReview)
    graph_builder.add_node("finalize_review", finalize_review)
    graph_builder.add_node("apply_fixes", apply_fixes)
    graph_builder.add_edge("apply_fixes", "finalize_review")
    graph_builder.add_edge("finalize_review", END)

    if specs:
        # Register one node per check spec
        for spec in specs:
            graph_builder.add_node(f"run_check_{spec.id}", run_check(spec))

        # collect_issues: no-op; just a convergence point for all check branches
        def _collect(state: ChapterReview) -> dict:
            return {}

        graph_builder.add_node("collect_issues", _collect)
        graph_builder.add_conditional_edges(START, _fan_out_checks(specs))

        for spec in specs:
            graph_builder.add_edge(f"run_check_{spec.id}", "collect_issues")

        graph_builder.add_conditional_edges("collect_issues", _policy_route)
    else:
        # No checks registered for this stage — straight to finalize
        graph_builder.add_edge(START, "finalize_review")

    return graph_builder.compile()
