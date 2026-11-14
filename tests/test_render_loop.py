"""Checkpoints, failure reset, pause, and restart for the local render loop."""

import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from AI.qwen_api.client import QwenError
from AI.qwen_api.image import ImageTask
from AI.qwen_api.video import VideoTask
from end.render_loop import (
    _ffmpeg_bin,
    _missing_subtitle_glyphs,
    _normalize_join_command,
    assemble_shot_media,
    blank_step,
    build_subtitle_ass,
    choose_video_mode,
    delivery_prompt,
    ensure_reference_min_side,
    _reference_publish_name,
    image_prompt,
    display_caption,
    gate_for,
    image_input_urls,
    image_size,
    load_run_state,
    mark_user_pause,
    _reference_url,
    nearest_aspect_ratio,
    note_local_image,
    RenderContext,
    reset_current_step,
    resume_paused,
    run_local_pipeline as _run_local_pipeline,
    save_run_state,
    should_resume_project,
    split_caption,
    subtitle_cues,
)
from end.workflow_from_spec import collect_steps, task_plan


def setUpModule():
    from tests.offline_guard import install

    install()


def tearDownModule():
    from tests.offline_guard import uninstall

    uninstall()


def _prime_review(work, spec, brief="", template=None, soundtrack="off"):
    """Let render cases pass the semantic gate with a fake that reports no issues.

    The production path still refuses a folder that has no review.
    """
    from end.script_review import confirm_script, review_delivery, review_script, set_reviewer, user_image_ids

    folder = Path(work)
    folder.mkdir(parents=True, exist_ok=True)
    body = dict(spec or {})
    if not str(body.get("script") or "").strip():
        body["script"] = "她在雨里等车。"
    spec_path = folder / "spec.json"
    if not spec_path.is_file():
        spec_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    internal = folder / "_内部_"
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "剧本.md").write_text(body["script"] + "\n", encoding="utf-8")
    set_reviewer(lambda _messages: '{"issues":[]}')
    try:
        review_script(folder, body, brief=brief)
        confirm_script(folder, brief=brief)

        def ids_for(shot, _index):
            return user_image_ids(folder, shot)

        review_delivery(
            folder,
            body,
            brief=brief,
            template=template,
            soundtrack=soundtrack,
            user_ids_for=ids_for,
        )
    finally:
        set_reviewer(None)
    return body


def run_local_pipeline(spec, *args, **kwargs):
    """Existing cases lock 9:16 when the spec never named a frame."""
    copied = dict(spec or {})
    brief = kwargs.get("brief") or ""
    if not copied.get("aspect_ratio") and not kwargs.get("aspect") and "画幅" not in str(brief):
        copied["aspect_ratio"] = "9:16"
    work = args[0] if args else kwargs.get("work_dir")
    if work is not None and kwargs.get("reviewer") is None:
        from end.soundtrack import normalize_soundtrack

        copied = _prime_review(
            work,
            copied,
            brief=brief,
            template=kwargs.get("template"),
            soundtrack=normalize_soundtrack(kwargs.get("soundtrack") or "off"),
        )
    return _run_local_pipeline(copied, *args, **kwargs)

SPEC = {
    "title": "雨夜",
    "aspect_ratio": "9:16",
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
    "aspect_ratio": "9:16",
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

    def submit_video(self, text, model="MiniMax/MiniMax-H3", duration=4, aspect_ratio="9:16", images=None, idempotency_key=None, timeout=60, mode=None):
        self.submits.append(
            {
                "key": idempotency_key,
                "model": model,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "images": images,
                "text": text,
                "mode": mode,
            }
        )
        if self.fail_submit:
            self.fail_submit -= 1
            raise QwenError("千问网络错误：timed out")
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
            raise QwenError("千问返回 429：rate limit", 429, "rate limit")
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
        self.assertEqual(cr.submits[0]["model"], "MiniMax/MiniMax-H3")
        self.assertIsNone(cr.submits[0]["aspect_ratio"])
        self.assertEqual(cr.submits[0]["duration"], 5)
        self.assertEqual(cr.submits[0]["images"][0]["role"], "reference_image")
        self.assertEqual(cr.submits[0]["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertEqual(cr.submits[0]["mode"], "generate")
        self.assertTrue((self.work / "outputs" / "joined.mp4").is_file())
        state = load_run_state(self.work)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["steps"]["A1"]["remote_task_id"], "old")

    def test_preview_parameter_mode_is_selected_for_retouch(self):
        from end.render_loop import RenderContext, normalize_video_submit_mode

        self.assertEqual(normalize_video_submit_mode(None), "generate")
        self.assertEqual(normalize_video_submit_mode("preview"), "preview")
        self.assertEqual(normalize_video_submit_mode("GENERATE"), "generate")
        self.assertEqual(normalize_video_submit_mode("draft"), "generate")
        preview = RenderContext(self.work / "preview-route", video_submit_mode="preview")
        self.assertEqual(preview.video_submit_mode, "preview")

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
        self.assertEqual(cr.submits[0]["model"], "MiniMax/MiniMax-H3")
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

    def test_image_submit_timeout_does_not_auto_resubmit(self):
        class Once(FakeICreat):
            def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
                self._n += 1
                self.submits.append({"prompt": prompt, "size": size, "image": image})
                if self._n == 1:
                    raise RuntimeError("生图超过 600 秒，没有自动重试，避免重复计费")
                return ImageTask({"task_id": "img-ok", "status": "SUCCEEDED"})

        icreat = Once()
        project_id = "no-retry-still"
        box = {}

        def runner():
            try:
                box["result"] = run_local_pipeline(
                    ONE,
                    self.work,
                    project_id=project_id,
                    icreat=icreat,
                    sleeper=_noop_sleep,
                    images_only=True,
                )
            except Exception as error:
                box["error"] = error

        thread = threading.Thread(target=runner)
        thread.start()
        self.assertTrue(gate_for(project_id).blocked.wait(5))
        self.assertEqual(len(icreat.submits), 1)
        state = load_run_state(self.work)
        self.assertEqual(state["paused_by"], "failure")
        self.assertIn("没有自动重试", state["pause_reason"])
        gate_for(project_id).resume()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", box)
        self.assertEqual(box["result"]["stopped_after"], "images")
        self.assertEqual(len(icreat.submits), 2)
        hosted = "%s/media/projects/%s/images/A1.png" % (PUBLIC_BASE, project_id)
        self.assertEqual(load_run_state(self.work)["steps"]["A1"]["public_url"], hosted)

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
        self.assertEqual(tasks[-1]["detail"], "写出 字幕.ass")

    def test_pipeline_writes_ass_without_burning_the_film(self):
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
        from unittest.mock import patch

        from end.soundtrack import mix_command

        def fail_burn(*_args, **_kwargs):
            raise AssertionError("成片不应再烧字幕")

        with patch("end.render_loop.ffmpeg_burn_subtitles", fail_burn), patch(
            "end.render_loop.burn_joined_subtitles", fail_burn
        ):
            result = run_local_pipeline(
                spec,
                self.work,
                icreat=FakeICreat(),
                cr=FakeCR(),
                sleeper=_noop_sleep,
            )
        self.assertEqual(result["video_file"], "joined.mp4")
        self.assertEqual((self.work / "outputs" / "joined.mp4").read_bytes(), b"mp4")
        self.assertFalse((self.work / "outputs" / "joined.burn.mp4").exists())
        ass = (self.work / "outputs" / "字幕.ass").read_text(encoding="utf-8")
        self.assertIn("再站一会儿就好", ass)
        log = (self.work / "outputs" / "render.log").read_text(encoding="utf-8")
        self.assertIn("字幕已另存，成片不烧字幕", log)
        self.assertNotIn("正在把字幕烧进成片", log)
        state = load_run_state(self.work)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["steps"]["subtitles"]["status"], "done")
        self.assertEqual(state["steps"]["subtitles"]["output_file"], "字幕.ass")
        join = _normalize_join_command(
            "ffmpeg",
            ["a.mp4", "b.mp4"],
            [{"has_audio": True, "duration": 2}, {"has_audio": True, "duration": 2}],
            "joined.mp4",
            "9:16",
        )
        mix = mix_command(
            "ffmpeg",
            "concat.mp4",
            "music.mp3",
            [{"file": "n.mp3", "start": 0, "end": 2}],
            "voice",
            "mixed.mp4",
            2,
        )
        for command in (join, mix):
            text = " ".join(command)
            self.assertNotIn("ass=", text)
            self.assertNotIn("subtitles=", text)
        self.assertIn("concat=", " ".join(join))
        self.assertIn("-map [a]", " ".join(join))
        self.assertIn("amix=", " ".join(mix))
        self.assertIn("-map 0:v", " ".join(mix))
        self.assertIn("-map [a]", " ".join(mix))

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

    def test_full_h3_delivery_bans_painted_captions_and_does_not_burn_ass(self):
        unit = "\n".join((
            "## 【投喂单元 1/1】",
            "duration: 8",
            "subject_definitions:",
            "<Subject 1> is a woman.",
            "summary:",
            "She waits.",
            "retention_analysis:",
            "<Subject 1>: fully_preserved.",
            "detailed_description:",
            "[Shot 1] She says <d>[Chinese] 等我。</d>",
            "[Shot 2] He answers <d>[Chinese] 你说煮水茶好有了吗</d>",
            "overall_soundscape:",
            "Rain.",
            "non_diegetic_music:",
            "none.",
        ))
        spec = {
            "title": "雨夜",
            "aspect_ratio": "9:16",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人站在车站"}],
            "feed": unit,
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "旧分镜",
                "images": ["A1"],
                "dialogue": "",
                "narration": "",
            }],
        }
        burned = []

        def burner(_raw, ass_path, target):
            burned.append(Path(ass_path).read_text(encoding="utf-8"))
            Path(target).write_bytes(b"burned-mp4")

        cr = FakeCR()
        run_local_pipeline(
            spec,
            self.work,
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
            burner=burner,
        )
        text = cr.submits[0]["text"]
        self.assertTrue(text.startswith(unit.strip()))
        self.assertIn("<d>[Chinese] 等我。</d>", text)
        self.assertIn("<d>[Chinese] 你说煮水茶好有了吗</d>", text)
        self.assertIn("No readable on-screen subtitles, captions, or overlaid dialogue text.", text)
        self.assertIn("Lines inside <d> are spoken audio only", text)
        self.assertIn("Chinese caption glyphs", text)
        self.assertNotIn("全程无字幕", text)
        self.assertNotIn("本镜无对白", text)
        self.assertNotIn("从这张首帧", text)
        self.assertNotIn("旧分镜", text)
        self.assertEqual(burned, [])
        self.assertFalse((self.work / "outputs" / "字幕.ass").exists())
        log = (self.work / "outputs" / "render.log").read_text(encoding="utf-8")
        self.assertIn("这一集没有对白，成片不烧字幕", log)

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
        from end.script_review import GenerationBlocked

        with self.assertRaises(GenerationBlocked):
            run_local_pipeline(stale, self.work, icreat=icreat, cr=cr, sleeper=_noop_sleep, burner=burner)
        self.assertEqual(icreat.submits, [])
        self.assertEqual(cr.submits, [])
        self.assertIn("新的提示词", (self.work / "spec.json").read_text(encoding="utf-8"))

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
        from end.script_review import GenerationBlocked

        with self.assertRaises(GenerationBlocked):
            run_local_pipeline(ONE, self.work, icreat=icreat, sleeper=_noop_sleep, images_only=True)
        self.assertEqual(icreat.submits, [])
        self.assertIn("img-keep", icreat.queries)
        self.assertNotEqual(load_run_state(self.work)["steps"]["A1"]["status"], "done")

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
        hosted = "%s/media/projects/%s/images/A2.png" % (PUBLIC_BASE, project_id)
        self.assertEqual(cr.submits[0]["images"][0]["url"], hosted)
        self.assertNotIn("cdn.example", cr.submits[0]["images"][0]["url"])
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
        self.assertEqual(cr.submits[0]["model"], "MiniMax/MiniMax-H3")
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
        self.assertEqual(choose_video_mode({"images": []}, continues=True), "text")
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
        self.assertEqual([item["role"] for item in continued], ["reference_image"])
        self.assertNotIn("TAIL", continued[0]["url"])
        _aspect, tailed = assemble_shot_media(
            {"id": "S02", "images": ["A2"], "video_mode": "first_frame", "first_frame_source": "previous_tail"},
            "first_frame",
            url_for,
            "data:image/jpeg;base64,TAIL",
        )
        self.assertEqual([item["role"] for item in tailed], ["first_frame"])
        self.assertTrue(tailed[0]["url"].startswith("data:image/jpeg"))
        kept = delivery_prompt({"prompt": "她回头"}, [], 1, True, "reference")
        self.assertNotIn("轮子在转", kept)
        self.assertIn("本镜无对白", kept)
        product = image_prompt({"id": "A04", "prompt": "纯白无缝影棚台面"})
        self.assertEqual(product, "纯白无缝影棚台面")
        self.assertNotIn("轮胎接地", product)
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
        hosted = "%s/media/projects/%s/images/A1.png" % (PUBLIC_BASE, project_id)
        self.assertEqual(icreat.submits[1]["image"], [hosted])
        self.assertNotIn("https://cdn.example/U1.png", icreat.submits[1]["image"])
        video = cr.submits[0]
        self.assertEqual(video["model"], "MiniMax/MiniMax-H3")
        self.assertEqual(video["images"][0]["url"], hosted)
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
        self.assertEqual(cr.submits[0]["model"], "MiniMax/MiniMax-H3")
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
        self.assertEqual(opened["model"], "MiniMax/MiniMax-H3")
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
        self.assertEqual(chosen["model"], "MiniMax/MiniMax-H3")
        self.assertIsNone(chosen["aspect_ratio"])
        self.assertEqual(chosen["images"][0]["role"], "reference_image")
        self.assertEqual(chosen["images"][0]["url"], "https://cdn.example/img-1.png")
        self.assertNotEqual(chosen["images"][0]["url"], "https://cdn.example/U1.png")
        self.assertNotIn("轮子在转", chosen["text"])

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

        cr = FakeCR()
        run_local_pipeline(
            spec,
            self.work,
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
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
        self.assertEqual((self.work / "outputs" / "joined.mp4").read_bytes(), b"mixed-film")
        self.assertIn("夜色已经很深了", (self.work / "outputs" / "字幕.ass").read_text(encoding="utf-8"))
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
            soundtrack="music_narration",
            brief="雨夜的车",
            minimax=Mini(),
            mixer=mixer,
            duration_of=duration_of,
        )
        self.assertTrue(any(item.get("message") == "没有旁白，保留镜头原声" for item in quiet))
        self.assertTrue(all(item.get("soundtrack_note") == "没有旁白" for item in quiet))

    def test_reference_publish_name_keeps_a_chinese_asset_id(self):
        self.assertEqual(_reference_publish_name("图1.png"), "图1_ref.png")
        self.assertEqual(_reference_publish_name("图1_3.png"), "图1_3_ref.png")
        self.assertEqual(_reference_publish_name("U2.png"), "U2_ref.png")

    def test_tiny_reference_still_is_enlarged_before_wan_submit(self):
        if not _ffmpeg_bin():
            self.skipTest("本机没有 ffmpeg")
        project_id = "tinyref"
        source = _write_png(self.work / "outputs" / "images" / "U2.png", _png(299, 168))
        self.assertEqual(image_size(source), (299, 168))
        hosted, relative = ensure_reference_min_side(
            self.work,
            project_id,
            "U2",
            "https://cdn.example/U2.png",
            source=source,
        )
        enlarged = self.work / "outputs" / relative
        width, height = image_size(enlarged)
        self.assertGreaterEqual(width, 240)
        self.assertGreaterEqual(height, 240)
        self.assertLess(abs((width / float(height)) - (299 / 168.0)), 0.02)
        self.assertTrue(hosted.split("?", 1)[0].endswith("/U2_ref.png"))
        self.assertEqual(image_size(source), (299, 168))
        log = (self.work / "outputs" / "render.log").read_text(encoding="utf-8")
        self.assertIn("参考图 U2 小于 240×240（299×168）", log)
        self.assertIn("已放大到", log)

        # A still that was published before this guard keeps the tiny URL in
        # run_state. The submit path has to replace it, and a retry must reuse
        # the enlarged URL instead of the original object.
        stale = self.work / "stale"
        stale_png = _write_png(stale / "outputs" / "images" / "U2.png", _png(299, 168))
        step = blank_step("image")
        step["status"] = "done"
        step["output_file"] = "images/U2.png"
        step["public_url"] = "https://cdn.example/U2.png"
        step["local_override"] = True
        state = load_run_state(stale)
        state["steps"]["U2"] = step
        save_run_state(stale, state)
        icreat = FakeICreat()
        ctx = RenderContext(stale, project_id=project_id, icreat=icreat, sleeper=_noop_sleep)
        asset = {"id": "A1", "prompt": "风与水的重逢", "references": ["U2"]}
        urls = image_input_urls(ctx, asset)
        icreat.submit_image("风与水的重逢", image=urls or None)
        self.assertEqual(len(icreat.submits), 1)
        submitted = icreat.submits[0]["image"]
        self.assertEqual(submitted, urls)
        self.assertEqual(len(submitted), 1)
        self.assertTrue(submitted[0].split("?", 1)[0].endswith("/U2_ref.png"))
        self.assertNotIn("cdn.example", submitted[0])
        saved = load_run_state(stale)["steps"]["U2"]
        self.assertEqual(saved["public_url"], submitted[0])
        self.assertEqual(saved["output_file"], "images/U2_ref.png")
        enlarged_width, enlarged_height = image_size(stale / "outputs" / "images" / "U2_ref.png")
        self.assertGreaterEqual(enlarged_width, 240)
        self.assertGreaterEqual(enlarged_height, 240)
        self.assertEqual(image_size(stale_png), (299, 168))
        self.assertEqual(image_input_urls(ctx, asset), submitted)
        stale_log = (stale / "outputs" / "render.log").read_text(encoding="utf-8")
        self.assertEqual(stale_log.count("已放大到"), 1)
        self.assertIn("299×168", stale_log)

    def test_text_aspect_comes_from_the_brief_and_a_missing_lock_does_not_default(self):
        wide = {
            "title": "海",
            "assets": [{"id": "A1", "title": "闲置", "prompt": "一张桌子"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": "海面慢慢亮起来", "images": []}],
        }
        cr = FakeCR()
        wide_dir = self.work / "wide"
        primed = _prime_review(wide_dir, wide, brief="画幅：16:9")
        _run_local_pipeline(
            primed,
            wide_dir,
            icreat=FakeICreat(),
            cr=cr,
            sleeper=_noop_sleep,
            brief="画幅：16:9",
        )
        self.assertEqual(cr.submits[0]["aspect_ratio"], "16:9")
        with self.assertRaises(RuntimeError) as caught:
            _run_local_pipeline(
                wide,
                self.work / "open",
                icreat=FakeICreat(),
                cr=FakeCR(),
                sleeper=_noop_sleep,
                brief="雨夜",
            )
        self.assertIn("没有锁定画幅", str(caught.exception))


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
