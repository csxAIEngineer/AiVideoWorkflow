"""Script worker: machine check, then semantic review, in one loop.

    python3 -m unittest tests.test_script_two_critics
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AI.skills.registry import suite_files
from Agents.workers.feed import compile_feed_graph
from Agents.workers.script import compile_script_graph, run_script_loop
from Agents.workers.storyboard import compile_storyboard_graph
from end.script_review import load_reviews, set_reviewer
from end.stage_runner import execute
from tests.offline_guard import install, uninstall

ROOT = Path(__file__).resolve().parents[1]
STORY = "她把药盒放在桌上。"
SCRIPT = "她把药盒放在桌上。雨还在下。\n"
SENTINEL = "SEMANTIC-REWRITE-SENTINEL"
NAMED = "sd-script-engine/references/roundtable-review.md"


def setUpModule():
    install()


def tearDownModule():
    uninstall()


def _routing():
    return "阶段：P1\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n"


def _script_json(text=SCRIPT):
    return json.dumps({"script": text, "title": "题"}, ensure_ascii=False)


def _complete(roles, prompts, systems=None, text=SCRIPT):
    def complete(role, messages):
        roles.append(role)
        if role == "specialist":
            if systems is not None:
                systems.append(messages[0]["content"])
            return json.dumps({"text": "节点"}, ensure_ascii=False)
        if role == "director":
            prompts.append(messages[-1]["content"])
            return _script_json(text)
        return '{"verdict":"accept","issues":[]}'

    return complete


def _ok(warnings=None):
    return {
        "ok": True,
        "errors": [],
        "warnings": list(warnings or []),
        "fatal": "",
        "needs_human": False,
    }


def _issue(severity, reason, category="causal"):
    return {
        "location": "第1场",
        "evidence": "她把药盒放在桌上",
        "category": category,
        "severity": severity,
        "reason": reason,
        "owner": "script",
        "revision": "只改这一处",
    }


class ScriptGraphOrderTest(unittest.TestCase):
    def test_semantic_review_sits_after_the_machine_check(self):
        drawn = compile_script_graph(lambda role, messages: "").get_graph()
        edges = {(edge.source, edge.target) for edge in drawn.edges}
        self.assertIn(("__start__", "generate"), edges)
        self.assertIn(("generate", "critique"), edges)
        self.assertIn(("critique", "machine_check"), edges)
        self.assertIn(("machine_check", "generate"), edges)
        self.assertIn(("machine_check", "__end__"), edges)
        self.assertIn(("machine_check", "semantic_review"), edges)
        self.assertIn(("semantic_review", "generate"), edges)
        self.assertIn(("semantic_review", "__end__"), edges)
        self.assertNotIn(("critique", "semantic_review"), edges)
        for compile_graph in (compile_storyboard_graph, compile_feed_graph):
            nodes = set(compile_graph(lambda role, messages: "").get_graph().nodes)
            self.assertNotIn("semantic_review", nodes)


class ScriptTwoCriticLoopTest(unittest.TestCase):
    def test_machine_failure_retries_before_semantic_review(self):
        order = []
        roles = []
        prompts = []

        def checker(text, state):
            order.append("machine")
            if order.count("machine") == 1:
                return {
                    "ok": False,
                    "errors": ["没看见图，却写了已核验"],
                    "warnings": [],
                    "fatal": "",
                    "needs_human": False,
                }
            return _ok(["口气可以再收"])

        def reviewer(draft, state):
            order.append("semantic")
            return {"status": "passed", "issues": [], "reason": ""}

        result = run_script_loop(
            STORY,
            max_attempts=2,
            complete=_complete(roles, prompts),
            checker=checker,
            reviewer=reviewer,
        )
        self.assertEqual(order, ["machine", "machine", "semantic"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stop_reason"], "script_gate_pass")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["semantic_report"]["status"], "passed")
        self.assertIn("口气可以再收", result["machine_report"]["warnings"])
        self.assertEqual(roles.count("critic"), 2)

    def test_semantic_block_retries_generate_and_does_not_rewrite(self):
        files = suite_files("script", _routing())
        self.assertIn(NAMED, files)
        self.assertNotIn("sd-script-engine/references/story-bible-and-ip.md", files)
        roles = []
        prompts = []
        systems = []
        seen = []

        def reviewer(draft, state):
            seen.append(draft)
            if len(seen) == 1:
                return {
                    "status": "blocked",
                    "reason": "因果断了，要改 %s" % NAMED,
                    "script": SENTINEL,
                    "issues": [_issue("block", "因果断了")],
                }
            return {"status": "passed", "issues": [], "reason": ""}

        result = run_script_loop(
            STORY,
            max_attempts=3,
            complete=_complete(roles, prompts, systems),
            checker=lambda text, state: _ok(),
            reviewer=reviewer,
        )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(seen), 2)
        self.assertNotIn(SENTINEL, result["draft"])
        self.assertNotIn(SENTINEL, prompts[1])
        self.assertIn("因果断了", prompts[1])
        self.assertIn(NAMED, prompts[1])
        self.assertIn("语义审查报告", prompts[1])
        self.assertNotIn("机检没过", prompts[1])
        self.assertNotIn("语义审查报告", prompts[0])
        first = roles.index("critic")
        self.assertEqual(roles[:first].count("specialist"), len(files))
        self.assertEqual(roles[first + 1:].count("specialist"), 1)
        self.assertIn(NAMED, systems[-1])
        self.assertEqual(roles.count("critic"), 2)

    def test_semantic_failure_retries_generate(self):
        roles = []
        prompts = []
        statuses = ["failed", "passed"]

        def reviewer(draft, state):
            status = statuses.pop(0)
            reason = "语义审查结果不是合格的 JSON，不能当作通过" if status == "failed" else ""
            return {"status": status, "reason": reason, "issues": []}

        result = run_script_loop(
            STORY,
            max_attempts=3,
            complete=_complete(roles, prompts),
            checker=lambda text, state: _ok(),
            reviewer=reviewer,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempts"], 2)
        self.assertIn("不是合格的 JSON", prompts[1])
        self.assertEqual(statuses, [])

    def test_blocked_semantic_review_stops_at_the_attempt_cap(self):
        seen = []

        def reviewer(draft, state):
            seen.append(draft)
            return {
                "status": "blocked",
                "reason": "动机对不上",
                "issues": [_issue("block", "动机对不上", "motivation")],
            }

        result = run_script_loop(
            STORY,
            max_attempts=3,
            complete=_complete([], []),
            checker=lambda text, state: _ok(),
            reviewer=reviewer,
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "semantic_blocked")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(len(seen), 3)
        self.assertFalse(result["passed"])
        self.assertEqual(result["semantic_report"]["status"], "blocked")
        self.assertNotIn(SENTINEL, result["draft"])

    def test_preferences_and_suspects_do_not_fail_the_loop(self):
        def suspect(draft, state):
            return {
                "status": "suspect",
                "reason": "有可疑项",
                "issues": [
                    _issue("preference", "想再短一点", "preference"),
                    _issue("suspect", "动机可能不足", "motivation"),
                ],
            }

        warned = run_script_loop(
            STORY,
            max_attempts=3,
            complete=_complete([], []),
            checker=lambda text, state: _ok(["只是提醒"]),
            reviewer=suspect,
        )
        self.assertEqual(warned["status"], "pass")
        self.assertEqual(warned["stop_reason"], "script_gate_pass")
        self.assertEqual(warned["attempts"], 1)
        self.assertEqual(warned["semantic_report"]["status"], "suspect")
        self.assertIn("只是提醒", warned["machine_report"]["warnings"])

        def preference(draft, state):
            return {
                "status": "passed",
                "reason": "偏好（不阻断）开头：想再短一点",
                "issues": [_issue("preference", "想再短一点", "preference")],
            }

        liked = run_script_loop(
            STORY,
            max_attempts=3,
            complete=_complete([], []),
            checker=lambda text, state: _ok(),
            reviewer=preference,
        )
        self.assertEqual(liked["status"], "pass")
        self.assertEqual(liked["attempts"], 1)
        self.assertEqual(liked["semantic_report"]["status"], "passed")


class EndUsesWorkerReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.calls = []
        self.response = '{"issues":[]}'
        set_reviewer(self._review)

    def tearDown(self):
        set_reviewer(None)
        self.tmp.cleanup()

    def _review(self, messages):
        self.calls.append(messages)
        return self.response

    def _run(self, script):
        def writer(brief, skill_pack="", debug_dir=None, model=None, template=None, trace_prompts=None):
            return {"title": "题", "script": script, "internal": "圆桌"}

        def boom(*args, **kwargs):
            raise AssertionError("End 又跑了一遍语义审查")

        with patch("end.stage_runner.check_stage", return_value=(True, "")), \
                patch("Agents.text.complete_text", return_value='{"verdict":"accept","issues":[]}'), \
                patch("end.script_review.review_script", side_effect=boom):
            return execute(
                {
                    "template": "short-drama-director",
                    "brief": "用户原文：雨夜等车",
                    "model": "fake",
                    "progress": {"pipeline": {"action": "write", "current": "script", "accepted": [], "only": ""}},
                },
                self.folder,
                {"duration_seconds": 60, "aspect": "16:9", "track": "1"},
                writer,
                lambda *args, **kwargs: "pack",
            )

    def test_a_passing_worker_is_not_reviewed_again(self):
        script = "甲在车站等车。她想离开这座城。"
        outcome = self._run(script)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("不改写故事", self.calls[0][0]["content"])
        self.assertIn("causal", self.calls[0][0]["content"])
        self.assertEqual(outcome["progress"]["pipeline"]["semantic_status"], "passed")
        self.assertIn("继续", outcome["message"]["options"])
        self.assertIn("语义审查已通过", outcome["message"]["content"])
        self.assertEqual((self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), script)
        self.assertEqual(load_reviews(self.folder)["script"]["status"], "passed")

    def test_suspects_stay_on_the_worker_report(self):
        script = "甲看着雨。"
        self.response = json.dumps({"issues": [_issue("suspect", "动机可能不足", "continuity")]}, ensure_ascii=False)
        outcome = self._run(script)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(outcome["progress"]["pipeline"]["semantic_status"], "suspect")
        self.assertIn("接受可疑项并继续", outcome["message"]["options"])
        self.assertNotIn("继续", outcome["message"]["options"])
        self.assertIn("可疑", outcome["message"]["content"])
        stored = load_reviews(self.folder)["script"]
        self.assertEqual(stored["status"], "suspect")
        self.assertTrue(stored["issues"])

    def test_exhausted_semantic_block_keeps_the_revise_path(self):
        script = "甲把信交给乙。甲并不知道信的内容。"
        self.response = json.dumps({"issues": [_issue("block", "因果断了")]}, ensure_ascii=False)
        outcome = self._run(script)
        self.assertEqual(len(self.calls), 3)
        pipeline = outcome["progress"]["pipeline"]
        self.assertEqual(pipeline["semantic_status"], "blocked")
        self.assertFalse(pipeline["passed"])
        self.assertIn("根据建议修改", outcome["message"]["options"])
        self.assertNotIn("继续", outcome["message"]["options"])
        self.assertTrue(outcome["message"]["content"].startswith("语义审查未通过"))
        self.assertTrue(outcome["progress"]["message"].startswith("语义审查未通过"))
        self.assertNotIn("机检已过", outcome["progress"]["message"])
        self.assertEqual((self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), script)
        self.assertEqual(load_reviews(self.folder)["script"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
