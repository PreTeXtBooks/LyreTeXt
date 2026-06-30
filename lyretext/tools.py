from __future__ import annotations

import glob
import shutil
from typing import Any
import yaml

import os
import re
from pathlib import Path


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


def split_markdown_by_h1(input_file, output_dir):
    """
    Splits markdown/rmd/qmd files by H1, preserving preamble and extension.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Get the file extension from the original file
    _, ext = os.path.splitext(input_file)

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Regex to split by H1 headings
    # The split includes the delimiter (# Heading)
    parts = re.split(r'(^# .+)', content, flags=re.M)

    #Iterate through H1 sections
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i+1]
        
        # Create a safe filename
        safe_name = re.sub(r'[^\w\s-]', '', heading.replace('#', '').strip())
        safe_name = safe_name.replace(' ', '_').lower()
        
        file_path = os.path.join(output_dir, f"{safe_name}{ext}")

        # Account for any preample before the first H1 heading
        if i == 1 and (preamble:= parts[0].strip()):
            heading = f"{preamble}\n\n{heading}"
        
        with open(file_path, 'w', encoding='utf-8') as out_f:
            out_f.write(f"{heading}\n\n{body.strip()}")
        
        print(f"Created: {file_path}")

def find_rscript() -> str:
	# Prefer PATH, but fall back to common Windows install locations.
	found = shutil.which("Rscript")
	if found:
		return found

	candidates = []
	for base in [
		r"C:\\Program Files\\R",
		r"C:\\Program Files\\R\\R-*",
		r"C:\\Program Files\\R\\R-*\\bin",
		r"C:\\Program Files\\R\\R-*\\bin\\x64",
		r"C:\\Program Files\\R\\R-*\\bin\\x86",
	]:
		candidates.extend(glob.glob(os.path.join(base, "Rscript.exe")))

	if candidates:
		return sorted(candidates)[-1]

	raise FileNotFoundError(
		"Rscript.exe was not found. Add R to PATH or set RSCRIPT_EXE to its full path."
	)

def create_files_from_json(target_dir: str, file_data: list[dict[str, str]]) -> None:
    """
    Creates files in the specified directory based on a list of objects 
    containing 'name' and 'content'.
    
    Args:
        target_dir: The path to the folder where files should be created.
        file_data: A list of dictionaries, e.g., [{"name": "test.txt", "content": "hello"}]
    """
    # Convert string path to a Path object and ensure it exists
    base_path = Path(target_dir)
    base_path.mkdir(parents=True, exist_ok=True)
    
    print(file_data, type(file_data))

    for item in file_data:
        file_name = item.get("file_name")
        content = item.get("content", "")
        
        if file_name:
            # Construct full path and write the file
            file_path = base_path / file_name
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"Successfully created: {file_path}")
            except IOError as e:
                print(f"Error creating {file_name}: {e}")
        else:
            print("Skipping entry: Missing 'name' key.")

def get_before_chapter_scripts(project_dir: str = ".") -> list[str]:
    """
    Parses _bookdown.yml to extract any 'before_chapter_script' paths.
    Always returns a list of strings for consistent downstream iteration.
    """
    yaml_path = os.path.join(project_dir, "_bookdown.yml")
    
    # 1. Check if the configuration file actually exists
    if not os.path.exists(yaml_path):
        print(f"[*] No _bookdown.yml found at {yaml_path}. Skipping.")
        return []
        
    try:
        # 2. Read and parse the YAML safely
        with open(yaml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        if not config:
            return []
            
        # 3. Extract the target key
        scripts = config.get("before_chapter_script", [])
        
        # 4. Normalize the output: YAML allows a single string or a list.
        #    We force it to always be a list so your loop doesn't break.
        if isinstance(scripts, str):
            return [scripts]
        elif isinstance(scripts, list):
            return [str(s) for s in scripts]
        else:
            return []
            
    except yaml.YAMLError as e:
        print(f"[!] Error parsing YAML file: {e}")
        return []
    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        return []

# Example usage:
# data = [
#     {"name": "readme.txt", "content": "This is a test file."},
#     {"name": "config.json", "content": '{"status": "active"}'}
# ]
# create_files_from_json("./my_output_dir", data)