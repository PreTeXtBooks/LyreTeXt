import os
import time

from ..utils.prompts import load_prompts
from ..utils.filesystem import create_files_from_json
from .state import SkeletonState
from ..translate.state import ChapterTranslation
from ..config import create_llm, initialise_client, resolve_node_opts
from ..pipeline import PipelineRegistry
from typing import Any
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from .structure import ChapterStructure, TemporaryManifest
from pathlib import Path
_PROMPTS_FILE = Path(__file__).parent / "prompts" / "prompts.md"

def read_chapter(
    state: ChapterTranslation,
    run_config: RunnableConfig | None = None,
) -> dict[Any]:
    opts = resolve_node_opts(state, "read_chapter", run_config)
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

def process_to_markdown(
    state: SkeletonState,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "process_to_markdown", run_config)
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


def upload_project(
    state: SkeletonState,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "upload_project", run_config)
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

def structure_project(
    state: SkeletonState,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "structure_project", run_config)
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



# ---------------------------------------------------------------------------
# P4 — Project resource scanner
# ---------------------------------------------------------------------------

# Patterns we surface as "project resources" in the UI sources subtab
_RESOURCE_PATTERNS: list[tuple[str, str, str]] = [
    ("_bookdown.yml",  "config",   "Bookdown project configuration"),
    ("_output.yml",    "config",   "Output format configuration"),
    ("_common.R",      "script",   "Shared R setup script"),
    ("references.bib", "bib",      "BibTeX references"),
    ("preamble.tex",   "tex",      "LaTeX preamble"),
    ("*.bib",          "bib",      "BibTeX file"),
    ("*.R",            "script",   "R script"),
    ("images",         "dir",      "Images directory"),
    ("figures",        "dir",      "Figures directory"),
]


def scan_project_resources(
    state: SkeletonState,
    run_config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Scan *project_source* for well-known resource files and directories.

    Returns ``{"project_resources": [...]}`` where each entry is
    ``{name, kind, desc, path}``.  Files/dirs that don't exist are skipped.
    """
    source = Path(state.get("project_source", ""))
    if not source.exists():
        return {"project_resources": []}

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pattern, kind, desc in _RESOURCE_PATTERNS:
        if kind == "dir":
            d = source / pattern
            if d.is_dir() and pattern not in seen:
                found.append({"name": pattern, "kind": kind, "desc": desc, "path": str(d)})
                seen.add(pattern)
        else:
            for p in sorted(source.glob(pattern)):
                if p.name not in seen:
                    found.append({"name": p.name, "kind": kind, "desc": desc, "path": str(p)})
                    seen.add(p.name)

    return {"project_resources": found}

