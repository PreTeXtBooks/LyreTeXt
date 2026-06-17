from __future__ import annotations

from typing import Any


def load_r_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as source_file:
        return source_file.read()
    
def load_prompts(filepath: str) -> dict:
    prompts = {}
    current_heading = None
    current_lines = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            # Look for H2 headings (## Prompt Name)
            if line.startswith("## "):
                # Save the previous prompt before starting a new one
                if current_heading:
                    prompts[current_heading] = "".join(current_lines).strip()
                
                # Extract the new prompt name
                current_heading = line.replace("## ", "").strip()
                current_lines = []
            elif current_heading:
                # Append lines belonging to the current prompt
                current_lines.append(line)

        # Don't forget to save the very last prompt in the file
        if current_heading:
            prompts[current_heading] = "".join(current_lines).strip()

    return prompts


def split_into_sections(content: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    chunks = [chunk.strip() for chunk in content.split("\n\n") if chunk.strip()]
    for index, chunk in enumerate(chunks, start=1):
        sections.append({"id": f"section_{index}", "content": chunk})
    return sections


def merge_pretext_document(translated_sections: list[dict[str, Any]]) -> str:
    body = "\n\n".join(section["content"][0]["text"] for section in translated_sections)
    return f"<pretext>\n{body}\n</pretext>"
