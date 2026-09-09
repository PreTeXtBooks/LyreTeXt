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
from .grouping import finding_identity, group_key
from .state import ChapterReview
from .structure import Issue, IssueDraft, ReviewReport

_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"


def _numbered(text: str) -> str:
    """Prepend line numbers to text for LLM consumption."""
    lines = text.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i+1:>{width}} | {line}" for i, line in enumerate(lines))


def _prior_context(prior: list[dict[str, Any]], check_id: str) -> str | None:
    """Render this check's previous findings as additive context for the LLM.

    Feeding the previous pass back in is what makes review stateful rather than
    a cold re-derivation (#33): the model re-affirms findings that still apply
    instead of randomly re-inventing the set, so a disappearance is a genuine
    judgement and finalize_review can safely auto-resolve it. Dismissed findings
    are included as context only — a user set them aside, so they must not be
    re-reported, but the model still needs to know they were seen.
    """
    mine = [p for p in prior if isinstance(p, dict) and p.get("check_id") == check_id]
    if not mine:
        return None
    lines = []
    for p in mine:
        tag = " [dismissed by the user — context only, do not report]" if p.get("ignored") else ""
        where = f" (was near line {p['line']})" if p.get("line") else ""
        msg = p.get("message", "")
        sug = f" — suggested: {p['suggestion']}" if p.get("suggestion") else ""
        lines.append(f"- {msg}{where}{sug}{tag}")
    return (
        "The previous review of this document recorded the findings below. "
        "Re-report every one that STILL applies to the document above (you may "
        "correct its line number), and add any genuinely new findings. If a "
        "finding no longer applies, simply omit it.\n\n" + "\n".join(lines)
    )


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

        content: list[dict[str, str]] = [
            {"type": "text", "text": prompt_text},
            {
                "type": "text",
                "text": (
                    f"Chapter ID: {chapter_id}\n\n"
                    f"```xml\n{_numbered(artifact)}\n```"
                ),
            },
        ]
        prior_ctx = _prior_context(state.get("prior_findings") or [], spec.id)
        if prior_ctx:
            content.append({"type": "text", "text": prior_ctx})

        message = HumanMessage(content=content)

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

def _load_prior_issues(sidecar_path: Path | None) -> list[dict[str, Any]]:
    """The full issue list from the previous sidecar (all lifecycle states).

    Every pass rewrites the sidecar, so this is the only carrier of prior state
    into the next reconciliation. Missing or corrupt sidecars reconcile as a
    first pass — an unreadable history must never lose the current findings.
    """
    if sidecar_path is None or not sidecar_path.exists():
        return []
    try:
        previous = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    issues = previous.get("issues", [])
    return [i for i in issues if isinstance(i, dict)]


def _reconcile(observed: list[Issue], prior: list[dict[str, Any]]) -> list[Issue]:
    """Merge freshly-observed findings with the previous pass by identity (#33).

    Deterministic, per decision #37 — the LLM observes, this code keeps the
    books. By finding_identity:
      * observed          → open; inherits a prior dismissal so a re-seen
                            finding the user set aside stays set aside.
      * prior open, gone  → fixed (auto-resolved: the review no longer sees it).
      * prior ignored,gone→ kept, still ignored (shown as ignored, fed as
                            context next pass).
      * prior fixed, gone → stays fixed.
    The record is append-only over identities; nothing is deleted — lifecycle
    state transitions instead, so "the list only grows".
    """
    # Identity matches across passes at the group level; within a pass every
    # occurrence is kept (the UI collapses them, the sidecar does not).
    observed_ids = {finding_identity(i) for i in observed}
    prior_by_id: dict[str, list[dict[str, Any]]] = {}
    for p in prior:
        prior_by_id.setdefault(finding_identity(p), []).append(p)
    ignored_ids = {
        ident for ident, ps in prior_by_id.items() if any(x.get("ignored") for x in ps)
    }

    result: list[Issue] = []

    # 1. Everything the current review sees is open; a prior dismissal of that
    #    identity carries so a re-seen finding the user set aside stays aside.
    for issue in observed:
        issue.status = "open"
        if finding_identity(issue) in ignored_ids:
            issue.ignored = True
        result.append(issue)

    # 2. Prior identities the review no longer sees — carry forward their
    #    occurrences, transitioned. Dismissed stays dismissed; anything else the
    #    review has stopped reporting is auto-resolved to fixed.
    for ident, ps in prior_by_id.items():
        if ident in observed_ids:
            continue
        for p in ps:
            result.append(Issue(
                check_id=p.get("check_id", ""),
                severity=p.get("severity", "warn"),
                line=p.get("line"),
                end_line=p.get("end_line"),
                message=p.get("message", ""),
                suggestion=p.get("suggestion"),
                auto_fixable=bool(p.get("auto_fixable", False)),
                rule_id=p.get("rule_id"),
                block_id=p.get("block_id"),
                group_key=p.get("group_key"),
                ignored=bool(p.get("ignored", False)),
                status="fixed" if not p.get("ignored") else p.get("status", "open"),
            ))

    return result


def finalize_review(state: ChapterReview, config: RunnableConfig = None) -> dict[str, Any]:
    """Stamp identities, reconcile with the previous pass, write the sidecar."""
    observed = list(state.get("issues", []))
    output_path = state.get("output_path", "")
    chapter_id = state.get("chapter_id", "")

    # Identity must be stamped before reconciliation keys on it.
    for issue in observed:
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
    # Prefer prior findings threaded through state (the same list fed to the
    # checks); fall back to the sidecar so finalize_review is correct when
    # called directly (tests, ad-hoc revalidation).
    prior = state.get("prior_findings")
    if prior is None:
        prior = _load_prior_issues(sidecar_path)

    issues = _reconcile(observed, prior)

    _sev_order = {"error": 0, "warn": 1, "info": 2}
    # Fixed findings sink below active ones; active kept in (line, severity) order.
    issues.sort(key=lambda i: (i.status == "fixed", i.line or 99_999, _sev_order.get(i.severity, 9)))

    active = [i for i in issues if not i.ignored and i.status != "fixed"]
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
