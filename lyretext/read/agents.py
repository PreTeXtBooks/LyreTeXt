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
    config: RunnableConfig = None,
) -> dict[Any]:
    opts = resolve_node_opts(state, "read_chapter", config)
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

def _pipeline_source_files(project_path: Path, pipeline) -> list[Path]:
    """Files directly under *project_path* that *pipeline* knows how to read."""
    if not project_path.is_dir():
        return []
    extensions = {ext.lower() for ext in pipeline.get_supported_extensions()}
    return [
        f for f in sorted(project_path.iterdir())
        if f.is_file() and f.suffix.lower() in extensions
    ]


def _detect_pipeline(project_path: Path, exclude: str) -> str | None:
    """Find a registered pipeline (other than *exclude*) matching the project.

    Registration order is the precedence order, so a bookdown project that
    also ships a preamble.tex still resolves to rmd rather than tex.
    """
    for name in PipelineRegistry.list_available():
        if name == exclude:
            continue
        candidate = PipelineRegistry.get(name)
        if candidate is not None and _pipeline_source_files(project_path, candidate):
            return name
    return None


def process_to_markdown(
    state: SkeletonState,
    config: RunnableConfig = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "process_to_markdown", config)
    pipeline_name = opts["pipeline"]
    project_source = state["project_source"]
    temp_dir = state.get("temp_dir", "temp_output")

    pipeline = PipelineRegistry.get(pipeline_name)
    if pipeline is None:
        raise ValueError(
            f"Unknown pipeline '{pipeline_name}'. "
            f"Available: {PipelineRegistry.list_available()}"
        )

    # The configured pipeline is a project-wide default, so pointing it at a
    # project authored in another format used to fail silently: file discovery
    # matches on extension, found nothing, reported no errors, and the run
    # continued to an empty manifest. Fall back to whichever pipeline actually
    # matches the source, and say so.
    warnings: list[dict[str, Any]] = []
    source_root = Path(project_source)
    if source_root.is_dir() and not _pipeline_source_files(source_root, pipeline):
        detected = _detect_pipeline(source_root, exclude=pipeline_name)
        if detected is not None:
            warnings.append({
                "code": "pipeline_autodetected",
                "message": (
                    f"No {pipeline_name} source files found, but {detected} files were — "
                    f"using the {detected} pipeline for this run. "
                    f"Set Pipeline to {detected} in Settings to make this the default."
                ),
            })
            pipeline_name = detected
            pipeline = PipelineRegistry.get(detected)
        else:
            extensions = ", ".join(pipeline.get_supported_extensions())
            warnings.append({
                "code": "no_source_files",
                "message": (
                    f"No source files found in {project_source}. The {pipeline_name} "
                    f"pipeline looks for {extensions} files directly inside the project "
                    f"folder — check the folder you selected is the one holding them."
                ),
            })

    result = pipeline.compile_to_markdown(
        project_path=project_source,
        temp_dir=temp_dir,
    )

    if result["errors"]:
        print(f"[WARN] Compilation errors: {result['errors']}")
        warnings.extend(
            {"code": "compile_error", "message": str(err)} for err in result["errors"]
        )

    result_dict: dict[str, Any] = {
        "project_md_source": result["output_dir"],
        "temp_dir": temp_dir,
        "read_stage_warnings": warnings,
    }
    if "main_file" in result:
        result_dict["main_file"] = result["main_file"]
    if "project_root" in result:
        result_dict["project_root"] = result["project_root"]
    return result_dict


def upload_project(
    state: SkeletonState,
    config: RunnableConfig = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "upload_project", config)
    execution_mode = opts["execution_mode"]
    provider = opts["provider"]

    print("EXECUTION MODE:", execution_mode)

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
    config: RunnableConfig = None,
) -> dict[str, Any]:
    opts = resolve_node_opts(state, "structure_project", config)
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
    #print("Project structure response:", response)
    return {"manifest": response.get("manifest")}

def create_temp_directory(state: SkeletonState) -> dict[str, Any]:
    temp_dir = state.get("temp_dir", "temp_output")
    output_dir = state.get("output_dir")
    file_manifest = state.get("manifest", [])
    if not Path(temp_dir).exists():
        Path(temp_dir).mkdir(parents=True)

    if not file_manifest:
        project_md_source = state.get("project_md_source")
        folder = Path(project_md_source)
        file_manifest = []
        for p in sorted(folder.iterdir()):
            if p.is_file():
                # LaTeX sources in particular are often latin-1 rather than
                # UTF-8; a decode error here would take down the whole read
                # stage over one stray accented character.
                content = p.read_text(encoding="utf-8", errors="replace")
                file_manifest.append({"name": p.name, "file_name": p.name, "content": content, "type": "chapter"})

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

    # An empty manifest means the gate has nothing to show and no chapter can
    # ever run, so it must not reach the UI as a silently blank table.
    warnings = list(state.get("read_stage_warnings") or [])
    if not manifest and not any(w.get("code") == "empty_manifest" for w in warnings):
        warnings.append({
            "code": "empty_manifest",
            "message": (
                "No chapters were detected in this project, so there is nothing to "
                "translate. Check the project folder and the Pipeline setting."
            ),
        })

    return {"manifest": manifest, "read_stage_warnings": warnings}



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
    ("*.cls",          "style",    "LaTeX class file"),
    ("*.sty",          "style",    "LaTeX package file"),
    ("*.bst",          "style",    "BibTeX style file"),
]


def scan_project_resources(
    state: SkeletonState,
    config: RunnableConfig = None,
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

