"""Qwen client, text graph, and media calls. Nothing here touches the network."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from AI.qwen_api.audio import MUSIC_UNAVAILABLE, synthesize
from AI.qwen_api.client import DEFAULT_BASE_URL, QwenClient, QwenError
from AI.qwen_api.graph import bind_trace_prompts, generate_text, log_prompt, understand_text
from AI.qwen_api.image import IMAGE_MODEL, IMAGE_PATH, ImageTask, QwenImageClient
from AI.qwen_api.video import VIDEO_MODEL, VIDEO_PATH, QwenVideoClient, VideoTask
from end.render_loop import IMAGE_FAILURE, IMAGE_SUCCESS, VIDEO_FAILURE, VIDEO_SUCCESS

HOST = "https://maas.qianwenaiapi.com"


class _Body(object):
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")

    def read(self):
        return self.payload

    def close(self):
        return None


def _client(opener, api_key="test-key", base_url=HOST):
    return QwenClient(api_key=api_key, base_url=base_url, opener=opener, env_path="/tmp/aivideo-no-env")


class QwenClientTest(unittest.TestCase):
    def test_missing_key_ignores_dashscope(self):
        def opener(_request, timeout=None):
            raise AssertionError("should not request")

        with patch.dict(
            os.environ,
            {
                "QWEN_API_KEY": "",
                "QWEN_ENV": "",
                "QWEN_BASE_URL": "",
                "TOKE_PLAN_QWEN_API_KEY": "plan-key",
                "DASHSCOPE_API_KEY": "dash-secret",
                "DASHSCOPE_BASE_URL": "https://other.example",
            },
        ):
            with self.assertRaises(QwenError) as caught:
                QwenClient(opener=opener, env_path="/tmp/aivideo-no-env")
        self.assertEqual(str(caught.exception), "未配置 QWEN_API_KEY")

    def test_base_url_is_a_host_and_tasks_use_one_path(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            return _Body('{"output":{"task_id":"t1","task_status":"PENDING"}}')

        client = QwenClient(
            api_key="test-key",
            base_url="https://example.test/compatible-mode/v1",
            opener=opener,
            env_path="/tmp/aivideo-no-env",
        )
        self.assertEqual(client.base_url, "https://example.test")
        self.assertEqual(client.compatible_base, "https://example.test/compatible-mode/v1")
        client.get_task("task-9")
        self.assertEqual(captured["url"], "https://example.test/api/v1/tasks/task-9")
        self.assertEqual(captured["auth"], "Bearer test-key")

    def test_default_host_comes_from_the_env_override(self):
        with patch.dict(os.environ, {"QWEN_BASE_URL": "", "QWEN_ENV": "", "DASHSCOPE_BASE_URL": "https://other.example"}):
            client = QwenClient(api_key="test-key", env_path="/tmp/aivideo-no-env")
        self.assertEqual(client.base_url, DEFAULT_BASE_URL)

    def test_qwen_env_switches_production_and_test(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        with patch.dict(os.environ, {"QWEN_ENV": "prod", "QWEN_BASE_URL": ""}):
            production = QwenClient(api_key="test-key", env_path=root / ".env")
        self.assertEqual(production.base_url, "https://token-plan.maas.qianwenaiapi.com")
        self.assertEqual(
            production.compatible_base,
            "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        )
        with patch.dict(os.environ, {"QWEN_ENV": "测试", "QWEN_BASE_URL": ""}):
            test = QwenClient(api_key="test-key", env_path=root / ".env")
        self.assertEqual(test.base_url, "https://maas.qianwenaiapi.com")
        self.assertEqual(test.compatible_base, "https://maas.qianwenaiapi.com/compatible-mode/v1")

    def test_saved_choice_wins_over_env_and_base_url(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        (root / "data").mkdir()
        (root / "data" / "qwen_env").write_text("prod\n", encoding="utf-8")
        with patch.dict(os.environ, {"QWEN_ENV": "test", "QWEN_BASE_URL": "https://custom.example"}):
            client = QwenClient(api_key="test-key", env_path=root / ".env")
        self.assertEqual(client.base_url, "https://token-plan.maas.qianwenaiapi.com")

    def test_base_url_is_used_when_no_named_env_is_set(self):
        with patch.dict(os.environ, {"QWEN_ENV": "", "QWEN_BASE_URL": "https://custom.example/compatible-mode/v1"}):
            client = QwenClient(api_key="test-key", env_path="/tmp/aivideo-no-env")
        self.assertEqual(client.base_url, "https://custom.example")

    def test_each_environment_uses_its_own_key(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        data = root / "data"
        data.mkdir()
        (data / "qwen_env").write_text("prod\n", encoding="utf-8")
        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key", "TOKE_PLAN_QWEN_API_KEY": "plan-key", "QWEN_ENV": "test"}):
            production = QwenClient(env_path=root / ".env")
        self.assertEqual(production.api_key, "plan-key")
        self.assertEqual(production.base_url, "https://token-plan.maas.qianwenaiapi.com")
        (data / "qwen_env").write_text("test\n", encoding="utf-8")
        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key", "TOKE_PLAN_QWEN_API_KEY": "plan-key", "QWEN_ENV": "prod"}):
            testing = QwenClient(env_path=root / ".env")
        self.assertEqual(testing.api_key, "test-key")
        self.assertEqual(testing.base_url, "https://maas.qianwenaiapi.com")
        given = QwenClient(api_key="given", env_path=root / ".env")
        self.assertEqual(given.api_key, "given")

    def test_missing_token_plan_key_names_that_variable(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        (root / "data").mkdir()
        (root / "data" / "qwen_env").write_text("prod\n", encoding="utf-8")
        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key", "TOKE_PLAN_QWEN_API_KEY": "", "QWEN_ENV": ""}):
            with self.assertRaises(QwenError) as caught:
                QwenClient(env_path=root / ".env")
        self.assertEqual(str(caught.exception), "未配置 TOKE_PLAN_QWEN_API_KEY")

    def test_download_does_not_send_the_key(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            return _Body(b"mp4")

        dest = Path(tempfile.mkdtemp()) / "clip.mp4"
        _client(opener).download("https://cdn.example/a.mp4", dest)
        self.assertIsNone(captured["auth"])
        self.assertEqual(dest.read_bytes(), b"mp4")
        self.assertFalse(dest.with_suffix(".mp4.part").exists())

    def test_timeout_is_not_written_into_the_body(self):
        captured = {}

        def opener(request, data=None, timeout=None):
            if data is not None:
                request.data = data
            captured["body"] = request.data
            captured["timeout"] = timeout
            return _Body('{"output":{"task_id":"t1","task_status":"PENDING"}}')

        _client(opener).request("POST", "/api/v1/services/aigc/image-generation/generation", {"model": "x"}, timeout=60)
        self.assertIsInstance(captured["body"], bytes)
        self.assertIn(b'"model"', captured["body"])
        self.assertEqual(captured["timeout"], 60)


class QwenTextTest(unittest.TestCase):
    def test_generate_and_understand_use_qwen_flash(self):
        class Responses(object):
            def __init__(self):
                self.calls = []
                self.responses = self

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs.get("tools"):
                    return {
                        "output": [
                            {
                                "type": "function_call",
                                "name": "submit_intent",
                                "arguments": '{"phase":"ask","reply":"问","options":[],"brief":""}',
                            }
                        ]
                    }
                return {"output_text": "写好了"}

        client = Responses()
        written = generate_text([{"role": "user", "content": "写"}], model="gpt-5.4", max_output_tokens=16, timeout=3, client=client)
        understood = understand_text([{"role": "user", "content": "问"}], timeout=3, client=client)
        self.assertEqual(written, "写好了")
        self.assertEqual(understood, '{"phase":"ask","reply":"问","options":[],"brief":""}')
        self.assertEqual(client.calls[0]["model"], "qwen3.8-flash")
        self.assertNotIn("reasoning", client.calls[0])
        self.assertEqual(client.calls[0]["extra_body"], {"enable_thinking": False})
        self.assertNotIn("tools", client.calls[0])
        self.assertEqual(client.calls[1]["model"], "qwen3.8-flash")
        self.assertNotIn("reasoning", client.calls[1])
        self.assertEqual(client.calls[1]["extra_body"], {"enable_thinking": False})
        self.assertEqual(client.calls[1]["tool_choice"], "required")
        self.assertEqual(client.calls[1]["tools"][0]["name"], "submit_intent")

    def test_generate_can_turn_reasoning_off(self):
        class Responses(object):
            def __init__(self):
                self.calls = []
                self.responses = self

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return {"output_text": "续写"}

        client = Responses()
        written = generate_text(
            [{"role": "user", "content": "续"}],
            max_output_tokens=32000,
            effort="none",
            client=client,
        )
        self.assertEqual(written, "续写")
        self.assertNotIn("reasoning", client.calls[0])
        self.assertEqual(client.calls[0]["extra_body"], {"enable_thinking": False})
        self.assertEqual(client.calls[0]["max_output_tokens"], 32000)

    def test_prompt_debug_does_not_print_the_prompt(self):
        class Responses(object):
            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                return {"output_text": "好"}

        client = Responses()
        messages = [
            {"role": "system", "content": "规则全文"},
            {"role": "user", "content": "写这一段"},
        ]
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_PROMPT": "1"}):
            captured = io.StringIO()
            with redirect_stdout(captured):
                generate_text(messages, client=client)
        printed = captured.getvalue()
        self.assertNotIn("规则全文", printed)
        self.assertNotIn("写这一段", printed)
        self.assertNotIn("输入长度", printed)

    def test_prompt_debug_stays_quiet_when_off(self):
        class Responses(object):
            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                return {"output_text": "好"}

        with patch.dict(os.environ, {"AIVIDEO_DEBUG_PROMPT": "0", "AIVIDEO_DEBUG_TRACE": "0"}):
            captured = io.StringIO()
            with redirect_stdout(captured):
                generate_text([{"role": "user", "content": "静"}], client=Responses())
        self.assertNotIn("输入长度", captured.getvalue())

    def test_trace_flag_does_not_print_the_prompt(self):
        class Responses(object):
            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                return {"output_text": "好"}

        messages = [
            {"role": "system", "content": "规则全文"},
            {"role": "user", "content": "写这一段"},
        ]
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_PROMPT": "0", "AIVIDEO_DEBUG_TRACE": "1"}):
            captured = io.StringIO()
            with redirect_stdout(captured):
                generate_text(messages, client=Responses())
        printed = captured.getvalue()
        self.assertNotIn("规则全文", printed)
        self.assertNotIn("写这一段", printed)
        self.assertNotIn("输入长度", printed)

    def test_bound_trace_saves_the_prompt_without_printing(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "prompts.jsonl"
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_PROMPT": "0", "AIVIDEO_DEBUG_TRACE": "0"}):
            captured = io.StringIO()
            with bind_trace_prompts(path):
                with redirect_stdout(captured):
                    log_prompt([{"role": "user", "content": "完整提示词"}])
        self.assertNotIn("输入长度", captured.getvalue())
        self.assertIn("完整提示词", path.read_text(encoding="utf-8"))


class QwenMediaTest(unittest.TestCase):
    def _capture(self, payload):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["async"] = request.get_header("X-dashscope-async")
            if request.data:
                captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Body(json.dumps(payload))

        return captured, opener

    def test_image_text_and_reference_share_one_model(self):
        captured, opener = self._capture({"output": {"task_id": "img-1", "task_status": "PENDING"}})
        task = QwenImageClient(client=_client(opener)).submit_image(
            "雨夜车站",
            image=["https://cdn.example/ref.png"],
            size="9:16",
        )
        self.assertEqual(task.status, "IN_PROGRESS")
        self.assertEqual(task.id, "img-1")
        self.assertEqual(captured["url"], HOST + IMAGE_PATH)
        self.assertEqual(captured["async"], "enable")
        body = captured["body"]
        self.assertEqual(body["model"], "wan2.7-image")
        self.assertEqual(body["model"], IMAGE_MODEL)
        self.assertNotIn("prompt_extend", body["parameters"])
        self.assertNotIn("thinking_mode", body["parameters"])
        self.assertEqual(body["parameters"]["n"], 1)
        self.assertEqual(body["parameters"]["watermark"], False)
        self.assertEqual(body["parameters"]["size"], "768*1344")
        content = body["input"]["messages"][0]["content"]
        self.assertEqual(content[0], {"image": "https://cdn.example/ref.png"})
        self.assertEqual(content[1], {"text": "雨夜车站"})
        text_only, text_opener = self._capture({"output": {"task_id": "img-2", "task_status": "PENDING"}})
        QwenImageClient(client=_client(text_opener)).submit_image("雨夜车站", size="16:9")
        self.assertEqual(text_only["body"]["model"], "wan2.7-image")
        self.assertEqual(text_only["body"]["parameters"]["thinking_mode"], False)
        self.assertNotIn("prompt_extend", text_only["body"]["parameters"])

    def test_image_sizes_and_status_match_the_render_loop(self):
        from AI.qwen_api.image import qwen_image_size

        self.assertEqual(qwen_image_size("16:9"), "1344*768")
        self.assertEqual(qwen_image_size("21:9"), "1792*768")
        self.assertEqual(qwen_image_size("768*1344"), "768*1344")
        self.assertEqual(qwen_image_size(""), "")
        with self.assertRaises(QwenError):
            qwen_image_size("100*100")
        pending = ImageTask({"output": {"task_id": "a", "task_status": "PENDING"}})
        running = ImageTask({"status": "RUNNING", "task_id": "b"})
        done = ImageTask({"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://cdn.example/a.png"}]}})
        failed = ImageTask({"output": {"task_status": "FAILED", "message": "boom"}})
        self.assertEqual(pending.status, "IN_PROGRESS")
        self.assertEqual(running.status, "IN_PROGRESS")
        self.assertIn(done.status, IMAGE_SUCCESS)
        self.assertEqual(done.url, "https://cdn.example/a.png")
        self.assertIn(failed.status, IMAGE_FAILURE)
        self.assertNotIn(pending.status, IMAGE_SUCCESS)
        self.assertNotIn(pending.status, IMAGE_FAILURE)

    def test_video_keeps_documented_ratios_and_separates_roles(self):
        captured, opener = self._capture({"output": {"task_id": "vid-1", "task_status": "PENDING"}})
        task = QwenVideoClient(client=_client(opener)).submit_video(
            "海面亮起来",
            model="minimax-h3-text-to-video",
            duration=5,
            aspect_ratio="21:9",
        )
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.poll_url, HOST + "/api/v1/tasks/vid-1")
        self.assertEqual(captured["url"], HOST + VIDEO_PATH)
        self.assertEqual(captured["async"], "enable")
        body = captured["body"]
        self.assertEqual(body["model"], VIDEO_MODEL)
        self.assertEqual(body["parameters"]["ratio"], "21:9")
        self.assertEqual(body["parameters"]["duration"], 5)
        self.assertEqual(body["parameters"]["prompt_extend"], False)
        self.assertNotIn("media", body["input"])
        self.assertNotIn(task.status, VIDEO_SUCCESS)
        self.assertNotIn(task.status, VIDEO_FAILURE)

        captured, opener = self._capture({"output": {"task_id": "vid-2", "task_status": "SUCCEEDED", "video_url": "https://cdn.example/a.mp4"}})
        done = QwenVideoClient(client=_client(opener)).submit_video("海", aspect_ratio=None, images=[{"role": "first_frame", "url": "https://cdn.example/a.png"}, {"role": "last_frame", "url": "https://cdn.example/b.png"}])
        self.assertIn(done.status, VIDEO_SUCCESS)
        self.assertEqual(done.url, "https://cdn.example/a.mp4")
        self.assertEqual(captured["body"]["parameters"]["ratio"], "adaptive")
        roles = [item["type"] for item in captured["body"]["input"]["media"]]
        self.assertEqual(roles, ["first_frame", "last_frame"])
        failed = VideoTask({"output": {"task_status": "FAILED", "message": "no"}})
        self.assertIn(failed.status, VIDEO_FAILURE)
        self.assertEqual(VideoTask({"id": "v", "status": "completed", "data": [{"url": "https://cdn.example/old.mp4"}]}).status, "completed")

        client = QwenVideoClient(client=_client(lambda request, timeout=None: _Body("{}")))
        with self.assertRaises(QwenError) as mixed:
            client.submit_video(
                "海",
                images=[
                    {"role": "first_frame", "url": "https://cdn.example/a.png"},
                    {"role": "reference_image", "url": "https://cdn.example/b.png"},
                ],
            )
        self.assertIn("不能同时使用首尾帧和参考素材", str(mixed.exception))
        with self.assertRaises(QwenError):
            client.submit_video("海", duration=3)
        with self.assertRaises(QwenError):
            client.submit_video("海", duration=16)

    def test_speech_downloads_and_music_is_refused(self):
        seen = []

        def opener(request, timeout=None):
            seen.append(request)
            if request.data:
                return _Body(json.dumps({"output": {"audio": {"url": "https://cdn.example/voice.mp3"}}}))
            return _Body(b"ID3")

        dest = Path(tempfile.mkdtemp()) / "S01.mp3"
        with patch.dict(os.environ, {"QWEN_TTS_VOICE": "other_voice", "DASHSCOPE_API_KEY": "dash-secret"}):
            path = synthesize("夜色已经很深了", dest, client=_client(opener))
        self.assertEqual(path.read_bytes(), b"ID3")
        body = json.loads(seen[0].data.decode("utf-8"))
        self.assertEqual(seen[0].full_url, HOST + "/api/v1/services/audio/tts/SpeechSynthesizer")
        self.assertIsNone(seen[0].get_header("X-dashscope-async"))
        self.assertEqual(body["model"], "qwen-audio-3.0-tts-flash")
        self.assertEqual(body["input"]["voice"], "other_voice")
        self.assertEqual(body["input"]["text"], "夜色已经很深了")
        self.assertEqual(body["input"]["format"], "mp3")
        self.assertIsNone(seen[1].get_header("Authorization"))
        voiced = []

        def opener_voice(request, timeout=None):
            if request.data:
                voiced.append(json.loads(request.data.decode("utf-8")))
                return _Body(json.dumps({"output": {"audio": {"url": "https://cdn.example/voice.mp3"}}}))
            return _Body(b"ID3")

        with patch.dict(os.environ, {"QWEN_TTS_VOICE": "env-voice"}):
            synthesize("你好", dest, voice_id="caller-voice", client=_client(opener_voice))
        self.assertEqual(voiced[0]["input"]["voice"], "caller-voice")
        with self.assertRaises(QwenError) as caught:
            from AI.qwen_api.audio import reject_music

            reject_music()
        self.assertEqual(str(caught.exception), MUSIC_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
