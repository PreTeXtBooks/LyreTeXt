import os
import time

from ..tools import load_prompts, create_files_from_json
from .state import SkeletonState
from ..translate.state import ChapterTranslation
from ..config import create_llm, initialise_client, resolve_node_opts
from ..pipeline import PipelineRegistry
from typing import Any
from langchain.messages import HumanMessage
from .structure import ChapterStructure, ProjectManifest, TemporaryManifest
from pathlib import Path
_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"

def read_chapter(state: ChapterTranslation) -> dict[Any]:
    opts = resolve_node_opts(state, "read_chapter")
    execution_mode = opts["execution_mode"]
    provider = opts["provider"]
    source_path = state["source_path"]
    print("Reading chapter from:", source_path)
    prompt = str(load_prompts(_PROMPTS_FILE).get("read_chapter"))

    if execution_mode == "direct":
        content = Path(source_path).read_text(encoding="utf-8")
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "text", "text": content},
            ]
        )
    else:
        client = initialise_client(provider)
        myfile = client.files.upload(file=source_path, config={"mime_type": "text/markdown"})
        while myfile.state.name == "PROCESSING":
            time.sleep(2)
            myfile = client.files.get(name=myfile.name)
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "file", "file_id": myfile.uri, "mime_type": "text/markdown"},
            ]
        )

    llm = create_llm(provider).with_structured_output(ChapterStructure.model_json_schema())
    response = llm.invoke([message])
    return {"chapter_structure": response.get("content")}

def process_to_markdown(state: SkeletonState) -> dict[str, Any]:
    opts = resolve_node_opts(state, "process_to_markdown")
    pipeline_name = opts["pipeline"]
    project_source = state["project_source"]
    temp_dir = state.get("temp_dir", "temp_output")

    pipeline = PipelineRegistry.get(pipeline_name)
    if pipeline is None:
        raise ValueError(
            f"Unknown pipeline '{pipeline_name}'. "
            f"Available: {PipelineRegistry.list_available()}"
        )

    result = pipeline.compile_to_markdown(
        project_path=project_source,
        temp_dir=temp_dir,
    )

    if result["errors"]:
        print(f"[WARN] Compilation errors: {result['errors']}")

    return {"project_md_source": result["output_dir"], "temp_dir": temp_dir}


def upload_project(state: SkeletonState) -> dict[str, Any]:
    opts = resolve_node_opts(state, "upload_project")
    execution_mode = opts["execution_mode"]
    provider = opts["provider"]

    if execution_mode == "direct":
        # In direct mode file content is read inline; no upload needed.
        return {"source_files": []}

    client = initialise_client(provider)
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
    opts = resolve_node_opts(state, "structure_project")
    execution_mode = opts["execution_mode"]
    provider = opts["provider"]
    prompt = str(load_prompts(_PROMPTS_FILE).get("project_structurer"))

    if execution_mode == "direct":
        project_md_source = state.get("project_md_source")
        folder = Path(project_md_source)
        file_blocks = []
        for p in sorted(folder.iterdir()):
            if p.is_file():
                content = p.read_text(encoding="utf-8")
                file_blocks.append({"type": "text", "text": f"# File: {p.name}\n\n{content}"})
        print("Structuring project files (direct)...", len(file_blocks))
        message = HumanMessage(
            content=[{"type": "text", "text": prompt}] + file_blocks
        )
    else:
        source_files = state.get("source_files", [])
        print("Structuring project files (upload)...", len(source_files))
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
            ] + [
                {"type": "file", "file_id": file.uri, "mime_type": "text/markdown"}
                for file in source_files
            ]
        )

    llm = create_llm(provider).with_structured_output(TemporaryManifest.model_json_schema())
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
    opts = resolve_node_opts(state, "read_project")
    provider = opts["provider"]
    files = state.get("source_files", [])
    prompt = str(load_prompts(_PROMPTS_FILE).get("read_project"))
    message = HumanMessage(
        content = [
            {"type": "text", "text": prompt},
        ] + 
        [
            {"type": "file", "file_id": file.uri, "mime_type": "text/markdown"} for file in files
        ]
    )
    llm = create_llm(provider).with_structured_output(ProjectManifest.model_json_schema())
    response = llm.invoke([message])
    return {"manifest": response}



