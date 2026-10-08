import unittest

from sia import mock_llm

TASK = "Write the text `hello` to a file named out.txt in the working directory."


def body(*turns, tools=None):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(turns)]
    return {"model": "m", "messages": msgs, "tools": tools or []}


def tool_result(text):
    return [{"type": "tool_result", "tool_use_id": "x", "content": [{"type": "text", "text": text}]}]


def first_tool(resp):
    block = resp["content"][0]
    return block["name"], block["input"]


class MockLLMTest(unittest.TestCase):
    def test_text_of(self):
        self.assertEqual(mock_llm._text_of("s"), "s")
        content = [{"type": "text", "text": "a"}, {"type": "tool_result", "content": "b"}, {"type": "image"}]
        self.assertEqual(mock_llm._text_of(content), "a\nb")

    def test_message_shape(self):
        resp = mock_llm._bash("ls", body(TASK))
        self.assertEqual(resp["role"], "assistant")
        self.assertEqual(resp["stop_reason"], "tool_use")
        self.assertEqual(resp["model"], "m")
        self.assertIn("input_tokens", resp["usage"])

    def test_solve(self):
        name, inp = first_tool(mock_llm.solve(body(TASK)))
        self.assertEqual(name, "bash")
        self.assertIn("printf %s 'hello' > out.txt", inp["command"])
        name, _ = first_tool(mock_llm.solve(body(TASK, "...", tool_result("SOLVED"))))
        self.assertEqual(name, "submit")
        resp = mock_llm.solve(body("Something else"))
        self.assertEqual(resp["stop_reason"], "end_turn")

    def test_self_modify_sequence(self):
        _, inp = first_tool(mock_llm.self_modify(body(TASK)))
        self.assertIn(".mock_patch", inp["command"])
        _, inp = first_tool(mock_llm.self_modify(body(TASK, "...", tool_result("MOCK_PATCH_MISSING"))))
        self.assertIn("harness/tools/note.py", inp["command"])
        # Restart uses the restart tool if offered, else kills the process.
        b = body(TASK, "...", tool_result("MOCK_PATCHED"))
        self.assertEqual(first_tool(mock_llm.self_modify(b))[1]["command"], "kill -TERM $PPID")
        b["tools"] = [{"name": "restart"}]
        self.assertEqual(first_tool(mock_llm.self_modify(b))[0], "restart")
        _, inp = first_tool(mock_llm.self_modify(body(TASK, "...", tool_result("MOCK_PATCH_DONE"))))
        self.assertIn("printf", inp["command"])

    def test_break_harness_sequence(self):
        _, inp = first_tool(mock_llm.break_harness(body(TASK)))
        self.assertIn(".mock_broke", inp["command"])
        _, inp = first_tool(mock_llm.break_harness(body(TASK, "...", tool_result("MOCK_NOT_BROKEN"))))
        self.assertIn("def broken(:", inp["command"])
        _, inp = first_tool(mock_llm.break_harness(body(TASK, "...", tool_result("MOCK_BROKE"))))
        self.assertIn("printf", inp["command"])

    def test_policies_registry(self):
        self.assertEqual(set(mock_llm.POLICIES), {"solve", "self_modify", "break_harness"})


if __name__ == "__main__":
    unittest.main()
