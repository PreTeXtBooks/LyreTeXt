from lyretext.pipeline.tex import TexPipeline, _find_main_tex_file
from pathlib import Path

project_source = Path("examples\\my-paper\\source")


result = TexPipeline().compile_to_markdown(
    project_source,
    output_dir="examples\\my-paper\\temp\\markdown",
    temp_dir="examples\\my-paper\\temp",
)


print(result)
