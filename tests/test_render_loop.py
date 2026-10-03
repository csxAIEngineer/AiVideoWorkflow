"""Checkpoints, failure reset, pause, and restart for the local render loop."""

import tempfile
import threading
import unittest
from pathlib import Path

from cr_api.client import CRError, VideoTask
from icreat_api.client import ImageTask
from web.render_loop import (
    gate_for,
    load_run_state,
    mark_user_pause,
    reset_current_step,
    run_local_pipeline,
    save_run_state,
    should_resume_project,
)

SPEC = {
    "title": "雨夜",
    "assets": [
        {"id": "A1", "title": "主角", "prompt": "一个女人站在车站"},
        {"id": "A2", "title": "雨中", "prompt": "同一个人淋着雨", "depends_on": "A1"},
    ],
    "shots": [
        {"id": "S01", "duration": 5, "prompt": "她转身离开", "images": ["A2"]},
    ],
}

ONE = {
    "title": "雨夜",
    "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
    "shots": [{"id": "S01", "duration": 5, "prompt": "她转身离开", "images": ["A1"]}],
}


class StopRender(BaseException):
    pass


def _noop_sleep(_seconds):
    return None


class FakeICreat(object):
    def __init__(self, fail_status=0, boom_on=None):
        self.fail_status = fail_status
        self.boom_on = boom_on
        self.submits = []
        self.queries = []
        self._n = 0

    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
        self._n += 1
        task_id = "img-%s" % self._n
        self.submits.append({"id": task_id, "prompt": prompt, "image": image, "size": size, "quality": quality})
        return ImageTask({"task_id": task_id, "status": "SUBMITTED"})

    def query_status(self, task_id, timeout=60):
        self.queries.append(task_id)
        if self.boom_on and task_id == self.boom_on and self.queries.count(task_id) == 1:
            raise StopRender()
        if self.fail_status:
            self.fail_status -= 1
            return ImageTask({"task_id": task_id, "status": "FAILED", "error": "remote boom"})
        return ImageTask({"task_id": task_id, "status": "SUCCEEDED"})

    def get_result(self, task_id, timeout=60):
        task = ImageTask({"task_id": task_id, "status": "SUCCEEDED"})
        task.url = "https://cdn.example/%s.png" % task_id
        return task

    def download_image(self, url, dest, timeout=120):
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG")
        return path


class FakeCR(object):
    def __init__(self, fail_submit=0, fail_poll=0):
        self.fail_submit = fail_submit
        self.fail_poll = fail_poll
        self.submits = []
        self.polls = []
        self._n = 0

    def submit_video(self, text, model="minimax-h3-text-to-video", duration=4, aspect_ratio="9:16", images=None, idempotency_key=None, timeout=60):
        self.submits.append(
            {
                "key": idempotency_key,
                "model": model,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "images": images,
                "text": text,
            }
        )
        if self.fail_submit:
            self.fail_submit -= 1
            raise CRError("CR API 网络错误：timed out")
        self._n += 1
        return VideoTask(
            {
                "id": "vid-%s" % self._n,
                "status": "submitted",
                "poll_url": "https://api.example/poll/%s" % self._n,
            }
        )

    def poll_video(self, task, timeout=60):
        self.polls.append({"id": task.id, "poll_url": task.poll_url})
        if self.fail_poll:
            self.fail_poll -= 1
            raise CRError("CR API 返回 429：rate limit", 429, "rate limit")
        return VideoTask(
            {
                "id": task.id,
                "status": "completed",
                "poll_url": task.poll_url,
                "data": [{"url": "https://cdn.example/%s.mp4" % task.id}],
            }
        )

    def download_video(self, task, dest, timeout=300):
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4")
        return path


class RenderLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_checkpoint_skips_finished_still_and_locks_face(self):
        image = self.work / "outputs" / "images" / "A1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG")
        save_run_state(
            self.work,
            {
                "phase": "images",
                "paused": False,
                "pause_reason": "",
                "paused_by": "",
                "current_step": "A1",
                "steps": {
                    "A1": {
                        "kind": "image",
                        "status": "done",
                        "remote_task_id": "old",
                        "poll_url": "",
                        "idempotency_key": "keep",
                        "attempts": 1,
                        "epoch": 0,
                        "output_file": "images/A1.png",
                        "public_url": "https://cdn.example/A1.png",
                        "error": "",
                    }
                },
            },
        )
        icreat = FakeICreat()
        cr = FakeCR()
        result = run_local_pipeline(SPEC, self.work, icreat=icreat, cr=cr, sleeper=_noop_sleep)
        self.assertEqual(result["video_file"], "joined.mp4")
        self.assertEqual(len(icreat.submits), 1)
        variant = icreat.submits[0]
        self.assertIn("锁定同一张脸", variant["prompt"])
        self.assertEqual(variant["image"], ["https://cdn.example/A1.png"])
        self.assertEqual(cr.submits[0]["model"], "minimax-h3-reference-to-video")
        self.assertEqual(cr.submits[0]["aspect_ratio"], "9:16")
        self.assertEqual(cr.submits[0]["duration"], 5)
        self.assertEqual(cr.submits[0]["images"][0]["role"], "reference_image")
        self.assertTrue((self.work / "outputs" / "joined.mp4").is_file())
        state = load_run_state(self.work)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["steps"]["A1"]["remote_task_id"], "old")

    def test_remote_failure_resets_step_then_pause_and_manual_reset(self):
        icreat = FakeICreat(fail_status=5)
        cr = FakeCR()
        project_id = "fail-step"
        box = {}

        def runner():
            try:
                box["result"] = run_local_pipeline(
                    ONE,
                    self.work,
                    project_id=project_id,
                    icreat=icreat,
                    cr=cr,
                    sleeper=_noop_sleep,
                )
            except Exception as error:
                box["error"] = error

        thread = threading.Thread(target=runner)
        thread.start()
        self.assertTrue(gate_for(project_id).blocked.wait(5))
        state = load_run_state(self.work)
        self.assertEqual(state["paused_by"], "failure")
        self.assertEqual(state["steps"]["A1"]["remote_task_id"], "")
        self.assertGreaterEqual(state["steps"]["A1"]["attempts"], 5)
        self.assertEqual(len(icreat.submits), 5)
        self.assertEqual(cr.submits, [])
        reset_current_step(self.work, project_id)
        gate_for(project_id).resume()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", box)
        self.assertEqual(box["result"]["video_file"], "joined.mp4")
        self.assertEqual(len(icreat.submits), 6)
        self.assertTrue((self.work / "outputs" / "images" / "A1.png").is_file())

    def test_transient_keeps_task_and_idempotency_key(self):
        icreat = FakeICreat()
        cr = FakeCR(fail_submit=1, fail_poll=1)
        run_local_pipeline(ONE, self.work, icreat=icreat, cr=cr, sleeper=_noop_sleep)
        self.assertEqual(len(cr.submits), 2)
        self.assertTrue(cr.submits[0]["key"])
        self.assertEqual(cr.submits[0]["key"], cr.submits[1]["key"])
        self.assertEqual(len(cr.polls), 2)
        self.assertEqual(cr.polls[0]["id"], cr.polls[1]["id"])
        self.assertTrue(cr.polls[0]["poll_url"])

    def test_pause_between_polls_and_resume(self):
        icreat = FakeICreat()
        real_query = icreat.query_status

        def query_status(task_id, timeout=60):
            if len(icreat.queries) == 0:
                icreat.queries.append(task_id)
                return ImageTask({"task_id": task_id, "status": "IN_PROGRESS"})
            return real_query(task_id, timeout)

        icreat.query_status = query_status
        cr = FakeCR()
        project_id = "pause-step"
        ready = threading.Event()
        hold = threading.Event()

        def sleeper(seconds):
            if seconds == 4 and not ready.is_set():
                ready.set()
                self.assertTrue(hold.wait(5))

        def runner():
            run_local_pipeline(
                ONE,
                self.work,
                project_id=project_id,
                icreat=icreat,
                cr=cr,
                sleeper=sleeper,
            )

        thread = threading.Thread(target=runner)
        thread.start()
        self.assertTrue(ready.wait(5))
        mark_user_pause(self.work, project_id, "已暂停")
        hold.set()
        self.assertTrue(gate_for(project_id).blocked.wait(5))
        self.assertEqual(len(icreat.queries), 1)
        self.assertEqual(cr.submits, [])
        from web.render_loop import resume_paused

        resume_paused(self.work, project_id)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(icreat.queries), 2)
        self.assertEqual(load_run_state(self.work)["phase"], "done")

    def test_restart_continues_the_open_task(self):
        first = FakeICreat(boom_on="img-2")
        second = FakeICreat()
        cr = FakeCR()
        with self.assertRaises(StopRender):
            run_local_pipeline(SPEC, self.work, icreat=first, cr=cr, sleeper=_noop_sleep)
        state = load_run_state(self.work)
        self.assertEqual(state["steps"]["A1"]["status"], "done")
        self.assertTrue(state["steps"]["A2"]["remote_task_id"])
        self.assertTrue((self.work / "outputs" / "images" / "A1.png").is_file())
        run_local_pipeline(SPEC, self.work, icreat=second, cr=cr, sleeper=_noop_sleep)
        self.assertEqual(second.submits, [])
        self.assertIn(state["steps"]["A2"]["remote_task_id"], second.queries)
        self.assertEqual(len(cr.submits), 1)

    def test_images_only_stops_before_video(self):
        icreat = FakeICreat()
        cr = FakeCR()
        result = run_local_pipeline(
            SPEC,
            self.work,
            icreat=icreat,
            cr=cr,
            sleeper=_noop_sleep,
            images_only=True,
        )
        self.assertEqual(result["stopped_after"], "images")
        self.assertEqual(result["video_file"], "")
        self.assertEqual(len(icreat.submits), 2)
        self.assertEqual(cr.submits, [])
        self.assertEqual(cr.polls, [])
        self.assertTrue((self.work / "outputs" / "images" / "A1.png").is_file())
        self.assertTrue((self.work / "outputs" / "images" / "A2.png").is_file())
        self.assertFalse((self.work / "outputs" / "joined.mp4").exists())
        self.assertEqual(load_run_state(self.work)["phase"], "images")

    def test_should_resume_only_unfinished_rendering(self):
        spec = self.work / "spec.json"
        spec.write_text("{}", encoding="utf-8")
        self.assertFalse(should_resume_project({"status": "paused"}, self.work))
        self.assertTrue(should_resume_project({"status": "rendering"}, self.work))
        video = self.work / "outputs" / "joined.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"mp4")
        save_run_state(self.work, {"phase": "done", "steps": {}})
        self.assertFalse(should_resume_project({"status": "rendering"}, self.work))

    def test_wait_video_reports_status_changes(self):
        from cr_api.client import CRClient

        class Client(CRClient):
            def __init__(self):
                self.api_key = "test"
                self.base_url = "https://example.invalid"
                self.left = ["processing", "completed"]

            def poll_video(self, task, timeout=60):
                status = self.left.pop(0)
                payload = {"id": task.id, "status": status, "poll_url": task.poll_url}
                if status == "completed":
                    payload["data"] = [{"url": "https://cdn.example/a.mp4"}]
                return VideoTask(payload)

        seen = []
        task = VideoTask({"id": "vid-1", "status": "submitted", "poll_url": "https://example/poll"})
        finished = Client().wait_video(task, interval=0, on_status=lambda item: seen.append(item.status))
        self.assertEqual(seen, ["submitted", "processing", "completed"])
        self.assertEqual(finished.status, "completed")


if __name__ == "__main__":
    unittest.main()
