"""The editing agent — an LLM that rewrites an existing .ptx artifact.

Two triggers, both routed through the same node:
  - automatic: the review loop found actionable (auto_fixable) issues and
    ``auto_edit`` is on — see ``route_after_review`` in translate/graph.py
  - manual: the user supplied a free-text ``instruction`` at the review gate

Adapted from what used to be ``review.agents.apply_fixes``, which lived inside
the review subgraph and ran unprompted. It is now a first-class, checkpointed
node on the chapter graph so it appears in the jobs view and can be invoked
directly.
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from ..config import create_llm, resolve_node_opts
from ..format import format_pretext, repair_xml_entities
from ..translate.state import ChapterTranslation
from ..utils import load_prompts

_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"

_EDIT_SCHEMA = {
    "type": "object",
    "properties": {"xml": {"type": "string"}},
    "required": ["xml"],
}


def _actionable_issues(state: ChapterTranslation) -> list[dict[str, Any]]:
    """Issues the AUTOMATIC loop should act on — see auto_edit, off by default.

    Only consulted when no instruction is present. A human-directed edit carries
    its own instruction naming exactly the findings the user picked (built by
    service.fix_issues), and folding this list in on top of it meant clicking
    "Fix" on one row silently rewrote every other auto_fixable finding too.

    Reads the serialised findings dict written by finalize_review rather than
    Issue models — chapter state crosses a checkpoint boundary, so it holds
    plain dicts.
    """
    findings = state.get("chapter_findings") or {}
    issues = findings.get("issues", []) if isinstance(findings, dict) else []
    return [
        i
        for i in issues
        if isinstance(i, dict) and i.get("auto_fixable") and not i.get("ignored")
    ]


class MalformedEditError(RuntimeError):
    """The editing agent returned XML that does not parse and cannot be repaired."""


def _reject_if_malformed(xml_text: str, output_path: str) -> None:
    """Raise rather than write markup that would leave the chapter unparseable.

    Refusing is the only safe answer: the chapter on disk is currently valid, and
    replacing it with something that neither parses nor renders destroys work the
    user can otherwise still see. The raise propagates to the API's per-chapter
    error store, so the chapter card says what happened instead of the edit
    appearing to have succeeded.
    """
    try:
        ET.fromstring(xml_text)
    except ET.ParseError as exc:
        line, _column = getattr(exc, "position", (None, None)) or (None, None)
        excerpt = ""
        if line:
            lines = xml_text.split("\n")
            if 0 < line <= len(lines):
                excerpt = f" Offending line {line}: {lines[line - 1].strip()[:120]!r}"
        raise MalformedEditError(
            f"The editing agent returned malformed XML, so {output_path} was left "
            f"unchanged: {exc}.{excerpt}"
        ) from exc


def edit_chapter(
    state: ChapterTranslation,
    config: RunnableConfig = None,
) -> dict[str, Any]:
    """Apply review fixes and/or a user instruction to the chapter's .ptx."""
    opts = resolve_node_opts(state, "edit_chapter", config)
    provider = opts["provider"]
    apply_mode = opts["apply_mode"]
    create_backup = opts["create_backup"]

    output_path = state.get("output_path", "")
    instruction = state.get("instruction")
    issues = [] if instruction else _actionable_issues(state)

    if not issues and not instruction:
        # Nothing to do — don't burn an LLM call.
        return {}

    path_obj = Path(output_path) if output_path else None
    artifact = ""
    if path_obj is not None and path_obj.exists():
        try:
            artifact = path_obj.read_text(encoding="utf-8")
        except OSError:
            pass
    if not artifact:
        artifact = state.get("pretext_output", "") or ""
    if not artifact:
        return {}

    content: list[dict] = [
        {"type": "text", "text": str(load_prompts(_PROMPTS_FILE).get("edit_chapter"))}
    ]

    if issues:
        issue_desc = "\n".join(
            f"- Line {i.get('line') or '?'}: [{i.get('severity')}] {i.get('message')}"
            + (f"\n  Suggestion: {i['suggestion']}" if i.get("suggestion") else "")
            for i in issues
        )
        content.append({"type": "text", "text": f"Issues to fix:\n{issue_desc}"})

    if instruction:
        content.append({"type": "text", "text": f"User instruction: {instruction}"})

    content.append({"type": "text", "text": f"Current PreTeXt:\n```xml\n{artifact}\n```"})

    llm = create_llm(provider).with_structured_output(_EDIT_SCHEMA)
    response = llm.invoke([HumanMessage(content=content)])
    edited_xml: str = (
        response.get("xml", artifact) if isinstance(response, dict) else artifact
    )

    # Never let the editor hand back something that isn't XML. Models routinely
    # un-escape entities they read as content — `<m>\lambda&lt;1</m>` comes back
    # as `<m>\lambda<1</m>`, which is correct mathematics and invalid markup —
    # and the result used to overwrite a perfectly good chapter, which then
    # neither parsed nor rendered. Repair that specific fault if we can;
    # otherwise keep the artifact we already have.
    edited_xml, repaired, repair_notes = repair_xml_entities(edited_xml)
    for note in repair_notes:
        print(f"[REPAIR] {note}")
    if repaired:
        print(f"[REPAIR] {output_path}: fixed malformed XML returned by the editing agent")
    else:
        _reject_if_malformed(edited_xml, output_path)

    # Format before the comparison below, not after: an LLM that reflows the
    # markup without changing a word then produces no diff at all, so the
    # chapter isn't rewritten and its recorded line numbers stay valid.
    if opts["format_output"]:
        edited_xml, format_notes = format_pretext(edited_xml)
        for note in format_notes:
            print(f"[FORMAT] {note}")

    edit_iterations = state.get("edit_iterations", 0) + 1

    if apply_mode == "dry_run":
        preview = edited_xml[:400] + ("..." if len(edited_xml) > 400 else "")
        print(f"[DRY-RUN] Would write {len(edited_xml)} chars to {output_path}:\n{preview}")
    elif edited_xml != artifact and path_obj is not None:
        if create_backup and path_obj.exists():
            backup_path = path_obj.with_suffix(".ptx.bak")
            shutil.copy2(path_obj, backup_path)
            print(f"Backup created: {backup_path}")
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(edited_xml, encoding="utf-8")
        print(f"Edited: {output_path}")

    # Clearing instruction stops it re-firing on every subsequent loop pass.
    return {
        "pretext_output": edited_xml,
        "edit_iterations": edit_iterations,
        "instruction": None,
    }
