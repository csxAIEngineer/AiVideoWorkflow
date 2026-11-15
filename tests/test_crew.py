"""Crew dots follow the stage that is already running. They are not a second pipeline."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.crew import CREW, active_role, attach_crew
from end.db import read_project, write_project
from end.server import public_view


def project(status, phase, message, current="", action="write", **extra):
    progress = {
        "percent": 40,
        "phase": phase,
        "message": message,
        "pipeline": {"current": current, "action": action, "passed": True},
    }
    progress.update(extra.pop("progress", {}))
    body = {"id": "crew", "status": status, "stage": message, "progress": progress}
    body.update(extra)
    return body


class CrewSignalTest(unittest.TestCase):
    def test_names_follow_the_real_stages(self):
        self.assertEqual([item["id"] for item in CREW], ["script", "asset", "storyboard", "check", "feed", "seam"])
        self.assertEqual([item["name"] for item in CREW], ["编剧", "资产", "分镜", "机检", "提示词封装", "分镜衔接"])

    def test_writing_and_gates_light_the_matching_role(self):
        cases = [
            (project("writing", "writing", "赛道分流"), "script"),
            (project("writing", "writing", "剧本", current="script"), "script"),
            (project("writing", "writing", "写剧本"), "script"),
            (project("gate", "gate_script", "剧本门。机检已过，没有打回项。", current="script", action="wait"), "script"),
            (project("gate", "gate_intel", "对标情报卡写好了。", current="recon", action="wait"), "script"),
            (project("writing", "writing", "资产锁定", current="asset"), "asset"),
            (project("writing", "writing", "资产提示词"), "asset"),
            (project("gate", "gate_asset", "资产门。机检已过。", current="asset", action="wait"), "asset"),
            (project("gate", "gate_asset", "机检没过。下面是还要改的地方。", current="asset", action="wait"), "asset"),
            (project("writing", "writing", "分镜", current="storyboard"), "storyboard"),
            (project("gate", "gate_storyboard", "分镜门。继续之后才写提示词封装。", current="storyboard", action="wait"), "storyboard"),
            (project("writing", "writing", "提示词封装", current="feed"), "feed"),
            (project("writing", "writing", "封装稿"), "feed"),
            (project("writing", "writing", "投喂稿"), "feed"),
            (project("gate", "gate_feed", "提示词封装。投喂机检已过。继续就做分镜衔接。", current="feed", action="wait"), "feed"),
            (project("writing", "writing", "投喂机检", current="feed", action="finish"), "check"),
            (project("writing", "writing", "机检"), "check"),
            (project("gate", "gate_machine", "投喂机检没过。", current="feed", action="wait"), "check"),
            (project("writing", "writing", "独立质检", current="qc"), "check"),
            (project("writing", "qc", "独立质检"), "check"),
        ]
        for item, role in cases:
            self.assertEqual(active_role(item), role, item["progress"]["message"])

    def test_idle_done_and_picture_work_settle(self):
        quiet = [
            project("done", "prompts_ready", "提示词已就绪。", current="feed", action="finish"),
            project("done", "done", "成片已生成"),
            project("paused", "writing", "分镜", current="storyboard"),
            project("error", "error", "机检未通过"),
            project("rendering", "images", "出图"),
            project("building", "building", "拼接"),
            project("writing", "writing", "看板"),
            project("writing", "writing", "离线看板"),
            project("queued", "", ""),
            {"id": "empty", "status": "", "stage": "", "progress": {}},
        ]
        for item in quiet:
            self.assertEqual(active_role(item), "", item.get("stage"))

    def test_attach_crew_does_not_write_back(self):
        item = project("writing", "writing", "分镜", current="storyboard")
        original = dict(item["progress"])
        shown = attach_crew(item)
        self.assertEqual(item["progress"], original)
        self.assertEqual(shown["active_role"], "storyboard")
        self.assertEqual(shown["message"], "分镜")
        self.assertEqual(shown["crew"][2]["name"], "分镜")
        self.assertNotIn("active_role", item["progress"])


class CrewViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "crew.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_poll_payload_carries_crew_and_the_file_does_not(self):
        stored = project("writing", "writing", "提示词封装", current="feed")
        stored["id"] = "crewfeed01"
        stored["brief"] = "雨"
        stored["messages"] = []
        stored["title"] = "雨"
        stored["script"] = ""
        stored["storyboard"] = ""
        stored["image_prompts"] = ""
        stored["error"] = None
        stored["events"] = []
        stored["tasks"] = []
        stored["model"] = "qwen3.8-flash"
        stored["template"] = "short-drama-director"
        stored["created_at"] = "2026-01-01T00:00:00+08:00"
        write_project(stored)
        with patch("end.server.normalize_model", side_effect=lambda value: value or "qwen3.8-flash"), patch(
            "end.server.model_label", side_effect=lambda value: value or "Qwen"
        ), patch("end.server.normalize_template", side_effect=lambda value: value or "short-drama-director"), patch(
            "end.server.template_label", side_effect=lambda value: value or "短剧"
        ):
            view = public_view(read_project("crewfeed01"))
        self.assertEqual(view["progress"]["active_role"], "feed")
        self.assertEqual(view["progress"]["crew"][4]["name"], "提示词封装")
        saved = read_project("crewfeed01")
        self.assertNotIn("crew", saved["progress"])
        self.assertNotIn("active_role", saved["progress"])


if __name__ == "__main__":
    unittest.main()
