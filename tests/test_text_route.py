"""Text calls use Token Plan DeepSeek. Image stays on the Qwen host and wan2.7-image."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AI.qwen_api.graph import TEXT_HOST, TEXT_MODEL, generate_text, responses_client, text_base_host
from AI.qwen_api.client import QwenClient
from AI.qwen_api.image import IMAGE_MODEL, QwenImageClient


class _FakeResponses(object):
    def __init__(self):
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"output_text": "ok"}


class TextRouteTest(unittest.TestCase):
    def test_text_request_uses_token_plan_deepseek_and_image_stays_wan(self):
        seen = {}

        class FakeOpenAI(object):
            def __init__(self, api_key=None, base_url=None, max_retries=0):
                seen["api_key"] = api_key
                seen["base_url"] = base_url
                seen["max_retries"] = max_retries

        root = Path(tempfile.mkdtemp())
        (root / "config").mkdir()
        (root / "config" / "qwen.json").write_text(json.dumps({"env": "test"}), encoding="utf-8")
        env = {
            "TOKE_PLAN_QWEN_API_KEY": "plan-test",
            "QWEN_API_KEY": "qwen-test",
            "AIVIDEO_TEXT_BASE_URL": "",
            "AIVIDEO_TEXT_MODEL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("openai.OpenAI", FakeOpenAI):
                responses_client()
            fake = _FakeResponses()
            text = generate_text(
                [{"role": "user", "content": "写"}],
                model="qwen3.8-flash",
                client=fake,
            )
            image = QwenImageClient(client=QwenClient(api_key="qwen-test", env_path=root))
        self.assertEqual(text, "ok")
        self.assertEqual(seen["base_url"], TEXT_HOST + "/compatible-mode/v1")
        self.assertEqual(seen["api_key"], "plan-test")
        self.assertEqual(fake.calls[0]["model"], TEXT_MODEL)
        self.assertEqual(fake.calls[0]["model"], "deepseek-v4-flash-0731")
        self.assertNotEqual(fake.calls[0]["model"], IMAGE_MODEL)
        self.assertEqual(image.client.base_url, "https://maas.qianwenaiapi.com")
        self.assertNotIn("token-plan", image.client.base_url)
        self.assertEqual(IMAGE_MODEL, "wan2.7-image")

    def test_text_host_and_model_can_be_overridden_without_a_config_file(self):
        with patch.dict(
            os.environ,
            {"AIVIDEO_TEXT_BASE_URL": "https://token-plan.example.test", "AIVIDEO_TEXT_MODEL": "deepseek-v4-flash-0731"},
            clear=False,
        ):
            self.assertEqual(text_base_host(), "https://token-plan.example.test")
            fake = _FakeResponses()
            generate_text([{"role": "user", "content": "写"}], model="other", client=fake)
        self.assertEqual(fake.calls[0]["model"], "deepseek-v4-flash-0731")

    def test_missing_token_plan_key_is_named(self):
        from AI.qwen_api.client import QwenError

        with patch.dict(os.environ, {"TOKE_PLAN_QWEN_API_KEY": ""}, clear=False):
            with self.assertRaises(QwenError) as caught:
                responses_client(api_key="")
        self.assertEqual(str(caught.exception), "未配置 TOKE_PLAN_QWEN_API_KEY")


if __name__ == "__main__":
    unittest.main()
