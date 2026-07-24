"""Text and document splitting utilities."""
from __future__ import annotations

import os
import re
from typing import Any


def split_into_sections(content: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    chunks = [chunk.strip() for chunk in content.split("\n\n") if chunk.strip()]
    for index, chunk in enumerate(chunks, start=1):
        sections.append({"id": f"section_{index}", "content": chunk})
    return sections


def merge_pretext_document(translated_sections: list[dict[str, Any]]) -> str:
    body = "\n\n".join(section["content"][0]["text"] for section in translated_sections)
    return f"<pretext>\n{body}\n</pretext>"


def split_markdown_by_h1(input_file: str, output_dir: str) -> None:
    """Split a markdown/rmd/qmd file by H1 headings, preserving preamble and extension."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    _, ext = os.path.splitext(input_file)

    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()

    parts = re.split(r"(^# .+)", content, flags=re.M)

    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1]

        safe_name = re.sub(r"[^\w\s-]", "", heading.replace("#", "").strip())
        safe_name = safe_name.replace(" ", "_").lower()

        file_path = os.path.join(output_dir, f"{safe_name}{ext}")

        if i == 1 and (preamble := parts[0].strip()):
            heading = f"{preamble}\n\n{heading}"

        with open(file_path, "w", encoding="utf-8") as out_f:
            out_f.write(f"{heading}\n\n{body.strip()}")

        print(f"Created: {file_path}")
