"""Lean history-list query: project only needed columns, keep full list_projects."""

import tempfile
import unittest
from pathlib import Path
import end.db as db
from end.db import (
    LIST_SUMMARY_COLUMNS,
    LIST_SUMMARY_SQL,
    list_project_summaries,
    list_projects,
    write_project,
)
from end.server import menu_view


HEAVY_KEYS = (
    "messages",
    "script",
    "storyboard",
    "image_prompts",
    "events",
    "tasks",
    "cloud_log",
    "prompt_id",
    "video_file",
    "model",
    "template",
)


def _project(project_id, created_at, **extra):
    project = {
        "id": project_id,
        "brief": "雨夜简报 " + project_id,
        "messages": [{"role": "user", "content": "很长的对话 " * 4000}],
        "title": "标题-%s" % project_id,
        "status": "writing",
        "stage": "正在写剧本",
        "script": "完整剧本 " * 20000,
        "storyboard": "完整分镜 " * 8000,
        "image_prompts": "提示词 " * 8000,
        "error": None,
        "events": [{"at": created_at, "message": "正在写剧本"}],
        "tasks": [{"id": "script", "state": "running"}],
        "progress": {
            "percent": 12,
            "phase": "writing",
            "message": "正在写剧本",
            "debug_llm": False,
            "debug_stop": "",
            "pipeline": {"current": "script", "action": "write"},
        },
        "cloud_log": "log",
        "prompt_id": "p1",
        "video_file": "",
        "model": "qwen3.8-flash",
        "template": "short-drama-director",
        "created_at": created_at,
        "started_at": created_at,
        "updated_at": created_at,
        "finished_at": None,
    }
    project.update(extra)
    write_project(project)
    return project


class ProjectListSummariesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "list.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_summary_sql_projects_needed_columns_not_select_star(self):
        self.assertNotIn("*", LIST_SUMMARY_SQL)
        self.assertIn("FROM projects", LIST_SUMMARY_SQL)
        self.assertIn("ORDER BY created_at DESC", LIST_SUMMARY_SQL)
        for name in LIST_SUMMARY_COLUMNS:
            self.assertIn(name, LIST_SUMMARY_SQL)
        for heavy in (
            "script",
            "storyboard",
            "image_prompts",
            "messages_json",
            "events_json",
            "tasks_json",
            "cloud_log",
            "prompt_id",
            "video_file",
            "model",
            "template",
        ):
            self.assertNotIn(heavy, LIST_SUMMARY_SQL)
        self.assertNotIn("messages", LIST_SUMMARY_SQL.replace("messages_json", ""))

    def test_list_project_summaries_runs_the_projected_sql(self):
        _project("c11d00000041", "2026-01-02T00:00:00+08:00")
        conn = db.connect()
        seen = []
        conn.set_trace_callback(seen.append)
        try:
            rows = list_project_summaries(conn)
        finally:
            conn.set_trace_callback(None)
        project_selects = [sql for sql in seen if "FROM projects" in sql and "table_info" not in sql]
        self.assertTrue(project_selects)
        self.assertEqual(project_selects[-1], LIST_SUMMARY_SQL)
        self.assertNotIn("SELECT *", project_selects[-1])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("script", rows[0])
        self.assertNotIn("messages", rows[0])

    def test_summaries_omit_heavy_fields_and_menu_view_stays_compatible(self):
        _project(
            "c11d00000042",
            "2026-01-04T00:00:00+08:00",
            progress={
                "percent": 40,
                "phase": "writing",
                "message": "正在写剧本",
                "debug_llm": True,
                "debug_stop": "images",
                "pipeline": {"current": "script", "action": "write"},
            },
        )
        _project("c11d00000043", "2026-01-05T00:00:00+08:00")
        summaries = list_project_summaries()
        self.assertEqual([item["id"] for item in summaries], ["c11d00000043", "c11d00000042"])
        for item in summaries:
            for key in HEAVY_KEYS:
                self.assertNotIn(key, item)
        first = menu_view(summaries[1])
        self.assertEqual(first["id"], "c11d00000042")
        self.assertEqual(first["brief"], "雨夜简报 c11d00000042")
        self.assertEqual(first["title"], "标题-c11d00000042")
        self.assertTrue(first["debug_llm"])
        self.assertEqual(first["debug_stop"], "images")
        self.assertEqual(first["progress"]["phase"], "writing")
        self.assertEqual(first["progress"]["percent"], 40)
        self.assertIn("crew", first["progress"])
        self.assertIn("active_role", first["progress"])

    def test_list_projects_still_returns_full_rows_for_resume(self):
        packed = "完整剧本 " * 20000
        messages = [{"role": "user", "content": "很长的对话 " * 4000}]
        _project(
            "c11d00000044",
            "2026-01-06T00:00:00+08:00",
            script=packed,
            messages=messages,
            storyboard="完整分镜 " * 8000,
        )
        full = list_projects()
        self.assertEqual(len(full), 1)
        row = full[0]
        self.assertEqual(row["id"], "c11d00000044")
        self.assertEqual(row["script"], packed)
        self.assertEqual(row["messages"], messages)
        self.assertEqual(row["storyboard"], "完整分镜 " * 8000)
        self.assertEqual(row["events"][0]["message"], "正在写剧本")
        self.assertEqual(row["tasks"][0]["id"], "script")
        lean = list_project_summaries()
        self.assertNotIn("script", lean[0])
        self.assertNotIn("messages", lean[0])
        self.assertEqual(lean[0]["id"], row["id"])
        self.assertEqual(lean[0]["title"], row["title"])
        self.assertEqual(lean[0]["status"], row["status"])


if __name__ == "__main__":
    unittest.main()
