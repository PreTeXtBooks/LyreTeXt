from pydantic import BaseModel, Field
from typing import Literal

BLOCK_TYPES = Literal["HEADING", "PARAGRAPH", "CODE_BLOCK", "MATH_DISPLAY", "LIST", "TABLE", "FIGURE", "YAML_BLOCK"]

class UnitStructure(BaseModel):
    order: int = Field(..., description="1-indexed position in the document")
    type: BLOCK_TYPES = Field(..., description="syntactic type of the unit")
    depth: int = Field(..., description="integer 1–6 for HEADING blocks, null for all others")
    content: str = Field(..., description="raw content of the block, including any markdown or LaTeX syntax")
    #preview: str = Field(..., description="first 80 characters of the raw block content (truncated)")
    #line_start: int = Field(..., description="line number where the block begins")

class ChapterStructure(BaseModel):
    content: list[UnitStructure] = Field(..., description="list of units in the chapter, in order")

class TemporaryFile(BaseModel):
    name: str = Field(..., description="the H1 heading of the file, without the `#` symbol")
    file_name: str = Field(..., description="the name of the file, i.e. name + extension")
    content: str = Field(..., description="markdown content of the file")
    type: str = Field(..., description="type of the file, e.g., 'front_matter', 'chapter', 'appendix' or 'back_matter'")

class TemporaryManifest(BaseModel):
    manifest: list[TemporaryFile] = Field(..., description="list of files in the project, in order of processing")

class ChapterManifest(BaseModel):
    type: Literal["front_matter", "chapter", "back_matter"] = Field(..., description="type of the chapter")
    name: str = Field(..., description="name of the chapter")
    source_path: str = Field(..., description="The full URI of the corresponding source file that the user provided, the one beginning with `https://generativelanguage.googleapis.com/`")

class ProjectManifest(BaseModel):
    manifest: list[ChapterManifest] = Field(..., description="list of chapters in the project, in order")