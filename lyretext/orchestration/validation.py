from __future__ import annotations

from typing import Any, Literal, TypedDict


class TransitionDecision(TypedDict):
    allowed: bool
    reason: str
    from_stage: str
    to_stage: str
    requires_human_signoff: bool


def default_validation_summary() -> dict[str, Any]:
    return {
        "results": [],
        "requires_human_signoff": True,
    }


def evaluate_stage_transition(
    state: dict[str, Any],
    *,
    from_stage: str,
    to_stage: str,
) -> TransitionDecision:
    summary = dict(state.get("validation_summary") or default_validation_summary())
    requires_human_signoff = bool(summary.get("requires_human_signoff", True))

    transition_policy = dict(state.get("transition_policy") or {})
    policy_key = f"{from_stage}->{to_stage}"
    policy = dict(transition_policy.get(policy_key) or {})

    if "requires_human_signoff" in policy:
        requires_human_signoff = bool(policy["requires_human_signoff"])

    blocked_severities = set(policy.get("blocked_severities", ["error"]))

    has_blocking_findings = False
    for result in summary.get("results", []):
        if not isinstance(result, dict):
            continue
        if result.get("stage_id") != from_stage:
            continue
        severity = str(result.get("severity", "warn"))
        status = str(result.get("status", "passed"))
        if severity in blocked_severities and status != "passed":
            has_blocking_findings = True
            break

    if has_blocking_findings:
        return {
            "allowed": False,
            "reason": "validation_blocking_findings",
            "from_stage": from_stage,
            "to_stage": to_stage,
            "requires_human_signoff": requires_human_signoff,
        }

    if requires_human_signoff:
        signoffs = dict(state.get("human_signoffs", {}))
        if not bool(signoffs.get(policy_key, False)):
            return {
                "allowed": False,
                "reason": "human_signoff_required",
                "from_stage": from_stage,
                "to_stage": to_stage,
                "requires_human_signoff": requires_human_signoff,
            }

    return {
        "allowed": True,
        "reason": "allowed",
        "from_stage": from_stage,
        "to_stage": to_stage,
        "requires_human_signoff": requires_human_signoff,
    }
