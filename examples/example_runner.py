import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from lyretext.orchestration.graph import build_workflow_graph, resolve_config_for_graph

dir = "examples\\example_rmd_project\\source"
temp_dir = "examples\\example_rmd_project\\temp"
output_dir = "examples\\example_rmd_project\\output"


result = build_workflow_graph().invoke(
    {
        "project_source": dir,
        "temp_dir": temp_dir,
        "output_dir": output_dir,
        **resolve_config_for_graph(config_file="config.yml"),
        #"execution_mode": "direct",
        #"provider": "gemini",
    }
)

print(result)
