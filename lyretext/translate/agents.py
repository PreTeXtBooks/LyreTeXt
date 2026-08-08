"""Markdown → PreTeXt conversion node.

This step is deterministic: it shells out to pandoc with Oscar Levin's
``pretext.lua`` custom writer rather than prompting an LLM. The agentic role
has moved to ``lyretext.edit`` — an editing agent that operates *on* the
generated PreTeXt, driven by the review loop or a user instruction.

The node id remains "translate_chapter" so existing checkpoint threads,
config node_overrides, and UI labels keep resolving.
"""
import json
from pathlib import Path
import shutil
from typing import Any

from langchain_core.runnables import RunnableConfig

from .state import ChapterTranslation
from ..config import resolve_node_opts
from ..convert import PandocNotFoundError, convert_markdown_to_pretext, split_pretext_by_section
from ..format import format_pretext
from ..review.grouping import group_key
from ..review.structure import Issue


def _conversion_failure(chapter_id: str, message: str) -> dict[str, Any]:
    """Build a blocking findings payload for a failed conversion.

    Reuses the findings contract that finalize_review writes, so the existing
    UI findings panel surfaces conversion errors through the normal path
    instead of the chapter silently arriving at its gate with no output. Built
    from the Issue model rather than a hand-written dict so it cannot drift out
    of step with that contract, and so it carries a group_key like any other
    finding — a conversion error is dismissible too.
    """
    issue = Issue(
        check_id="pandoc_convert",
        severity="error",
        message=message,
    )
    issue.group_key = group_key(
        check_id=issue.check_id,
        message=issue.message,
        suggestion=issue.suggestion,
    )
    return {
        "pretext_output": "",
        "chapter_findings": {
            "chapter_id": chapter_id,
            "review_status": "blocking",
            "counts": {"error": 1, "warn": 0, "info": 0},
            "issues": [issue.model_dump()],
        },
    }


def _parse_pandoc_extra_args(raw: str | None) -> list[str]:
    """Parse the pandoc_extra_args config value into a pandoc argv list.

    Accepts a JSON list (e.g. '["--lua-filter", "foo.lua"]') or a plain
    comma-separated string (e.g. '--toc, --standalone').
    """
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in raw.split(",") if part.strip()]


def _translate_chapter_tex(
    state: ChapterTranslation,
    opts: dict[str, Any],
    chapter_id: str,
    output_path: str | None,
) -> dict[str, Any]:
    """LaTeX pipeline variant: pandoc converts the whole project in one shot.

    pandoc/pretext.lua resolves \\input/\\include natively via cwd, so there
    is no per-chapter markdown source to convert. The first chapter to reach
    this node runs pandoc once on the main .tex file and splits the resulting
    monolithic PreTeXt document across every chapter's output_path; any
    chapter that arrives after that just reads its own already-written file.
    """
    apply_mode = opts["apply_mode"]
    create_backup = opts["create_backup"]

    path_obj = Path(output_path) if output_path else None
    if path_obj is not None and path_obj.exists():
        content = path_obj.read_text(encoding="utf-8")
        return {"pretext_output": content, "edit_iterations": 0}

    main_file = state.get("main_file", "")
    project_root = state.get("project_root", "")
    chapter_ids = state.get("chapter_ids", [])

    if not main_file:
        return _conversion_failure(
            chapter_id, "LaTeX pipeline: no main_file available in state"
        )

    extra_args = _parse_pandoc_extra_args(opts.get("pandoc_extra_args"))

    try:
        xml_content, messages = convert_markdown_to_pretext(
            main_file,
            pandoc_exe=opts["pandoc_exe"],
            writer=opts["pandoc_writer"],
            extra_args=extra_args,
            cwd=project_root or None,
        )
    except PandocNotFoundError as exc:
        print(f"[PANDOC] {exc}")
        return _conversion_failure(chapter_id, str(exc))

    if not xml_content:
        detail = "; ".join(messages) or "pandoc produced no output"
        print(f"[PANDOC] Conversion failed for {chapter_id}: {detail}")
        return _conversion_failure(chapter_id, detail)

    for message in messages:
        print(f"[WARN] {message}")

    mapping, warnings = split_pretext_by_section(xml_content, chapter_ids)
    for warning in warnings:
        print(f"[SPLIT] {warning}")

    output_dir = path_obj.parent if path_obj is not None else None
    this_chapter_output = ""

    for cid, fragment in mapping.items():
        if opts["format_output"]:
            fragment, format_notes = format_pretext(fragment)
            for note in format_notes:
                print(f"[FORMAT] {note}")

        if cid == chapter_id:
            this_chapter_output = fragment
            cid_output_path = output_path
        elif output_dir is not None:
            cid_output_path = str(output_dir / f"{cid}.ptx")
        else:
            cid_output_path = None

        if not cid_output_path:
            continue

        cid_path_obj = Path(cid_output_path)

        if apply_mode == "dry_run":
            preview = fragment[:400] + ("..." if len(fragment) > 400 else "")
            print(f"[DRY-RUN] Would write {len(fragment)} chars to {cid_output_path}:\n{preview}")
            continue

        if create_backup and cid_path_obj.exists():
            backup_path = cid_path_obj.with_suffix(".ptx.bak")
            shutil.copy2(cid_path_obj, backup_path)
            print(f"Backup created: {backup_path}")
        cid_path_obj.parent.mkdir(parents=True, exist_ok=True)
        with open(cid_output_path, "w", encoding="utf-8") as f:
            f.write(fragment)
        print(f"Written: {cid_output_path}")

    if not this_chapter_output:
        # This chapter's id wasn't in the split mapping (e.g. count mismatch
        # fell back to the first chapter) -- fall back to whatever was
        # produced for this run so the caller still gets something.
        this_chapter_output = mapping.get(chapter_id, "")

    return {"pretext_output": this_chapter_output, "edit_iterations": 0}


def translate_chapter(
    state: ChapterTranslation,
    config: RunnableConfig = None,
) -> dict[str, Any]:
    """Convert this chapter's Markdown source to PreTeXt via pandoc."""
    opts = resolve_node_opts(state, "translate_chapter", config)
    apply_mode = opts["apply_mode"]
    create_backup = opts["create_backup"]

    source_path = state["source_path"]
    output_path = state.get("output_path")
    chapter_id = state.get("chapter_id") or Path(output_path or "").stem

    if opts["pipeline"] == "tex":
        return _translate_chapter_tex(state, opts, chapter_id, output_path)

    try:
        xml_content, messages = convert_markdown_to_pretext(
            source_path,
            pandoc_exe=opts["pandoc_exe"],
            writer=opts["pandoc_writer"],
        )
    except PandocNotFoundError as exc:
        print(f"[PANDOC] {exc}")
        return _conversion_failure(chapter_id, str(exc))

    # Failure is signalled by empty output, not by non-empty messages —
    # pandoc reports non-fatal warnings on stderr after a successful run.
    if not xml_content:
        detail = "; ".join(messages) or "pandoc produced no output"
        print(f"[PANDOC] Conversion failed for {chapter_id}: {detail}")
        return _conversion_failure(chapter_id, detail)

    for message in messages:
        print(f"[WARN] {message}")

    if opts["format_output"]:
        xml_content, format_notes = format_pretext(xml_content)
        for note in format_notes:
            print(f"[FORMAT] {note}")

    path_obj = Path(output_path)

    if apply_mode == "dry_run":
        preview = xml_content[:400] + ("..." if len(xml_content) > 400 else "")
        print(f"[DRY-RUN] Would write {len(xml_content)} chars to {output_path}:\n{preview}")
    else:
        if create_backup and path_obj.exists():
            backup_path = path_obj.with_suffix(".ptx.bak")
            shutil.copy2(path_obj, backup_path)
            print(f"Backup created: {backup_path}")
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        print(f"Written: {output_path}")

    # A fresh conversion discards any prior edit history for this chapter, so
    # the editing agent gets its full iteration budget back.
    return {"pretext_output": xml_content, "edit_iterations": 0}
