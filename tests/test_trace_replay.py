"""Process traces: one write per record, then restore that output and move on."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end.script_review import set_reviewer
from end.stage_runner import execute
from end.trace import begin_trace, finish_trace, list_traces, restore_trace

_PASSING_ASSET = "\n".join((
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
_PASSING_SCRIPT = "出场：林浅\n林浅在雨夜车站等一班车。"


def setUpModule():
    from tests.offline_guard import install

    install()
    set_reviewer(lambda _messages: '{"issues":[]}')


def tearDownModule():
    from tests.offline_guard import uninstall

    set_reviewer(None)
    uninstall()


class TraceReplayTest(unittest.TestCase):
    def test_vision_replay_restores_the_reading_and_starts_the_script(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "intake.json").write_text(
            json.dumps(
                [{"id": "U1", "title": "图", "narration": "旁白", "reading": "旧读图"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("旧剧本\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": "旧剧本"}), encoding="utf-8")
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "vision", False, "读图")
            saved = finish_trace(folder, {"readings": [{"id": "U1", "reading": "新读图"}]})
        plan = restore_trace(folder, saved["id"], {}, "")
        self.assertEqual(plan["action"], "write")
        self.assertEqual(plan["current"], "script")
        self.assertEqual(plan["accepted"], [])
        items = json.loads((folder / "intake.json").read_text(encoding="utf-8"))
        self.assertEqual(items[0]["reading"], "新读图")
        self.assertEqual(items[0]["narration"], "旁白")
        self.assertFalse((internal / "剧本.md").exists())
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertNotIn("script", spec)

    def test_flag_off_writes_nothing(self):
        folder = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "0"}):
            self.assertEqual(begin_trace(folder, "script", False, "简报"), "")
            self.assertEqual(list_traces(folder), [])
        self.assertFalse((folder / "debug" / "trace").exists())

    def test_asset_replay_restores_output_and_clears_later_stages(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text(_PASSING_SCRIPT + "\n", encoding="utf-8")
        (internal / "分镜.md").write_text("旧分镜", encoding="utf-8")
        (internal / "previs.md").write_text("旧预演", encoding="utf-8")
        (internal / "feed.md").write_text("旧投喂", encoding="utf-8")
        (folder / "feed.md").write_text("旧发布", encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps(
                {
                    "script": _PASSING_SCRIPT,
                    "image_prompts": "旧资产",
                    "storyboard": "旧分镜",
                    "previs": "旧预演",
                    "feed": "旧投喂",
                    "shots": [{"id": "S1"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1", "AIVIDEO_DEBUG_PROMPT": "0"}):
            first = begin_trace(folder, "asset", False, "第一稿")
            Path(first).write_text(json.dumps({"prompt": "第一提示"}) + "\n", encoding="utf-8")
            finish_trace(folder, {"image_prompts": _PASSING_ASSET, "assets": []})
            second = begin_trace(folder, "asset", True, "返修简报")
            Path(second).write_text(json.dumps({"prompt": "完整提示词"}) + "\n", encoding="utf-8")
            saved = finish_trace(folder, {"image_prompts": _PASSING_ASSET, "assets": [{"id": "图1"}]})
            rows = list_traces(folder)
        self.assertEqual(saved["label"], "资产返修 2")
        self.assertEqual(saved["attempt"], 2)
        self.assertEqual(saved["input"]["brief"], "返修简报")
        self.assertEqual(saved["input"]["prompts"], ["完整提示词"])
        self.assertEqual(rows[1]["label"], "资产返修 2")
        self.assertNotIn("prompts", rows[1])
        plan = restore_trace(folder, saved["id"], {}, "")
        self.assertEqual(plan["action"], "write")
        self.assertEqual(plan["current"], "storyboard")
        self.assertEqual(plan["next"], "storyboard")
        self.assertIn("asset", plan["accepted"])
        self.assertNotIn("storyboard", plan["accepted"])
        self.assertEqual((internal / "assets.md").read_text(encoding="utf-8").strip(), _PASSING_ASSET)
        self.assertFalse((internal / "分镜.md").exists())
        self.assertFalse((internal / "previs.md").exists())
        self.assertFalse((internal / "feed.md").exists())
        self.assertFalse((folder / "feed.md").exists())
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["image_prompts"], _PASSING_ASSET)
        self.assertEqual(spec["assets"], [{"id": "图1"}])
        self.assertEqual(spec["script"], _PASSING_SCRIPT)
        self.assertNotIn("storyboard", spec)
        self.assertNotIn("feed", spec)
        self.assertNotIn("shots", spec)
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "0"}):
            self.assertEqual(list_traces(folder), [])

    def test_last_stage_replay_waits_on_that_gate(self):
        folder = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "feed", False, "投喂简报")
            finish_trace(folder, {"feed": "投喂正文", "shots": []})
        plan = restore_trace(folder, "1", {}, "")
        self.assertEqual(plan["action"], "wait")
        self.assertEqual(plan["current"], "feed")
        self.assertEqual(plan["next"], "")
        self.assertFalse(plan["passed"])
        self.assertNotIn("feed", plan["accepted"])
        self.assertNotIn("seam", plan["accepted"])
        self.assertEqual((folder / "_内部_" / "feed.md").read_text(encoding="utf-8").strip(), "投喂正文")

    def test_route_trace_is_not_replayed(self):
        folder = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "route", False, "简报", label="赛道分流")
            finish_trace(folder, {"text": "路线"})
        with self.assertRaises(ValueError):
            restore_trace(folder, "0001", {}, "")

    def test_failed_trace_is_not_replayed(self):
        folder = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "asset", False, "简报")
            finish_trace(folder, {}, error="没写完")
        with self.assertRaises(ValueError):
            restore_trace(folder, "0001", {}, "")

    def test_execute_saves_the_script_that_landed(self):
        folder = Path(tempfile.mkdtemp())
        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4.1-flash",
            "brief": "故事",
            "progress": {
                "pipeline": {"action": "write", "current": "script", "accepted": [], "only": ""},
            },
        }

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            Path(trace_prompts).write_text(
                json.dumps({"prompt": "完整提示词"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if "任务：赛道分流" in brief:
                return {"text": "路线说明"}
            return {"title": "题", "script": "剧本正文", "internal": "圆桌意见"}

        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1", "AIVIDEO_DEBUG_PROMPT": "0"}):
            with patch("end.stage_runner.check_stage", return_value=(True, "")):
                execute(project, folder, {}, writer, lambda *args, **kwargs: "pack")
            rows = list_traces(folder)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["label"] for row in rows], ["赛道分流", "剧本"])
        self.assertEqual(rows[0]["error"], "")
        route = json.loads((folder / "debug" / "trace" / "0001.json").read_text(encoding="utf-8"))
        self.assertEqual(route["input"]["prompts"], ["完整提示词"])
        self.assertNotIn("用户原文", route["input"]["brief"])
        self.assertFalse(route["pending"])
        record = json.loads((folder / "debug" / "trace" / "0002.json").read_text(encoding="utf-8"))
        self.assertEqual(record["input"]["prompts"], ["完整提示词"])
        self.assertIn("故事", record["input"]["brief"])
        self.assertIn("用户原文", record["input"]["brief"])
        self.assertEqual(record["output"]["script"], "剧本正文")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), "剧本正文")

    def test_execute_records_a_failed_write(self):
        folder = Path(tempfile.mkdtemp())
        project = {
            "template": "short-drama-director",
            "brief": "故事",
            "progress": {
                "pipeline": {"action": "write", "current": "asset", "accepted": ["script"], "only": ""},
            },
        }

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            raise RuntimeError("这一段没有写完")

        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            execute(project, folder, {}, writer, lambda *args, **kwargs: "pack")
            rows = list_traces(folder)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error"], "这一段没有写完")
        self.assertFalse(rows[0]["pending"])

    def test_replay_rpc_starts_the_next_stage(self):
        import end.db as db
        from end.server import rpc_replay
        from tests.test_director_pipeline import make_project

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        try:
            project = make_project(
                "abc123abc123",
                status="gate",
                stage="资产门",
                progress={
                    "percent": 46,
                    "phase": "gate_asset",
                    "message": "资产门",
                    "pipeline": {
                        "action": "wait",
                        "current": "asset",
                        "accepted": ["script"],
                        "passed": True,
                        "only": "",
                    },
                },
            )
            folder = db.project_dir(project["id"])
            internal = folder / "_内部_"
            internal.mkdir(parents=True, exist_ok=True)
            (internal / "剧本.md").write_text(_PASSING_SCRIPT + "\n", encoding="utf-8")
            (internal / "assets.md").write_text("旧资产\n", encoding="utf-8")
            (internal / "分镜.md").write_text("旧分镜\n", encoding="utf-8")
            (folder / "spec.json").write_text(
                json.dumps({"script": _PASSING_SCRIPT, "image_prompts": "旧资产", "storyboard": "旧分镜"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
                begin_trace(folder, "asset", True, "返修简报")
                saved = finish_trace(folder, {"image_prompts": _PASSING_ASSET, "assets": [{"id": "图1"}]})
                with patch("end.server.submit_agent_task") as submit:
                    view = rpc_replay({"id": project["id"], "trace_id": saved["id"]})
            self.assertEqual(submit.call_args.args[0].kind, "write")
            self.assertEqual(view["status"], "writing")
            self.assertEqual(view["progress"]["pipeline"]["current"], "storyboard")
            self.assertEqual(view["progress"]["pipeline"]["action"], "write")
            self.assertIn("asset", view["progress"]["pipeline"]["accepted"])
            self.assertNotIn("storyboard", view["progress"]["pipeline"]["accepted"])
            self.assertIn("已恢复", view["messages"][-1]["content"])
            self.assertEqual((internal / "assets.md").read_text(encoding="utf-8").strip(), _PASSING_ASSET)
            self.assertFalse((internal / "分镜.md").exists())
            self.assertEqual(view["image_prompts"], _PASSING_ASSET)
            self.assertEqual(view["storyboard"], "")
        finally:
            conn = db._CONN
            db.DATA, db.DB_PATH, db._CONN = old
            if conn is not None and conn is not old[2]:
                conn.close()
            tmp.cleanup()

    def test_replay_rpc_keeps_a_failed_storyboard_at_its_gate(self):
        import end.db as db
        from end.server import rpc_replay
        from end.storyboard_contract import HOLD, note_hold, write_marker
        from tests.test_director_pipeline import make_project

        board = "\n".join((
            "## S01",
            "时长：8",
            "资产：图1",
            "对白：无",
            "旁白：无",
        ))
        hold = "分镜未完成：S01 缺动作、景别机位"
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        try:
            project = make_project(
                "abc123abc123",
                status="gate",
                stage="分镜门",
                progress={
                    "percent": 60,
                    "phase": "gate_storyboard",
                    "message": "分镜门",
                    "pipeline": {
                        "action": "wait",
                        "current": "storyboard",
                        "accepted": ["script", "asset"],
                        "passed": False,
                        "only": "",
                        "report": hold,
                        "check": "machine",
                    },
                },
            )
            folder = db.project_dir(project["id"])
            internal = folder / "_内部_"
            internal.mkdir(parents=True, exist_ok=True)
            (internal / "分镜.md").write_text(board + "\n", encoding="utf-8")
            (internal / "feed.md").write_text("旧封装\n", encoding="utf-8")
            write_marker(folder)
            note_hold(folder, hold)
            (folder / "spec.json").write_text(
                json.dumps(
                    {
                        "script": "旧剧本",
                        "image_prompts": "旧资产",
                        "assets": [{"id": "图1", "title": "沈棠"}],
                        "storyboard": board,
                        "storyboard_contract": "shot-prose-v1",
                        "storyboard_hold": hold,
                        "feed": "旧封装",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
                begin_trace(folder, "storyboard", False, "分镜简报")
                saved = finish_trace(folder, {"storyboard": board})
                with patch("end.server.submit_agent_task") as submit:
                    view = rpc_replay({"id": project["id"], "trace_id": saved["id"]})
            submit.assert_not_called()
            pipeline = view["progress"]["pipeline"]
            self.assertEqual(view["status"], "gate")
            self.assertEqual(pipeline["current"], "storyboard")
            self.assertEqual(pipeline["action"], "wait")
            self.assertFalse(pipeline["passed"])
            self.assertIn("asset", pipeline["accepted"])
            self.assertNotIn("storyboard", pipeline["accepted"])
            self.assertNotIn("feed", pipeline["accepted"])
            self.assertIn("缺动作", pipeline.get("report") or view["progress"].get("message") or "")
            self.assertEqual((internal / "分镜.md").read_text(encoding="utf-8").strip(), board)
            self.assertTrue((internal / HOLD).is_file() or "缺动作" in (view["messages"][-1]["content"]))
            self.assertNotIn("写封装稿", view["messages"][-1]["content"])
            self.assertNotIn("接下来写", view["messages"][-1]["content"])
        finally:
            conn = db._CONN
            db.DATA, db.DB_PATH, db._CONN = old
            if conn is not None and conn is not old[2]:
                conn.close()
            tmp.cleanup()
