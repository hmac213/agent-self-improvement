"""Agent harness: the loop that drives the model.

Started by start.sh. Reads the current task from the supervisor's mailbox
(see env.py), calls the model, runs the tools it asks for, and checkpoints
after every step (see state.py) so it can resume if the process restarts.
"""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import anthropic  # noqa: E402

import env  # noqa: E402
import state as state_mod  # noqa: E402
import tools as tools_mod  # noqa: E402
from llm import LLM  # noqa: E402

NUDGE = "Continue working on the task. Call submit when you are finished."


def log(msg: str) -> None:
    print(f"[harness {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def add_user_text(st: state_mod.State, texts: list[str]) -> None:
    """Attach text to the next user turn (the trailing user message, or a new one)."""
    blocks = [{"type": "text", "text": t} for t in texts]
    if st.messages and st.messages[-1]["role"] == "user":
        st.messages[-1]["content"].extend(blocks)
    else:
        st.messages.append({"role": "user", "content": blocks})


def run_tools(st, response_content, toolset, ctx) -> bool:
    """Run requested tools and append their results. Returns True if a restart was requested."""
    results, restart = [], False
    for block in response_content:
        if block.get("type") != "tool_use":
            continue
        tool = toolset.get(block["name"])
        try:
            if tool is None:
                raise ValueError(f"unknown tool {block['name']!r}")
            output, is_error = tool.run(block["input"], ctx), False
        except tools_mod.RestartRequested:
            output, is_error, restart = "Restarting harness...", False, True
        except Exception as e:  # report tool failures to the model
            output, is_error = f"{type(e).__name__}: {e}", True
        results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output, "is_error": is_error})
    st.messages.append({"role": "user", "content": results})
    return restart


def main() -> int:
    cfg = json.loads((HERE / "config.json").read_text())
    system = (HERE / "system_prompt.md").read_text()
    toolset = tools_mod.load_all()
    tool_specs = [t.SPEC for t in toolset.values()]
    llm = LLM(cfg)

    st, resumed = state_mod.load()
    if resumed:
        st.close_dangling_tool_calls("Interrupted: the harness process restarted before this call returned.")
        st.pending.append(f"[Harness restarted (generation {os.environ.get('SIA_GENERATION', '?')}); resumed from checkpoint.]")
        st.save()
    log(f"started; resumed={resumed}; tools={sorted(toolset)}")

    while True:
        task = env.current_task()
        if task is None:
            time.sleep(1)
            continue
        if task.get("done"):
            log("no more tasks; exiting")
            st.save()
            return 0
        if task["task_id"] != st.task_id:
            log(f"starting task {task['task_id']}")
            st.start_task(task)
            st.save()
        if st.submitted:
            time.sleep(1)
            continue

        notices, st.notice_offset = env.read_notices(st.notice_offset)
        texts = st.pending + [f"[Notice] {n}" for n in notices]
        if st.messages[-1]["role"] == "assistant":
            texts.append(NUDGE)
        if texts:
            add_user_text(st, texts)
        st.pending = []
        st.save()

        try:
            response = llm.call(system, st.messages, tool_specs)
        except anthropic.APIStatusError as e:
            # e.g. a budget limit; wait and re-check the task, which may have moved on.
            log(f"API error {e.status_code}: {e.message}")
            time.sleep(5)
            continue
        content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
        if not content:  # e.g. a refusal; an empty assistant turn would be rejected
            content = [{"type": "text", "text": f"(empty response, stop_reason={response.stop_reason})"}]
        st.messages.append({"role": "assistant", "content": content})
        st.save()
        log(f"model: stop_reason={response.stop_reason} usage=in:{response.usage.input_tokens} out:{response.usage.output_tokens}")

        if any(b.get("type") == "tool_use" for b in content):
            ctx = tools_mod.Context(workdir=Path(task["workdir"]), task_id=st.task_id, cfg=cfg)
            restart = run_tools(st, content, toolset, ctx)
            st.submitted = ctx.submitted
            st.save()
            if restart:
                log("restart requested")
                return 75


if __name__ == "__main__":
    sys.exit(main())
