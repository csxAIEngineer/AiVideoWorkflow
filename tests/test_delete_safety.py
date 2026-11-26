"""Deleting a project must stop its worker and never let it resurrect.

Covers the tombstone in write_project, the render stop signal, waiting for the
worker to exit before removing files, and the OSS reference-URL presign fix.
"""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
import end.render_loop as rl
import end.server as server
from end.image_store import OssImageStore
from end.image_store import store as store_module

PID = "fffdd6d6b188"


class _Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "safety.db"
        db._CONN = None
        db.DATA.mkdir()
        db.clear_deleted(PID)
        rl.clear_stop(PID)
        rl._GATES.pop(PID, None)
        rl._THREADS.pop(PID, None)

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        db.clear_deleted(PID)
        rl.clear_stop(PID)
        rl._GATES.pop(PID, None)
        rl._THREADS.pop(PID, None)
        self.tmp.cleanup()

    def _project(self, title="t"):
        return {
            "id": PID,
            "brief": "b",
            "title": title,
            "status": "rendering",
            "stage": "",
            "messages": [],
            "events": [],
            "tasks": [],
            "progress": {},
        }


class TombstoneTest(_Isolated):
    def test_write_project_skips_a_deleted_row(self):
        db.write_project(self._project("original"))
        self.assertEqual(db.read_project(PID)["title"], "original")
        db.mark_deleted(PID)
        db.write_project(self._project("resurrected"))
        self.assertEqual(db.read_project(PID)["title"], "original")

    def test_delete_tombstones_and_blocks_resurrection(self):
        project = self._project()
        db.write_project(project)
        self.assertTrue(db.project_dir(PID).is_dir())
        self.assertTrue(db.delete_project(PID))
        self.assertIsNone(db.read_project(PID))
        self.assertFalse(db.project_dir(PID).exists())
        # A winding-down worker upsert must not bring the row or folder back.
        db.write_project(project)
        self.assertIsNone(db.read_project(PID))
        self.assertFalse(db.project_dir(PID).exists())

    def test_clear_deleted_reallows_a_fresh_write(self):
        db.mark_deleted(PID)
        db.write_project(self._project())
        self.assertIsNone(db.read_project(PID))
        db.clear_deleted(PID)
        db.write_project(self._project())
        self.assertIsNotNone(db.read_project(PID))


class StopSignalTest(_Isolated):
    def test_request_stop_unblocks_gate_and_sleep_raises(self):
        gate = rl.gate_for(PID)
        gate.pause("paused")
        self.assertFalse(gate.is_running())
        rl.request_stop(PID)
        self.assertTrue(gate.is_running())
        self.assertTrue(rl.render_stopped(PID))
        ctx = rl.RenderContext(work_dir=self.root, project_id=PID)
        with self.assertRaises(rl.RenderCancelled):
            ctx.sleep(0.01)
        with self.assertRaises(rl.RenderCancelled):
            ctx.block_while_paused()

    def test_wait_render_exit_returns_after_worker_stops(self):
        ctx = rl.RenderContext(work_dir=self.root, project_id=PID)
        done = threading.Event()

        def worker():
            rl.register_thread(PID)
            try:
                while True:
                    ctx.sleep(0.02)
            except rl.RenderCancelled:
                done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.assertTrue(rl.thread_alive(PID))
        rl.request_stop(PID)
        self.assertTrue(rl.wait_render_exit(PID, timeout=3))
        self.assertTrue(done.is_set())
        self.assertFalse(rl.thread_alive(PID))


class DeleteRpcTest(_Isolated):
    def test_delete_stops_worker_and_leaves_no_resurrection(self):
        project = self._project()
        db.write_project(project)
        ctx = rl.RenderContext(work_dir=db.project_dir(PID), project_id=PID)
        exited = threading.Event()

        def worker():
            rl.register_thread(PID)
            try:
                while True:
                    ctx.sleep(0.02)
            except rl.RenderCancelled:
                exited.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.assertTrue(rl.thread_alive(PID))

        result = server.rpc_delete_project({"id": PID})
        self.assertEqual(result, {"ok": True, "id": PID})
        self.assertTrue(exited.is_set())
        self.assertFalse(rl.thread_alive(PID))
        self.assertIsNone(db.read_project(PID))
        self.assertFalse(db.project_dir(PID).exists())

        # Even a late write from the dead worker's context cannot resurrect.
        db.write_project(project)
        self.assertIsNone(db.read_project(PID))
        self.assertFalse(db.project_dir(PID).exists())


class ReferenceUrlPresignTest(_Isolated):
    def test_publish_step_url_presigns_own_canonical_reference(self):
        store = OssImageStore(
            bucket="demo-bucket",
            domain="https://demo-bucket.oss-cn-hangzhou.aliyuncs.com",
            root=self.root,
            access_key_id="id",
            access_key_secret="secret",
        )
        canonical = "https://demo-bucket.oss-cn-hangzhou.aliyuncs.com/projects/%s/images/U1.png" % PID
        signed = canonical + "?OSSAccessKeyId=id&Expires=1795802558&Signature=abc%3D"
        ctx = rl.RenderContext(work_dir=self.root, project_id=PID)
        step = {"public_url": canonical, "output_file": "images/U1.png"}
        with patch.object(rl, "get_image_store", return_value=store), patch.object(
            store_module, "_oss_presign_get", return_value=signed
        ):
            url = rl._publish_step_url(ctx, "U1", step)
        self.assertEqual(url, signed)
        self.assertNotEqual(url, canonical)


if __name__ == "__main__":
    unittest.main()
