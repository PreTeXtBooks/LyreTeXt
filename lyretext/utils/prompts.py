"""Prompt loading utilities."""
from __future__ import annotations

from pathlib import Path


def load_prompts(filepath: str | Path) -> dict:
    """
    Load prompts from a markdown file keyed by H2 headings.
    
    If filepath is relative, it is resolved from the project root (the
    directory containing lyretext/). For prompt files that live inside the
    lyretext package, prefer passing an absolute path via Path(__file__).parent.
    """
    path = Path(filepath)
    prompts = {}
    current_heading = None
    current_lines = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                if current_heading:
                    prompts[current_heading] = "".join(current_lines).strip()
                current_heading = line.replace("## ", "").strip()
                current_lines = []
            elif current_heading:
                current_lines.append(line)

        if current_heading:
            prompts[current_heading] = "".join(current_lines).strip()

    return prompts
