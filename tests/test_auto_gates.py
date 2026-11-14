"""Automatic gates: only the script stage waits for a person."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project
from end.locks import prepare_locks, read_locks, write_locks as save_locks
from end.script_review import set_reviewer
from end.server import answer_gate, run_project
from end.stage_runner import REPAIR_LIMIT, auto_gate_action
from tests.test_director_pipeline import _base_locks, make_project


def setUpModule():
    from tests.offline_guard import install

    install()
    set_reviewer(lambda _messages: '{"issues":[]}')


def tearDownModule():
    from tests.offline_guard import uninstall

    set_reviewer(None)
    uninstall()


def _gate(stage, passed, repairs=0, retry_write=False, options=None, check="", auto_retries=0, machine_repairs=0, question_id=""):
    pipeline = {
        "current": stage,
        "passed": passed,
        "repairs": repairs,
        "retry_write": retry_write,
        "check": check,
        "auto_retries": auto_retries,
        "machine_repairs": machine_repairs,
        "report": "" if passed else "FAIL",
        "action": "wait",
    }
    question = {
        "id": question_id or stage,
        "options": list(options or (["继续"] if passed or retry_write else ["根据建议修改"])),
    }
    return {
        "terminal": "gate",
        "progress": {"pipeline": pipeline, "question": question},
    }


def _ready_locks(**extra):
    locks = prepare_locks(_base_locks(), "时长：12秒")
    locks["style_look"] = "自然光，写实肤色"
    locks["style"] = locks["style_look"]
    locks["seams"] = []
    locks["delivery"] = "2"
    locks.update(extra)
    return locks


class AutoGateDecisionTest(unittest.TestCase):
    def test_script_pass_and_polish_stay_human(self):
        self.assertEqual(auto_gate_action(_gate("script", True, options=["继续", "精修"])), "stop")
        revise = _gate("script", True, question_id="script_revise", options=["更贴我写的故事"])
        self.assertEqual(auto_gate_action(revise), "stop")

    def test_passed_auto_stages_advance(self):
        for stage in ("recon", "asset", "storyboard", "seam", "feed"):
            self.assertEqual(auto_gate_action(_gate(stage, True)), "advance", stage)

    def test_failed_auto_stages_repair_until_the_limit(self):
        for stage in ("asset", "storyboard", "seam", "feed"):
            self.assertEqual(auto_gate_action(_gate(stage, False, repairs=1)), "rewrite", stage)
            self.assertEqual(auto_gate_action(_gate(stage, False, repairs=REPAIR_LIMIT)), "stop", stage)

    def test_unfinished_write_retries_then_stops(self):
        first = _gate("feed", False, retry_write=True, options=["继续"])
        self.assertEqual(auto_gate_action(first), "retry_write")
        spent = _gate("feed", False, retry_write=True, options=["继续"], auto_retries=REPAIR_LIMIT - 1)
        self.assertEqual(auto_gate_action(spent), "stop")
        script = _gate("script", False, retry_write=True, options=["继续"])
        self.assertEqual(auto_gate_action(script), "retry_write")

    def test_machine_check_repairs_then_stops(self):
        self.assertEqual(auto_gate_action(_gate("feed", False, check="machine")), "rewrite")
        spent = _gate("feed", False, check="machine", machine_repairs=REPAIR_LIMIT)
        self.assertEqual(auto_gate_action(spent), "stop")

    def test_an_open_block_choice_is_not_chained(self):
        waiting = _gate("asset", False, question_id="asset_repair_choice", options=["交给模型改这一块"])
        self.assertEqual(auto_gate_action(waiting), "stop")


class AutoAdvanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "auto.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _writing(self, project_id, pipeline, locks):
        make_project(
            project_id,
            status="writing",
            progress={
                "percent": 12,
                "phase": "writing",
                "message": "下一段",
                "lock_machine": True,
                "pipeline": pipeline,
            },
        )
        save_locks(project_dir(project_id), locks)

    def test_recon_confirms_itself_and_stops_at_the_script(self):
        locks = _ready_locks(source="3", intel_confirmed=False)
        self._writing(
            "p-recon",
            {"current": "recon", "accepted": [], "action": "write", "only": "", "passed": True},
            locks,
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            if "任务：赛道分流" in brief:
                return {"text": "路线"}
            if "任务：剧本" in brief:
                return {"title": "雨夜", "script": "她在雨里等车。"}
            if "对标情报" in brief:
                return {"intel": "来源：一条爆款。这不是事实。"}
            raise AssertionError(brief[:120])

        with patch("end.stage_runner.check_stage", return_value=(True, "")), patch(
            "end.server.call_deepseek", writer
        ), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-recon")
        saved = read_project("p-recon")
        self.assertTrue(read_locks(project_dir("p-recon"))["intel_confirmed"])
        self.assertEqual(saved["status"], "gate")
        self.assertEqual(saved["progress"]["phase"], "gate_script")
        self.assertIn("继续", saved["messages"][-1]["options"])
        self.assertIn("精修", saved["messages"][-1]["options"])
        self.assertTrue(any("对标情报已确认" in (item.get("content") or "") for item in saved["messages"]))
        self.assertTrue(any("任务：剧本" in call for call in calls))
        self.assertFalse(any(item.get("options") == ["确认", "继续"] for item in saved["messages"]))

    def test_hot_source_still_asks_the_lock_after_recon(self):
        locks = _ready_locks(source="4", intel_confirmed=False)
        self._writing(
            "p-hot",
            {"current": "recon", "accepted": [], "action": "write", "only": "", "passed": True},
            locks,
        )

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            if "任务：赛道分流" in brief:
                return {"text": "路线"}
            if "对标情报" in brief:
                return {"intel": "热点来源，不是事实。"}
            raise AssertionError("不该写剧本")

        with patch("end.stage_runner.check_stage", return_value=(True, "")), patch(
            "end.server.call_deepseek", writer
        ), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-hot")
        saved = read_project("p-hot")
        self.assertTrue(read_locks(project_dir("p-hot"))["intel_confirmed"])
        self.assertEqual(saved["progress"]["phase"], "lock_wait")
        self.assertEqual(saved["progress"]["question"]["id"], "source_landed")
        self.assertFalse((project_dir("p-hot") / "_内部_" / "剧本.md").is_file())

    def test_script_confirm_runs_the_rest_without_another_click(self):
        locks = _ready_locks()
        self._writing(
            "p-chain",
            {"current": "script", "accepted": [], "action": "write", "only": "", "passed": True},
            locks,
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            if "任务：分镜衔接" in brief or "阶段：seam" in brief:
                return {"seam_ledger": {"entries": []}}
            if "任务：赛道分流" in brief:
                return {"text": "路线"}
            if "任务：剧本" in brief:
                return {"title": "雨夜", "script": "她在雨里等车。"}
            if "任务：资产锁定" in brief:
                return {"image_prompts": "#### 图1 · 女\n同上"}
            if "任务：分镜" in brief:
                return {"storyboard": "她进门。", "previs": "## 段 1\n第 1 格：人在门内。"}
            if "任务：建议修补" in brief and "阶段：asset" in brief:
                return {"image_prompts": "#### 图1 · 女\n改过的资产"}
            if "任务：建议修补" in brief and "阶段：storyboard" in brief:
                return {
                    "storyboard": "她进门。",
                    "previs": "## 段 1\n第 1 格：人在门内。改过的分镜，身体朝门，视线看向门外。",
                }
            if "任务：提示词封装" in brief:
                return {"feed": "## 【封装单元 1/1】\n投喂正文"}
            if "任务：建议修补" in brief and "阶段：feed" in brief:
                return {"feed": "## 【封装单元 1/1】\n改过的投喂"}
            raise AssertionError(brief[:160])

        def check(folder, stage, locks, title=""):
            folder = Path(folder)
            internal = folder / "_内部_"
            if stage == "asset":
                text = (internal / "assets.md").read_text(encoding="utf-8") if (internal / "assets.md").is_file() else ""
                if "改过" not in text:
                    return False, "FAIL: A1 图1 同上"
                return True, ""
            if stage == "storyboard":
                text = (internal / "分镜.md").read_text(encoding="utf-8") if (internal / "分镜.md").is_file() else ""
                if not text.strip():
                    return False, "分镜是空的"
                return True, ""
            if stage == "feed":
                text = (internal / "feed.md").read_text(encoding="utf-8") if (internal / "feed.md").is_file() else ""
                if "改过" not in text:
                    return False, "FAIL: H1 缺段"
                return True, ""
            return True, ""

        with patch("end.stage_runner.check_stage", check), patch(
            "end.stage_runner.check_prompt", return_value=(0, "PASSED")
        ), patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-chain")
            saved = read_project("p-chain")
            self.assertEqual(saved["status"], "gate")
            self.assertEqual(saved["progress"]["phase"], "gate_script")
            self.assertEqual(saved["messages"][-1]["options"], ["继续", "精修"])
            with patch("end.server.submit_agent_task"):
                answer_gate(read_project("p-chain"), "继续")
            run_project("p-chain")
        saved = read_project("p-chain")
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompts_ready")
        joined = "\n".join(calls)
        self.assertIn("任务：资产锁定", joined)
        self.assertIn("阶段：asset", joined)
        self.assertIn("任务：分镜", joined)
        self.assertNotIn("预演：1", joined)
        self.assertIn("景别机位", joined)
        self.assertIn("任务：提示词封装", joined)
        self.assertIn("阶段：feed", joined)
        self.assertIn("改过的资产", (project_dir("p-chain") / "_内部_" / "assets.md").read_text(encoding="utf-8"))
        self.assertIn("改过的投喂", (project_dir("p-chain") / "_内部_" / "feed.md").read_text(encoding="utf-8"))
        self.assertNotIn("asset_repair_choice", joined)
        self.assertTrue(saved["messages"][-1]["content"].startswith("提示词已就绪"))

    def test_failed_asset_stops_after_the_repair_limit_without_a_choice(self):
        locks = _ready_locks()
        self._writing(
            "p-asset-limit",
            {
                "current": "asset",
                "accepted": ["script"],
                "action": "write",
                "only": "asset",
                "passed": True,
            },
            locks,
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图1 · 女\n同上"}

        def check(folder, stage, locks, title=""):
            if stage == "asset":
                return False, "FAIL: A1 图1 同上"
            return True, ""

        with patch("end.stage_runner.check_stage", check), patch("end.server.call_deepseek", writer), patch(
            "end.server.load_skill_pack", return_value="pack"
        ):
            run_project("p-asset-limit")
        saved = read_project("p-asset-limit")
        self.assertEqual(len(calls), REPAIR_LIMIT)
        self.assertIn("任务：建议修补", calls[1])
        self.assertEqual(saved["status"], "gate")
        self.assertEqual(saved["progress"]["phase"], "gate_asset")
        self.assertGreaterEqual(saved["progress"]["pipeline"]["repairs"], REPAIR_LIMIT)
        self.assertEqual(saved["progress"]["question"]["id"], "asset")
        self.assertIn("根据建议修改", saved["messages"][-1]["options"])
        self.assertNotIn("交给模型改这一块", saved["messages"][-1]["options"])

    def test_feed_machine_check_repairs_without_a_click(self):
        locks = _ready_locks()
        self._writing(
            "p-machine",
            {
                "current": "feed",
                "accepted": ["script", "asset", "storyboard"],
                "action": "write",
                "only": "",
                "passed": True,
            },
            locks,
        )
        calls = []
        checks = {"n": 0}

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            if "任务：分镜衔接" in brief or "阶段：seam" in brief:
                return {"seam_ledger": {"entries": [{
                    "shot_id": "封装单元 1",
                    "d_lock": "none",
                    "reason": "开场",
                    "scene_relation": "opening",
                }]}}
            if "任务：建议修补" in brief:
                return {"feed": "## 【封装单元 1/1】\n改过的投喂"}
            return {"feed": "## 【封装单元 1/1】\n投喂正文"}

        def check(folder, stage, locks, title=""):
            return True, ""

        def check_prompt(*_args, **_kwargs):
            checks["n"] += 1
            if checks["n"] == 1:
                return 1, "FAIL: H1 缺段"
            return 0, "PASSED"

        with patch("end.stage_runner.check_stage", check), patch(
            "end.stage_runner.check_prompt", check_prompt
        ), patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-machine")
        saved = read_project("p-machine")
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompts_ready")
        self.assertGreaterEqual(checks["n"], 2)
        self.assertTrue(saved["messages"][-1]["content"].startswith("提示词已就绪"))

    def test_unfinished_feed_retries_without_a_click_then_stops(self):
        locks = _ready_locks()
        self._writing(
            "p-blank",
            {
                "current": "feed",
                "accepted": ["script", "asset", "storyboard"],
                "action": "write",
                "only": "",
                "passed": True,
            },
            locks,
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(1)
            raise RuntimeError("模型没有返回 JSON")

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-blank")
        saved = read_project("p-blank")
        self.assertEqual(len(calls), REPAIR_LIMIT)
        self.assertEqual(saved["status"], "gate")
        self.assertIn("没写完", saved["messages"][-1]["content"])
        self.assertEqual(saved["messages"][-1]["options"], ["继续"])
        self.assertTrue(saved["progress"]["pipeline"]["retry_write"])


if __name__ == "__main__":
    unittest.main()
