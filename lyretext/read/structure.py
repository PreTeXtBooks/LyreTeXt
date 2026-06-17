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

class ChapterManifest(BaseModel):
    type: Literal["front_matter", "chapter", "back_matter"] = Field(..., description="type of the chapter")
    name: str = Field(..., description="name of the chapter")
    source_path: str = Field(..., description="The full URI of the corresponding source file that the user provided, the one beginning with `https://generativelanguage.googleapis.com/`")

class ProjectManifest(BaseModel):
    manifest: list[ChapterManifest] = Field(..., description="list of chapters in the project, in order")