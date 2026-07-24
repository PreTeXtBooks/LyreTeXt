"""Mock pipeline for UI testing.

Replaces AI/knitr nodes with fast file-copy operations using pre-translated
demo output, so the full interrupt-gate workflow can be exercised without
waiting for LLM or knitr execution.

Activate by setting the environment variable before starting the server:
    $env:LYRETEXT_MOCK = "1"
    python -m lyretext --reload

The mock always uses the demo chapter set from demo/example_output regardless
of the source path supplied by the UI.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ..read.state import SkeletonState
from ..translate.state import ChapterTranslation
from ..translate.graph import chapter_review_gate, route_after_chapter_gate
from .state import TranslationState
from .validation import default_validation_summary, evaluate_stage_transition

# ---------------------------------------------------------------------------
# Demo asset paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_DEMO_TEMP = _REPO_ROOT / "demo" / "temp_output"
_DEMO_OUTPUT = _REPO_ROOT / "demo" / "example_output"

# Canonical chapter list matching demo/example_output
_MOCK_CHAPTERS = [
    {"id": "fm-foreword",               "name": "Foreword",          "type": "frontmatter"},
    {"id": "fm-preface",                "name": "Preface",           "type": "frontmatter"},
    {"id": "chapter-1-introduction",    "name": "Introduction",      "type": "chapter"},
    {"id": "chapter-2-central-tendancy","name": "Central Tendency",  "type": "chapter"},
    {"id": "chapter-3-measuring-spread","name": "Measuring Spread",  "type": "chapter"},
    {"id": "app-appendix",              "name": "Appendix",          "type": "appendix"},
    {"id": "bm-references",             "name": "References",        "type": "backmatter"},
]

# ---------------------------------------------------------------------------
# Mock skeleton nodes
# ---------------------------------------------------------------------------

def _mock_process_to_markdown(state: SkeletonState) -> dict[str, Any]:
    """Skip knitr; copy pre-existing demo .md files into temp_dir."""
    time.sleep(0.4)
    temp_dir = Path(state.get("temp_dir", "temp"))
    temp_dir.mkdir(parents=True, exist_ok=True)

    for ch in _MOCK_CHAPTERS:
        src = _DEMO_TEMP / f"{ch['id']}.md"
        dst = temp_dir / f"{ch['id']}.md"
        if src.exists():
            shutil.copy2(src, dst)
        else:
            dst.write_text(f"# {ch['name']}\n\nMock content for {ch['id']}.\n", encoding="utf-8")

    return {"project_md_source": str(temp_dir)}


def _mock_structure_project(state: SkeletonState) -> dict[str, Any]:
    """Skip LLM; build manifest directly from the demo chapter list."""
    time.sleep(0.3)
    temp_dir = Path(state.get("temp_dir", "temp"))
    output_dir = Path(state.get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = [
        {
            "type": ch["type"],
            "name": ch["name"],
            "source_path": str(temp_dir / f"{ch['id']}.md"),
            "output_path": str(output_dir / f"{ch['id']}.ptx"),
        }
        for ch in _MOCK_CHAPTERS
    ]
    return {"manifest": manifest, "project_resources": []}


def build_mock_skeleton_graph():
    """Compiled skeleton subgraph using mock nodes (no knitr, no LLM)."""
    g = StateGraph(SkeletonState)
    g.add_node("process_to_markdown", _mock_process_to_markdown)
    g.add_node("structure_project", _mock_structure_project)
    g.add_edge(START, "process_to_markdown")
    g.add_edge("process_to_markdown", "structure_project")
    g.add_edge("structure_project", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Mock chapter node
# ---------------------------------------------------------------------------

def _mock_translate(state: ChapterTranslation) -> dict[str, Any]:
    """Skip LLM translate/review; copy pre-translated .ptx from demo/example_output."""
    time.sleep(0.4)
    output_path = Path(state["output_path"])
    ch_id = output_path.stem
    src_ptx = _DEMO_OUTPUT / f"{ch_id}.ptx"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if src_ptx.exists():
        shutil.copy2(src_ptx, output_path)
        content = src_ptx.read_text(encoding="utf-8")
    else:
        content = (
            f"<chapter>\n  <title>Mock: {ch_id}</title>\n"
            f"  <p>No pre-translated file found for {ch_id}.</p>\n</chapter>"
        )
        output_path.write_text(content, encoding="utf-8")

    return {
        "pretext_output": content,
        "chapter_structure": [],
        "chapter_findings": {"issues": [], "quality_score": 1.0, "mock": True},
    }


def _mock_chapter_dispatch_gate(state: ChapterTranslation) -> dict:
    """Pre-translate gate: fires one interrupt per chapter so the user can
    start chapters one at a time instead of all in parallel."""
    from langgraph.types import interrupt as _interrupt
    from ..orchestration.graph import _chapter_id_from_path  # reuse helper

    chapter_id = state.get("chapter_id") or _chapter_id_from_path(state["output_path"])
    _interrupt({
        "type": "chapter_dispatch",
        "chapter_id": chapter_id,
        "output_path": state["output_path"],
    })
    # On resume (any value), proceed to translate
    return {"chapter_id": chapter_id}


def build_mock_chapter_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compiled chapter subgraph: dispatch gate → mock translate → real review-gate.

    Invoked independently per chapter (its own checkpoint thread), mirroring
    the real build_chapter_graph — see orchestration/graph.py.
    """
    g = StateGraph(ChapterTranslation)
    g.add_node("chapter_dispatch_gate", _mock_chapter_dispatch_gate)
    g.add_node("mock_translate", _mock_translate)
    g.add_node("chapter_review_gate", chapter_review_gate)

    g.add_edge(START, "chapter_dispatch_gate")
    g.add_edge("chapter_dispatch_gate", "mock_translate")
    g.add_edge("mock_translate", "chapter_review_gate")
    g.add_conditional_edges(
        "chapter_review_gate",
        route_after_chapter_gate,
        {
            "translate_chapter": "mock_translate",
            "review_chapter":    "mock_translate",
            "read_chapter":      "mock_translate",
            END: END,
        },
    )
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Full mock workflow graph
# ---------------------------------------------------------------------------
#
# Chapters are no longer fanned out via Send inside this graph — mirroring
# the real orchestration/graph.py, each chapter now runs as an independent
# invocation of build_mock_chapter_graph on its own checkpoint thread (see
# orchestration/graph.py::_active_chapter_graph, which switches to
# build_mock_chapter_graph when LYRETEXT_MOCK is set).

def _mock_route_after_read_gate(state: TranslationState):
    # This graph's job ends once the manifest/read-gate is resolved, whatever
    # the outcome — chapters are dispatched independently by the service layer.
    return END


def build_mock_workflow_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Project-level mock graph: read stage + read gate only (no real AI/knitr calls)."""
    from .graph import evaluate_read_stage_gate  # reuse real gate logic (interrupt intact)
    from langgraph.graph import END as _END, START as _START

    mock_skeleton = build_mock_skeleton_graph()

    g = StateGraph(TranslationState)
    g.add_node("build_skeleton",       mock_skeleton)
    g.add_node("evaluate_read_gate",   evaluate_read_stage_gate)

    g.add_edge(_START, "build_skeleton")
    g.add_edge("build_skeleton", "evaluate_read_gate")
    g.add_conditional_edges("evaluate_read_gate", _mock_route_after_read_gate)

    return g.compile(checkpointer=checkpointer)
