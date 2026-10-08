import unittest

from sia import prompts


class PromptsTest(unittest.TestCase):
    def test_levels_are_cumulative(self):
        texts = [prompts.render(level, "/home/agent") for level in range(4)]
        self.assertTrue(texts[0].startswith("You are a coding agent"))
        self.assertIn("/home/agent", texts[0])
        self.assertNotIn("harness", texts[0])
        self.assertIn("/home/agent/harness", texts[1])
        self.assertNotIn("`restart`", texts[1])
        self.assertIn("`restart`", texts[2])
        self.assertNotIn("You may modify", texts[2])
        self.assertIn("You may modify your harness", texts[3])
        for t in texts:
            self.assertTrue(t.endswith("\n"))
            self.assertNotIn("{agent_home}", t)

    def test_has_restart_tool(self):
        self.assertEqual([prompts.has_restart_tool(i) for i in range(4)], [False, False, True, True])


if __name__ == "__main__":
    unittest.main()
