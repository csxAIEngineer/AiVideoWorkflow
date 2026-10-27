"""The prepared feed and 图 blocks are what image, video, and speech receive."""

import tempfile
import unittest
from pathlib import Path

from AI.qwen_api.image import ImageTask
from AI.qwen_api.video import VideoTask
from end.board_shots import apply_storyboard_shots, shots_from_storyboard
from end.prompt_gate import feed_text
from end.prompt_handoff import (
    choose_image_text,
    feed_units,
    prepare_delivery_spec,
    video_mode_for,
)
from end.render_loop import assemble_shot_media, delivery_prompt, run_local_pipeline
from end.server import _merge_spec
from end.soundtrack import prepare_lines
from end.workflow_from_spec import collect_steps

BLOCK = "#### 图1 · 女主林浅\n雨夜车站，黑色大衣，正面半身，脸部清楚。\n"
UNIT = "\n".join((
    "## 【投喂单元 1/1 · 段1】",
    "duration: 8",
    "subject_definitions:",
    "<Subject 1> is a woman in a black coat.",
    "summary:",
    "She waits at the station.",
    "retention_analysis:",
    "<Subject 1>: fully_preserved for the woman.",
    "detailed_description:",
    "[Shot 1] She stands under the eave and says <d>[Chinese] 等我。</d>",
    "overall_soundscape:",
    "Rain and room tone.",
    "non_diegetic_music:",
    "none.",
))
PLACEHOLDER = "按这张资产图演这一镜"


def _spec():
    return {
        "title": "雨夜",
        "aspect_ratio": "9:16",
        "image_prompts": BLOCK,
        "feed": UNIT,
        "assets": [{"id": "图1", "title": "女主林浅", "prompt": "女主"}],
        "shots": [{
            "id": "S01",
            "duration": 5,
            "prompt": PLACEHOLDER,
            "images": ["图1"],
            "dialogue": "",
            "narration": "",
        }],
    }


class FakeImage(object):
    def __init__(self):
        self.submits = []

    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
        self.submits.append({"prompt": prompt, "size": size, "image": image})
        return ImageTask({"task_id": "img-1", "status": "SUCCEEDED"})

    def query_status(self, task_id, timeout=60):
        return ImageTask({"task_id": task_id, "status": "SUCCEEDED"})

    def get_result(self, task_id, timeout=60):
        task = ImageTask({"task_id": task_id, "status": "SUCCEEDED"})
        task.url = "https://cdn.example/img-1.png"
        return task

    def download_image(self, url, dest, timeout=120):
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG")
        return path


class FakeVideo(object):
    def __init__(self):
        self.submits = []

    def submit_video(self, text, model=None, duration=4, aspect_ratio=None, images=None, idempotency_key=None, timeout=180):
        self.submits.append({
            "text": text,
            "model": model,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "images": images,
        })
        return VideoTask({"task_id": "vid-1", "status": "completed", "url": "https://cdn.example/a.mp4"})

    def poll_video(self, task, timeout=60):
        done = VideoTask({"task_id": getattr(task, "id", ""), "status": "completed"})
        done.url = "https://cdn.example/a.mp4"
        return done

    def download_video(self, task, dest, timeout=300):
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4")
        return path


class FakeVoice(object):
    def __init__(self):
        self.lines = []

    def synthesize(self, text, dest, voice_id=None):
        self.lines.append(text)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"mp3")
        return dest


class HandoffTest(unittest.TestCase):
    def test_image_block_and_feed_unit_are_the_payloads(self):
        work = Path(tempfile.mkdtemp())
        images = FakeImage()
        videos = FakeVideo()
        voice = FakeVoice()
        burned = []

        def burner(_raw, ass_path, target):
            burned.append(Path(ass_path).read_text(encoding="utf-8"))
            Path(target).write_bytes(b"burned")

        run_local_pipeline(
            _spec(),
            work,
            icreat=images,
            cr=videos,
            sleeper=lambda _seconds: None,
            burner=burner,
            soundtrack="narration",
            minimax=voice,
            duration_of=lambda _path: 8,
        )
        self.assertEqual(images.submits[0]["prompt"], BLOCK.strip())
        self.assertEqual(images.submits[0]["size"], "768*1344")
        self.assertIsNone(images.submits[0]["image"])
        sent = videos.submits[0]
        self.assertEqual(sent["text"], UNIT.strip())
        self.assertEqual(sent["model"], "wan3.0-video")
        self.assertEqual(sent["duration"], 8)
        self.assertIsNone(sent["aspect_ratio"])
        self.assertEqual([item["role"] for item in sent["images"]], ["reference_image"])
        self.assertNotIn("本镜无对白", sent["text"])
        self.assertNotIn("non_diegetic_music: N/A", sent["text"])
        self.assertNotIn("从这张首帧", sent["text"])
        self.assertNotIn(PLACEHOLDER, sent["text"])
        self.assertEqual(voice.lines, [])
        self.assertIn("等我", burned[0])

    def test_speech_uses_only_lines_the_feed_left_out(self):
        spec = _spec()
        spec["shots"][0]["narration"] = "这一句不在投喂稿里"
        _assets, shots = collect_steps(spec)
        self.assertIn("等我", shots[0]["narration"])
        self.assertIn("这一句不在投喂稿里", shots[0]["narration"])
        self.assertEqual(shots[0]["tts_text"], "这一句不在投喂稿里")
        folder = Path(tempfile.mkdtemp())
        voice = FakeVoice()
        _lines, requests = prepare_lines(folder, shots, {}, voice, lambda _path: 1, "narration")
        self.assertEqual(requests, 1)
        self.assertEqual(voice.lines, ["这一句不在投喂稿里"])

    def test_two_units_keep_their_own_text(self):
        second = UNIT.replace("段1】", "段2】").replace("1/1", "2/2").replace("等我。", "走吧。")
        first = UNIT.replace("1/1", "1/2")
        spec = _spec()
        spec["feed"] = first + "\n\n" + second
        spec["shots"] = [
            {"id": "S01", "duration": 5, "prompt": PLACEHOLDER, "images": ["图1"]},
            {"id": "S02", "duration": 5, "prompt": "中景\n她回头", "images": ["图1"]},
        ]
        _assets, shots = collect_steps(spec)
        units = feed_units(spec["feed"])
        self.assertEqual([shot["prompt"] for shot in shots], [unit["text"] for unit in units])
        self.assertEqual([shot["id"] for shot in shots], ["S01", "S02"])
        self.assertNotIn("中景", shots[1]["prompt"])
        self.assertIn("走吧", shots[1]["prompt"])

    def test_feed_text_and_merge_keep_the_delivery(self):
        spec = _spec()
        self.assertEqual(feed_text(spec), UNIT.strip())
        self.assertNotIn(PLACEHOLDER, feed_text(spec))
        merged = _merge_spec({"title": "旧"}, {"feed": UNIT, "previs": "## 段 1"})
        self.assertEqual(merged["feed"], UNIT)
        self.assertEqual(merged["previs"], "## 段 1")
        untouched = apply_storyboard_shots({
            "feed": UNIT,
            "storyboard": "[{'shot_id': '镜头 01', 'visual_content': '短'}]",
            "shots": [{"id": "S01", "prompt": PLACEHOLDER, "images": ["图1"]}],
        })
        self.assertEqual(untouched["shots"][0]["prompt"], PLACEHOLDER)

    def test_short_column_prompt_is_not_the_only_text_when_the_row_is_longer(self):
        shots = shots_from_storyboard(
            [{"prompt": "完整的镜头正文" * 5, "camera_spec": "中景", "visual_content": "短"}],
            [],
        )
        self.assertIn("完整的镜头正文", shots[0]["prompt"])
        self.assertNotEqual(shots[0]["prompt"], "中景\n短")

    def test_a_longer_asset_edit_stays_and_a_short_line_does_not(self):
        self.assertEqual(choose_image_text("女主", BLOCK), BLOCK.strip())
        edited = "用户后来改成的另一套外观描述。" * 8
        self.assertEqual(choose_image_text(edited, BLOCK), edited)
        prepared = prepare_delivery_spec(_spec())
        self.assertEqual(prepared["assets"][0]["prompt"], BLOCK.strip())

    def test_frame_roles_follow_the_feed_instruction(self):
        pictures = ["图1"]
        self.assertEqual(video_mode_for(UNIT, pictures), "reference")
        last = "L2VA: Picture 1 aligns with the end of the target video.\n"
        self.assertEqual(video_mode_for(last, pictures), "last_frame")
        opening = "I2VA: Picture 1 is fully referenced.\nintegrated_multimodal_description:\nShe waits.\n"
        self.assertEqual(video_mode_for(opening, pictures), "first_frame")

        def url_for(_asset_id):
            return "https://cdn.example/a.png", None

        _aspect, media = assemble_shot_media(
            {"id": "S01", "images": ["图1"], "video_mode": "last_frame"},
            "last_frame",
            url_for,
        )
        self.assertEqual(media, [{"role": "last_frame", "url": "https://cdn.example/a.png"}])

    def test_plain_shot_still_gets_the_room_tone_lines(self):
        prompt = delivery_prompt({"prompt": "她回头"}, [], 1, False, "reference")
        self.assertIn("本镜无对白", prompt)
        self.assertIn("non_diegetic_music: N/A", prompt)
        kept = delivery_prompt({"prompt": UNIT}, [], 0, False, "first_frame")
        self.assertEqual(kept, UNIT.strip())
