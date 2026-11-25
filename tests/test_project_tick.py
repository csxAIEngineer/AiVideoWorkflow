"""Read-only project get, in-memory shot fill, and lightweight progress ticks."""

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.board_shots import apply_storyboard_shots
from end.db import (
    TICK_SQL,
    content_revision_from_project,
    project_dir,
    read_project,
    read_project_tick,
    write_project,
)
from end.server import (
    apply_spec_shots,
    compose_content_revision,
    persist_spec_shots_for_render,
    public_view,
    remember,
    rpc_call,
)
from tests.test_board_shots import ASSETS, BOARD


PLACEHOLDER_SHOTS = [
    {
        "id": "S01",
        "duration": 4,
        "prompt": "按这张资产图演这一镜",
        "images": ["A1"],
        "dialogue": "",
        "narration": "",
    }
]


def make_project(project_id, **extra):
    created = "2026-01-01T00:00:00+08:00"
    project = {
        "id": project_id,
        "brief": "雨夜",
        "messages": [{"role": "user", "content": "雨夜的方向"}],
        "title": "雨夜",
        "status": "writing",
        "stage": "正在写剧本",
        "script": "旧剧本",
        "storyboard": BOARD,
        "image_prompts": "",
        "error": None,
        "events": [{"at": created, "message": "正在写剧本"}],
        "tasks": [{"id": "script", "title": "剧本", "state": "running", "detail": "写稿"}],
        "progress": {"percent": 8, "phase": "writing", "message": "正在写剧本", "debug_llm": False},
        "cloud_log": "",
        "prompt_id": "",
        "video_file": "",
        "model": "deepseek-v4-pro",
        "created_at": created,
        "started_at": created,
        "updated_at": created,
        "finished_at": None,
    }
    project.update(extra)
    write_project(project)
    folder = project_dir(project_id)
    spec = {
        "title": "雨夜",
        "script": project.get("script") or "",
        "storyboard": BOARD,
        "assets": ASSETS,
        "shots": list(PLACEHOLDER_SHOTS),
    }
    (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


class ProjectTickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "tick.db"
        db._CONN = None
        db.DATA.mkdir()
        self._env = {
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
        }
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        os.environ["AIVIDEO_PUBLIC_BASE"] = "http://203.0.113.10:8787"

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_get_fills_placeholder_shots_without_writing_spec(self):
        project_id = "a11c0de00031"
        make_project(project_id)
        path = project_dir(project_id) / "spec.json"
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        view = public_view(read_project(project_id))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual([shot["id"] for shot in view["shots"]], ["S01", "S02"])
        self.assertIn("content_revision", view)
        filled, changed = apply_spec_shots(json.loads(before.decode("utf-8")))
        self.assertTrue(changed)
        self.assertEqual([shot["id"] for shot in filled["shots"]], ["S01", "S02"])

    def test_render_path_persists_filled_shots(self):
        project_id = "a11c0de00032"
        make_project(project_id)
        path = project_dir(project_id) / "spec.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([shot["id"] for shot in before["shots"]], ["S01"])
        filled = persist_spec_shots_for_render(project_id, before)
        self.assertEqual([shot["id"] for shot in filled["shots"]], ["S01", "S02"])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["shots"], filled["shots"])
        self.assertEqual(apply_storyboard_shots(before)["shots"], saved["shots"])
        mtime = path.stat().st_mtime_ns
        again = persist_spec_shots_for_render(project_id, saved)
        self.assertEqual(again["shots"], saved["shots"])
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_tick_uses_projected_sql_and_does_not_upload_or_write(self):
        project_id = "a11c0de00033"
        make_project(project_id)
        path = project_dir(project_id) / "spec.json"
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        sqls = []

        def tracer(statement):
            if statement.startswith("--"):
                return
            sqls.append(statement)

        conn = db.connect()
        conn.set_trace_callback(tracer)
        try:
            row = read_project_tick(project_id, conn)
        finally:
            conn.set_trace_callback(None)
        self.assertIsNotNone(row)
        self.assertNotIn("script", row)
        self.assertNotIn("messages", row)
        self.assertNotIn("storyboard", row)
        joined = " ".join(sqls).upper()
        self.assertIn("FROM PROJECTS", joined)
        self.assertNotIn("SELECT *", joined)
        self.assertIn("CONTENT_REVISION", joined)
        self.assertNotIn("MESSAGES_JSON", joined)
        self.assertNotIn("SCRIPT,", joined + ",")
        self.assertNotIn("STORYBOARD", joined)
        self.assertEqual(TICK_SQL.split()[0].upper(), "SELECT")
        self.assertNotIn("*", TICK_SQL.split("FROM")[0])

        def boom(*_args, **_kwargs):
            raise AssertionError("tick must not publish or upload media")

        with patch("end.server.get_image_store", boom), patch(
            "end.server.assemble_steps", boom
        ) if False else patch("end.workflow_from_spec.assemble_steps", boom):
            tick = rpc_call("projects.tick", {"id": project_id})
        self.assertEqual(tick["id"], project_id)
        self.assertEqual(tick["status"], "writing")
        self.assertIn("progress", tick)
        self.assertIn("can_reply", tick)
        self.assertIn("tasks", tick)
        self.assertIn("events", tick)
        self.assertIn("content_revision", tick)
        self.assertNotIn("script", tick)
        self.assertNotIn("messages", tick)
        self.assertNotIn("shots", tick)
        self.assertNotIn("images", tick)
        self.assertNotIn("clips", tick)
        self.assertNotIn("video_url", tick)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_revision_stable_for_progress_and_changes_for_content(self):
        project_id = "a11c0de00034"
        project = make_project(project_id)
        first = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        second = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertEqual(first, second)
        remember(read_project(project_id), "writing", "还在写", progress={"percent": 20, "phase": "writing", "message": "还在写"})
        after_progress = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertEqual(after_progress, first)
        project = read_project(project_id)
        project["messages"] = [{"role": "user", "content": "雨夜的方向已改"}]
        write_project(project)
        after_messages = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_messages, first)
        project = read_project(project_id)
        project["script"] = "新剧本同一条数"
        write_project(project)
        after_script = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_script, after_messages)
        images = project_dir(project_id) / "outputs" / "images"
        images.mkdir(parents=True)
        png = images / "A1.png"
        png.write_bytes(b"one")
        after_image = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_image, after_script)
        time.sleep(0.05)
        png.write_bytes(b"two")
        after_replace = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_replace, after_image)
        clips = project_dir(project_id) / "outputs" / "clips"
        clips.mkdir(parents=True)
        (clips / "S01.mp4").write_bytes(b"clip")
        after_clip = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_clip, after_replace)
        project = read_project(project_id)
        project["video_file"] = "film.mp4"
        write_project(project)
        (project_dir(project_id) / "outputs" / "film.mp4").write_bytes(b"film")
        after_film = rpc_call("projects.tick", {"id": project_id})["content_revision"]
        self.assertNotEqual(after_film, after_clip)
        token = content_revision_from_project(read_project(project_id))
        self.assertTrue(token)
        self.assertEqual(
            compose_content_revision(project_id, read_project(project_id)),
            after_film,
        )

    def test_get_contract_still_returns_full_project(self):
        project_id = "a11c0de00035"
        make_project(project_id, status="done", stage="完成", progress={"percent": 100, "phase": "done", "message": "完成"})
        view = rpc_call("projects.get", {"id": project_id})
        self.assertEqual(view["script"], "旧剧本")
        self.assertIn("messages", view)
        self.assertIn("shots", view)
        self.assertIn("assets", view)
        self.assertIn("progress", view)


if __name__ == "__main__":
    unittest.main()
