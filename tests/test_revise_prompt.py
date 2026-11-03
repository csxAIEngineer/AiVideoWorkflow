"""A canvas suggestion rewrites one prompt and leaves the rest of the draft."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.revise import accept_revise, block_brief, perform_revise, revision_brief
from end.server import _PIPELINE_HANDOFF
from end.workflow_from_spec import collect_steps
from tests.test_project_api import make_project


LONG = "旧的很长的提示词" * 8


class ReviseTextTest(unittest.TestCase):
    def test_briefs_keep_the_current_text_and_the_suggestion(self):
        script = revision_brief("script", "旧剧本", "雨更大，她不要回头")
        self.assertIn("任务：剧本", script)
        self.assertIn("旧剧本", script)
        self.assertIn("雨更大，她不要回头", script)
        board = revision_brief("storyboard", "旧分镜", "第二段改口")
        self.assertIn("任务：分镜", board)
        self.assertIn("旧分镜", board)
        self.assertIn("第二段改口", board)
        block = block_brief("asset", "旧提示", "衣服改红")
        self.assertIn("旧提示", block)
        self.assertIn("衣服改红", block)
        self.assertIn("不要写其他图", block)


class ReviseProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        self.ids = []

    def tearDown(self):
        for project_id in self.ids:
            _PIPELINE_HANDOFF.pop(project_id, None)
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_script_revision_saves_only_the_script(self):
        project_id = "5c01b7000001"
        folder = self._ready(project_id, {"script": "旧剧本", "storyboard": "旧分镜"})
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text("资产留下\n", encoding="utf-8")
        (folder / "outputs" / "images").mkdir(parents=True)
        (folder / "outputs" / "images" / "A1.png").write_bytes(b"png")
        self._queue(project_id, "script", "", "雨更大，她不要回头")

        def fake(brief, _model):
            self.assertIn("任务：剧本", brief)
            self.assertIn("旧剧本", brief)
            self.assertIn("雨更大，她不要回头", brief)
            return {"script": "新剧本结尾"}

        with patch("end.revise.run_suite_call", side_effect=fake) as suite, patch(
            "end.revise.run_block_call"
        ) as block, patch("end.server.start_render_thread") as render:
            perform_revise(project_id)
        suite.assert_called_once()
        block.assert_not_called()
        render.assert_not_called()
        project = read_project(project_id)
        self.assertEqual(project["script"], "新剧本结尾")
        self.assertEqual(project["storyboard"], "旧分镜")
        self.assertEqual(project["status"], "done")
        self.assertNotIn("revising", project["progress"])
        self.assertEqual((internal / "assets.md").read_text(encoding="utf-8"), "资产留下\n")
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertIn("新剧本结尾", (internal / "剧本.md").read_text(encoding="utf-8"))

    def test_storyboard_revision_keeps_assets_and_stores_previs(self):
        project_id = "5704b0000002"
        folder = self._ready(project_id, {"storyboard": "旧分镜"})
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text("资产留下\n", encoding="utf-8")
        self._queue(project_id, "storyboard", "", "第二段改口")

        def fake(brief, _model):
            self.assertIn("任务：分镜", brief)
            self.assertIn("旧分镜", brief)
            self.assertIn("第二段改口", brief)
            return {"storyboard": "新分镜", "previs": "新预演"}

        with patch("end.revise.run_suite_call", side_effect=fake), patch(
            "end.revise.run_block_call"
        ) as block, patch("end.server.start_render_thread") as render:
            perform_revise(project_id)
        block.assert_not_called()
        render.assert_not_called()
        project = read_project(project_id)
        self.assertEqual(project["storyboard"], "新分镜")
        self.assertEqual(project["script"], "旧剧本")
        self.assertEqual((internal / "assets.md").read_text(encoding="utf-8"), "资产留下\n")
        self.assertIn("新分镜", (internal / "分镜.md").read_text(encoding="utf-8"))
        self.assertIn("新预演", (internal / "previs.md").read_text(encoding="utf-8"))
        self.assertNotIn("revising", project["progress"])

    def test_asset_revision_replaces_one_block(self):
        project_id = "a55e70000003"
        self._ready(
            project_id,
            {
                "image_prompts": "#### 图1 · 主角\n%s\n\n#### 图2 · 雨\n旧雨\n" % LONG,
                "assets": [
                    {"id": "图1", "title": "主角", "prompt": LONG},
                    {"id": "图2", "title": "雨", "prompt": "旧雨"},
                ],
            },
        )
        self._queue(project_id, "asset", "图1", "衣服改红")

        def fake(kind, current, suggestion, _model):
            self.assertEqual(kind, "asset")
            self.assertIn("旧", current)
            self.assertEqual(suggestion, "衣服改红")
            return "短提示"

        with patch("end.revise.run_block_call", side_effect=fake) as block, patch(
            "end.revise.run_suite_call"
        ) as suite, patch("end.server.start_render_thread") as render:
            perform_revise(project_id)
        block.assert_called_once()
        suite.assert_not_called()
        render.assert_not_called()
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        assets, _shots = collect_steps(saved)
        self.assertEqual(assets[0]["prompt"], "短提示")
        self.assertIn("旧雨", assets[1]["prompt"])
        self.assertNotIn(LONG, saved["image_prompts"])
        self.assertIn("短提示", saved["image_prompts"])
        self.assertIn("旧雨", saved["image_prompts"])

    def test_shot_revision_replaces_one_unit(self):
        project_id = "510700000004"
        self._ready(
            project_id,
            {
                "feed": "## 【投喂单元 1/2】\n第一镜原文\n\n## 【投喂单元 2/2】\n第二镜原文\n",
                "shots": [
                    {"id": "S01", "duration": 5, "prompt": "第一镜原文", "images": ["A1"]},
                    {"id": "S02", "duration": 5, "prompt": "第二镜原文", "images": ["A1"]},
                ],
            },
        )
        self._queue(project_id, "shot", "S01", "她不要回头")

        def fake(kind, current, suggestion, _model):
            self.assertEqual(kind, "shot")
            self.assertIn("第一镜原文", current)
            self.assertEqual(suggestion, "她不要回头")
            return "新的第一镜"

        with patch("end.revise.run_block_call", side_effect=fake), patch(
            "end.revise.run_suite_call"
        ) as suite:
            perform_revise(project_id)
        suite.assert_not_called()
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        _assets, shots = collect_steps(saved)
        self.assertIn("新的第一镜", shots[0]["prompt"])
        self.assertNotIn("第一镜原文", shots[0]["prompt"])
        self.assertIn("第二镜原文", shots[1]["prompt"])
        self.assertNotIn("新的第一镜", shots[1]["prompt"])

    def test_empty_suggestion_is_rejected(self):
        project_id = "b17570000005"
        folder = self._ready(project_id, {"script": "旧剧本"})
        with patch("end.server.submit_agent_task") as submit:
            with self.assertRaises(ValueError) as raised:
                accept_revise(project_id, "script", "", "  ")
        self.assertIn("空", str(raised.exception))
        submit.assert_not_called()
        self.assertEqual(json.loads((folder / "spec.json").read_text(encoding="utf-8"))["script"], "旧剧本")
        self.assertNotIn("revising", read_project(project_id)["progress"])

    def test_busy_worker_is_rejected(self):
        project_id = "0ea470000006"
        folder = self._ready(project_id, {"script": "旧剧本"})
        _PIPELINE_HANDOFF[project_id] = "writer"
        with patch("end.server.thread_alive", return_value=True), patch(
            "end.server.submit_agent_task"
        ) as submit:
            with self.assertRaises(RuntimeError) as raised:
                accept_revise(project_id, "script", "", "雨更大")
        self.assertIn("正在跑", str(raised.exception))
        submit.assert_not_called()
        self.assertEqual(json.loads((folder / "spec.json").read_text(encoding="utf-8"))["script"], "旧剧本")
        self.assertNotIn("revising", read_project(project_id)["progress"])

    def _ready(self, project_id, spec_patch):
        self.ids.append(project_id)
        make_project(project_id, status="done")
        project = read_project(project_id)
        project["status"] = "done"
        project["stage"] = "提示词已就绪"
        project["progress"] = {
            "percent": 100,
            "phase": "prompts_ready",
            "message": "提示词已就绪",
            "lock_machine": True,
        }
        write_project(project)
        folder = project_dir(project_id)
        spec = {
            "title": "雨夜",
            "script": "旧剧本",
            "storyboard": "旧分镜",
            "image_prompts": "",
            "assets": [{"id": "A1", "title": "主角", "prompt": "旧提示"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "离开", "images": ["A1"]}],
        }
        spec.update(spec_patch)
        project["script"] = spec.get("script") or ""
        project["storyboard"] = spec.get("storyboard") or ""
        project["image_prompts"] = spec.get("image_prompts") or ""
        write_project(project)
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        return folder

    def _queue(self, project_id, node, target_id, suggestion):
        project = read_project(project_id)
        progress = dict(project["progress"])
        progress["revising"] = {
            "node": node,
            "target_id": target_id,
            "suggestion": suggestion,
        }
        project["progress"] = progress
        write_project(project)
