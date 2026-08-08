import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))

from lyretext.orchestration.graph import invoke_workflow_graph

dir = "examples\\intro-statistics\\source"
temp_dir = "examples\\intro-statistics\\temp"
output_dir = "examples\\intro-statistics\\output"

run_id = str(uuid4())

result = invoke_workflow_graph(
    {
        "project_source": dir,
        "temp_dir": temp_dir,
        "output_dir": output_dir,
    },
    config_file="config.yml",
    run_id=run_id,
)

print(f"Run ID: {run_id}")
#print(result)

