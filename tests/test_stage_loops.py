"""Script, storyboard, and feed workers. Fakes only. No live model calls.

    python3 -m unittest tests.test_stage_loops
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.workers.feed import compile_feed_graph, run_feed_loop
from Agents.workers.script import compile_script_graph, run_script_loop
from Agents.workers.storyboard import compile_storyboard_graph, run_storyboard_loop
from end.stage_runner import (
    _declared_h3_duration,
    _fill_duration_block,
    check_stage,
    execute,
    feed_duration_failures,
    group_shot_durations,
    patch_h3_voiceover,
)

ROOT = Path(__file__).resolve().parents[1]
SHOTS = [8, 5, 5, 4, 4, 4, 5, 6, 4, 5, 5, 5]
BOARD = "\n".join(
    "## S%02d\n动作：动作%s\n景别机位：中景\n时长：%s\n资产：A1" % (index, index, seconds)
    for index, seconds in enumerate(SHOTS, start=1)
)
SHORT_FEED = "\n\n".join(
    "\n".join((
        "## 【投喂单元 %s/4】" % index,
        "duration: %s" % seconds,
        "subject_definitions:",
        "<Subject 1> is a woman.",
        "summary:",
        "She waits.",
        "retention_analysis:",
        "<Subject 1>: fully_preserved.",
        "detailed_description:",
        "[Shot 1] <Subject 1> stands.",
        "overall_soundscape:",
        "Room tone.",
        "non_diegetic_music:",
        "none.",
    ))
    for index, seconds in enumerate((9, 8, 11, 10), start=1)
)
SCRIPT = "林浅在雨夜车站等一班车。"
PASS_SCRIPT = "林浅在雨夜车站等一班车，手里攥着一盒药。\n"
NAMED_BOARD = "\n".join((
    "## S01",
    "动作：她进门",
    "景别机位：中景",
    "资产：A1",
))
FIXED_BOARD = NAMED_BOARD + "\n时长：6\n"
ACCEPT = '{"verdict":"accept","issues":[]}'
SENTINEL = "CRITIC-REWRITE-SENTINEL-不要采用"


def _roles(script_text):
    def complete(role, messages):
        if role == "generator":
            return json.dumps({"script": script_text, "title": "雨夜"}, ensure_ascii=False)
        if role != "critic":
            raise AssertionError(role)
        return ACCEPT

    return complete


class GraphShapeTest(unittest.TestCase):
    def test_each_stage_is_generate_critique_machine_check(self):
        for compile_graph in (compile_script_graph, compile_storyboard_graph, compile_feed_graph):
            drawn = compile_graph(lambda role, messages: "").get_graph()
            self.assertTrue({"generate", "critique", "machine_check"} <= set(drawn.nodes))
            edges = {(edge.source, edge.target) for edge in drawn.edges}
            self.assertIn(("__start__", "generate"), edges)
            self.assertIn(("generate", "critique"), edges)
            self.assertIn(("critique", "machine_check"), edges)
            self.assertIn(("machine_check", "generate"), edges)
            self.assertIn(("machine_check", "__end__"), edges)


class ScriptLoopTest(unittest.TestCase):
    def test_machine_pass_halts_even_when_the_critic_wants_a_rewrite(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            if role == "generator":
                return json.dumps({"script": PASS_SCRIPT}, ensure_ascii=False)
            return json.dumps({
                "verdict": "revise",
                "issues": [{"target": "开头", "severity": "blocking", "detail": "可以再具体"}],
            }, ensure_ascii=False)

        result = run_script_loop(SCRIPT, complete=complete)
        self.assertEqual(roles, ["generator", "critic"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stop_reason"], "machine_check_pass")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["script"], result["draft"])
        self.assertIn("一盒药", result["script"])

    def test_unverified_claim_retries_then_stops_at_the_budget(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            if role == "generator":
                return json.dumps({"script": "画面已核验。"}, ensure_ascii=False)
            return ACCEPT

        result = run_script_loop(SCRIPT, max_attempts=2, complete=complete)
        self.assertEqual(roles, ["generator", "critic", "generator", "critic"])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "budget_exhausted")
        self.assertEqual(result["attempts"], 2)
        self.assertTrue(any("已核验" in item for item in result["machine_report"]["errors"]))

    def test_retry_uses_the_machine_failure_and_the_critic_does_not_rewrite(self):
        prompts = []

        def complete(role, messages):
            if role == "generator":
                prompts.append(messages[-1]["content"])
                if len(prompts) == 1:
                    return json.dumps({"script": "画面已核验。"}, ensure_ascii=False)
                return json.dumps({"script": PASS_SCRIPT}, ensure_ascii=False)
            return json.dumps({
                "verdict": "revise",
                "script": SENTINEL,
                "issues": [{"target": "开头", "severity": "blocking", "detail": "不要写已核验"}],
            }, ensure_ascii=False)

        result = run_script_loop(SCRIPT, max_attempts=3, complete=complete)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn(SENTINEL, result["draft"])
        self.assertNotIn(SENTINEL, prompts[1])
        self.assertIn("不要写已核验", prompts[1])
        self.assertIn("已核验", prompts[1])
        self.assertIn("机检没过", prompts[1])
        self.assertTrue(result["critic_report"]["stripped_rewrite"])

    def test_invented_upload_fails_and_a_missing_story_does_not_call_the_model(self):
        result = run_script_loop(
            "基于上传照片 U0 写成人物。",
            intake=[{"id": "U1", "title": "药盒"}],
            complete=_roles("基于上传照片 U0 写成人物。"),
            max_attempts=1,
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("U0" in item for item in result["machine_report"]["errors"]))

        def explode(role, messages):
            raise AssertionError("model should not be called")

        missing = run_script_loop("  ", complete=explode)
        self.assertEqual(missing["stop_reason"], "missing_script")
        self.assertEqual(missing["attempts"], 0)

    def test_checker_unavailable_stops_without_burning_the_budget(self):
        calls = []

        def complete(role, messages):
            calls.append(role)
            return _roles(PASS_SCRIPT)(role, messages)

        def checker(text, state):
            return {
                "ok": False,
                "fatal": "checker_unavailable",
                "errors": ["缺少机检"],
                "warnings": [],
                "needs_human": False,
            }

        result = run_script_loop(SCRIPT, max_attempts=3, complete=complete, checker=checker)
        self.assertEqual(calls, ["generator", "critic"])
        self.assertEqual(result["stop_reason"], "checker_unavailable")


class StoryboardLoopTest(unittest.TestCase):
    def test_named_field_is_repaired_without_replacing_the_other_shot(self):
        kept = "\n".join((
            "## S02",
            "动作：她坐下",
            "景别机位：近景",
            "时长：4",
            "资产：A1",
        ))
        first = NAMED_BOARD + "\n\n" + kept + "\n"
        prompts = []

        def complete(role, messages):
            if role == "generator":
                prompts.append(messages[-1]["content"])
                if len(prompts) == 1:
                    return json.dumps({"storyboard": first}, ensure_ascii=False)
                return json.dumps({"storyboard": FIXED_BOARD + "\n\n" + kept}, ensure_ascii=False)
            return json.dumps({
                "verdict": "revise",
                "storyboard": SENTINEL,
                "issues": [{"target": "S01", "severity": "blocking", "detail": "S01 缺时长"}],
            }, ensure_ascii=False)

        result = run_storyboard_loop(
            SCRIPT,
            known_ids=["A1"],
            max_attempts=3,
            complete=complete,
        )
        self.assertEqual(result["status"], "pass", result["machine_report"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("她坐下", result["storyboard"])
        self.assertIn("时长：6", result["storyboard"])
        self.assertNotIn(SENTINEL, result["draft"])
        self.assertNotIn(SENTINEL, prompts[1])
        self.assertIn("S01", prompts[1])
        self.assertIn("缺时长", prompts[1])
        self.assertNotIn("补全", prompts[0])

    def test_an_empty_board_stops_for_a_person(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            if role == "generator":
                return json.dumps({"previs": "不是分镜"}, ensure_ascii=False)
            return ACCEPT

        result = run_storyboard_loop(SCRIPT, max_attempts=3, complete=complete)
        self.assertEqual(roles, ["generator", "critic"])
        self.assertEqual(result["stop_reason"], "needs_human")
        self.assertEqual(result["attempts"], 1)
        self.assertIn("分镜是空的", result["machine_report"]["report"])


class FeedDurationTest(unittest.TestCase):
    def test_thirty_eight_seconds_fails_and_the_repair_splits_instead_of_clamping(self):
        errors = feed_duration_failures(SHORT_FEED, BOARD)
        self.assertTrue(errors, errors)
        self.assertIn("时长被压缩", errors[0])
        self.assertIn("60", errors[0])
        self.assertIn("38", errors[0])
        groups = group_shot_durations(SHOTS)
        self.assertEqual(sum(sum(group) for group in groups), 60)
        self.assertTrue(all(sum(group) <= 15 or len(group) == 1 for group in groups))
        self.assertTrue(all(sum(group) <= 15 for group in groups))
        self.assertEqual(_declared_h3_duration(18), 18)
        self.assertEqual(_declared_h3_duration(4.5), 5)
        block = "\n".join((
            "## 【投喂单元 1/1】",
            "detailed_description:",
            "[Shot 1] she sits.",
            "[Shot 2] At 00:11.000, the camera cuts closer.",
        ))
        filled = _fill_duration_block(block, last_shot_seconds=5)
        self.assertIn("duration: 16", filled)
        self.assertNotIn("duration: 15", filled)
        clamped = _fill_duration_block(block + "\nduration: 9\n", last_shot_seconds=5)
        self.assertIn("duration: 9", clamped)
        self.assertNotIn("duration: 16", clamped)

    def test_check_stage_fails_a_short_pack_when_the_validator_only_prints_info(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "分镜.md").write_text(BOARD + "\n", encoding="utf-8")
        (internal / "feed.md").write_text(SHORT_FEED + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps({"shots": [{"id": "S%02d" % index, "duration": seconds} for index, seconds in enumerate(SHOTS, 1)]}),
            encoding="utf-8",
        )
        with patch("end.stage_runner.check_feed", return_value=(0, "[+] 时长38.0s | model=h3")):
            passed, report = check_stage(folder, "feed", {"model": "h3", "material_mode": "M1", "track": "1"})
        self.assertFalse(passed)
        self.assertIn("时长被压缩", report)
        self.assertIn("38", report)

    def test_deleted_dialogue_fails_and_the_loop_repairs_duration(self):
        spoken = BOARD + "\n对白：别改了\n"
        errors = feed_duration_failures(SHORT_FEED, spoken)
        self.assertTrue(any("别改了" in item for item in errors))
        prompts = []

        def checker(text, state):
            found = feed_duration_failures(text, spoken)
            return {
                "ok": not found,
                "errors": found,
                "warnings": ["H4 正文出现中文"],
                "fatal": "",
                "needs_human": False,
            }

        def complete(role, messages):
            if role == "generator":
                prompts.append(messages[-1]["content"])
                if len(prompts) == 1:
                    return json.dumps({"feed": SHORT_FEED}, ensure_ascii=False)
                restored = SHORT_FEED.replace("duration: 9", "duration: 13", 1)
                restored = restored.replace("duration: 8", "duration: 13", 1)
                restored = restored.replace("duration: 11", "duration: 15", 1)
                restored = restored.replace("duration: 10", "duration: 19", 1)
                restored += "\n<d>[Chinese] 别改了</d>\n"
                return json.dumps({"feed": restored}, ensure_ascii=False)
            return json.dumps({
                "verdict": "revise",
                "feed": SENTINEL,
                "issues": [{"target": "时长", "severity": "blocking", "detail": "总时长被缩短"}],
            }, ensure_ascii=False)

        result = run_feed_loop(spoken, shots=[{"duration": seconds} for seconds in SHOTS], max_attempts=3, complete=complete, checker=checker)
        self.assertEqual(result["status"], "pass", result["machine_report"])
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn(SENTINEL, result["feed"])
        self.assertIn("时长被压缩", prompts[1])
        self.assertIn("别改了", result["feed"])
        self.assertIn("总时长被缩短", prompts[1])
        self.assertTrue(result["critic_report"]["stripped_rewrite"])


class VoiceoverPatchTest(unittest.TestCase):
    def _unit(self, body, index=1, total=1):
        return "\n".join((
            "## 【投喂单元 %s/%s】" % (index, total),
            "subject_definitions:",
            "<Subject 1> is a woman.",
            "summary:",
            "She waits.",
            "retention_analysis:",
            "<Subject 1>: fully_preserved.",
            "detailed_description:",
            body,
            "overall_soundscape:",
            "Room tone.",
            "non_diegetic_music:",
            "none.",
        ))

    def test_unofficial_voiceover_gets_lips_on_that_line_not_at_the_end(self):
        raw = "\n\n".join((
            self._unit(
                "A calm voice-over narrates: <d>[Chinese] 别改了。</d>\n"
                "She says: <d>[Chinese] 你好。</d>"
            ),
            self._unit(
                "[Shot 1] She says: <d>[Chinese] 走。</d> while his/her lips remain completely closed.",
                index=2,
                total=2,
            ),
        ))
        patched = patch_h3_voiceover(raw)
        self.assertIn(
            "<Subject 1> (S1) says in an off-screen voiceover: <d>[Chinese] 别改了。</d> while his/her lips remain completely closed",
            patched,
        )
        voice = patched.split("别改了。</d>", 1)[1][:80]
        self.assertIn("lips remain completely closed", voice)
        spoken = patched.split("你好。</d>", 1)[1][:40]
        self.assertNotIn("lips", spoken)
        self.assertFalse(patched.rstrip().endswith("while their lips remain completely closed"))
        self.assertIn("She says: <d>[Chinese] 你好。</d>", patched)

    def test_the_feed_loop_patches_the_voiceover_before_the_gate(self):
        body = self._unit("A calm voice-over narrates: <d>[Chinese] 别改了。</d>")
        seen = {}

        def checker(text, state):
            seen["text"] = text
            official = "says in an off-screen voiceover" in text and "别改了" in text
            lips_at_vo = "别改了。</d> while his/her lips remain completely closed" in text
            return {"ok": official and lips_at_vo, "errors": [] if official and lips_at_vo else ["H9"], "warnings": [], "fatal": "", "needs_human": False}

        def complete(role, messages):
            if role == "generator":
                return json.dumps({"feed": body}, ensure_ascii=False)
            return ACCEPT

        result = run_feed_loop("分镜", complete=complete, checker=checker)
        self.assertEqual(result["status"], "pass", seen)
        self.assertIn("别改了。</d> while his/her lips remain completely closed", result["feed"])
        self.assertLess(
            result["feed"].index("别改了。</d> while his/her lips"),
            result["feed"].index("non_diegetic_music:"),
        )


class EndDispatchTest(unittest.TestCase):
    def test_execute_sends_script_storyboard_and_feed_through_the_loops(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text(PASS_SCRIPT, encoding="utf-8")
        (internal / "assets.md").write_text("#### 图1 · 门\n16:9\n", encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps({"assets": [{"id": "A1", "title": "门"}]}),
            encoding="utf-8",
        )
        calls = []

        def writer(brief, skill_pack="", debug_dir=None, model=None, template=None, trace_prompts=None):
            calls.append(brief)
            if "任务：分镜" in brief or "阶段：storyboard" in brief:
                if "缺时长" in brief or "机检没过" in brief:
                    return {"storyboard": FIXED_BOARD}
                return {"storyboard": NAMED_BOARD}
            raise AssertionError(brief[:160])

        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-flash-0731",
            "brief": SCRIPT,
            "title": "雨夜",
            "progress": {
                "pipeline": {
                    "action": "write",
                    "current": "storyboard",
                    "accepted": ["script", "asset"],
                    "only": "",
                }
            },
        }
        with patch("Agents.text.complete_text", return_value=ACCEPT):
            outcome = execute(project, folder, {"material_mode": "M1", "model": "h3", "track": "1"}, writer, lambda *args, **kwargs: "")
        self.assertTrue(outcome["progress"]["pipeline"]["passed"], outcome["progress"]["pipeline"].get("report"))
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("缺时长", calls[1])
        self.assertNotIn(SENTINEL, (internal / "分镜.md").read_text(encoding="utf-8"))
        self.assertIn("时长：6", (internal / "分镜.md").read_text(encoding="utf-8"))
        self.assertTrue(outcome["progress"]["pipeline"].get("worker_loop"))
        self.assertNotIn("worker_exhausted", outcome["progress"]["pipeline"])


if __name__ == "__main__":
    unittest.main()
