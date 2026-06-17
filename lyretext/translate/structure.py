from pydantic import BaseModel, Field
from typing import Literal

class PreTeXtOutput(BaseModel):
    xml: str = Field(..., description="PreTeXt XML output for the chapter")