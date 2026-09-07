"""Check registry — mirrors PipelineRegistry pattern.

Adding a new check = register a CheckSpec + write its prompt under a ## heading
in review/prompts/prompts.md.  No graph rewiring needed.

A check may also be *deterministic*: set ``impl`` to a node function and it runs
in place of the LLM, using the identical Issue contract so nothing downstream
can tell the difference. This is the seam for schema validators (e.g. jing
against pretext.rng) as well as for cheap local parses.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from ..format import repair_xml_entities
from .structure import Issue


@dataclass
class CheckSpec:
    """Descriptor for a single review check-agent."""

    id: str
    name: str
    target_stage: str          # "translate" | "read" | …
    prompt_key: str            # ## heading in prompts.md
    default_severity: Literal["error", "warn", "info"] = "warn"
    # Machine autonomy only: may the OPTIONAL automatic review→edit→review loop
    # attempt this check's findings without being asked? (See ``auto_edit``,
    # off by default.) It says nothing about whether a human may request the
    # fix — at the review gate every finding is actionable, whatever this says.
    # run_check stamps it onto each Issue, so the LLM never reports it.
    auto_fixable: bool = False
    enabled: bool = True
    # Deterministic implementation. When set, build_review_graph uses this
    # instead of run_check(spec) and prompt_key is ignored.
    impl: Optional[Callable[..., dict[str, Any]]] = None


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
# Deterministic check implementations
# ---------------------------------------------------------------------------

def _diagnose(artifact: str, exc: ET.ParseError) -> tuple[str, str]:
    """Turn a bare expat error into something a reader can act on.

    "not well-formed (invalid token): line 819, column 25" is technically
    accurate and practically useless: at line 819 sits ``<m>\\lambda<1</m>``,
    which looks like perfectly good mathematics, so there is nothing visibly
    wrong to find. Naming the offending character and quoting the line is what
    turns that into a fixable report.
    """
    position = getattr(exc, "position", None) or (None, None)
    line, column = position
    lines = artifact.split("\n")
    source = lines[line - 1] if line and 0 < line <= len(lines) else ""
    excerpt = source.strip()
    if len(excerpt) > 140:
        excerpt = excerpt[:140] + "…"

    where = f" at line {line}, column {column}" if line else ""
    context = f' Offending line: "{excerpt}"' if excerpt else ""
    kind = str(exc)

    if "junk after document element" in kind:
        return (
            f"XML is not well-formed{where}: the file has more than one top-level "
            f"element, and a PreTeXt file must have exactly one.{context}",
            "Wrap the top-level elements in a single enclosing <chapter> or "
            "<section> so the file has one root.",
        )

    if "mismatched tag" in kind or "no element found" in kind:
        return (
            f"XML is not well-formed{where}: a tag is unclosed or closed out of "
            f"order.{context}",
            "Balance the tags so every element opened is closed in the right order.",
        )

    # The remaining common cause, and the one that looks like nothing is wrong:
    # a '<' or '&' written as a character where XML requires an entity. It gets
    # into the file when an editing agent "resolves" &lt; back to '<'. Rather
    # than guess from the reported column — which points just past the offending
    # character — ask the repairer, which is authoritative by construction: if
    # escaping the stray characters makes the document parse, that was the fault.
    _repaired_text, is_escaping_fault, _notes = repair_xml_entities(artifact)
    if is_escaping_fault:
        stray = ""
        if source and column:
            stray = next((ch for ch in source[max(0, column - 2):column + 1] if ch in "<&"), "")
        named = {"<": "&lt;", "&": "&amp;"}.get(stray, "&lt; / &amp;")
        subject = f"a literal '{stray}'" if stray else "a literal '<' or '&'"
        return (
            f"XML is not well-formed{where}: {subject} is being read as the start "
            f"of markup.{context}",
            f"Write it as {named}. Inside <m>, <me>, <md>, <c> and <cd> the "
            f"characters < and & must still be escaped, even though what they mean "
            f"there is mathematics or code rather than markup.",
        )

    return (
        f"XML is not well-formed{where}: {kind}.{context}",
        "Repair the markup so the document parses as XML.",
    )


def check_wellformed(state: Any, config=None) -> dict[str, Any]:
    """Verify the artifact is well-formed XML by actually parsing it.

    Now that conversion is pandoc rather than an LLM, the output is XML by
    construction — so when this fires it is almost always reporting damage done
    *after* conversion, by the editing agent. It is never a false positive: the
    same ElementTree parse gates the render pane, so anything reported here is
    also the reason the chapter will not display.

    This checks *well-formedness* only (balanced tags, valid entities), not
    PreTeXt schema conformance. The latter needs jing against pretext.rng and
    can be registered as a further CheckSpec with its own ``impl`` once the
    schema and a Java runtime ship with the product.
    """
    artifact = state.get("artifact", "")
    if not artifact.strip():
        return {"issues": []}

    try:
        ET.fromstring(artifact)
    except ET.ParseError as exc:
        position = getattr(exc, "position", None)
        line = position[0] if position else None
        message, suggestion = _diagnose(artifact, exc)
        return {
            "issues": [
                Issue(
                    check_id="xml_wellformed",
                    severity="error",
                    line=line,
                    message=message,
                    suggestion=suggestion,
                    auto_fixable=True,
                )
            ]
        }

    return {"issues": []}


# ---------------------------------------------------------------------------
# Built-in checks (translate stage)
# ---------------------------------------------------------------------------

register_check(CheckSpec(
    id="xml_wellformed",
    name="XML Well-Formedness",
    target_stage="translate",
    prompt_key="xml_wellformed",
    default_severity="error",
    # Deterministic parse errors are exactly the kind of thing the editing
    # agent can repair, and it now has the exact line to work from.
    auto_fixable=True,
    enabled=True,
    impl=check_wellformed,
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
