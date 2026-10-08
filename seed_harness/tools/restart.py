from tools import RestartRequested

SPEC = {
    "name": "restart",
    "description": "Restart the harness process. It is relaunched from its source directory and resumes this conversation.",
    "input_schema": {"type": "object", "properties": {}},
}


def run(input: dict, ctx) -> str:
    raise RestartRequested()
