"""Rule repairs run before the model and do not spend an AI attempt."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.gates import GATE_CHECK, check_feed, check_gate
from end.locks import prepare_locks, write_locks
from end.repair_rules import run_rules
from end.server import run_project
from tests.test_auto_gates import _ready_locks
from tests.test_director_pipeline import make_project

_MISSING = "未找到投喂单元（## 【投喂单元 N/M】格式）"
_STILL = _MISSING + "\n[-] FAIL: 缺【画幅风格】"
_SEEDANCE = "\n".join((
    "【画幅风格】16:9 写实",
    "【时间轴分镜】",
    "【镜头1】00:00-00:04 她站在门内。",
))
_BARE_HEAD = "\n".join((
    "## 投喂单元 1/1",
    "【画幅风格】16:9 写实",
    "【时间轴分镜】",
    "【镜头1】00:00-00:04 她站在门内。",
))
_H3 = "\n".join((
    "subject_definitions",
    "<Subject 1> is a woman standing indoors.",
    "summary",
    "She waits by the door.",
    "retention_analysis",
    "<Subject 1>: fully_preserved for the woman.",
    "detailed_description",
    "[Shot 1] At 00:00.000, she stands inside.",
    "overall_soundscape",
    "Room tone.",
    "non_diegetic_music",
    "none.",
))


def _parse_units(text):
    spec = importlib.util.spec_from_file_location("gate_check_for_rules", GATE_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_units(text)


def _apply(text, report=_MISSING):
    updated, applied = run_rules("feed", report, text, {})
    return updated, applied


class FeedUnitHeaderRuleTest(unittest.TestCase):
    def test_missing_headers_become_units_the_checker_can_split(self):
        self.assertEqual(_parse_units(_SEEDANCE), [])
        folder = Path(tempfile.mkdtemp())
        raw = folder / "raw.md"
        raw.write_text(_SEEDANCE, encoding="utf-8")
        _code, before = check_gate(raw, "2.5", "M1")
        self.assertIn(_MISSING, before)
        updated, applied = _apply(_SEEDANCE)
        self.assertEqual(applied, ["feed-unit-headers"])
        self.assertIn("## 【投喂单元 1/1】", updated)
        self.assertEqual(len(_parse_units(updated)), 1)
        fixed = folder / "fixed.md"
        fixed.write_text(updated, encoding="utf-8")
        _after_code, after = check_gate(fixed, "2.5", "M1")
        self.assertNotIn("未找到投喂单元", after)
        again, second = _apply(updated)
        self.assertEqual(second, [])
        self.assertEqual(again.strip(), updated.strip())

    def test_a_head_without_brackets_is_rewritten(self):
        self.assertEqual(_parse_units(_BARE_HEAD), [])
        updated, applied = _apply(_BARE_HEAD)
        self.assertEqual(applied, ["feed-unit-headers"])
        self.assertIn("## 【投喂单元 1/1】", updated)
        self.assertNotIn("## 投喂单元 1/1\n", updated)
        self.assertEqual(len(_parse_units(updated)), 1)

    def test_loose_h3_gets_a_unit_head_and_loses_that_failure(self):
        updated, applied = _apply(_H3)
        self.assertEqual(applied, ["feed-unit-headers"])
        self.assertIn("## 【投喂单元", updated)
        path = Path(tempfile.mkdtemp()) / "feed.md"
        path.write_text(updated, encoding="utf-8")
        _code, report = check_feed(path, "h3", "M1", "1")
        self.assertNotIn("未找到投喂单元", report)

    def test_other_failures_and_other_stages_are_left_alone(self):
        updated, applied = run_rules("feed", "FAIL: H1 缺段", _SEEDANCE, {})
        self.assertEqual(applied, [])
        self.assertEqual(updated, _SEEDANCE)
        updated, applied = run_rules("asset", _MISSING, _SEEDANCE, {})
        self.assertEqual(applied, [])


class RuleBeforeModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "rules.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _gate(self, project_id, body, report):
        locks = _ready_locks()
        make_project(
            project_id,
            status="writing",
            progress={
                "percent": 76,
                "phase": "gate_feed",
                "message": "投喂前",
                "lock_machine": True,
                "pipeline": {
                    "current": "feed",
                    "accepted": ["script", "asset", "storyboard"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "repairs": 0,
                    "report": report,
                    "repair": report,
                },
            },
        )
        folder = project_dir(project_id)
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "feed.md").write_text(body, encoding="utf-8")
        return folder

    def test_a_fixable_header_does_not_call_the_model(self):
        folder = self._gate("p-rule-fix", _SEEDANCE, _MISSING)
        calls = []

        def writer(*_args, **_kwargs):
            calls.append("ai")
            raise AssertionError("规则已经能改，不应叫模型")

        def check(folder, stage, locks, title=""):
            text = (Path(folder) / "_内部_" / "feed.md").read_text(encoding="utf-8")
            if stage == "feed" and "## 【投喂单元" not in text:
                return False, _MISSING
            return True, ""

        with patch("end.stage_runner.check_stage", check), patch(
            "end.stage_runner.check_prompt", return_value=(0, "PASSED")
        ), patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-rule-fix")
        saved = read_project("p-rule-fix")
        stored = (folder / "_内部_" / "feed.md").read_text(encoding="utf-8")
        self.assertEqual(calls, [])
        self.assertIn("## 【投喂单元 1/1】", stored)
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompts_ready")
        self.assertGreaterEqual((saved["progress"].get("pipeline") or {}).get("rule_repairs") or 0, 1)
        self.assertEqual((saved["progress"].get("pipeline") or {}).get("repairs") or 0, 0)

    def test_machine_check_is_cleared_by_the_rule_without_a_model(self):
        folder = self._gate("p-rule-machine", _SEEDANCE, _MISSING)
        project = read_project("p-rule-machine")
        project["progress"]["pipeline"]["check"] = "machine"
        write_project(project)
        calls = []

        def writer(*_args, **_kwargs):
            calls.append("ai")
            raise AssertionError("投喂机检已被规则修好")

        def check_prompt(path, model, track):
            del model, track
            text = Path(path).read_text(encoding="utf-8")
            if "## 【投喂单元" not in text:
                return 1, _MISSING
            return 0, "PASSED"

        with patch("end.stage_runner.check_prompt", check_prompt), patch(
            "end.server.call_deepseek", writer
        ), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-rule-machine")
        saved = read_project("p-rule-machine")
        stored = (folder / "_内部_" / "feed.md").read_text(encoding="utf-8")
        self.assertEqual(calls, [])
        self.assertIn("## 【投喂单元 1/1】", stored)
        self.assertEqual(saved["status"], "done")
        self.assertEqual((saved["progress"].get("pipeline") or {}).get("repairs") or 0, 0)
        self.assertEqual((saved["progress"].get("pipeline") or {}).get("machine_repairs") or 0, 0)

    def test_rules_run_before_each_model_repair_and_do_not_add_attempts(self):
        self._gate("p-rule-then-ai", _SEEDANCE, _STILL)
        order = []
        import end.repair_rules as repair_rules

        real_rules = repair_rules.run_rules

        def spy_rules(*args, **kwargs):
            order.append("rules")
            return real_rules(*args, **kwargs)

        def writer(*_args, **_kwargs):
            order.append("ai")
            return {"feed": _SEEDANCE}

        def check(folder, stage, locks, title=""):
            del folder, locks, title
            if stage == "feed":
                return False, _STILL
            return True, ""

        with patch("end.repair_rules.run_rules", spy_rules), patch(
            "end.stage_runner.check_stage", check
        ), patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-rule-then-ai")
        saved = read_project("p-rule-then-ai")
        self.assertEqual(order, ["rules", "ai", "rules", "ai", "rules", "ai"])
        self.assertEqual(saved["status"], "gate")
        self.assertEqual(saved["progress"]["pipeline"]["repairs"], 3)
        self.assertEqual(saved["progress"]["pipeline"]["rule_repairs"], 3)
        self.assertIn("未找到投喂单元", saved["messages"][-1]["content"])
        self.assertIn("缺【画幅风格】", saved["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
