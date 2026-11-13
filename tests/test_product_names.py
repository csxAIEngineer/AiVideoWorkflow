"""Lock the packing-stage glossary so old stage copy cannot come back.

Parsers still accept a historical unit heading. New drafts, gate copy,
traces, and generator instructions use the current names.
"""

import importlib.util
import unittest
from pathlib import Path

from AI.qwen_api.revise_graph import _UNIT_HEAD as REVISE_HEAD
from AI.skills.registry import _MERGE_SHAPE
from Agents.workers.feed import CRITIC_SYSTEM, GENERATOR_SYSTEM, critic_messages
from end.crew import CREW
from end.product_names import (
    CANONICAL_HEAD,
    DRAFT_NAME,
    EMPTY_DRAFT,
    EMPTY_DRAFT_REPLY,
    FEED_PASS,
    HEADING_RULE,
    LEGACY_EMPTY_DRAFT,
    LEGACY_HEAD,
    LEGACY_MISSING_UNITS,
    MACHINE_FAIL,
    MACHINE_GATE,
    MISSING_UNITS,
    REWRITE_LABEL,
    STAGE_NAME,
    STORYBOARD_PASS,
    UNIT_NAME,
    WRITE_LABEL,
    is_empty_draft,
    unit_heading,
)
from end.prompt_gate import align_feed_unit_headers, delivery_units
from end.repair_rules.feed_unit_headers import FeedUnitHeaderRule
from end.rerun import _RESUME_LABEL
from end.stage_runner import _FEED_SEEDANCE, _GATE_LABEL, _INSTRUCTION, _gate_copy, _unfinished_write, render_feed_text
from end.trace import _NAME, _WRITE_LABEL

ROOT = Path(__file__).resolve().parents[1]
_BANNED = ("投喂前", "写投喂稿", "## 【投喂单元")
_LOCKED = (
    "end/product_names.py",
    "end/stage_runner.py",
    "end/trace.py",
    "end/rerun.py",
    "end/prompt_gate.py",
    "end/repair_rules/feed_unit_headers.py",
    "end/repair_rules/README.md",
    "end/crew.py",
    "end/server.py",
    "end/revise.py",
    "end/gates.py",
    "end/repair_units.py",
    "end/script_review.py",
    "Agents/checks.py",
    "Agents/workers/feed.py",
    "Agents/workers/asset.py",
    "Agents/workers/script.py",
    "AI/skills/registry.py",
    "AI/qwen_api/revise_graph.py",
    "front/app.js",
    "AI/skills/manju-laoli-skill/sd-feed-engine/scripts/gate_check.py",
    "AI/skills/manju-laoli-skill/short-drama-director/scripts/validate_prompt.py",
    "AI/skills/manju-laoli-skill/short-drama-director/scripts/build_board_lite.py",
    "AI/skills/manju-laoli-skill/short-drama-director/scripts/compile_feed.py",
    "AI/skills/manju-laoli-skill/short-drama-director/scripts/audit_contract.py",
)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GlossaryLockTest(unittest.TestCase):
    def test_gate_trace_and_prompts_use_the_current_names(self):
        self.assertEqual(_gate_copy("storyboard", True, "")[0], STORYBOARD_PASS)
        self.assertEqual(_gate_copy("feed", True, "")[0], FEED_PASS)
        self.assertIn(MACHINE_GATE, FEED_PASS)
        self.assertNotIn("投喂前", FEED_PASS)
        self.assertNotIn("投喂稿", STORYBOARD_PASS)
        reply, options = _gate_copy("feed", False, EMPTY_DRAFT)
        self.assertEqual(reply, EMPTY_DRAFT_REPLY)
        self.assertEqual(options, ["根据建议修改"])
        legacy, _options = _gate_copy("feed", False, LEGACY_EMPTY_DRAFT)
        self.assertEqual(legacy, EMPTY_DRAFT_REPLY)
        failed, _options = _gate_copy("feed", False, "FAIL: H1")
        self.assertTrue(failed.startswith(MACHINE_FAIL))
        unfinished = _unfinished_write("feed", {}, "")
        self.assertEqual(unfinished["stage"], STAGE_NAME)
        self.assertIn("提示词封装没写完", unfinished["message"]["content"])
        self.assertNotIn("投喂前", unfinished["message"]["content"])
        self.assertEqual(_GATE_LABEL["feed"], STAGE_NAME)
        self.assertEqual(_NAME["feed"], STAGE_NAME)
        self.assertEqual(_WRITE_LABEL["feed"], WRITE_LABEL)
        self.assertEqual(CREW[4]["name"], STAGE_NAME)
        self.assertIn(HEADING_RULE, _INSTRUCTION["feed"])
        self.assertIn(HEADING_RULE, _FEED_SEEDANCE)
        self.assertIn(DRAFT_NAME, _INSTRUCTION["feed"])
        self.assertIn(HEADING_RULE, _MERGE_SHAPE["feed"])
        self.assertIn(UNIT_NAME, _MERGE_SHAPE["feed"])
        self.assertIn(HEADING_RULE, GENERATOR_SYSTEM)
        self.assertIn(DRAFT_NAME, GENERATOR_SYSTEM)
        self.assertIn("你是%s审查员" % DRAFT_NAME, CRITIC_SYSTEM)
        pending = critic_messages({"draft": "hello"})[1]["content"]
        self.assertIn("待审%s" % DRAFT_NAME, pending)
        self.assertEqual(_RESUME_LABEL["feed"], REWRITE_LABEL)
        app = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        self.assertIn('name: "提示词封装"', app)
        self.assertIn("正在自动写提示词封装…", app)
        self.assertIn(MACHINE_GATE, app)

    def test_new_heading_is_emitted_and_the_old_one_still_splits(self):
        rendered = render_feed_text({
            "segments": [{
                "time_range": "0:00-0:08",
                "content": {"detailed_description": "[Shot 1] She waits."},
            }],
        })
        self.assertTrue(rendered.startswith(unit_heading(1, 1, 1)))
        self.assertTrue(rendered.startswith(CANONICAL_HEAD))
        old = "## 【投喂单元 1/2】\nsubject_definitions:\n<Subject 1> a woman.\n\n## 【投喂单元 2/2】\none more.\n"
        new = old.replace("投喂单元", UNIT_NAME)
        self.assertEqual(len(delivery_units(old)), 2)
        self.assertEqual(len(delivery_units(new)), 2)
        aligned = align_feed_unit_headers(old)
        self.assertIn(LEGACY_HEAD, aligned)
        self.assertNotIn(CANONICAL_HEAD, aligned)
        inserted = align_feed_unit_headers("subject_definitions:\n<Subject 1> a woman.\n")
        self.assertTrue(inserted.startswith(unit_heading(1, 1)))
        self.assertTrue(is_empty_draft(EMPTY_DRAFT))
        self.assertTrue(is_empty_draft(LEGACY_EMPTY_DRAFT))
        rule = FeedUnitHeaderRule()
        self.assertTrue(rule.match(MISSING_UNITS, "feed", "", {}))
        self.assertTrue(rule.match(LEGACY_MISSING_UNITS + "（旧格式）", "feed", "", {}))
        self.assertTrue(REVISE_HEAD.search(old))
        self.assertTrue(REVISE_HEAD.search(new))

    def test_machine_checks_split_both_headings(self):
        gate = _load(
            ROOT / "AI/skills/manju-laoli-skill/sd-feed-engine/scripts/gate_check.py",
            "gate_check_names",
        )
        board = _load(
            ROOT / "AI/skills/manju-laoli-skill/short-drama-director/scripts/build_board_lite.py",
            "board_names",
        )
        validator = _load(
            ROOT / "AI/skills/manju-laoli-skill/short-drama-director/scripts/validate_prompt.py",
            "validator_names",
        )
        body = (
            "```text\n【画幅风格】16:9\n【场景资产】门\n【核心人物】甲\n"
            "【站位声明】甲在门内\n【时间轴分镜】\n00:00-00:04 [镜头1] 甲。\n"
            "【音效】环境音\n【强制禁止项】无字幕\n```\n"
        )
        for token in (UNIT_NAME, "投喂单元"):
            text = "## 【%s 1/1 · 段1】\n%s" % (token, body)
            units = gate.parse_units(text)
            self.assertEqual(len(units), 1, token)
            parsed = board.parse_md("# 题\n" + text)
            self.assertEqual(len(parsed["segments"]), 1, token)
            self.assertTrue(validator._looks_like_feed(text), token)
        split = "\n".join((
            "## 【封装单元 1/2】",
            "subject_definitions:\n<Subject 1> a woman.",
            "## 【投喂单元 2/2】",
            "subject_definitions:\n<Subject 1> a woman.",
        ))
        pieces = validator._split_feed_segments(split)
        self.assertEqual([label for label, _body in pieces], ["段1", "段2"])
        audit = _load(
            ROOT / "AI/skills/manju-laoli-skill/short-drama-director/scripts/audit_contract.py",
            "audit_names",
        )
        for token in (UNIT_NAME, "投喂单元"):
            sample = "\n".join((
                "## 【%s 1/1】" % token,
                "subject_definitions:",
                "<Subject 1> is a woman.",
                "detailed_description:",
                "[Shot 1] She waits.",
            ))
            issues, _warns, passed = audit.audit_markdown(sample, delivery="feed-only")
            self.assertTrue(any(item.startswith("CT5-P4F") for item in passed), token)
            self.assertFalse(any(item.startswith("CT5-P4F") for item in issues), token)
        source = (ROOT / "AI/skills/manju-laoli-skill/sd-feed-engine/scripts/gate_check.py").read_text(encoding="utf-8")
        self.assertIn(MISSING_UNITS, source)
        skill = (
            ROOT / "AI/skills/manju-laoli-skill/sd-feed-engine/references/feed-mode-and-gates.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## 【封装单元 N/M · 段K】", skill)
        self.assertNotIn("## 【投喂单元", skill)

    def test_banned_stage_copy_stays_out_of_the_locked_files(self):
        for relative in _LOCKED:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for banned in _BANNED:
                self.assertNotIn(banned, text, "%s still contains %s" % (relative, banned))


if __name__ == "__main__":
    unittest.main()
