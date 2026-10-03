"""iCreat GPT Image 2 client: submit, status, result, download."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

from icreat_api.client import ICreatClient, ICreatError


class _Response(object):
    def __init__(self, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body

    def read(self):
        return self._body

    def close(self):
        return None


class ICreatClientTest(unittest.TestCase):
    def test_submit_status_result_and_download(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            url = request.full_url
            if url.endswith("/openai/gpt-image-2/text-to-image"):
                body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(body["prompt"], "a cat")
                self.assertEqual(body["size"], "1440x2560")
                self.assertEqual(body["quality"], "high")
                self.assertNotIn("image", body)
                self.assertEqual(set(body), {"prompt", "size", "quality"})
                self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
                self.assertEqual(request.get_header("User-agent"), "AiVideo/1.0")
                return _Response('{"task_id": "task-1"}')
            if url.endswith("/v1/task/result"):
                self.assertEqual(json.loads(request.data.decode("utf-8")), {"task_id": "task-1"})
                seen = sum(1 for item in calls if item.full_url.endswith("/v1/task/result"))
                if seen == 1:
                    return _Response('{"task_id": "task-1", "status": "IN_PROGRESS", "result": []}')
                return _Response(
                    '{"task_id": "task-1", "status": "SUCCEEDED", "costUSD": 0.04, '
                    '"result": [{"type": "Image", "url": "https://cdn.example/a.png", '
                    '"download_url": "https://cdn.example/a.png"}]}'
                )
            if url == "https://cdn.example/a.png":
                self.assertIsNone(request.get_header("Authorization"))
                return _Response(b"png-bytes")
            raise AssertionError(url)

        client = ICreatClient(api_key="sk-test", opener=opener)
        task = client.submit_image("a cat")
        self.assertEqual(task.id, "task-1")
        self.assertEqual(task.status, "SUBMITTED")
        status = client.query_status(task.id)
        self.assertEqual(status.status, "IN_PROGRESS")
        self.assertEqual(status.id, "task-1")
        self.assertEqual(status.url, "")
        result = client.get_result(task.id)
        self.assertEqual(result.url, "https://cdn.example/a.png")
        with tempfile.TemporaryDirectory() as tmp:
            path = client.download_image(result.url, Path(tmp) / "hero.png")
            self.assertEqual(path.read_bytes(), b"png-bytes")
        self.assertEqual(len(calls), 4)

    def test_reference_image_is_a_list_and_env_overrides_size(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return _Response('{"taskId": "task-2"}')

        previous_size = os.environ.get("ICREAT_IMAGE_SIZE")
        previous_quality = os.environ.get("ICREAT_IMAGE_QUALITY")
        os.environ["ICREAT_IMAGE_SIZE"] = "1280x720"
        os.environ["ICREAT_IMAGE_QUALITY"] = "low"
        try:
            client = ICreatClient(api_key="sk-test", opener=opener)
            task = client.submit_image("edit the coat", image="https://cdn.example/src.png")
        finally:
            if previous_size is None:
                os.environ.pop("ICREAT_IMAGE_SIZE", None)
            else:
                os.environ["ICREAT_IMAGE_SIZE"] = previous_size
            if previous_quality is None:
                os.environ.pop("ICREAT_IMAGE_QUALITY", None)
            else:
                os.environ["ICREAT_IMAGE_QUALITY"] = previous_quality
        self.assertEqual(task.id, "task-2")
        self.assertTrue(seen["url"].endswith("/openai/gpt-image-2/text-to-image"))
        self.assertEqual(seen["body"]["size"], "1280x720")
        self.assertEqual(seen["body"]["quality"], "low")
        self.assertEqual(seen["body"]["image"], ["https://cdn.example/src.png"])

    def test_http_and_network_errors_keep_status(self):
        def http_opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b"busy"))

        client = ICreatClient(api_key="sk-test", opener=http_opener)
        with self.assertRaises(ICreatError) as caught:
            client.query_status("task-1")
        self.assertEqual(caught.exception.status, 503)

        def network_opener(request, timeout):
            raise urllib.error.URLError("timed out")

        client = ICreatClient(api_key="sk-test", opener=network_opener)
        with self.assertRaises(ICreatError) as caught:
            client.submit_image("a cat")
        self.assertIsNone(caught.exception.status)
        self.assertIn("网络错误", str(caught.exception))

    def test_unknown_size_is_rejected_before_submit(self):
        def opener(request, timeout):
            raise AssertionError("should not submit")

        previous = os.environ.get("ICREAT_IMAGE_SIZE")
        os.environ["ICREAT_IMAGE_SIZE"] = "1024x1536"
        try:
            client = ICreatClient(api_key="sk-test", opener=opener)
            with self.assertRaises(ICreatError) as caught:
                client.submit_image("a cat")
        finally:
            if previous is None:
                os.environ.pop("ICREAT_IMAGE_SIZE", None)
            else:
                os.environ["ICREAT_IMAGE_SIZE"] = previous
        self.assertIn("1024x1536", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
