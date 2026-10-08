import env

SPEC = {
    "name": "submit",
    "description": "Submit your work on the current task for grading. Call this once when you are finished.",
    "input_schema": {"type": "object", "properties": {}},
}


def run(input: dict, ctx) -> str:
    env.submit(ctx.task_id)
    ctx.submitted = True
    return "Submitted. The next task will follow."
