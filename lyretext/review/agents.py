"""Review layer node functions.

Two roles:
  run_check(spec)   — factory that returns a node running one CheckSpec via LLM
  finalize_review   — sorts issues, writes .findings.json sidecar

Deterministic checks live alongside their CheckSpec in checks.py (see
check_wellformed). The review subgraph is read-only: fixing is the editing
agent's job, and lives on the chapter graph (lyretext.edit.agents.edit_chapter).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from ..config import create_llm, resolve_node_opts
from ..utils import load_prompts
from .checks import CheckSpec
from .grouping import group_key
from .state import ChapterReview
from .structure import Issue, IssueDraft, ReviewReport

_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"


def _numbered(text: str) -> str:
    """Prepend line numbers to text for LLM consumption."""
    lines = text.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i+1:>{width}} | {line}" for i, line in enumerate(lines))


# ---------------------------------------------------------------------------
# run_check node factory
# ---------------------------------------------------------------------------

def run_check(spec: CheckSpec):
    """Return a node function that runs a single registered check.

    The returned node reads `artifact` from ChapterReview, calls the LLM with
    the check's prompt, and returns ``{"issues": [...]}`` which the reducer
    appends to the shared issues channel.
    """

    def _node(state: ChapterReview, config: RunnableConfig = None) -> dict[str, Any]:
        opts = resolve_node_opts(state, f"review:{spec.id}", config)
        provider = opts["provider"]

        artifact = state.get("artifact", "")
        chapter_id = state.get("chapter_id", "")

        prompts = load_prompts(_PROMPTS_FILE)
        prompt_text = str(
            prompts.get(spec.prompt_key, f"Review the PreTeXt for: {spec.name}")
        )

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_text},
                {
                    "type": "text",
                    "text": (
                        f"Chapter ID: {chapter_id}\n\n"
                        f"```xml\n{_numbered(artifact)}\n```"
                    ),
                },
            ]
        )

        llm = create_llm(provider).with_structured_output(ReviewReport.model_json_schema())
        response = llm.invoke([message])

        raw_issues: list[Any] = (
            response.get("issues", []) if isinstance(response, dict) else []
        )
        # The LLM is only asked for an IssueDraft — what it can judge from the
        # document. Which check found it, and whether the optional auto-edit
        # loop may act on it unasked, are properties of the CheckSpec, so they
        # are attached here rather than requested in prose in prompts.md.
        issues: list[Issue] = []
        for raw in raw_issues:
            try:
                draft = raw if isinstance(raw, IssueDraft) else IssueDraft(**raw)
            except Exception:
                continue
            issues.append(Issue(
                **draft.model_dump(),
                check_id=spec.id,
                auto_fixable=spec.auto_fixable,
            ))

        return {"issues": issues}

    _node.__name__ = f"run_check_{spec.id}"
    return _node


# ---------------------------------------------------------------------------
# finalize_review
# ---------------------------------------------------------------------------

def _dismissed_group_keys(sidecar_path: Path) -> set[str]:
    """Group keys the user has already dismissed, from the previous sidecar.

    Every review pass rewrites the sidecar from scratch, so without this a
    dismissal survives only until the next pass — and now that fixing is driven
    by the human at the gate, passes are frequent. Keyed on group_key rather
    than position so a dismissal still lands after line numbers have shifted.
    """
    if not sidecar_path.exists():
        return set()
    try:
        previous = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        i["group_key"]
        for i in previous.get("issues", [])
        if isinstance(i, dict) and i.get("ignored") and i.get("group_key")
    }


def finalize_review(state: ChapterReview, config: RunnableConfig = None) -> dict[str, Any]:
    """Stamp group keys, carry dismissals forward, write the .findings.json sidecar."""
    issues = list(state.get("issues", []))
    output_path = state.get("output_path", "")
    chapter_id = state.get("chapter_id", "")

    _sev_order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: (i.line or 99_999, _sev_order.get(i.severity, 9)))

    for issue in issues:
        issue.group_key = group_key(
            check_id=issue.check_id,
            message=issue.message,
            suggestion=issue.suggestion,
        )

    sidecar_path = (
        Path(output_path).parent / ".lyretext" / f"{chapter_id}.findings.json"
        if output_path
        else None
    )
    if sidecar_path is not None:
        dismissed = _dismissed_group_keys(sidecar_path)
        for issue in issues:
            if issue.group_key in dismissed:
                issue.ignored = True

    active = [i for i in issues if not i.ignored]
    review_status = "blocking" if any(i.severity == "error" for i in active) else "passing"

    findings: dict[str, Any] = {
        "chapter_id": chapter_id,
        "review_status": review_status,
        "counts": {
            "error": sum(1 for i in active if i.severity == "error"),
            "warn": sum(1 for i in active if i.severity == "warn"),
            "info": sum(1 for i in active if i.severity == "info"),
        },
        "issues": [i.model_dump() for i in issues],
    }

    if sidecar_path is not None:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    return {
        "review_status": review_status,
        "findings": findings,
        "issues": issues,
    }
