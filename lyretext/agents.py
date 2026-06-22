from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from .orchestration.state import TranslationState
from .tools import load_r_markdown, merge_pretext_document, split_into_sections


def read_ingest_agent(state: TranslationState) -> dict[str, Any]:
    source_path = state["source_path"]
    source_content = load_r_markdown(source_path)
    return {"source_content": source_content}


def read_structure_agent(state: TranslationState) -> dict[str, Any]:
    parsed_sections = split_into_sections(state["source_content"])
    print("Parsed sections:", parsed_sections)  # Debug print to verify parsing
    return {"parsed_sections": parsed_sections}


def build_translate_content_agent(llm: BaseChatModel):
    def translate_content_agent(state: TranslationState) -> dict[str, Any]:
        translated_sections: list[dict[str, Any]] = []
        for section in state["parsed_sections"]:
            prompt = (
                "You are a translation agent. Convert the following R Markdown content into "
                "a placeholder PreTeXt-compatible snippet. Keep semantics unchanged.\n\n"
                f"Section ID: {section['id']}\n"
                f"Content:\n{section['content']}"
            )
            response = llm.invoke(prompt)
            translated_sections.append({"id": section["id"], "content": response.content})
        return {"translated_sections": translated_sections}

    return translate_content_agent


def translate_math_agent(state: TranslationState) -> dict[str, Any]:
    normalized_sections: list[dict[str, Any]] = []
    for section in state["translated_sections"]:
        normalized_sections.append(
            {"id": section["id"], "content": section["content"].replace("$$", "<me>").replace("$", "<m>")}
        )
    return {"translated_sections": normalized_sections}


def review_agent(state: TranslationState) -> dict[str, Any]:
    notes = [
        "Placeholder review completed.",
        f"Translated sections counted: {len(state['translated_sections'])}.",
        "Future review checks can be added in this stage.",
    ]
    return {"review_notes": notes}


def enhance_agent(state: TranslationState) -> dict[str, Any]:
    print(state["translated_sections"])
    enhanced_output = merge_pretext_document(state["translated_sections"])
    return {"enhanced_output": enhanced_output}
