"""Worker generator complete() is one model call, never a write suite.

    python3 -m unittest tests.test_worker_complete
"""

import inspect
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from Agents.worker import bound_complete, revision_brief
from Agents.workers.asset import _bound_complete, run_p2_asset_loop
from Agents.workers.feed import extract_feed, run_feed_loop
from Agents.workers.script import run_script_loop
from Agents.workers.storyboard import run_storyboard_loop
from AI.skills.registry import suite_files, suite_task_for
from end.product_names import unit_heading
from end.stage_runner import _worker_complete as production_worker_complete

ACCEPT = '{"verdict":"accept","issues":[]}'
FEED_V1 = unit_heading(1, 1) + "\n第一版封装正文。\n"
FEED_V2 = unit_heading(1, 1) + "\n改过的封装正文。\n"
ROUTE = "阶段：P4\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n"
BOARD = "\n".join((
    "## S01",
    "动作：她推门",
    "景别机位：中景",
    "时长：6",
    "资产：A1",
))
PASS = {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False, "report": ""}


def _bomb(name):
    def explode(*_args, **_kwargs):
        raise AssertionError("generator complete 不能进入 %s" % name)

    return explode


@contextmanager
def _one_node(fake):
    """Patch the suite entry points. ``fake`` is the single-shot complete_text."""
    targets = {
        "AI.qwen_api.suite_graph.run_suite": "run_suite",
        "AI.agent.layout.run_suite": "layout.run_suite",
        "AI.qwen_api.repair_graph.run_repair": "run_repair",
        "AI.qwen_api.repair_graph.compile_repair_graph": "compile_repair_graph",
        "AI.qwen_api.repair_graph.repair_job_nodes": "repair_job_nodes",
        "AI.api.write": "AI.write",
        "Agents.api.write": "Agents.write",
        "AI.qwen_api.graph.generate_text": "generate_text",
        "AI.agent.layout.suite_task_for": "layout.suite_task_for",
        "AI.skills.registry.suite_task_for": "suite_task_for",
    }
    patches = [patch("Agents.text.complete_text", side_effect=fake)]
    for target, name in targets.items():
        patches.append(patch(target, side_effect=_bomb(name)))
    started = []
    for item in patches:
        item.start()
        started.append(item)
    try:
        yield
    finally:
        for item in reversed(started):
            item.stop()


def _write_model(*_args, **_kwargs):
    raise AssertionError("generator complete 不能调用 write")


def _factory():
    project = {"model": "deepseek-v4-flash-0731", "template": "short-drama-director", "progress": {}}
    return production_worker_complete(project, Path("."), _write_model, "")


class WorkerNodeCompleteTest(unittest.TestCase):
    def test_production_complete_does_not_call_the_write_suite(self):
        source = inspect.getsource(production_worker_complete)
        self.assertIn("complete_text", source)
        self.assertNotIn("_write_model(", source)
        self.assertNotIn("run_suite", source)
        self.assertNotIn("suite_task_for", source)
        self.assertNotIn("run_repair", source)
        self.assertIn("complete_text", inspect.getsource(_bound_complete))
        self.assertIn("complete_text", inspect.getsource(bound_complete))
        # The marker that used to relaunch a suite is still the repair marker.
        # The worker must not consult it.
        self.assertEqual(suite_task_for("任务：建议修补\n阶段：feed\n"), "repair")
        self.assertEqual(suite_task_for("任务：提示词封装\n阶段：P4\n"), "feed")

    def test_director_feed_is_the_worker_draft(self):
        seen = []

        def fake(role, messages, model=None, client=None, timeout=None):
            seen.append(role)
            if role == "director":
                self.assertIn("你是汇总节点", messages[0]["content"])
                self.assertNotIn("short-drama-director/SKILL.md", messages[0]["content"])
                return json.dumps({"feed": FEED_V1}, ensure_ascii=False)
            if role == "critic":
                return ACCEPT
            self.assertEqual(role, "specialist")
            self.assertEqual(messages[0]["content"].count("本节点原文"), 1)
            self.assertIn("任务：提示词封装", messages[-1]["content"])
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        with _one_node(fake):
            result = run_feed_loop(
                "分镜正文",
                opening="任务：提示词封装\n分镜：\n她推门。",
                max_attempts=1,
                complete=_factory(),
                checker=lambda text, state: dict(PASS),
            )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["draft"], FEED_V1.strip())
        self.assertEqual(result["feed"], FEED_V1.strip())
        self.assertGreaterEqual(seen.count("specialist"), 1)
        self.assertEqual(seen.count("director"), 1)
        self.assertEqual(seen.count("critic"), 1)
        self.assertLess(seen.index("director"), seen.index("critic"))
        self.assertEqual(extract_feed(json.dumps({"text": FEED_V2}, ensure_ascii=False)), FEED_V2.strip())
        self.assertEqual(
            extract_feed(json.dumps({"feed": FEED_V1, "text": FEED_V2}, ensure_ascii=False)),
            FEED_V1.strip(),
        )

    def test_unnamed_repair_does_not_launch_an_empty_repair_graph(self):
        """A revision brief starts with 任务：建议修补 and names no skill file.

        That used to select the repair graph with units=0. Named nodes stay on
        per-node complete(); with no name, only the director runs.
        """
        directors = {"n": 0}
        seen = []

        def fake(role, messages, model=None, client=None, timeout=None):
            user = messages[-1]["content"]
            seen.append((role, user))
            if role == "director":
                directors["n"] += 1
                if directors["n"] > 1:
                    self.assertTrue(user.find("任务：建议修补") >= 0)
                    self.assertIn("FAIL: H1 缺段", user)
                body = FEED_V1 if directors["n"] == 1 else FEED_V2
                return json.dumps({"feed": body}, ensure_ascii=False)
            if role == "critic":
                return ACCEPT
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        def checker(text, state):
            if int(state.get("attempt") or 0) < 2:
                return {
                    "ok": False,
                    "errors": ["FAIL: H1 缺段"],
                    "warnings": [],
                    "fatal": "",
                    "needs_human": False,
                    "report": "FAIL: H1 缺段",
                }
            return dict(PASS)

        with _one_node(fake):
            result = run_feed_loop(
                "分镜正文",
                opening="任务：提示词封装\n分镜：\n她推门。",
                max_attempts=2,
                complete=_factory(),
                checker=checker,
            )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["draft"], FEED_V2.strip())
        roles = [role for role, _user in seen]
        first_critic = roles.index("critic")
        second = roles[first_critic + 1:]
        self.assertEqual(second.count("specialist"), 0)
        self.assertEqual(second.count("director"), 1)
        self.assertTrue(revision_brief("feed", "任务：提示词封装", FEED_V1, {}, {
            "errors": ["FAIL: H1 缺段"],
            "warnings": [],
        }).startswith("任务：建议修补"))

    def test_named_repair_reruns_that_node_and_not_a_repair_graph(self):
        named = suite_files("feed", ROUTE)[0]
        seen = []

        def fake(role, messages, model=None, client=None, timeout=None):
            seen.append((role, messages[0]["content"]))
            if role == "director":
                return json.dumps({"feed": FEED_V2}, ensure_ascii=False)
            if role == "critic":
                return ACCEPT
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        def checker(text, state):
            if int(state.get("attempt") or 0) < 2:
                return {
                    "ok": False,
                    "errors": ["%s 没写完" % named],
                    "warnings": [],
                    "fatal": "",
                    "needs_human": False,
                    "report": "%s 没写完" % named,
                }
            return dict(PASS)

        with _one_node(fake):
            result = run_feed_loop(
                "分镜正文",
                opening="任务：提示词封装\n" + ROUTE,
                max_attempts=2,
                complete=_factory(),
                checker=checker,
            )
        self.assertEqual(result["status"], "pass", result)
        roles = [role for role, _system in seen]
        first_critic = roles.index("critic")
        second = seen[first_critic + 1:]
        specialists = [system for role, system in second if role == "specialist"]
        self.assertEqual(len(specialists), 1)
        self.assertIn(named, specialists[0])
        self.assertEqual(sum(role == "director" for role, _system in second), 1)

    def test_script_storyboard_and_asset_use_the_same_one_node_complete(self):
        def fake(role, messages, model=None, client=None, timeout=None):
            if role == "critic":
                return ACCEPT
            if role == "specialist":
                self.assertEqual(messages[0]["content"].count("本节点原文"), 1)
                return json.dumps({"text": "节点"}, ensure_ascii=False)
            self.assertEqual(role, "director")
            self.assertIn("你是汇总节点", messages[0]["content"])
            return json.dumps({
                "script": "她把药盒放在桌上。",
                "title": "雨夜",
                "internal": "圆桌",
                "storyboard": BOARD,
                "image_prompts": "#### 图1 · 林浅\n16:9\n",
                "feed": FEED_V1,
            }, ensure_ascii=False)

        def passed(text, state):
            return dict(PASS)

        with _one_node(fake):
            complete = _factory()
            script = run_script_loop(
                "她把药盒放在桌上。",
                max_attempts=1,
                complete=complete,
                checker=passed,
                reviewer=lambda draft, state: {"status": "passed", "issues": [], "reason": ""},
            )
            board = run_storyboard_loop(
                "她把药盒放在桌上。",
                known_ids=["A1"],
                max_attempts=1,
                complete=complete,
                checker=passed,
            )
            asset = run_p2_asset_loop(
                "她把药盒放在桌上。",
                max_attempts=1,
                complete=complete,
                checker=lambda text, min_blocks: {
                    "ok": "图1" in text,
                    "errors": [],
                    "warnings": [],
                    "exit_code": 0,
                    "fatal": "",
                    "stats": {},
                    "blocks": 1,
                    "report": "",
                },
            )
        self.assertEqual(script["status"], "pass", script)
        self.assertIn("药盒", script["draft"])
        self.assertEqual(board["status"], "pass", board)
        self.assertIn("时长：6", board["draft"])
        self.assertEqual(asset["status"], "pass", asset.get("machine_report"))
        self.assertIn("图1", asset["draft"])


if __name__ == "__main__":
    unittest.main()
