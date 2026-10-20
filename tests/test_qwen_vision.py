"""Qwen still readings stay on the intake note. Tests never call DashScope."""

import json
import os
import struct
import unittest
import urllib.error
import zlib
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from end.intake import enrich_brief, intake_note, load_intake, merge_intake, save_intake
from AI.vision import (
    DEFAULT_QWEN_VISION_MODEL,
    QwenVisionError,
    UNREAD,
    read_upload,
    vision_jpeg_bytes,
)
from end.render_loop import _ffmpeg_bin

ROOT = Path(__file__).resolve().parents[1]
HINT = "文字决定要拍成什么样：时长、广告、感觉、情节和结尾。用户没写的部分，用读图补上身份和场景。"
READING = "主体是一辆银色SUV，停在海边。建议一条海边公路的汽车广告。"


def tiny_png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeStream(object):
    def __init__(self, payload):
        self._lines = payload.splitlines(keepends=True)
        self._index = 0

    def readline(self):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def sse(chunks):
    lines = ["data: " + json.dumps(chunk, ensure_ascii=False) for chunk in chunks]
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


class QwenVisionTests(unittest.TestCase):
    def test_fake_stream_enters_the_note_and_the_brief(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeStream(
                sse(
                    [
                        {"choices": [{"delta": {"content": "主体是一辆银色SUV，停在海边。"}}]},
                        {"choices": [{"delta": {}}]},
                        {"choices": []},
                        {"choices": [{"delta": {"content": "建议一条海边公路的汽车广告。"}}]},
                    ]
                )
            )

        with patch("AI.vision.load_dotenv", lambda *args, **kwargs: None), patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_BASE_URL": "https://workspace.example/compatible-mode/v1",
                "QWEN_VISION_MODEL": "",
            },
        ), patch("AI.vision.vision_jpeg_bytes", return_value=b"\xff\xd8\xff\xd9"):
            text = read_upload("U1.png", opener=opener)

        self.assertEqual(text, READING)
        body = captured["body"]
        self.assertEqual(captured["url"], "https://workspace.example/compatible-mode/v1/chat/completions")
        self.assertEqual(captured["auth"], "Bearer test-key")
        self.assertEqual(body["model"], DEFAULT_QWEN_VISION_MODEL)
        self.assertEqual(body["modalities"], ["text"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        part = body["messages"][0]["content"][0]
        self.assertEqual(part["type"], "image_url")
        self.assertTrue(part["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("video_url", json.dumps(body))
        prompt = body["messages"][0]["content"][1]["text"]
        self.assertIn("主体种类", prompt)
        self.assertIn("不要编画面里没有的职业、载具或城市", prompt)
        self.assertNotIn("两轮车", prompt)
        item = {"id": "U1", "title": "海边", "narration": "上路吧", "reading": text}
        note = intake_note([item])
        brief = enrich_brief("基于这些素材做广告", [item])
        self.assertIn("素材读图", note)
        self.assertIn(READING, note)
        self.assertIn(READING, brief)
        self.assertIn(HINT, brief)
        self.assertIn("身份跟图", brief)
        self.assertIn("海边的汽车不能被说成两轮车", brief)
        self.assertIn("不要海边，改到清晨的城市", brief)
        self.assertIn("只有用户自己打出来的话", brief)
        spec = merge_intake({"assets": [], "shots": []}, [item])
        self.assertEqual(spec["assets"][0]["references"], ["U1"])
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            save_intake(tmp, [item])
            loaded = load_intake(tmp)
        self.assertEqual(loaded[0]["reading"], READING)
        self.assertIn(READING, enrich_brief("基于这些素材做广告", loaded))

    def test_model_override_is_sent(self):
        captured = {}

        def opener(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeStream(sse([{"choices": [{"delta": {"content": "海边的车"}}]}]))

        with patch("AI.vision.load_dotenv", lambda *args, **kwargs: None), patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_BASE_URL": "https://workspace.example/compatible-mode/v1",
                "QWEN_VISION_MODEL": "qwen-vision-test",
            },
        ), patch("AI.vision.vision_jpeg_bytes", return_value=b"\xff\xd8\xff\xd9"):
            text = read_upload("U1.png", opener=opener)
        self.assertEqual(text, "海边的车")
        self.assertEqual(captured["body"]["model"], "qwen-vision-test")

    def test_missing_key_does_not_request(self):
        def opener(request, timeout=None):
            raise AssertionError("should not request")

        with patch("AI.vision.load_dotenv", lambda *args, **kwargs: None), patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": ""}, clear=False
        ), patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            text = read_upload("missing.png", opener=opener)

        self.assertEqual(text, UNREAD)
        note = intake_note([{"id": "U1", "title": "海边", "narration": "", "reading": text}])
        brief = enrich_brief("做一条广告", [{"id": "U1", "title": "海边", "narration": "", "reading": text}])
        self.assertIn("素材读图", note)
        self.assertIn("尚未读图", note)
        self.assertIn("尚未读图", brief)

    def test_http_error_raises(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "denied",
                Message(),
                BytesIO(b"bad key"),
            )

        with patch("AI.vision.load_dotenv", lambda *args, **kwargs: None), patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_BASE_URL": "https://workspace.example/compatible-mode/v1",
            },
        ), patch("AI.vision.vision_jpeg_bytes", return_value=b"\xff\xd8\xff\xd9"):
            with self.assertRaises(QwenVisionError) as caught:
                read_upload("U1.png", opener=opener)
        self.assertIn("401", str(caught.exception))

    def test_missing_base_url_does_not_request(self):
        def opener(request, timeout=None):
            raise AssertionError("should not request")

        with patch("AI.vision.load_dotenv", lambda *args, **kwargs: None), patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "test-key", "DASHSCOPE_BASE_URL": ""},
        ), patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(QwenVisionError) as caught:
                read_upload("U1.png", opener=opener)
        self.assertIn("DASHSCOPE_BASE_URL", str(caught.exception))

    def test_env_example_names_dashscope_without_secrets(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DASHSCOPE_API_KEY=", text)
        self.assertIn("DASHSCOPE_BASE_URL=", text)
        self.assertIn("QWEN_VISION_MODEL", text)
        for line in text.splitlines():
            if line.startswith("DASHSCOPE_API_KEY=") or line.startswith("DASHSCOPE_BASE_URL="):
                self.assertEqual(line.split("=", 1)[1].strip(), "")

    def test_skill_keeps_identity_on_the_picture(self):
        phrases = (
            "身份跟图",
            "故事跟用户自己写下的话",
            "读图已定的身份不要再问",
            "不要给出和读图相反的选项",
            "用户原文里的感觉和故事照旧问",
            "只有用户自己打出来的话",
            "点选模型编的选项不算",
            "图上的旁白只做原句旁白",
            "不单独锁死剧情",
            "尚未读图",
        )
        for name in ("luxury-suv", "apple-product", "drama-product-skill", "drama-product-skill2"):
            text = (ROOT / "AI" / "skills" / name / "意图补充.md").read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, text, "%s missing %s" % (name, phrase))

    def test_composer_hint_is_shared(self):
        html = (ROOT / "front" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        self.assertIn(HINT, html)
        self.assertIn(HINT, script)
        self.assertIn('briefEl.placeholder = "写下你的方向"', script)
        self.assertNotIn("写下这段片子的背景", html)
        self.assertNotIn("写下这段片子的背景", script)
        self.assertNotIn("写下另一条片子的背景", script)

    def test_shrinks_a_still_to_jpeg(self):
        if not _ffmpeg_bin():
            self.skipTest("本机没有 ffmpeg")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "U1.png"
            path.write_bytes(tiny_png())
            data = vision_jpeg_bytes(path)
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))


if __name__ == "__main__":
    unittest.main()
