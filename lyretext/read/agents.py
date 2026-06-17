from time import time

from ..tools import load_prompts
from ..orchestration.state import TestState, TranslationState, ChapterTranslation, SkeletonState
from ..config import create_gemini_llm
from typing import Any
from langchain.messages import HumanMessage
from google import genai
from .structure import ChapterStructure, ProjectManifest
from pathlib import Path

def read_chapter(state: ChapterTranslation) -> dict[str, Any]:
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
    llm = create_gemini_llm().with_structured_output(ChapterStructure.model_json_schema())
    response = llm.invoke([message])
    #print("returning response", response.get("content"))
    return {"chapter_structure": response.get("content")}

def upload_project(state: SkeletonState) -> dict[str, Any]:
    client = genai.Client()

    project_source = state["project_source"]
    folder = Path(project_source)

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


def read_project(state: SkeletonState) -> dict[str, Any]:  
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
    llm = create_gemini_llm().with_structured_output(ProjectManifest.model_json_schema())
    response = llm.invoke([message])
    return {"manifest": response}



