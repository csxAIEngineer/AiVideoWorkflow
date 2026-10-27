"""Debug mode prints one task graph before that graph is compiled."""

import unittest
from unittest.mock import patch

from AI.qwen_api import suite_graph
from AI.skills.registry import pack_cohorts, suite_files

STORYBOARD = "\n".join((
    "阶段：P3",
    "赛道：1",
    "来源：1",
    "动作档：R2",
    "模型：h3",
    "素材形态：M2",
    "预演：1",
    "任务：分镜",
))


class SuiteGraphDebugTest(unittest.TestCase):
    def test_debug_prints_the_storyboard_graph_before_compile(self):
        files = suite_files("storyboard", STORYBOARD)
        cohorts = pack_cohorts(files)
        self.assertEqual(len(files), 15)
        self.assertLess(len(cohorts), len(files))
        order = []
        printed = []
        real_compile = suite_graph.StateGraph.compile

        def spy_compile(self, *args, **kwargs):
            order.append("compile")
            return real_compile(self, *args, **kwargs)

        def spy_print(*args, **kwargs):
            order.append("print")
            printed.append("\n".join(str(item) for item in args))

        with patch.object(suite_graph, "prompt_debug_enabled", return_value=True), patch.object(
            suite_graph.StateGraph, "compile", spy_compile
        ), patch("builtins.print", spy_print):
            compiled = suite_graph.compile_suite_graph(files, "storyboard", object(), "write")

        self.assertEqual(order, ["print", "compile"])
        text = printed[0]
        self.assertTrue(text.startswith("StateGraph 分镜"))
        self.assertIn("状态 TextState", text)
        self.assertIn("节点 %s" % (len(cohorts) + 1), text)
        for index, cohort in enumerate(cohorts):
            self.assertIn("skill-%s  %s" % (index, " + ".join(cohort)), text)
            self.assertIn("__start__ -> skill-%s" % index, text)
            self.assertIn("skill-%s -> merge" % index, text)
        self.assertIn("merge  汇总", text)
        self.assertIn("merge -> __end__", text)
        self.assertIn("merge", compiled.nodes)
        self.assertEqual(sum(1 for name in compiled.nodes if str(name).startswith("skill-")), len(cohorts))

    def test_structure_stays_quiet_when_debug_is_off(self):
        files = suite_files("board", "任务：离线看板")
        printed = []
        with patch.object(suite_graph, "prompt_debug_enabled", return_value=False), patch(
            "builtins.print", lambda *args, **kwargs: printed.append(args)
        ):
            suite_graph.compile_suite_graph(files, "board", object(), "write")
        self.assertEqual(printed, [])


if __name__ == "__main__":
    unittest.main()
