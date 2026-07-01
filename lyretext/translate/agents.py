from pathlib import Path
import shutil
from time import time

from ..tools import load_prompts
from ..orchestration.state import ChapterTranslation
from ..config import create_llm
from typing import Any
from langchain.messages import HumanMessage
from .structure import PreTeXtOutput

def translate_chapter(state: ChapterTranslation) -> dict[str, Any]:
    structure = state["chapter_structure"]
    output_path = state.get("output_path")
    apply_mode = state.get("apply_mode", "auto_apply")
    create_backup = state.get("create_backup", False)
    provider = state.get("provider", "gemini")

    prompt = str(load_prompts("lyretext\\translate\\prompts\\prompts.md").get("translate_chapter"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": str(structure)}
        ]
    )
    llm = create_llm(provider).with_structured_output(PreTeXtOutput.model_json_schema())
    response = llm.invoke([message])
    xml_content = response.get("xml", "")

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

    #return {"pretext_output": response}

def create_folder_structure(state: ChapterTranslation) -> dict[str, Any]:
    structure = state["chapter_structure"]
    prompt = str(load_prompts("lyretext\\translate\\prompts\\prompts.md").get("create_folder_structure"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": str(structure)}
        ]
    )
    llm = create_llm("gemini")
    response = llm.invoke([message])
    return {"folder_structure": response}


