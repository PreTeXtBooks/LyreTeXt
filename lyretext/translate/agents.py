from pathlib import Path
from time import time

from ..tools import load_prompts
from ..orchestration.state import ChapterTranslation
from ..config import create_gemini_llm
from typing import Any
from langchain.messages import HumanMessage
from .structure import PreTeXtOutput

def translate_chapter(state: ChapterTranslation) -> dict[str, Any]:
    structure = state["chapter_structure"]
    output_path = state.get("output_path")
    prompt = str(load_prompts("lyretext\\translate\\prompts\\prompts.md").get("translate_chapter"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": str(structure)}
        ]
    )
    llm = create_gemini_llm().with_structured_output(PreTeXtOutput.model_json_schema())
    response = llm.invoke([message])
    path_obj = Path(output_path)
    # This creates the necessary parent directories if they are missing
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(response.get("xml", ""))
    return {"pretext_output": response}

def create_folder_structure(state: ChapterTranslation) -> dict[str, Any]:
    structure = state["chapter_structure"]
    prompt = str(load_prompts("lyretext\\translate\\prompts\\prompts.md").get("create_folder_structure"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": str(structure)}
        ]
    )
    llm = create_gemini_llm()
    response = llm.invoke([message])
    return {"folder_structure": response}