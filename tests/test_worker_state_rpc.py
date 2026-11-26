"""projects.list, tick, and get expose End worker waiting vs running."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

import end.db as db
import end.server as server
from end.task_queue import Task, TaskQueue


def make_project(project_id, status="writing", stage="正在写剧本"):
    created = "2026-01-01T00:00:00+08:00"
    project = {
        "id": project_id,
        "brief": "雨夜",
        "title": "雨夜",
        "status": status,
        "stage": stage,
        "script": "",
        "error": None,
        "events": [],
        "tasks": [],
        "progress": {"percent": 8, "phase": "writing", "message": stage, "pipeline": {"current": "script", "action": "write"}},
        "model": "deepseek-v4-pro",
        "created_at": created,
        "started_at": created,
        "updated_at": created,
        "finished_at": None,
    }
    db.write_project(project)
    return project


class WorkerStateRpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old_db = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "worker-state.db"
        db._CONN = None
        db.DATA.mkdir()
        self._old_queue = server._AGENT_QUEUE
        self.release = threading.Event()
        self.started = threading.Event()

        def handler(task):
            del task
            self.started.set()
            self.release.wait(2)

        self.queue = TaskQueue(handler, workers=1)
        server._AGENT_QUEUE = self.queue

    def tearDown(self):
        self.release.set()
        self.queue.stop()
        server._AGENT_QUEUE = self._old_queue
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old_db
        if conn is not None and conn is not self._old_db[2]:
            conn.close()
        self.tmp.cleanup()

    def _wait_running(self, project_id):
        self.assertTrue(self.started.wait(2))
        deadline = time.time() + 1
        while time.time() < deadline:
            if self.queue.state_for(project_id).get("worker_state") == "running":
                return
            time.sleep(0.01)
        self.fail("worker did not start %s" % project_id)

    def test_tick_list_and_get_show_waiting_then_running(self):
        make_project("a00a00000001")
        make_project("a00a00000002")
        self.assertTrue(self.queue.submit(Task("write", "a00a00000001")))
        self.assertTrue(self.queue.submit(Task("write", "a00a00000002")))
        self._wait_running("a00a00000001")

        tick_running = server.rpc_call("projects.tick", {"id": "a00a00000001"})
        tick_waiting = server.rpc_call("projects.tick", {"id": "a00a00000002"})
        self.assertEqual(tick_running["worker_state"], "running")
        self.assertEqual(tick_running["worker_kind"], "write")
        self.assertNotIn("worker_wait_position", tick_running)
        self.assertEqual(tick_waiting["worker_state"], "waiting")
        self.assertEqual(tick_waiting["worker_kind"], "write")
        self.assertEqual(tick_waiting["worker_wait_position"], 1)

        rows = {item["id"]: item for item in server.rpc_call("projects.list", {})}
        self.assertEqual(rows["a00a00000001"]["worker_state"], "running")
        self.assertEqual(rows["a00a00000002"]["worker_state"], "waiting")
        self.assertEqual(rows["a00a00000002"]["worker_wait_position"], 1)

        got = server.rpc_call("projects.get", {"id": "a00a00000002"})
        self.assertEqual(got["worker_state"], "waiting")
        self.assertEqual(got["worker_kind"], "write")

        snapshot = {item["id"]: item for item in tick_waiting["worker_queue"]}
        self.assertEqual(snapshot["a00a00000001"]["worker_state"], "running")
        self.assertEqual(snapshot["a00a00000002"]["worker_state"], "waiting")
        self.assertEqual(snapshot["a00a00000002"]["worker_wait_position"], 1)
        self.assertNotIn("worker_queue", got)

    def test_idle_project_has_empty_worker_state(self):
        make_project("a00a00000003", status="gate", stage="剧本候选")
        tick = server.rpc_call("projects.tick", {"id": "a00a00000003"})
        self.assertEqual(tick["worker_state"], "")
        self.assertEqual(tick["worker_kind"], "")
        self.assertNotIn("worker_wait_position", tick)

    def test_missing_queue_does_not_start_workers(self):
        server._AGENT_QUEUE = None
        make_project("a00a00000004")
        tick = server.rpc_call("projects.tick", {"id": "a00a00000004"})
        self.assertEqual(tick["worker_state"], "")
        self.assertIsNone(server._AGENT_QUEUE)


class RenderStatusCopyTest(unittest.TestCase):
    def test_comfy_status_map_no_longer_says_cloud_queue(self):
        from end.pipeline import CLOUD_STATUS

        self.assertEqual(CLOUD_STATUS["pending"], "出片排队中")
        self.assertEqual(CLOUD_STATUS["queued"], "出片排队中")
        self.assertEqual(CLOUD_STATUS["running"], "正在出片")
        self.assertEqual(CLOUD_STATUS["completed"], "出片完成，正在下载")
        for text in CLOUD_STATUS.values():
            self.assertNotIn("云端排队中", text)
            self.assertNotIn("Comfy", text)

    def test_live_poll_status_uses_current_render_path_copy(self):
        from end.render_loop import remote_status_text

        self.assertEqual(remote_status_text("video", "PENDING"), "排队出片")
        self.assertEqual(remote_status_text("video", "queued"), "排队出片")
        self.assertEqual(remote_status_text("video", "RUNNING"), "正在出片")
        self.assertEqual(remote_status_text("image", "PENDING"), "排队生图")
        self.assertEqual(remote_status_text("video", "completed"), "completed")
        self.assertNotEqual(remote_status_text("video", "pending"), "等待中")
        self.assertNotEqual(remote_status_text("video", "pending"), "云端排队中")


if __name__ == "__main__":
    unittest.main()
