"""Filesystem and file I/O utilities."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_r_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as source_file:
        return source_file.read()


def create_files_from_json(target_dir: str, file_data: list[dict[str, str]]) -> None:
    """
    Create files in target_dir from a list of {file_name, content} dicts.
    """
    base_path = Path(target_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    print(file_data, type(file_data))

    for item in file_data:
        file_name = item.get("file_name")
        content = item.get("content", "")

        if file_name:
            file_path = base_path / file_name
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"Successfully created: {file_path}")
            except IOError as e:
                print(f"Error creating {file_name}: {e}")
        else:
            print("Skipping entry: Missing 'file_name' key.")
