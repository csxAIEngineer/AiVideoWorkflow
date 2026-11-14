"""MiniMax-H3-Image stills. HTTP is mocked. Nothing here touches the network."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from AI.minimax_api.image import (
    DEFAULT_BASE_URL,
    IMAGE_MODEL,
    KEY_VAR,
    MiniMaxImageClient,
    MiniMaxImageError,
    image_size_fields,
)
from end.render_loop import _publish_generated_still, _png_still_bytes

PUBLIC_BASE = "http://203.0.113.10:8787"


class _Body(object):
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")

    def read(self, _limit=-1):
        data = self.payload
        self.payload = b""
        return data

    def close(self):
        return None


def _ok(url="https://cdn.minimax.test/a.png", request_id="req-1", revised="雨夜增强"):
    return {
        "request_id": request_id,
        "output_format": "png",
        "data": [{"url": url, "size": "768x1344", "revised_prompt": revised}],
    }


class MiniMaxImageTest(unittest.TestCase):
    def _client(self, opener, api_key="still-key"):
        return MiniMaxImageClient(
            api_key=api_key,
            opener=opener,
            env_path=Path("/tmp/aivideo-no-minimax-env"),
        )

    def test_key_comes_from_minimax_api_key_not_the_video_key(self):
        def opener(_request, timeout=None):
            raise AssertionError("未发请求")

        with patch.dict(os.environ, {KEY_VAR: "", "MINIMAX_H3_API_KEY": "video-key"}, clear=False):
            with self.assertRaises(MiniMaxImageError) as caught:
                MiniMaxImageClient(opener=opener, env_path=Path("/tmp/aivideo-no-minimax-env"))
        self.assertIn(KEY_VAR, str(caught.exception))
        self.assertNotIn("video-key", str(caught.exception))

        captured = {}

        def record(request, timeout=None):
            captured["auth"] = request.get_header("Authorization")
            return _Body(json.dumps(_ok()))

        with patch.dict(os.environ, {KEY_VAR: "from-env", "MINIMAX_H3_API_KEY": "video-key"}, clear=False):
            client = MiniMaxImageClient(opener=record, env_path=Path("/tmp/aivideo-no-minimax-env"))
            client.submit_image("雨夜")
        self.assertEqual(client.api_key, "from-env")
        self.assertEqual(captured["auth"], "Bearer from-env")

    def test_text_only_posts_generations_and_known_frames_stay_explicit(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Body(json.dumps(_ok()))

        task = self._client(opener).submit_image("雨夜车站", size="9:16", timeout=60)
        self.assertEqual(task.status, "SUCCEEDED")
        self.assertEqual(task.id, "req-1")
        self.assertEqual(task.url, "https://cdn.minimax.test/a.png")
        self.assertEqual(task.revised_prompt, "雨夜增强")
        self.assertEqual(captured["url"], DEFAULT_BASE_URL + "/images/generations")
        self.assertEqual(captured["timeout"], 600)
        self.assertEqual(captured["auth"], "Bearer still-key")
        body = captured["body"]
        self.assertEqual(body["model"], IMAGE_MODEL)
        self.assertEqual(body["model"], "MiniMax-H3-Image")
        self.assertNotIn("Highspeed", body["model"])
        self.assertEqual(body["prompt"], "雨夜车站")
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["response_format"], "url")
        self.assertEqual(body["output_format"], "png")
        self.assertEqual(body["size"], "768x1344")
        self.assertNotIn("images", body)
        self.assertNotIn("stream", body)
        self.assertNotIn("disable_prompt_enhanced", body)
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("area", body)

        self.assertEqual(image_size_fields("16:9"), {"size": "1344x768"})
        self.assertEqual(image_size_fields("21:9"), {"size": "1792x768"})
        self.assertEqual(image_size_fields("768*1344"), {"size": "768x1344"})
        self.assertEqual(image_size_fields(""), {"area": "2K"})
        self.assertEqual(image_size_fields("3:4"), {"aspect_ratio": "3:4", "area": "2K"})

    def test_reference_images_post_edits(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Body(json.dumps(_ok(request_id="edit-1")))

        task = self._client(opener).submit_image(
            "保持脸不变",
            image=["https://cdn.example/ref.png", " https://cdn.example/side.png "],
            size="768*1344",
        )
        self.assertEqual(task.id, "edit-1")
        self.assertEqual(captured["url"], DEFAULT_BASE_URL + "/images/edits")
        body = captured["body"]
        self.assertEqual(
            body["images"],
            [
                {"image_url": "https://cdn.example/ref.png"},
                {"image_url": "https://cdn.example/side.png"},
            ],
        )
        self.assertEqual(body["size"], "768x1344")
        self.assertNotIn("disable_prompt_enhanced", body)
        self.assertNotIn("/generations", captured["url"])

    def test_success_is_not_submitted_again_when_queried(self):
        calls = {"n": 0}

        def opener(request, timeout=None):
            calls["n"] += 1
            if request.get_method() == "GET":
                return _Body(b"\x89PNG\r\n\x1a\nstill")
            return _Body(json.dumps(_ok()))

        client = self._client(opener)
        task = client.submit_image("雨夜", size="16:9")
        again = client.query_status(task.id)
        result = client.get_result(task.id)
        dest = Path(tempfile.mkdtemp()) / "hero.png"
        saved = client.download_image(result.url, dest)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(again.url, task.url)
        self.assertEqual(saved.read_bytes(), b"\x89PNG\r\n\x1a\nstill")
        with self.assertRaises(MiniMaxImageError) as lost:
            client.query_status("missing")
        self.assertIn("没有重新生成", str(lost.exception))
        self.assertEqual(calls["n"], 2)

    def test_error_item_and_http_errors_fail_once(self):
        calls = {"n": 0}

        def error_item(request, timeout=None):
            calls["n"] += 1
            return _Body(
                json.dumps(
                    {
                        "request_id": "bad-1",
                        "data": [{"error": {"code": "1027", "message": "output image failed content safety check"}}],
                    }
                )
            )

        with self.assertRaises(MiniMaxImageError) as safety:
            self._client(error_item).submit_image("雨夜")
        self.assertEqual(safety.exception.status, 422)
        self.assertIn("内容安全", str(safety.exception))
        self.assertEqual(calls["n"], 1)

        def missing_url(request, timeout=None):
            calls["n"] += 1
            return _Body(json.dumps({"request_id": "bad-2", "data": [{"size": "768x1344"}]}))

        with self.assertRaises(MiniMaxImageError) as missing:
            self._client(missing_url).submit_image("雨夜")
        self.assertIn("没有图片地址", str(missing.exception))

        def http_error(status, payload):
            def opener(request, timeout=None):
                calls["n"] += 1
                raise urllib.error.HTTPError(
                    request.full_url,
                    status,
                    "no",
                    hdrs=None,
                    fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
                )

            return opener

        with self.assertRaises(MiniMaxImageError) as blocked:
            self._client(http_error(422, {"error": {"message": "content safety"}})).submit_image("雨夜")
        self.assertEqual(blocked.exception.status, 422)
        self.assertIn("内容安全", str(blocked.exception))
        with self.assertRaises(MiniMaxImageError) as billing:
            self._client(http_error(402, {"error": {"message": "insufficient balance"}, "request_id": "bill-1"})).submit_image("雨夜")
        self.assertEqual(billing.exception.status, 402)
        self.assertIn("余额不足", str(billing.exception))
        self.assertIn("bill-1", str(billing.exception))

        def timed_out(request, timeout=None):
            calls["n"] += 1
            raise TimeoutError("timed out")

        before = calls["n"]
        with self.assertRaises(MiniMaxImageError) as late:
            self._client(timed_out).submit_image("雨夜")
        self.assertIn("没有自动重试", str(late.exception))
        self.assertNotIn("timed out", str(late.exception))
        self.assertEqual(calls["n"], before + 1)

    def test_bad_size_and_too_many_references_do_not_post(self):
        def opener(request, timeout=None):
            raise AssertionError("未发请求")

        client = self._client(opener)
        with self.assertRaises(MiniMaxImageError):
            client.submit_image("雨夜", size="100x100")
        with self.assertRaises(MiniMaxImageError) as many:
            client.submit_image("雨夜", image=["https://cdn.example/%s.png" % index for index in range(17)])
        self.assertIn("16", str(many.exception))
        with self.assertRaises(MiniMaxImageError):
            client.submit_image("  ")

    def test_image_submit_rpc_waits_out_the_sync_call(self):
        seen = {}

        def fake_call(method, params=None, timeout=60):
            seen["method"] = method
            seen["timeout"] = timeout
            seen["params"] = params
            return {"id": "req-9", "status": "SUCCEEDED", "url": "https://cdn.minimax.test/a.png", "error": ""}

        with patch("end.remote.call", fake_call):
            from end.remote import RemoteImageClient

            task = RemoteImageClient().submit_image("雨夜", size="9:16", image=None, timeout=60)
        self.assertEqual(seen["method"], "image.submit")
        self.assertGreaterEqual(seen["timeout"], 660)
        self.assertEqual(task.id, "req-9")
        self.assertEqual(seen["params"]["prompt"], "雨夜")

    def test_generated_bytes_are_published_and_the_ephemeral_url_is_not_kept(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "A1.png"
        path.write_bytes(b"\x89PNG-still")
        previous = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = PUBLIC_BASE
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"

        class Ctx(object):
            project_id = "pubstill01"
            work_dir = folder

        try:
            hosted = _publish_generated_still(
                Ctx(),
                "A1",
                path,
                "https://cdn.minimax.test/expire.png",
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(hosted, PUBLIC_BASE + "/media/projects/pubstill01/images/A1.png")
        self.assertNotIn("minimax", hosted)
        self.assertTrue((folder / "outputs" / "render.log").read_text(encoding="utf-8").startswith("生图已写入图片库"))
        self.assertEqual(_png_still_bytes(b"\x89PNG-still"), b"\x89PNG-still")


if __name__ == "__main__":
    unittest.main()
