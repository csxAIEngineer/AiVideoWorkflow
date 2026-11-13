"""P2 asset loop: retry, critic does not rewrite, machine-check halt.

Run from the repo root:

    python3 -m unittest tests.test_p2_asset_loop
"""

import ast
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.machine_check import ASSET_BLOCK_SCRIPT, check_asset_draft
from Agents.workers.asset import (
    DEFAULT_MAX_ATTEMPTS,
    apply_generator_output,
    compile_p2_asset_graph,
    merge_drafts,
    parse_critic,
    run_p2_asset_loop,
)
from Agents.text import complete_text

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "林浅在雨夜车站等一班车，手里攥着一盒药。"
PASS_ROLE = "\n".join((
    "#### 图1 · 林浅",
    "通过块不要动",
    "16:9",
    "全图共 4 格",
    "五官 10 项锁定",
    "声纹",
    "≤8 词",
    "fifth panel",
    "different face",
))
PASS_PROP = "\n".join((
    "#### 图2 · 药盒",
    "## 药盒道具设定图",
    "整板固定 16:9 横版。",
    "道具设定图。禁止手持。",
    "正面视图。45° 侧视图。顶部俯视图。关键细节特写。",
))
FAIL_CROSS = "\n".join((
    "#### 图1 · 林浅",
    "同上",
    "16:9",
))
FAIL_WITH_KEPT = "\n\n".join((
    PASS_ROLE,
    "\n".join((
        "#### 图2 · 药盒",
        "同上",
        "16:9",
    )),
))
WARN_ONLY = "\n".join((
    "#### 图1 · 某物",
    "16:9",
    "一张静物，没有分格。",
))
GRID_MISSING_LOCK = "\n".join((
    "#### 图3 · 客栈大堂（九宫格）",
    "16:9",
    "【场景定位】客栈大堂",
    "【九宫格内容分配】逐格",
    "【参考场景锁定模式】",
    "全图共 9 格",
    "格内编号 1~9，图外对照表",
    "本场景机位可以变化，建筑不要重画。",
))
ACCEPT = '{"verdict":"accept","issues":[]}'
SENTINEL = "CRITIC-REWRITE-SENTINEL-不要采用"


def _accept(role, _messages):
    if role == "specialist":
        return "节点正文"
    if role in ("generator", "director"):
        return PASS_ROLE
    if role != "critic":
        raise AssertionError(role)
    return ACCEPT


class MachineCheckScriptTest(unittest.TestCase):
    def test_real_script_fails_a1_and_passes_a_closed_role_block(self):
        failed = check_asset_draft(FAIL_CROSS, min_blocks=1)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["exit_code"], 1)
        self.assertTrue(any(item.startswith("A1 ") for item in failed["errors"]), failed)
        self.assertEqual(failed["fatal"], "")

        passed = check_asset_draft(PASS_ROLE + "\n", min_blocks=1)
        self.assertTrue(passed["ok"], passed["report"])
        self.assertEqual(passed["exit_code"], 0)
        self.assertEqual(passed["errors"], [])

    def test_warning_only_block_does_not_fail_the_gate(self):
        report = check_asset_draft(WARN_ONLY, min_blocks=1)
        self.assertTrue(report["ok"], report["report"])
        self.assertTrue(any(item.startswith("A7 ") for item in report["warnings"]), report)

    def test_subprocess_is_only_the_asset_block_script(self):
        calls = []
        real = subprocess.run

        def spy(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            calls.append(list(command))
            return real(*args, **kwargs)

        with patch("Agents.machine_check.subprocess.run", spy):
            check_asset_draft(FAIL_CROSS, min_blocks=1)
        self.assertEqual(len(calls), 1)
        command = " ".join(calls[0])
        self.assertIn(str(ASSET_BLOCK_SCRIPT), command)
        self.assertIn("check_asset_blocks.py", command)
        self.assertNotIn("validate_prompt.py", command)
        self.assertIn("--json", calls[0])
        self.assertIn("--min-blocks", calls[0])

    def test_a3_lock_sentence_is_the_script_failure(self):
        report = check_asset_draft(GRID_MISSING_LOCK, min_blocks=1)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("参考场景锁定模式" in item and item.startswith("A3 ") for item in report["errors"]),
            report["errors"],
        )

    def test_package_does_not_import_end_or_the_feed_validator(self):
        for path in (ROOT / "Agents").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules.append(node.module or "")
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    self.assertFalse(module.startswith("end"), module)
                    self.assertNotIn("validate_prompt", module)

    def test_missing_script_is_fatal(self):
        report = check_asset_draft(PASS_ROLE, script=str(ROOT / "missing-check.py"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["fatal"], "checker_unavailable")


class CriticReportTest(unittest.TestCase):
    def test_rewrite_fields_are_dropped(self):
        raw = json.dumps({
            "verdict": "revise",
            "issues": [{"target": "图1", "severity": "blocking", "detail": "跨条引用要写死"}],
            "draft": "#### 图1 · 林浅\n%s\n" % SENTINEL,
            "image_prompts": "#### 图1 · 林浅\n%s\n" % SENTINEL,
        }, ensure_ascii=False)
        report = parse_critic(raw)
        self.assertEqual(report["verdict"], "revise")
        self.assertTrue(report["stripped_rewrite"])
        self.assertEqual(report["issues"][0]["detail"], "跨条引用要写死")
        self.assertNotIn(SENTINEL, json.dumps(report, ensure_ascii=False))

    def test_a_bare_draft_is_not_a_report(self):
        report = parse_critic("#### 图1 · 林浅\n%s\n16:9\n" % SENTINEL)
        self.assertTrue(report["stripped_rewrite"])
        self.assertNotIn(SENTINEL, json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["issues"][0]["detail"], "审查员没有返回审查报告。")

    def test_blocking_issue_overrides_accept(self):
        report = parse_critic(json.dumps({
            "verdict": "accept",
            "issues": [{"target": "图2", "severity": "blocking", "detail": "缺道具设定图"}],
        }))
        self.assertEqual(report["verdict"], "revise")

    def test_partial_revision_keeps_the_block_the_model_did_not_return(self):
        merged = merge_drafts(FAIL_WITH_KEPT, PASS_PROP)
        self.assertIn("通过块不要动", merged)
        self.assertIn("道具设定图", merged)
        self.assertNotIn("同上", merged)
        self.assertEqual(apply_generator_output(PASS_PROP, previous=FAIL_WITH_KEPT), merged)


class AssetLoopTest(unittest.TestCase):
    def test_graph_is_generate_critique_machine_check(self):
        app = compile_p2_asset_graph(lambda role, messages: "")
        drawn = app.get_graph()
        self.assertTrue({"generate", "critique", "machine_check"} <= set(drawn.nodes))
        edges = {(edge.source, edge.target) for edge in drawn.edges}
        self.assertIn(("__start__", "generate"), edges)
        self.assertIn(("generate", "critique"), edges)
        self.assertIn(("critique", "machine_check"), edges)
        self.assertIn(("machine_check", "generate"), edges)
        self.assertIn(("machine_check", "__end__"), edges)

    def test_machine_pass_halts_without_another_generate(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            return _accept(role, messages)

        result = run_p2_asset_loop(SCRIPT, max_attempts=DEFAULT_MAX_ATTEMPTS, complete=complete)
        self.assertEqual(roles[-2:], ["director", "critic"])
        self.assertGreater(roles.count("specialist"), 1)
        self.assertEqual(roles.count("director"), 1)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["passed"])
        self.assertEqual(result["stop_reason"], "machine_check_pass")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["stage"], "P2")
        self.assertEqual(result["task"], "asset")
        self.assertEqual(result["image_prompts"], result["draft"])
        self.assertIn("通过块不要动", result["draft"])
        self.assertEqual(len(result["history"]), 1)
        self.assertTrue(result["machine_report"]["ok"])

    def test_machine_pass_halts_when_the_critic_still_asks_for_a_revision(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                return PASS_ROLE
            return json.dumps({
                "verdict": "revise",
                "issues": [{"target": "图1", "severity": "blocking", "detail": "名字可以再具体"}],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(SCRIPT, max_attempts=3, complete=complete)
        self.assertEqual(roles[-2:], ["director", "critic"])
        self.assertEqual(roles.count("director"), 1)
        self.assertGreater(roles.count("specialist"), 1)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stop_reason"], "machine_check_pass")
        self.assertEqual(result["critic_report"]["verdict"], "revise")

    def test_machine_fail_halts_at_the_attempt_budget(self):
        roles = []

        def complete(role, messages):
            roles.append(role)
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                return FAIL_CROSS
            return json.dumps({
                "verdict": "revise",
                "issues": [{"target": "图1", "severity": "blocking", "detail": "不要写同上"}],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(SCRIPT, max_attempts=2, complete=complete)
        self.assertEqual(roles.count("critic"), 2)
        self.assertEqual(roles.count("director"), 2)
        cut = roles.index("critic")
        self.assertGreater(roles[:cut].count("specialist"), 1)
        self.assertEqual(roles[cut + 1:], ["director", "critic"])
        self.assertNotIn("machine_check", roles)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["passed"])
        self.assertEqual(result["stop_reason"], "budget_exhausted")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["max_attempts"], 2)
        self.assertEqual(len(result["history"]), 2)
        self.assertTrue(any(item.startswith("A1 ") for item in result["machine_report"]["errors"]))
        self.assertIn("同上", result["draft"])

    def test_retry_uses_the_critic_report_and_the_machine_failures(self):
        prompts = []

        def complete(role, messages):
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                prompts.append(messages[-1]["content"])
                if len(prompts) == 1:
                    return FAIL_CROSS
                return PASS_ROLE
            return json.dumps({
                "verdict": "revise",
                "issues": [{"target": "图1", "severity": "blocking", "detail": "跨条引用要写死"}],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(SCRIPT, max_attempts=3, complete=complete)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["stop_reason"], "machine_check_pass")
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("跨条引用要写死", prompts[0])
        self.assertNotIn("上一稿", prompts[0])
        self.assertIn("上一稿", prompts[1])
        self.assertIn("跨条引用要写死", prompts[1])
        self.assertIn("A1 ", prompts[1])
        self.assertIn("同上", prompts[1])
        self.assertIn("通过块不要动", result["draft"])
        self.assertNotIn("同上", result["draft"])
        self.assertFalse(result["history"][0]["machine_report"]["ok"])
        self.assertTrue(result["history"][1]["machine_report"]["ok"])

    def test_critic_rewrite_is_not_adopted_even_when_that_text_would_pass(self):
        def complete(role, messages):
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                return FAIL_CROSS
            return PASS_ROLE.replace("通过块不要动", SENTINEL)

        result = run_p2_asset_loop(SCRIPT, max_attempts=1, complete=complete)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "budget_exhausted")
        self.assertIn("同上", result["draft"])
        self.assertNotIn(SENTINEL, result["draft"])
        self.assertNotIn(SENTINEL, result["history"][0]["draft"])
        self.assertTrue(result["critic_report"]["stripped_rewrite"])
        self.assertFalse(result["machine_report"]["ok"])
        passed = check_asset_draft(PASS_ROLE.replace("通过块不要动", SENTINEL), min_blocks=1)
        self.assertTrue(passed["ok"], passed["report"])

    def test_critic_rewrite_is_not_shown_to_the_next_generate(self):
        prompts = []

        def complete(role, messages):
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                prompts.append(messages[-1]["content"])
                if len(prompts) == 1:
                    return FAIL_CROSS
                return PASS_ROLE
            return json.dumps({
                "verdict": "revise",
                "draft": "#### 图1 · 林浅\n%s\n" % SENTINEL,
                "image_prompts": SENTINEL,
                "issues": [{"target": "图1", "severity": "blocking", "detail": "跨条引用要写死"}],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(SCRIPT, max_attempts=2, complete=complete)
        self.assertEqual(result["status"], "pass")
        self.assertNotIn(SENTINEL, prompts[1])
        self.assertIn("跨条引用要写死", prompts[1])
        self.assertIn("已丢弃", prompts[1])
        self.assertNotIn(SENTINEL, result["draft"])
        self.assertIn("通过块不要动", result["draft"])

    def test_partial_generate_keeps_the_passing_block(self):
        def complete(role, messages):
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                if "机检失败" not in messages[-1]["content"]:
                    return FAIL_WITH_KEPT
                return PASS_PROP
            return json.dumps({
                "verdict": "revise",
                "issues": [{"target": "图2", "severity": "blocking", "detail": "药盒不要写同上"}],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(SCRIPT, max_attempts=2, complete=complete)
        self.assertEqual(result["status"], "pass", result["machine_report"])
        self.assertIn("通过块不要动", result["draft"])
        self.assertIn("道具设定图", result["draft"])
        self.assertNotIn("同上", result["draft"])
        self.assertEqual([block["number"] for block in result["blocks"]], ["1", "2"])

    def test_generator_json_image_prompts_become_the_draft(self):
        def complete(role, messages):
            if role == "specialist":
                return "节点正文"
            if role in ("generator", "director"):
                return json.dumps({"image_prompts": PASS_ROLE}, ensure_ascii=False)
            return ACCEPT

        result = run_p2_asset_loop(SCRIPT, complete=complete)
        self.assertEqual(result["status"], "pass")
        self.assertIn("#### 图1 · 林浅", result["image_prompts"])

    def test_model_error_burns_one_attempt_and_can_still_pass(self):
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role == "specialist" and calls.count("specialist") == 1:
                raise RuntimeError("boom")
            return _accept(role, messages)

        result = run_p2_asset_loop(SCRIPT, max_attempts=2, complete=complete)
        self.assertEqual(calls[0], "specialist")
        self.assertEqual(calls[-2:], ["director", "critic"])
        self.assertEqual(calls.count("critic"), 1)
        self.assertGreater(calls.count("specialist"), 1)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempts"], 2)
        self.assertIn("boom", result["history"][0]["critic_report"]["issues"][0]["detail"])
        self.assertEqual(result["history"][0]["draft"], "")

    def test_checker_unavailable_stops_without_burning_the_budget(self):
        calls = []

        def complete(role, messages):
            calls.append(role)
            return _accept(role, messages)

        def checker(text, min_blocks):
            return {
                "ok": False,
                "fatal": "checker_unavailable",
                "exit_code": 2,
                "errors": ["缺少机检脚本"],
                "warnings": [],
                "stats": {},
                "blocks": 0,
                "report": "缺少机检脚本",
            }

        result = run_p2_asset_loop(SCRIPT, max_attempts=3, complete=complete, checker=checker)
        self.assertEqual(calls[-2:], ["director", "critic"])
        self.assertEqual(calls.count("director"), 1)
        self.assertEqual(calls.count("critic"), 1)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "checker_unavailable")
        self.assertEqual(result["attempts"], 1)

    def test_missing_script_does_not_call_the_model(self):
        def complete(role, messages):
            raise AssertionError("model should not be called")

        result = run_p2_asset_loop("  \n", complete=complete)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "missing_script")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["draft"], "")

    def test_attempt_budget_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            run_p2_asset_loop(SCRIPT, max_attempts=0, complete=_accept)


class SkillPackPromptTest(unittest.TestCase):
    def test_generator_uses_the_loaded_skill_and_not_a_hardcoded_digest(self):
        source = (ROOT / "Agents" / "workers" / "asset.py").read_text(encoding="utf-8")
        self.assertNotIn("A1 禁止跨条引用", source)
        self.assertNotIn("【九宫格内容分配】", source)
        seen = []

        def complete(role, messages):
            if role == "specialist":
                seen.append(messages[0]["content"])
                return "节点正文"
            if role in ("generator", "director"):
                seen.append(messages[0]["content"])
                return json.dumps({"image_prompts": PASS_ROLE}, ensure_ascii=False)
            user = messages[-1]["content"]
            self.assertNotIn("SKILL-SENTINEL-只在技能里", user)
            self.assertIn("check_asset_blocks.py", user)
            self.assertIn("A1 ", user)
            self.assertNotIn(SENTINEL, user)
            return json.dumps({
                "verdict": "revise",
                "image_prompts": SENTINEL,
                "issues": [],
            }, ensure_ascii=False)

        result = run_p2_asset_loop(
            SCRIPT,
            skill_pack="SKILL-SENTINEL-只在技能里",
            complete=complete,
        )
        self.assertEqual(result["status"], "pass")
        specialists = [item for item in seen if "本节点原文" in item]
        self.assertGreater(len(specialists), 1)
        self.assertTrue(all(item.count("本节点原文") == 1 for item in specialists))
        self.assertTrue(all("SKILL-SENTINEL-只在技能里" not in item for item in specialists))
        director = [item for item in seen if "你是汇总节点" in item and "本节点原文" not in item]
        self.assertEqual(len(director), 1)
        self.assertNotIn("short-drama-director/SKILL.md", director[0])
        self.assertNotIn("本节点原文", director[0])
        self.assertNotIn("SKILL-SENTINEL-只在技能里", director[0])
        self.assertNotIn(SENTINEL, result["draft"])

    def test_missing_skill_pack_does_not_call_the_model(self):
        def complete(role, messages):
            raise AssertionError("model should not be called")

        with patch("AI.skills.registry.load_skill_pack", return_value=""):
            result = run_p2_asset_loop(SCRIPT, skill_pack="", complete=complete)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["stop_reason"], "missing_skill_pack")
        self.assertEqual(result["attempts"], 0)


class TextClientTest(unittest.TestCase):
    def test_loop_uses_the_product_text_client_by_default(self):
        seen = []

        def fake(role, messages, *, model=None, client=None, timeout=None):
            seen.append({"role": role, "model": model, "timeout": timeout})
            return _accept(role, messages)

        with patch("Agents.workers.asset.complete_text", fake):
            result = run_p2_asset_loop(SCRIPT, model="qwen3.8-flash")
        roles = [item["role"] for item in seen]
        self.assertEqual(roles[-2:], ["director", "critic"])
        self.assertGreater(roles.count("specialist"), 1)
        self.assertEqual(roles.count("director"), 1)
        self.assertEqual(seen[0]["model"], "qwen3.8-flash")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["model"], "deepseek-v4-flash-0731")

    def test_complete_text_sends_deepseek_without_reasoning_effort(self):
        class FakeResponses(object):
            def __init__(self):
                self.calls = []
                self.responses = self

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return {"output_text": "ok"}

        fake = FakeResponses()
        text = complete_text(
            "critic",
            [{"role": "user", "content": "审"}],
            model="qwen3.8-flash",
            client=fake,
        )
        self.assertEqual(text, "ok")
        self.assertEqual(fake.calls[0]["model"], "deepseek-v4-flash-0731")
        self.assertEqual(fake.calls[0]["extra_body"], {"enable_thinking": False})
        self.assertNotIn("reasoning", fake.calls[0])
        self.assertEqual(fake.calls[0]["max_output_tokens"], 4000)


if __name__ == "__main__":
    unittest.main()
