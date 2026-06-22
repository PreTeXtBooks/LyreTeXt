import os
import subprocess
from time import time

from ..tools import load_prompts, split_markdown_by_h1, create_files_from_json, find_rscript
from ..orchestration.state import TestState, TranslationState, ChapterTranslation, SkeletonState
from ..config import create_llm
from typing import Any
from langchain.messages import HumanMessage
from google import genai
from .structure import ChapterStructure, ProjectManifest, TemporaryManifest
from pathlib import Path


def read_chapter(state: ChapterTranslation) -> dict[Any]:
    client = genai.Client()
    source_path = state["source_path"]
    print("Reading chapter from:", source_path) 
    myfile = client.files.upload(file=source_path, config={"mime_type": "text/markdown"})
    while myfile.state.name == "PROCESSING":
        time.sleep(2)
        myfile = client.files.get(name=myfile.name)
    prompt = str(load_prompts("lyretext\\read\\prompts\\prompts.md").get("read_chapter"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
            {"type": "file", "file_id": myfile.uri, "mime_type": "text/markdown"}
        ]
    )
    llm = create_llm("gemini").with_structured_output(ChapterStructure.model_json_schema())
    response = llm.invoke([message])
    #print("returning response", response.get("content"))
    return {"chapter_structure": response.get("content")}

def process_to_markdown(state: SkeletonState) -> dict[str, Any]:
    project_source = state["project_source"]
    project_source_path = Path(project_source)
    temp_dir = state.get("temp_dir", "temp_output")
    temp_dir_path = Path(temp_dir) / "markdown"
    if not temp_dir_path.exists():
        temp_dir_path.mkdir(parents=True)
    print("creating at", temp_dir_path)

    rscript = os.environ.get("RSCRIPT_EXE") or find_rscript()

    for file in project_source_path.iterdir():
        if not (file.is_file() and file.suffix in [".rmd", ".qmd"]):
            continue
        destination_file = Path(temp_dir_path) / file.with_suffix(".md").name

        knit_expr = f"knitr::knit('{file.as_posix()}', output='{destination_file.as_posix()}')"
        result = subprocess.run(
            [rscript, "-e", knit_expr],
            text=True,
            capture_output=True,
        )

    return {"project_md_source": temp_dir_path, "temp_dir": temp_dir}


def upload_project(state: SkeletonState) -> dict[str, Any]:
    client = genai.Client()

    project_md_source = state["project_md_source"]
    folder = Path(project_md_source)

    files = []
    for p in folder.iterdir():
        if p.is_file():
            print("Uploading file:", p)
            myfile = client.files.upload(file=p, config={"mime_type": "text/markdown"})
            while myfile.state.name == "PROCESSING":
                time.sleep(2)
                myfile = client.files.get(name=myfile.name)
            files.append(myfile)

    return {"source_files": files}

def structure_project(state: SkeletonState) -> dict[str, Any]:
    source_files = state.get("source_files", [])
    prompt = str(load_prompts("lyretext\\read\\prompts\\prompts.md").get("project_structurer"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
        ] + 
        [
            {"type": "file", "file_id": file.uri, "mime_type": "text/markdown"} for file in source_files
        ]
    )
    llm = create_llm("gemini").with_structured_output(TemporaryManifest.model_json_schema())
    response = llm.invoke([message])
    return {"manifest": response.get("manifest")}

def create_temp_directory(state: SkeletonState) -> dict[str, Any]:
    temp_dir = state.get("temp_dir", "temp_output")
    output_dir = state.get("output_dir")
    file_manifest = state.get("manifest", [])
    if not Path(temp_dir).exists():
        Path(temp_dir).mkdir(parents=True)

    create_files_from_json(temp_dir, file_manifest)

    manifest = [
        {
            "type": file.get("type"), 
            "name": file.get("name"), 
            "source_path": str(os.path.join(temp_dir, file.get("file_name"))),
            "output_path": str(os.path.join(output_dir, file.get("file_name").split(".")[0] + ".ptx"))
        } 
        for file in file_manifest
    ]

    return {"manifest": manifest}



def read_project(state: SkeletonState) -> dict[str, Any]:  
    """
    Deprecated
    """
    files = state.get("source_files", [])          
    prompt = str(load_prompts("lyretext\\read\\prompts\\prompts.md").get("read_project"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
        ] + 
        [
            {"type": "file", "file_id": file.uri, "mime_type": "text/markdown"} for file in files
        ]
    )
    llm = create_llm("gemini").with_structured_output(ProjectManifest.model_json_schema())
    response = llm.invoke([message])
    return {"manifest": response}



