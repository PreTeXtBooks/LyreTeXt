"""Check registry — mirrors PipelineRegistry pattern.

Adding a new check = register a CheckSpec + write its prompt under a ## heading
in review/prompts/prompts.md.  No graph rewiring needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CheckSpec:
    """Descriptor for a single review check-agent."""

    id: str
    name: str
    target_stage: str          # "translate" | "read" | …
    prompt_key: str            # ## heading in prompts.md
    default_severity: Literal["error", "warn", "info"] = "warn"
    auto_fixable: bool = False
    enabled: bool = True


class CheckRegistry:
    """Ordered registry of CheckSpec entries."""

    def __init__(self) -> None:
        self._specs: list[CheckSpec] = []

    def register(self, spec: CheckSpec) -> None:
        self._specs.append(spec)

    def get_for_stage(self, stage: str) -> list[CheckSpec]:
        return [s for s in self._specs if s.target_stage == stage and s.enabled]

    def get(self, check_id: str) -> CheckSpec | None:
        return next((s for s in self._specs if s.id == check_id), None)


# Module-level singleton registry
_registry = CheckRegistry()


def get_registry() -> CheckRegistry:
    return _registry


def register_check(spec: CheckSpec) -> None:
    _registry.register(spec)


# ---------------------------------------------------------------------------
# Built-in checks (translate stage)
# ---------------------------------------------------------------------------

register_check(CheckSpec(
    id="xml_wellformed",
    name="XML Well-Formedness",
    target_stage="translate",
    prompt_key="xml_wellformed",
    default_severity="error",
    auto_fixable=False,
    enabled=True,
))

register_check(CheckSpec(
    id="math_notation",
    name="Math Notation Consistency",
    target_stage="translate",
    prompt_key="math_notation",
    default_severity="warn",
    auto_fixable=True,
    enabled=True,
))

register_check(CheckSpec(
    id="pretext_structure",
    name="PreTeXt Structure Validity",
    target_stage="translate",
    prompt_key="pretext_structure",
    default_severity="warn",
    auto_fixable=False,
    enabled=True,
))
