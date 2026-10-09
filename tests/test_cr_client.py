"""CR video submit: reference media roles, and no mixing with first or last frames."""

import unittest

from cr_api.client import CRClient, CRError


class _Client(CRClient):
    def __init__(self):
        self.api_key = "test"
        self.base_url = "https://example.invalid"
        self.bodies = []

    def _request(self, method, path, body, timeout, headers=None):
        self.bodies.append(body)
        return {"id": "vid-1", "status": "submitted", "poll_url": "https://example/poll"}


class CRVideoRolesTest(unittest.TestCase):
    def test_reference_video_and_audio_are_separate_content_items(self):
        client = _Client()
        client.submit_video(
            "海边",
            model="minimax-h3-reference-to-video",
            images=[
                {"role": "reference_image", "url": "https://cdn.example/a.jpg"},
                {"role": "reference_video", "url": "https://cdn.example/a.mp4"},
                {"role": "reference_audio", "url": "https://cdn.example/a.mp3"},
            ],
        )
        content = client.bodies[0]["content"]
        self.assertEqual(
            [(item["type"], item.get("role")) for item in content],
            [
                ("text", None),
                ("image_url", "reference_image"),
                ("video_url", "reference_video"),
                ("audio_url", "reference_audio"),
            ],
        )
        self.assertEqual(content[2]["video_url"]["url"], "https://cdn.example/a.mp4")
        self.assertEqual(content[3]["audio_url"]["url"], "https://cdn.example/a.mp3")

    def test_frame_and_reference_roles_are_rejected(self):
        client = _Client()
        mixed = [
            (
                [
                    {"role": "first_frame", "url": "https://cdn.example/a.jpg"},
                    {"role": "reference_image", "url": "https://cdn.example/b.jpg"},
                ]
            ),
            (
                [
                    {"role": "last_frame", "url": "https://cdn.example/a.jpg"},
                    {"role": "reference_video", "url": "https://cdn.example/a.mp4"},
                ]
            ),
            (
                [
                    {"role": "first_frame", "url": "https://cdn.example/a.jpg"},
                    {"role": "last_frame", "url": "https://cdn.example/b.jpg"},
                    {"role": "reference_audio", "url": "https://cdn.example/a.mp3"},
                ]
            ),
        ]
        for images in mixed:
            with self.assertRaises(CRError) as raised:
                client.submit_video("海边", model="minimax-h3-image-to-video", images=images)
            self.assertIn("不能同时使用首尾帧和参考素材", str(raised.exception))
        self.assertEqual(client.bodies, [])

    def test_first_and_last_frame_together_are_allowed(self):
        client = _Client()
        client.submit_video(
            "从门口走到街上",
            model="minimax-h3-image-to-video",
            aspect_ratio="3:4",
            images=[
                {"role": "first_frame", "url": "https://cdn.example/start.jpg"},
                {"role": "last_frame", "url": "https://cdn.example/end.jpg"},
            ],
        )
        roles = [item.get("role") for item in client.bodies[0]["content"][1:]]
        self.assertEqual(roles, ["first_frame", "last_frame"])


if __name__ == "__main__":
    unittest.main()
