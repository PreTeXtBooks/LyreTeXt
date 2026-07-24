"""Review layer node functions.

Three roles:
  run_check(spec)   — factory that returns a node running one CheckSpec
  apply_fixes       — LLM-based auto-fixer for auto_fixable issues
  finalize_review   — sorts issues, writes .findings.json sidecar
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
from .state import ChapterReview
from .structure import Issue, ReviewReport

_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"


# ---------------------------------------------------------------------------
# run_check node factory
# ---------------------------------------------------------------------------

def run_check(spec: CheckSpec):
    """Return a node function that runs a single registered check.

    The returned node reads `artifact` from ChapterReview, calls the LLM with
    the check's prompt, and returns ``{"issues": [...]}`` which the reducer
    appends to the shared issues channel.
    """

    def _node(state: ChapterReview, run_config: RunnableConfig | None = None) -> dict[str, Any]:
        opts = resolve_node_opts(state, f"review:{spec.id}", run_config)
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
                        f"```xml\n{artifact}\n```"
                    ),
                },
            ]
        )

        llm = create_llm(provider).with_structured_output(ReviewReport.model_json_schema())
        response = llm.invoke([message])

        raw_issues: list[Any] = (
            response.get("issues", []) if isinstance(response, dict) else []
        )
        issues: list[Issue] = []
        for raw in raw_issues:
            if isinstance(raw, dict):
                # Inject check_id; LLM may omit it or set a wrong value
                raw["check_id"] = spec.id
                try:
                    issues.append(Issue(**raw))
                except Exception:
                    pass
            elif isinstance(raw, Issue):
                issues.append(raw)

        return {"issues": issues}

    _node.__name__ = f"run_check_{spec.id}"
    return _node


# ---------------------------------------------------------------------------
# apply_fixes
# ---------------------------------------------------------------------------

_FIX_SCHEMA = {
    "type": "object",
    "properties": {"xml": {"type": "string"}},
    "required": ["xml"],
}


def apply_fixes(state: ChapterReview, run_config: RunnableConfig | None = None) -> dict[str, Any]:
    """Ask the LLM to apply all auto_fixable issues in one shot.

    Rewrites the .ptx file when apply_mode != dry_run, then marks fixed issues
    as auto_fixed so finalize_review shows them collapsed in the UI.
    """
    opts = resolve_node_opts(state, "review:apply_fixes", run_config)
    provider = opts["provider"]
    apply_mode = opts["apply_mode"]

    fixable = [i for i in state.get("issues", []) if i.auto_fixable and not i.auto_fixed]
    if not fixable:
        return {}

    artifact = state.get("artifact", "")
    output_path = state.get("output_path", "")

    fix_desc = "\n".join(
        f"- Line {i.line or '?'}: [{i.severity}] {i.message}"
        + (f"\n  Suggestion: {i.suggestion}" if i.suggestion else "")
        for i in fixable
    )

    prompts = load_prompts(_PROMPTS_FILE)
    fix_prompt = str(
        prompts.get("apply_fixes", "Apply the listed fixes to the PreTeXt XML and return the corrected document.")
    )

    message = HumanMessage(
        content=[
            {"type": "text", "text": fix_prompt},
            {"type": "text", "text": f"Issues to fix:\n{fix_desc}"},
            {"type": "text", "text": f"Current PreTeXt:\n```xml\n{artifact}\n```"},
        ]
    )

    llm = create_llm(provider).with_structured_output(_FIX_SCHEMA)
    response = llm.invoke([message])
    fixed_xml: str = (
        response.get("xml", artifact) if isinstance(response, dict) else artifact
    )

    if apply_mode != "dry_run" and fixed_xml != artifact and output_path:
        Path(output_path).write_text(fixed_xml, encoding="utf-8")

    # Mark fixed issues as auto_fixed in the shared issues list
    updated: list[Issue] = []
    for issue in state.get("issues", []):
        if issue.auto_fixable and not issue.auto_fixed:
            updated.append(issue.model_copy(update={"auto_fixed": True}))
        else:
            updated.append(issue)

    return {"artifact": fixed_xml, "issues": updated}


# ---------------------------------------------------------------------------
# finalize_review
# ---------------------------------------------------------------------------

def finalize_review(state: ChapterReview, run_config: RunnableConfig | None = None) -> dict[str, Any]:
    """Determine review_status, write .findings.json sidecar, return summary."""
    issues = list(state.get("issues", []))
    output_path = state.get("output_path", "")
    chapter_id = state.get("chapter_id", "")

    _sev_order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: (i.line or 99_999, _sev_order.get(i.severity, 9)))

    blocking = [i for i in issues if i.severity == "error" and not i.auto_fixed]
    review_status = "blocking" if blocking else "passing"

    findings: dict[str, Any] = {
        "chapter_id": chapter_id,
        "review_status": review_status,
        "counts": {
            "error": sum(1 for i in issues if i.severity == "error" and not i.auto_fixed),
            "warn": sum(1 for i in issues if i.severity == "warn" and not i.auto_fixed),
            "info": sum(1 for i in issues if i.severity == "info" and not i.auto_fixed),
            "auto_fixed": sum(1 for i in issues if i.auto_fixed),
        },
        "issues": [i.model_dump() for i in issues],
    }

    if output_path:
        sidecar_dir = Path(output_path).parent / ".lyretext"
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = sidecar_dir / f"{chapter_id}.findings.json"
        sidecar_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    return {
        "review_status": review_status,
        "findings": findings,
        "issues": issues,
    }
