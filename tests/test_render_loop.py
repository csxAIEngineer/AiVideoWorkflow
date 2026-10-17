"""Checkpoints, failure reset, pause, and restart for the local render loop."""

import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from AI.cr_api.client import CRError, VideoTask
from AI.icreat_api.client import ImageTask
from end.render_loop import (
    _missing_subtitle_glyphs,
    assemble_shot_media,
    blank_step,
    build_subtitle_ass,
    choose_video_mode,
    delivery_prompt,
    image_prompt,
    display_caption,
    gate_for,
    image_size,
    load_run_state,
    mark_user_pause,
    _reference_url,
    nearest_aspect_ratio,
    note_local_image,
    reset_current_step,
    resume_paused,
    run_local_pipeline,
    save_run_state,
    should_resume_project,
    split_caption,
    subtitle_cues,
)
from end.workflow_from_spec import collect_steps, task_plan

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


PUBLIC_BASE = "http://203.0.113.10:8787"


class RenderLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self._env = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = PUBLIC_BASE
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"

    def tearDown(self):
        self.tmp.cleanup()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

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
        self.assertIsNone(cr.submits[0]["aspect_ratio"])
        self.assertEqual(cr.submits[0]["duration"], 5)
        self.assertEqual(cr.submits[0]["images"][0]["role"], "reference_image")
        self.assertEqual(cr.submits[0]["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertTrue((self.work / "outputs" / "joined.mp4").is_file())
        state = load_run_state(self.work)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["steps"]["A1"]["remote_task_id"], "old")

    def test_an_upload_still_stays_on_its_shot(self):
        assets, shots = collect_steps(
            {
                "assets": [
                    {"id": "U1", "title": "原图", "prompt": "直接用"},
                    {"id": "A1", "title": "人", "prompt": "一个人"},
                ],
                "shots": [{"id": "S01", "duration": 2, "prompt": "原图开场", "images": ["U1"]}],
            }
        )
        self.assertEqual([item["id"] for item in assets], ["A1"])
        self.assertEqual(shots[0]["images"], ["U1"])
        self.assertGreaterEqual(shots[0]["duration"], 4)

    def test_public_still_url_is_sent_and_a_local_file_is_not(self):
        image = self.work / "still.png"
        image.write_bytes(b"\x89PNG")
        self.assertEqual(
            _reference_url({"path": str(image), "url": "https://cdn.example/a.png"}),
            "https://cdn.example/a.png",
        )
        self.assertEqual(
            _reference_url(
                {"path": str(image), "url": "https://cdn.example/a.png", "local_override": True},
                project_id="fffdd6d6b188",
            ),
            "https://cdn.example/a.png",
        )
        hosted = _reference_url(
            {
                "path": str(self.work / "outputs" / "images" / "U1.png"),
                "url": "",
                "local_override": True,
            },
            project_id="fffdd6d6b188",
        )
        self.assertEqual(hosted, PUBLIC_BASE + "/media/projects/fffdd6d6b188/images/U1.png")
        with self.assertRaises(RuntimeError) as raised:
            _reference_url(
                {
                    "path": str(self.work / "outputs" / "images" / "A2.png"),
                    "url": "",
                    "local_override": False,
                },
                project_id="fffdd6d6b188",
            )
        self.assertIn("公网地址", str(raised.exception))
        with self.assertRaises(RuntimeError) as raised:
            _reference_url({"path": str(image), "url": ""})
        self.assertIn("公网地址", str(raised.exception))

        spec = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "她转身离开", "images": ["A1"]},
                {"id": "S02", "duration": 5, "prompt": "她继续往前走", "images": ["A1"]},
            ],
        }
        cr = FakeCR()

        def joiner(_clips, target):
            Path(target).write_bytes(b"joined")
            return target

        def burner(_raw, _ass, target):
            Path(target).write_bytes(b"burned")

        run_local_pipeline(
            spec,
            self.work / "two",
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
            joiner=joiner,
            burner=burner,
        )
        self.assertEqual(len(cr.submits), 2)
        for item in cr.submits:
            self.assertIsNone(item["aspect_ratio"])
            for media in item["images"]:
                self.assertTrue(media["url"].startswith("https://"))
        self.assertEqual(len(cr.submits[1]["images"]), 1)
        self.assertEqual(cr.submits[1]["images"][0]["url"], cr.submits[0]["images"][0]["url"])

    def test_local_still_uses_public_base_and_not_uguu(self):
        project_id = "host-still"
        image = self.work / "outputs" / "images" / "A1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG-local")
        note_local_image(self.work, project_id, "A1")
        step = load_run_state(self.work)["steps"]["A1"]
        hosted = "%s/media/projects/%s/images/A1.png" % (PUBLIC_BASE, project_id)
        self.assertTrue(step["local_override"])
        self.assertTrue(step["public_url"].startswith(PUBLIC_BASE))
        self.assertEqual(step["public_url"], hosted)
        state = load_run_state(self.work)
        state["steps"]["A1"]["public_url"] = ""
        save_run_state(self.work, state)

        cr = FakeCR()
        run_local_pipeline(
            ONE,
            self.work,
            project_id=project_id,
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
        )
        body = json.dumps(cr.submits)
        self.assertNotIn("uguu", body)
        self.assertEqual(cr.submits[0]["model"], "minimax-h3-image-to-video")
        self.assertIsNone(cr.submits[0]["aspect_ratio"])
        self.assertEqual(cr.submits[0]["images"][0]["url"], hosted)

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
        from end.render_loop import resume_paused

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
        from AI.cr_api.client import CRClient

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

    def test_subtitle_cues_follow_the_script_line(self):
        shown = display_caption("十几个号，粉丝加起来，没号多。")
        self.assertEqual(shown, "十几个号　粉丝加起来　没号多")
        self.assertEqual(split_caption(shown), ["十几个号　粉丝加起来", "没号多"])
        self.assertEqual(display_caption("五天，一百万？"), "五天　一百万？")
        cues = subtitle_cues(
            [
                {
                    "duration": 5,
                    "spoken": [{"speaker": "苏晚", "line": "再站一会儿就好。"}],
                }
            ]
        )
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "再站一会儿就好")
        self.assertEqual((cues[0]["start"], cues[0]["end"]), (0, 5))
        voiced = subtitle_cues(
            [
                {
                    "duration": 4,
                    "narration": "夜色已经很深了",
                    "spoken": [{"speaker": "她", "line": "走吧"}],
                }
            ]
        )
        self.assertEqual(voiced[0]["text"], "夜色已经很深了")
        prompt = delivery_prompt(
            {"prompt": "她抬头", "narration": "夜色已经很深了"},
            [{"speaker": "她", "line": "走吧"}],
            0,
            False,
        )
        self.assertIn("画外旁白", prompt)
        self.assertIn("<d>[Chinese] 夜色已经很深了</d>", prompt)
        self.assertNotIn("在画内开口", prompt)
        ass = build_subtitle_ass(cues)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:05.00,正片,,0,0,0,,再站一会儿就好", ass)
        self.assertIn("PlayResX: 768", ass)
        self.assertIn("PlayResY: 1344", ass)
        self.assertTrue(_missing_subtitle_glyphs("fontselect: failed to find any fallback with glyph 0x65E9"))
        self.assertFalse(_missing_subtitle_glyphs(""))

    def test_task_plan_ends_with_ffmpeg_subtitles(self):
        tasks = task_plan(SPEC)
        self.assertEqual([task["id"] for task in tasks[-2:]], ["join", "subtitles"])
        self.assertEqual(tasks[-1]["detail"], "ffmpeg 烧录 字幕.ass")

    def test_pipeline_burns_ass_after_join(self):
        spec = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "她转身离开",
                    "images": ["A1"],
                    "dialogue": "苏晚：再站一会儿就好。",
                }
            ],
        }
        burned = []

        def burner(raw, ass_path, target):
            burned.append((Path(raw).read_bytes(), Path(ass_path).read_text(encoding="utf-8")))
            Path(target).write_bytes(b"burned-mp4")

        result = run_local_pipeline(
            spec,
            self.work,
            icreat=FakeICreat(),
            cr=FakeCR(),
            sleeper=_noop_sleep,
            burner=burner,
        )
        self.assertEqual(result["video_file"], "joined.mp4")
        self.assertEqual((self.work / "outputs" / "joined.mp4").read_bytes(), b"burned-mp4")
        self.assertTrue((self.work / "outputs" / "concat.mp4").is_file())
        self.assertIn("再站一会儿就好", burned[0][1])
        state = load_run_state(self.work)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["steps"]["subtitles"]["status"], "done")
        self.assertEqual(state["steps"]["subtitles"]["output_file"], "字幕.ass")
        self.assertTrue((self.work / "outputs" / "字幕.ass").is_file())

    def test_pipeline_keeps_minimax_free_of_burned_text(self):
        spec = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "她转身离开",
                    "images": ["A1"],
                    "dialogue": "苏晚：再站一会儿就好。",
                }
            ],
        }
        cr = FakeCR()

        def burner(_raw, _ass_path, target):
            Path(target).write_bytes(b"burned-mp4")

        run_local_pipeline(spec, self.work, icreat=FakeICreat(), cr=cr, sleeper=_noop_sleep, burner=burner)
        prompt = cr.submits[0]["text"]
        self.assertIn("全程无字幕、无对白文字叠加", prompt)
        self.assertIn("<d>[Chinese] 再站一会儿就好。</d>", prompt)

    def test_submit_reads_spec_file_before_each_step(self):
        stale = {
            "title": "雨夜",
            "script": "",
            "assets": [{"id": "A1", "title": "主角", "prompt": "旧的提示词"}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "旧的镜头",
                    "images": ["A1"],
                    "dialogue": "苏晚：旧对白。",
                }
            ],
        }
        fresh = {
            "title": "雨夜",
            "script": "",
            "assets": [{"id": "A1", "title": "主角", "prompt": "新的提示词"}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "新的镜头",
                    "images": ["A1"],
                    "dialogue": "苏晚：新的对白。",
                }
            ],
        }
        (self.work / "spec.json").write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        burned = []

        def burner(_raw, ass_path, target):
            burned.append(Path(ass_path).read_text(encoding="utf-8"))
            Path(target).write_bytes(b"burned-mp4")

        icreat = FakeICreat()
        cr = FakeCR()
        run_local_pipeline(stale, self.work, icreat=icreat, cr=cr, sleeper=_noop_sleep, burner=burner)
        self.assertIn("新的提示词", icreat.submits[0]["prompt"])
        self.assertNotIn("旧的提示词", icreat.submits[0]["prompt"])
        self.assertIn("新的镜头", cr.submits[0]["text"])
        self.assertIn("新的对白", cr.submits[0]["text"])
        self.assertNotIn("旧的镜头", cr.submits[0]["text"])
        self.assertIn("新的对白", burned[0])
        self.assertNotIn("旧对白", burned[0])

    def test_open_task_does_not_resubmit_when_spec_changes(self):
        changed = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "主角", "prompt": "不该再提交的提示词"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "她转身离开", "images": ["A1"]}],
        }
        (self.work / "spec.json").write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        save_run_state(
            self.work,
            {
                "phase": "images",
                "steps": {
                    "A1": {
                        "kind": "image",
                        "status": "running",
                        "remote_task_id": "img-keep",
                        "poll_url": "",
                        "idempotency_key": "keep",
                        "attempts": 1,
                        "epoch": 0,
                        "output_file": "",
                        "public_url": "",
                        "local_override": False,
                        "error": "",
                    }
                },
            },
        )
        icreat = FakeICreat()
        run_local_pipeline(ONE, self.work, icreat=icreat, sleeper=_noop_sleep, images_only=True)
        self.assertEqual(icreat.submits, [])
        self.assertIn("img-keep", icreat.queries)

    def test_uploaded_still_is_not_a_reference_for_new_assets(self):
        project_id = "local-parent"
        image = self.work / "outputs" / "images" / "A1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG-local")
        note_local_image(self.work, project_id, "A1")
        step = load_run_state(self.work)["steps"]["A1"]
        self.assertTrue(step["local_override"])
        self.assertTrue(step["public_url"].startswith(PUBLIC_BASE))
        icreat = FakeICreat()
        cr = FakeCR()
        result = run_local_pipeline(
            SPEC,
            self.work,
            project_id=project_id,
            icreat=icreat,
            cr=cr,
            sleeper=_noop_sleep,
        )
        self.assertEqual(result["video_file"], "joined.mp4")
        self.assertEqual(len(icreat.submits), 1)
        self.assertIsNone(icreat.submits[0]["image"])
        self.assertNotIn("锁定同一张脸", icreat.submits[0]["prompt"])
        self.assertIn("同一个人淋着雨", icreat.submits[0]["prompt"])
        self.assertEqual(cr.submits[0]["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertNotIn("uguu", json.dumps(cr.submits))

    def test_replaced_still_opens_on_first_frame(self):
        project_id = "local-shot"
        image = self.work / "outputs" / "images" / "A1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG-local")
        note_local_image(self.work, project_id, "A1")
        state = load_run_state(self.work)
        state["steps"]["A1"]["public_url"] = "https://cdn.example/A1.png"
        save_run_state(self.work, state)
        icreat = FakeICreat()
        cr = FakeCR()
        run_local_pipeline(ONE, self.work, project_id=project_id, icreat=icreat, cr=cr, sleeper=_noop_sleep)
        self.assertEqual(icreat.submits, [])
        self.assertEqual(cr.submits[0]["model"], "minimax-h3-image-to-video")
        self.assertEqual([item["role"] for item in cr.submits[0]["images"]], ["first_frame"])
        self.assertEqual(cr.submits[0]["images"][0]["url"], "https://cdn.example/A1.png")
        self.assertNotIn("轮子在转", cr.submits[0]["text"])

    def test_mode_follows_the_shot_inputs(self):
        self.assertEqual(nearest_aspect_ratio(2160, 2880), "3:4")
        self.assertEqual(nearest_aspect_ratio(768, 1344), "9:16")
        portrait = _png(30, 40)
        self.assertEqual(image_size(_write_png(self.work / "portrait.png", portrait)), (30, 40))
        self.assertEqual(
            choose_video_mode({"images": ["U1"]}, user_image_ids=["U1"]),
            "first_frame",
        )
        self.assertEqual(
            choose_video_mode({"images": ["U1"], "end_image": "U2"}, user_image_ids=["U1"]),
            "first_last_frame",
        )
        self.assertEqual(choose_video_mode({"images": ["A1"]}), "reference")
        self.assertEqual(choose_video_mode({"images": []}), "text")
        self.assertEqual(choose_video_mode({"images": []}, continues=True), "reference")
        self.assertEqual(
            choose_video_mode({"images": ["U1"], "video_mode": "reference"}, user_image_ids=["U1"]),
            "reference",
        )
        with self.assertRaises(RuntimeError):
            choose_video_mode({"video_mode": "clip", "images": []})

        def url_for(_asset_id):
            return "data:image/png;base64,QQ==", None

        _aspect, continued = assemble_shot_media(
            {"id": "S02", "images": ["A2"]},
            "reference",
            url_for,
            "data:image/jpeg;base64,TAIL",
        )
        self.assertEqual([item["role"] for item in continued], ["reference_image", "reference_image"])
        self.assertTrue(continued[1]["url"].startswith("data:image/jpeg"))
        kept = delivery_prompt({"prompt": "她回头"}, [], 1, True, "reference")
        self.assertIn("轮子在转", kept)
        self.assertIn("不要照抄", kept)
        apple = delivery_prompt(
            {"prompt": "她回头"},
            [],
            1,
            True,
            "reference",
            template="apple-product",
        )
        self.assertNotIn("轮子在转", apple)
        self.assertNotIn("轮胎", apple)
        self.assertNotIn("同一台车", apple)
        self.assertNotIn("不要照抄", apple)
        product = image_prompt(
            {"id": "A04", "prompt": "纯白无缝影棚台面"},
            template="apple-product",
        )
        self.assertNotIn("轮胎", product)
        self.assertNotIn("车标", product)
        studio = image_prompt(
            {"id": "A04", "prompt": "纯白无缝影棚台面"},
            template="luxury-suv",
        )
        self.assertIn("轮胎接地", studio)
        self.assertIn("不要特写车标文字", studio)
        _aspect, opened = assemble_shot_media(
            {"id": "S02", "images": ["U2"]},
            "first_frame",
            url_for,
            "data:image/jpeg;base64,TAIL",
        )
        self.assertEqual([item["role"] for item in opened], ["first_frame"])
        locked = delivery_prompt({"prompt": "光扫过", "narration": "旁白"}, [], 1, False, "first_frame")
        self.assertIn("从这张首帧的可见内容开始", locked)
        self.assertIn("画外旁白", locked)
        self.assertNotIn("轮子在转", locked)
        self.assertNotIn("衣料", locked)
        self.assertNotIn("手持", locked)
        self.assertNotIn("不要照抄", locked)

    def test_upload_reference_is_sent_to_image2_and_video_uses_the_generated_still(self):
        project_id = "upload-ref"
        image = self.work / "outputs" / "images" / "U1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG-upload")
        note_local_image(self.work, project_id, "U1")
        state = load_run_state(self.work)
        self.assertTrue(state["steps"]["U1"]["local_override"])
        state["steps"]["U1"]["public_url"] = "https://cdn.example/U1.png"
        save_run_state(self.work, state)
        spec = {
            "title": "雨夜",
            "assets": [
                {"id": "U1", "title": "上传", "prompt": "原图不该再生成"},
                {"id": "A1", "title": "主角", "prompt": "一个女人站在车站", "references": ["U1"]},
                {"id": "A2", "title": "侧面", "prompt": "同一个人的左侧面", "depends_on": "A1"},
            ],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "她转身离开",
                    "images": ["A1"],
                    "narration": "她在等最后一班车",
                }
            ],
        }
        self.assertEqual(
            [task["id"] for task in task_plan(spec) if task["kind"] == "image"],
            ["A1", "A2"],
        )
        icreat = FakeICreat()
        cr = FakeCR()

        def joiner(_clips, target):
            Path(target).write_bytes(b"joined")
            return target

        def burner(_raw, _ass, target):
            Path(target).write_bytes(b"burned")

        run_local_pipeline(
            spec,
            self.work,
            project_id=project_id,
            icreat=icreat,
            cr=cr,
            sleeper=_noop_sleep,
            joiner=joiner,
            burner=burner,
        )
        self.assertEqual(len(icreat.submits), 2)
        self.assertEqual(icreat.submits[0]["image"], ["https://cdn.example/U1.png"])
        self.assertIn("锁定同一张脸", icreat.submits[0]["prompt"])
        self.assertEqual(icreat.submits[1]["image"], ["https://cdn.example/img-1.png"])
        self.assertNotIn("https://cdn.example/U1.png", icreat.submits[1]["image"])
        video = cr.submits[0]
        self.assertEqual(video["model"], "minimax-h3-reference-to-video")
        self.assertEqual(video["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertNotEqual(video["images"][0]["url"], "https://cdn.example/U1.png")
        self.assertIn("她在等最后一班车", video["text"])

    def test_text_and_end_frame_and_explicit_reference(self):
        text_spec = {
            "title": "海",
            "assets": [{"id": "A1", "title": "闲置", "prompt": "一张桌子"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": "海面慢慢亮起来", "images": []}],
        }
        cr = FakeCR()
        run_local_pipeline(text_spec, self.work, icreat=FakeICreat(), cr=cr, sleeper=_noop_sleep)
        self.assertEqual(cr.submits[0]["model"], "minimax-h3-text-to-video")
        self.assertEqual(cr.submits[0]["aspect_ratio"], "9:16")
        self.assertFalse(cr.submits[0]["images"])

        end_dir = self.work / "end"
        end_dir.mkdir()
        _seed_still(end_dir, "A1", _png(30, 40))
        _seed_still(end_dir, "A2", _png(40, 40))
        end_spec = {
            "title": "走到门口",
            "assets": [
                {"id": "A1", "title": "起", "prompt": "站在门口"},
                {"id": "A2", "title": "终", "prompt": "已经出门"},
            ],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "从站姿走到门口",
                    "images": ["A1"],
                    "end_image": "A2",
                }
            ],
        }
        cr = FakeCR()
        run_local_pipeline(end_spec, end_dir, icreat=FakeICreat(), cr=cr, sleeper=_noop_sleep)
        opened = cr.submits[0]
        self.assertEqual(opened["model"], "minimax-h3-image-to-video")
        self.assertIsNone(opened["aspect_ratio"])
        self.assertEqual([item["role"] for item in opened["images"]], ["first_frame", "last_frame"])
        self.assertIn("从这张首帧的可见内容开始", opened["text"])
        self.assertNotIn("轮子在转", opened["text"])
        self.assertNotIn("不要照抄", opened["text"])

        explicit = self.work / "explicit"
        explicit.mkdir()
        _seed_still(explicit, "U1", _png(30, 40), local=True)
        explicit_spec = {
            "title": "指定参考",
            "assets": [{"id": "A1", "title": "图", "prompt": "用户图", "references": ["U1"]}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "她转身离开",
                    "images": ["A1"],
                    "video_mode": "reference",
                }
            ],
        }
        icreat = FakeICreat()
        cr = FakeCR()
        run_local_pipeline(explicit_spec, explicit, icreat=icreat, cr=cr, sleeper=_noop_sleep)
        self.assertEqual(icreat.submits[0]["image"], ["https://cdn.example/U1.png"])
        chosen = cr.submits[0]
        self.assertEqual(chosen["model"], "minimax-h3-reference-to-video")
        self.assertIsNone(chosen["aspect_ratio"])
        self.assertEqual(chosen["images"][0]["role"], "reference_image")
        self.assertEqual(chosen["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertNotEqual(chosen["images"][0]["url"], "https://cdn.example/U1.png")
        self.assertIn("轮子在转", chosen["text"])

    def test_soundtrack_keeps_narration_out_of_the_h3_prompt(self):
        spec = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "prompt": "她抬头",
                    "images": ["A1"],
                    "narration": "夜色已经很深了",
                    "dialogue": "她：走吧。",
                }
            ],
        }
        recorded = []
        mixed_from = []

        class Mini(object):
            def synthesize(self, text, dest, voice_id=None):
                recorded.append(text)
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"voice")

            def generate_music(self, prompt, dest):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"music")

        def duration_of(path):
            if Path(path).suffix == ".mp3" and Path(path).name != "music.mp3":
                return 9.0
            return 2.5

        def mixer(_video, _music, cues, _voice_dir, target, film_end):
            if cues:
                mixed_from.append((cues[0]["end"], film_end))
            Path(target).write_bytes(b"mixed-film")

        def burner(raw, _ass, target):
            self.assertEqual(Path(raw).name, "mixed.mp4")
            Path(target).write_bytes(b"burned")

        cr = FakeCR()
        run_local_pipeline(
            spec,
            self.work,
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
            burner=burner,
            soundtrack="music_narration",
            brief="雨夜的车",
            minimax=Mini(),
            mixer=mixer,
            duration_of=duration_of,
        )
        prompt = cr.submits[0]["text"]
        self.assertNotIn("画外旁白", prompt)
        self.assertNotIn("在画内开口", prompt)
        self.assertIn("保留环境声", prompt)
        self.assertEqual(recorded, ["夜色已经很深了"])
        self.assertAlmostEqual(mixed_from[0][0], 2.5)
        self.assertAlmostEqual(mixed_from[0][1], 2.5)
        self.assertEqual((self.work / "outputs" / "concat.mp4").read_bytes(), b"mp4")
        self.assertEqual((self.work / "outputs" / "joined.mp4").read_bytes(), b"burned")
        cues = json.loads((self.work / "outputs" / "voice" / "cues.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(cues["timeline"][0]["end"], 2.5)

        quiet = []

        def on_progress(payload):
            if payload.get("soundtrack_note"):
                quiet.append(payload)

        run_local_pipeline(
            {
                "title": "雨夜",
                "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
                "shots": [{"id": "S01", "duration": 5, "prompt": "她抬头", "images": ["A1"]}],
            },
            self.work / "quiet",
            on_progress=on_progress,
            icreat=FakeICreat(),
            cr=FakeCR(),
            sleeper=_noop_sleep,
            burner=burner,
            soundtrack="music_narration",
            brief="雨夜的车",
            minimax=Mini(),
            mixer=mixer,
            duration_of=duration_of,
        )
        self.assertTrue(any(item.get("message") == "没有旁白，保留镜头原声" for item in quiet))
        self.assertTrue(all(item.get("soundtrack_note") == "没有旁白" for item in quiet))


def _png(width, height):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + (b"\xff\x00\x00" * width) for _index in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _write_png(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _seed_still(work, asset_id, data, local=False):
    folder = Path(work) / "outputs" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("%s.png" % asset_id)).write_bytes(data)
    if local:
        note_local_image(work, "mode-seed", asset_id)
        state = load_run_state(work)
        state["steps"][asset_id]["public_url"] = "https://cdn.example/%s.png" % asset_id
        save_run_state(work, state)
        return
    state = load_run_state(work)
    step = blank_step("image")
    step["status"] = "done"
    step["output_file"] = "images/%s.png" % asset_id
    step["public_url"] = "https://cdn.example/%s.png" % asset_id
    state["steps"][asset_id] = step
    save_run_state(work, state)


if __name__ == "__main__":
    unittest.main()
