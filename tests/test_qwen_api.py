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
from AI.qwen_api.video import (
    GENERATE_PARAMETERS,
    LOCAL_DEFAULT_PARAMETERS,
    DEFAULT_LOCAL_VIDEO_URL,
    LOCAL_DUMMY_KEY,
    MODE_CONFIG_KEYS,
    PREVIEW_PARAMETERS,
    VIDEO_MODEL,
    VIDEO_PATH,
    QwenVideoClient,
    VideoTask,
    video_mode_parameters,
)
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
            captured["authorization"] = request.get_header("Authorization")
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
        bare = self._catalog()
        task = QwenVideoClient(client=_client(opener), env_path=bare).submit_video(
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
        self.assertEqual(body["model"], "MiniMax/MiniMax-H3")
        self.assertEqual(body["parameters"]["resolution"], "768P")
        self.assertEqual(body["parameters"]["ratio"], "21:9")
        self.assertEqual(body["parameters"]["duration"], 5)
        self.assertEqual(body["parameters"]["watermark"], False)
        self.assertNotIn("preset", body["parameters"])
        self.assertNotIn("steps", body["parameters"])
        self.assertNotIn("frames", body["parameters"])
        self.assertNotIn("prompt_extend", body["parameters"])
        self.assertNotIn("media", body["input"])
        self.assertNotIn(task.status, VIDEO_SUCCESS)
        self.assertNotIn(task.status, VIDEO_FAILURE)

        captured, opener = self._capture({"output": {"task_id": "vid-2", "task_status": "SUCCEEDED", "video_url": "https://cdn.example/a.mp4"}})
        done = QwenVideoClient(client=_client(opener), env_path=bare).submit_video("海", aspect_ratio=None, images=[{"role": "first_frame", "url": "https://cdn.example/a.png"}, {"role": "last_frame", "url": "https://cdn.example/b.png"}])
        self.assertIn(done.status, VIDEO_SUCCESS)
        self.assertEqual(done.url, "https://cdn.example/a.mp4")
        self.assertEqual(captured["body"]["parameters"]["ratio"], "adaptive")
        roles = [item["type"] for item in captured["body"]["input"]["media"]]
        self.assertEqual(roles, ["first_frame", "last_frame"])
        failed = VideoTask({"output": {"task_status": "FAILED", "message": "no"}})
        self.assertIn(failed.status, VIDEO_FAILURE)
        self.assertEqual(VideoTask({"id": "v", "status": "completed", "data": [{"url": "https://cdn.example/old.mp4"}]}).status, "completed")

        client = QwenVideoClient(client=_client(lambda request, timeout=None: _Body("{}")), env_path=bare)
        with self.assertRaises(QwenError) as mixed:
            client.submit_video(
                "海",
                images=[
                    {"role": "first_frame", "url": "https://cdn.example/a.png"},
                    {"role": "reference_image", "url": "https://cdn.example/b.png"},
                ],
            )
        self.assertIn("不能同时使用首尾帧和参考素材", str(mixed.exception))
        captured, opener = self._capture({"output": {"task_id": "vid-ref", "task_status": "PENDING"}})
        QwenVideoClient(client=_client(opener), env_path=bare).submit_video(
            "海",
            images=[{"role": "reference_image", "url": "https://cdn.example/ref.png"}],
        )
        self.assertEqual(
            captured["body"]["input"]["media"],
            [{"type": "reference_image", "url": "https://cdn.example/ref.png"}],
        )
        self.assertNotIn("preset", captured["body"]["parameters"])
        with self.assertRaises(QwenError):
            client.submit_video("海", duration=3)
        with self.assertRaises(QwenError):
            client.submit_video("海", duration=16)

    def test_local_minimax_uses_localhost_and_cloud_stays_on_qwen(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        data = root / "data"
        data.mkdir()
        (data / "qwen_env").write_text("prod\n", encoding="utf-8")
        (data / "minimax_video").write_text("local\n", encoding="utf-8")
        captured, opener = self._capture(
            {"output": {"task_id": "vid-local", "task_status": "PENDING"}}
        )
        env = {
            "QWEN_API_KEY": "",
            "TOKE_PLAN_QWEN_API_KEY": "plan-key",
            "QWEN_ENV": "test",
            "QWEN_BASE_URL": "https://custom.example",
            "MINIMAX_VIDEO": "cloud",
            "MINIMAX_H3_BASE_URL": "",
            "MINIMAX_H3_API_KEY": "",
        }
        with patch.dict(os.environ, env):
            local = QwenVideoClient(opener=opener, env_path=root / ".env")
            task = local.submit_video(
                "海面亮起来",
                duration=6,
                aspect_ratio="16:9",
                images=[
                    {"role": "reference_image", "url": "https://cdn.example/ref.png"},
                    {"role": "reference_audio", "url": "https://cdn.example/ref.mp3"},
                ],
            )
            with self.assertRaises(QwenError) as frames:
                local.submit_video(
                    "海面亮起来",
                    duration=6,
                    images=[{"role": "first_frame", "url": "https://cdn.example/ref.png"}],
                )
            with self.assertRaises(QwenError) as text_only:
                local.submit_video("海面亮起来", duration=6)
            with self.assertRaises(QwenError) as audio_only:
                local.submit_video(
                    "海面亮起来",
                    duration=6,
                    images=[{"role": "reference_audio", "url": "https://cdn.example/ref.mp3"}],
                )
            submit_url = captured["url"]
            submit_async = captured["async"]
            body = dict(captured["body"])
            local.submit_video(
                "海面亮起来",
                duration=6,
                images=[{"role": "reference_video", "url": "https://cdn.example/ref.mp4"}],
            )
            video_media = captured["body"]["input"]["media"]
            local.poll("vid-local")
            poll_url = captured["url"]
            image_host = QwenClient(env_path=root / ".env")
        self.assertEqual(local.client.base_url, DEFAULT_LOCAL_VIDEO_URL)
        self.assertEqual(local.client.api_key, LOCAL_DUMMY_KEY)
        self.assertEqual(submit_url, DEFAULT_LOCAL_VIDEO_URL + VIDEO_PATH)
        self.assertEqual(submit_async, "enable")
        self.assertEqual(poll_url, DEFAULT_LOCAL_VIDEO_URL + "/api/v1/tasks/vid-local")
        self.assertEqual(body["model"], "MiniMax/MiniMax-H3")
        self.assertEqual(body["parameters"]["duration"], 6)
        self.assertEqual(body["parameters"]["ratio"], "16:9")
        self.assertEqual(body["parameters"]["resolution"], "768P")
        self.assertEqual(body["parameters"]["watermark"], False)
        self.assertEqual(body["parameters"]["resolution"], "768P")
        for key, value in LOCAL_DEFAULT_PARAMETERS.items():
            self.assertEqual(body["parameters"][key], value)
        self.assertNotIn("width", body["parameters"])
        self.assertNotIn("height", body["parameters"])
        self.assertIn("first_frame", str(frames.exception))
        self.assertIn("reference_image", str(text_only.exception))
        self.assertIn("reference_audio", str(audio_only.exception))
        self.assertNotIn("frames", body["parameters"])
        self.assertNotIn("steps", body["parameters"])
        self.assertNotIn("layers", body["parameters"])
        self.assertNotIn("reuse", body["parameters"])
        self.assertNotIn("ssd_streaming", body["parameters"])
        self.assertNotIn("render_width", body["parameters"])
        self.assertNotIn("render_height", body["parameters"])
        self.assertEqual(
            body["input"]["media"],
            [
                {"type": "reference_image", "url": "https://cdn.example/ref.png"},
                {"type": "reference_audio", "url": "https://cdn.example/ref.mp3"},
            ],
        )
        self.assertEqual(video_media, [{"type": "reference_video", "url": "https://cdn.example/ref.mp4"}])
        self.assertEqual(task.poll_url, DEFAULT_LOCAL_VIDEO_URL + "/api/v1/tasks/vid-local")
        self.assertEqual(image_host.base_url, "https://token-plan.maas.qianwenaiapi.com")
        self.assertEqual(image_host.api_key, "plan-key")

        (data / "minimax_video").write_text("cloud\n", encoding="utf-8")
        with patch.dict(os.environ, env):
            cloud = QwenVideoClient(env_path=root / ".env")
        self.assertEqual(cloud.client.base_url, "https://token-plan.maas.qianwenaiapi.com")
        self.assertEqual(cloud.client.api_key, "plan-key")

    def test_minimax_env_sets_local_host_when_the_page_has_not_chosen(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text(
            "QWEN_API_KEY=test-key\nMINIMAX_VIDEO=local\n"
            "MINIMAX_H3_BASE_URL=http://127.0.0.1:9000/api/v1\n"
            "MINIMAX_H3_API_KEY=secret\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "QWEN_API_KEY": "test-key",
                "MINIMAX_VIDEO": "",
                "MINIMAX_H3_BASE_URL": "",
                "MINIMAX_H3_API_KEY": "",
                "QWEN_ENV": "prod",
                "QWEN_BASE_URL": "https://custom.example",
            },
        ):
            client = QwenVideoClient(env_path=root / ".env")
        self.assertEqual(client.client.base_url, "http://127.0.0.1:9000")
        self.assertEqual(client.client.api_key, "secret")

    def test_video_rpc_client_follows_the_page_choice(self):
        from AI import api as ai_api

        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        (root / ".env").write_text("QWEN_API_KEY=test-key\n", encoding="utf-8")
        (root / "data" / "minimax_video").write_text("local\n", encoding="utf-8")
        previous = os.getcwd()
        ai_api._VIDEO = None
        try:
            os.chdir(root)
            with patch("AI.qwen_api.client._find_env", return_value=root / ".env"), patch.dict(
                os.environ,
                {
                    "QWEN_API_KEY": "test-key",
                    "TOKE_PLAN_QWEN_API_KEY": "",
                    "QWEN_ENV": "test",
                    "QWEN_BASE_URL": "",
                    "MINIMAX_VIDEO": "cloud",
                    "MINIMAX_H3_BASE_URL": "",
                    "MINIMAX_H3_API_KEY": "",
                },
            ):
                local = ai_api._video_client()
                self.assertEqual(local.client.base_url, DEFAULT_LOCAL_VIDEO_URL)
                self.assertEqual(local.client.api_key, LOCAL_DUMMY_KEY)
                (root / "data" / "minimax_video").write_text("cloud\n", encoding="utf-8")
                cloud = ai_api._video_client()
                self.assertEqual(cloud.client.base_url, HOST)
                self.assertEqual(cloud.client.api_key, "test-key")
                self.assertIsNot(cloud, local)
        finally:
            os.chdir(previous)
            ai_api._VIDEO = None

    def _catalog(self, payload=None, raw=None):
        root = Path(tempfile.mkdtemp())
        (root / "config").mkdir()
        env_file = root / ".env"
        env_file.write_text("", encoding="utf-8")
        if raw is not None or payload is not None:
            text = raw if raw is not None else json.dumps(payload, ensure_ascii=False)
            (root / "config" / "video_catalog.json").write_text(text, encoding="utf-8")
        return env_file

    def _mode_client(self, local, env_path=None):
        captured, opener = self._capture({"output": {"task_id": "vid-mode", "task_status": "PENDING"}})
        client = QwenVideoClient(client=_client(opener), env_path=env_path)
        client.local = bool(local)
        return captured, client

    def test_preview_and_generate_defaults_match_legacy_submit(self):
        self.assertIsNot(PREVIEW_PARAMETERS, GENERATE_PARAMETERS)
        self.assertEqual(PREVIEW_PARAMETERS, GENERATE_PARAMETERS)
        self.assertEqual(tuple(PREVIEW_PARAMETERS), MODE_CONFIG_KEYS)
        self.assertEqual(PREVIEW_PARAMETERS["resolution"], "768P")
        self.assertIsNone(PREVIEW_PARAMETERS["ratio"])
        self.assertIsNone(PREVIEW_PARAMETERS["duration"])
        self.assertIs(PREVIEW_PARAMETERS["watermark"], False)
        self.assertEqual(PREVIEW_PARAMETERS["preset"], LOCAL_DEFAULT_PARAMETERS["preset"])
        self.assertIsNone(PREVIEW_PARAMETERS["url"])
        self.assertEqual(PREVIEW_PARAMETERS["model"], VIDEO_MODEL)
        images = [{"role": "reference_image", "url": "https://cdn.example/ref.png"}]
        env_path = self._catalog()
        self.assertEqual(video_mode_parameters(None, env_path=env_path), dict(PREVIEW_PARAMETERS))
        self.assertEqual(video_mode_parameters("", env_path=env_path), dict(GENERATE_PARAMETERS))
        self.assertEqual(
            video_mode_parameters("生成", env_path=env_path),
            video_mode_parameters("preview", env_path=env_path),
        )
        self.assertEqual(
            video_mode_parameters("预览", env_path=env_path),
            video_mode_parameters("generate", env_path=env_path),
        )
        captured, client = self._mode_client(False, env_path)
        client.submit_video("海面亮起来", duration=5, aspect_ratio="21:9", model="caller-model")
        omitted = dict(captured["body"]["parameters"])
        omitted_url = captured["url"]
        omitted_model = captured["body"]["model"]
        client.submit_video("海面亮起来", duration=5, aspect_ratio="21:9", mode="generate")
        explicit = dict(captured["body"]["parameters"])
        client.submit_video("海面亮起来", duration=5, aspect_ratio="21:9", mode="preview")
        preview = dict(captured["body"]["parameters"])
        captured, local = self._mode_client(True, env_path)
        local.submit_video("海面亮起来", duration=6, aspect_ratio="16:9", images=images)
        local_omitted = dict(captured["body"]["parameters"])
        local.submit_video("海面亮起来", duration=6, aspect_ratio="16:9", images=images, mode="generate")
        local_generate = dict(captured["body"]["parameters"])
        local.submit_video("海面亮起来", duration=6, aspect_ratio="16:9", images=images, mode="预览")
        local_preview = dict(captured["body"]["parameters"])
        legacy = {
            "resolution": "768P",
            "ratio": "21:9",
            "duration": 5,
            "watermark": False,
        }
        self.assertEqual(omitted, legacy)
        self.assertEqual(explicit, legacy)
        self.assertEqual(preview, legacy)
        self.assertNotIn("preset", omitted)
        self.assertNotIn("url", omitted)
        self.assertNotIn("model", omitted)
        self.assertEqual(omitted_url, HOST + VIDEO_PATH)
        self.assertEqual(omitted_model, VIDEO_MODEL)
        self.assertEqual(local_omitted, local_generate)
        self.assertEqual(local_preview, local_generate)
        self.assertEqual(local_generate["ratio"], "16:9")
        self.assertEqual(local_generate["duration"], 6)
        self.assertEqual(local_generate["resolution"], "768P")
        self.assertIs(local_generate["watermark"], False)
        for key, value in LOCAL_DEFAULT_PARAMETERS.items():
            self.assertEqual(local_generate[key], value)
        self.assertNotIn("width", local_generate)
        self.assertNotIn("height", local_generate)

    def test_mode_parameter_overrides_diverge_and_local_merge_remains(self):
        images = [{"role": "reference_image", "url": "https://cdn.example/ref.png"}]
        env_path = self._catalog(
            {
                "profiles": {
                    "preview": {"resolution": "1080P", "ratio": "1:1", "watermark": False},
                    "generate": {"watermark": True, "duration": 8, "preset": "quality", "seed": 7},
                }
            }
        )
        preview_spec = video_mode_parameters("preview", env_path=env_path)
        generate_spec = video_mode_parameters("generate", env_path=env_path)
        self.assertEqual(tuple(preview_spec), MODE_CONFIG_KEYS)
        self.assertEqual(set(generate_spec), set(MODE_CONFIG_KEYS) | {"seed"})
        self.assertIsNone(preview_spec["url"])
        self.assertEqual(preview_spec["model"], VIDEO_MODEL)
        self.assertIsNone(generate_spec["url"])
        self.assertEqual(generate_spec["model"], VIDEO_MODEL)
        self.assertEqual(preview_spec["resolution"], "1080P")
        self.assertEqual(generate_spec["resolution"], "768P")
        self.assertEqual(video_mode_parameters("预览", env_path=env_path)["resolution"], "1080P")
        self.assertEqual(video_mode_parameters("生成", env_path=env_path)["preset"], "quality")
        self.assertEqual(preview_spec["preset"], "fast")
        self.assertEqual(generate_spec["preset"], "quality")
        self.assertIs(preview_spec["watermark"], False)
        self.assertIs(generate_spec["watermark"], True)
        captured, cloud = self._mode_client(False, env_path)
        cloud.submit_video("海面亮起来", duration=6, aspect_ratio="16:9", mode="preview")
        cloud_preview = dict(captured["body"]["parameters"])
        cloud.submit_video("海面亮起来", duration=6, aspect_ratio="16:9", mode="generate")
        cloud_generate = dict(captured["body"]["parameters"])
        cloud.submit_video("海面亮起来", duration=6, aspect_ratio="16:9")
        cloud_omitted = dict(captured["body"]["parameters"])
        captured, local = self._mode_client(True, env_path)
        with patch.dict(LOCAL_DEFAULT_PARAMETERS, {"trace_local_merge": "yes"}):
            local.submit_video(
                "海面亮起来",
                duration=6,
                aspect_ratio="16:9",
                images=images,
                mode="preview",
            )
            local_preview = dict(captured["body"]["parameters"])
            local.submit_video(
                "海面亮起来",
                duration=6,
                aspect_ratio="16:9",
                images=images,
                mode="generate",
            )
            local_generate = dict(captured["body"]["parameters"])
        self.assertEqual(
            cloud_preview,
            {"resolution": "1080P", "ratio": "1:1", "duration": 6, "watermark": False},
        )
        self.assertEqual(cloud_generate["resolution"], "768P")
        self.assertEqual(cloud_generate["ratio"], "16:9")
        self.assertEqual(cloud_generate["duration"], 8)
        self.assertIs(cloud_generate["watermark"], True)
        self.assertEqual(cloud_generate["seed"], 7)
        self.assertNotIn("preset", cloud_generate)
        self.assertNotIn("url", cloud_generate)
        self.assertNotIn("model", cloud_generate)
        self.assertEqual(cloud_omitted, cloud_generate)
        self.assertEqual(local_preview["resolution"], "1080P")
        self.assertEqual(local_preview["ratio"], "1:1")
        self.assertEqual(local_preview["duration"], 6)
        self.assertEqual(local_preview["preset"], "fast")
        self.assertEqual(local_preview["trace_local_merge"], "yes")
        self.assertNotIn("seed", local_preview)
        self.assertNotIn("width", local_preview)
        self.assertEqual(local_generate["preset"], "quality")
        self.assertEqual(local_generate["duration"], 8)
        self.assertEqual(local_generate["seed"], 7)
        self.assertEqual(local_generate["trace_local_merge"], "yes")
        self.assertIs(local_generate["watermark"], True)
        self.assertEqual(local_generate["resolution"], "768P")
        self.assertNotIn("width", local_generate)
        self.assertNotIn("height", local_generate)

    def test_unknown_submit_mode_and_bad_parameter_json(self):
        def opener(request, timeout=None):
            raise AssertionError("should not request")

        client = QwenVideoClient(client=_client(opener), env_path=self._catalog())
        with self.assertRaises(QwenError) as unknown:
            client.submit_video("海", mode="local")
        self.assertIn("preview", str(unknown.exception))
        bad_client = QwenVideoClient(client=_client(opener), env_path=self._catalog(raw="not-json"))
        with self.assertRaises(QwenError) as bad:
            bad_client.submit_video("海", mode="preview")
        self.assertIn("video_catalog.json", str(bad.exception))
        self.assertNotIn("not-json", str(bad.exception))
        listed_client = QwenVideoClient(client=_client(opener), env_path=self._catalog(raw="[1]"))
        with self.assertRaises(QwenError) as listed:
            listed_client.submit_video("海", mode="generate")
        self.assertIn("video_catalog.json", str(listed.exception))
        short_client = QwenVideoClient(
            client=_client(opener),
            env_path=self._catalog({"profiles": {"generate": {"duration": 3}}}),
        )
        with self.assertRaises(QwenError) as short:
            short_client.submit_video("海", duration=6, mode="generate")
        self.assertIn("4 到 15", str(short.exception))
        url_client = QwenVideoClient(
            client=_client(opener),
            env_path=self._catalog({"routes": {"preview": {"url": "ftp://preview.example"}}}),
        )
        with self.assertRaises(QwenError) as bad_url:
            url_client.submit_video("海", mode="preview")
        self.assertIn("url", str(bad_url.exception))
        model_client = QwenVideoClient(
            client=_client(opener),
            env_path=self._catalog({"routes": {"generate": {"model": 1}}}),
        )
        with self.assertRaises(QwenError) as bad_model:
            model_client.submit_video("海", mode="generate")
        self.assertIn("model", str(bad_model.exception))

    def test_video_submit_rpc_defaults_to_generate_parameters(self):
        from AI import api as ai_api

        env_path = self._catalog({"profiles": {"preview": {"resolution": "1080P"}}})
        captured, client = self._mode_client(False, env_path)
        with patch.object(ai_api, "_video_client", return_value=client):
            ai_api.video_submit({"text": "海面亮起来", "duration": 5, "aspect_ratio": "16:9"})
            omitted = dict(captured["body"]["parameters"])
            ai_api.video_submit({"text": "海面亮起来", "duration": 5, "aspect_ratio": "16:9", "mode": ""})
            blank = dict(captured["body"]["parameters"])
            ai_api.video_submit(
                {"text": "海面亮起来", "duration": 5, "aspect_ratio": "16:9", "mode": "generate"}
            )
            explicit = dict(captured["body"]["parameters"])
            ai_api.video_submit(
                {"text": "海面亮起来", "duration": 5, "aspect_ratio": "16:9", "mode": "preview"}
            )
            preview = dict(captured["body"]["parameters"])
        self.assertEqual(omitted["resolution"], "768P")
        self.assertEqual(omitted["ratio"], "16:9")
        self.assertEqual(omitted["duration"], 5)
        self.assertIs(omitted["watermark"], False)
        self.assertNotIn("preset", omitted)
        self.assertEqual(blank, omitted)
        self.assertEqual(explicit, omitted)
        self.assertEqual(preview["resolution"], "1080P")
        self.assertEqual(preview["duration"], 5)
        self.assertNotIn("preset", preview)
        self.assertEqual(captured["body"]["model"], VIDEO_MODEL)
        self.assertEqual(captured["url"], HOST + VIDEO_PATH)

    def test_mode_url_and_model_stay_on_that_set(self):
        from AI import api as ai_api

        images = [{"role": "reference_image", "url": "https://cdn.example/ref.png"}]
        env_path = self._catalog(
            {
                "routes": {
                    "preview": {
                        "url": "http://preview.example:8765/api/v1",
                        "model": "MiniMax-H3-preview",
                    },
                    "generate": {
                        "url": "https://generate.example/compatible-mode/v1",
                        "model": "MiniMax/MiniMax-H3-gen",
                    },
                }
            }
        )
        captured, cloud = self._mode_client(False, env_path)
        cloud_captured = captured
        preview_task = cloud.submit_video(
            "海面亮起来",
            duration=5,
            aspect_ratio="16:9",
            mode="preview",
            model="caller-model",
        )
        preview_url = captured["url"]
        preview_model = captured["body"]["model"]
        preview_parameters = dict(captured["body"]["parameters"])
        generate_task = cloud.submit_video(
            "海面亮起来",
            duration=5,
            aspect_ratio="16:9",
            mode="generate",
        )
        generate_url = captured["url"]
        generate_model = captured["body"]["model"]
        cloud.poll(preview_task.id, poll_url=preview_task.poll_url)
        preview_poll = captured["url"]
        cloud.poll(generate_task.id, previous=generate_task)
        generate_poll = captured["url"]
        captured, local = self._mode_client(True, env_path)
        local.submit_video(
            "海面亮起来",
            duration=6,
            images=images,
            mode="preview",
        )
        local_url = captured["url"]
        local_body = dict(captured["body"])
        with patch.object(ai_api, "_video_client", return_value=cloud):
            ai_api.video_submit(
                {
                    "text": "海面亮起来",
                    "duration": 5,
                    "aspect_ratio": "16:9",
                    "mode": "preview",
                    "model": "other-model",
                }
            )
            rpc_url = cloud_captured["url"]
            rpc_model = cloud_captured["body"]["model"]
            polled = ai_api.video_poll(
                {"task_id": preview_task.id, "poll_url": preview_task.poll_url}
            )
            rpc_poll = cloud_captured["url"]
        self.assertEqual(preview_url, "http://preview.example:8765" + VIDEO_PATH)
        self.assertEqual(preview_model, "MiniMax-H3-preview")
        self.assertNotIn("url", preview_parameters)
        self.assertNotIn("model", preview_parameters)
        self.assertEqual(preview_parameters["resolution"], "768P")
        self.assertEqual(preview_task.poll_url, "http://preview.example:8765/api/v1/tasks/vid-mode")
        self.assertEqual(generate_url, "https://generate.example" + VIDEO_PATH)
        self.assertEqual(generate_model, "MiniMax/MiniMax-H3-gen")
        self.assertEqual(generate_task.poll_url, "https://generate.example/api/v1/tasks/vid-mode")
        self.assertEqual(preview_poll, preview_task.poll_url)
        self.assertEqual(generate_poll, generate_task.poll_url)
        self.assertEqual(local_url, preview_url)
        self.assertEqual(local_body["model"], "MiniMax-H3-preview")
        self.assertEqual(local_body["parameters"]["preset"], "fast")
        self.assertNotIn("url", local_body["parameters"])
        self.assertEqual(rpc_url, preview_url)
        self.assertEqual(rpc_model, "MiniMax-H3-preview")
        self.assertEqual(rpc_poll, preview_task.poll_url)
        self.assertEqual(polled["poll_url"], preview_task.poll_url)

    def test_platform_model_and_profile_combine_independently(self):
        from AI.qwen_api.video_catalog import register_adapter, unregister_adapter, video_registry

        class ProbeAdapter(object):
            name = "probe"
            submit_path = "/probe/submit"
            headers = {"X-Probe": "yes"}
            task_prefix = "/probe/tasks/"

        register_adapter(ProbeAdapter())
        images = [{"role": "reference_image", "url": "https://cdn.example/ref.png"}]
        env_path = self._catalog(
            {
                "models": {"studio-take": {"id": "studio/take-1"}},
                "profiles": {
                    "preview": {"watermark": True},
                    "generate": {"resolution": "1080P"},
                },
                "platforms": {
                    "studio": {
                        "adapter": "probe",
                        "host": "https://studio.example",
                        "auth": "client",
                        "media": "open",
                        "emit_local_defaults": False,
                    }
                },
                "routes": {
                    "preview": {"platform": "studio", "model": "studio-take"},
                    "generate": {
                        "platform": "omniref",
                        "model": "minimax-h3",
                        "url": "http://generate.example:8765",
                    },
                },
            }
        )
        seen = {}

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["probe"] = request.get_header("X-probe")
            seen["authorization"] = request.get_header("Authorization")
            if request.data:
                seen["body"] = json.loads(request.data.decode("utf-8"))
            return _Body(json.dumps({"output": {"task_id": "vid-mode", "task_status": "PENDING"}}))

        try:
            with patch.dict(os.environ, {"MINIMAX_H3_API_KEY": ""}):
                registry = video_registry(env_path)
                self.assertIn("dashscope", registry["adapters"])
                self.assertIn("probe", registry["adapters"])
                self.assertEqual(registry["models"]["minimax-h3"]["id"], VIDEO_MODEL)
                self.assertEqual(registry["models"]["studio-take"]["id"], "studio/take-1")
                self.assertNotIn("model", registry["profiles"]["preview"])
                self.assertNotIn("url", registry["profiles"]["generate"])
                self.assertIs(registry["profiles"]["preview"]["watermark"], True)
                self.assertEqual(registry["profiles"]["generate"]["resolution"], "1080P")
                self.assertEqual(registry["profiles"]["preview"]["resolution"], "768P")
                client = QwenVideoClient(client=_client(opener), env_path=env_path)
                preview = client.submit_video("海面亮起来", duration=5, aspect_ratio="16:9", mode="preview")
                preview_url = seen["url"]
                preview_probe = seen["probe"]
                preview_body = dict(seen["body"])
                client.submit_video(
                    "海面亮起来",
                    duration=6,
                    aspect_ratio="16:9",
                    images=images,
                    mode="generate",
                )
                generate_url = seen["url"]
                generate_auth = seen["authorization"]
                generate_body = dict(seen["body"])
            self.assertEqual(preview_url, "https://studio.example/probe/submit")
            self.assertEqual(preview_probe, "yes")
            self.assertEqual(preview_body["model"], "studio/take-1")
            self.assertIs(preview_body["parameters"]["watermark"], True)
            self.assertEqual(preview_body["parameters"]["resolution"], "768P")
            self.assertNotIn("preset", preview_body["parameters"])
            self.assertEqual(preview.poll_url, "https://studio.example/probe/tasks/vid-mode")
            self.assertEqual(preview.platform, "studio")
            self.assertEqual(generate_url, "http://generate.example:8765" + VIDEO_PATH)
            self.assertEqual(generate_body["model"], VIDEO_MODEL)
            self.assertEqual(generate_body["parameters"]["resolution"], "1080P")
            self.assertEqual(generate_body["parameters"]["preset"], "fast")
            self.assertIs(generate_body["parameters"]["watermark"], False)
            self.assertEqual(generate_auth, "Bearer local")
            self.assertNotEqual(preview_body["model"], generate_body["model"])
        finally:
            unregister_adapter("probe")

    def test_shipped_catalog_selects_wan3_and_keeps_minimax(self):
        from AI.qwen_api.client import QWEN_ENVS
        from AI.qwen_api.video_catalog import catalog_path, video_registry

        shipped = Path(__file__).resolve().parents[1] / "config" / "video_catalog.json"
        self.assertEqual(catalog_path(None), shipped)
        self.assertTrue(shipped.is_file())
        images = [{"role": "reference_image", "url": "https://cdn.example/ref.png"}]
        with patch.dict(os.environ, {"TOKE_PLAN_QWEN_API_KEY": "plan-key"}):
            registry = video_registry()
            self.assertEqual(set(registry["platforms"]), {"qwen", "omniref", "token-plan"})
            self.assertEqual(registry["models"]["minimax-h3"]["id"], "MiniMax/MiniMax-H3")
            self.assertEqual(registry["models"]["wan3.0-video"]["id"], "wan3.0-video")
            self.assertEqual(registry["profiles"]["minimax"]["resolution"], "768P")
            self.assertIs(registry["profiles"]["minimax"]["watermark"], False)
            self.assertEqual(registry["profiles"]["minimax"]["preset"], "fast")
            self.assertEqual(registry["profiles"]["preview"]["resolution"], "480P")
            self.assertEqual(registry["profiles"]["generate"]["resolution"], "720P")
            self.assertEqual(registry["routes"]["preview"]["platform"], "token-plan")
            self.assertEqual(registry["routes"]["preview"]["model"], "wan3.0-video")
            self.assertEqual(registry["routes"]["generate"]["platform"], "token-plan")
            self.assertEqual(registry["routes"]["generate"]["model"], "wan3.0-video")
            captured, client = self._mode_client(False)
            preview = client.submit_video("海面亮起来", duration=2, aspect_ratio="16:9", mode="preview")
            preview_body = dict(captured["body"])
            preview_url = captured["url"]
            preview_auth = captured["authorization"]
            client.submit_video(
                "海面亮起来",
                duration=30,
                aspect_ratio=None,
                images=images,
                mode="generate",
            )
            generate_body = dict(captured["body"])
            client.submit_video("海面亮起来", duration=-1, aspect_ratio="9:16", mode="generate")
            negative = dict(captured["body"]["parameters"])
            with self.assertRaises(QwenError) as too_short:
                client.submit_video("海面亮起来", duration=1, mode="preview")
        self.assertEqual(preview_url, QWEN_ENVS["prod"] + VIDEO_PATH)
        self.assertEqual(preview_auth, "Bearer plan-key")
        self.assertEqual(preview.platform, "token-plan")
        self.assertEqual(preview_body["model"], "wan3.0-video")
        self.assertEqual(preview_body["parameters"]["resolution"], "480P")
        self.assertEqual(preview_body["parameters"]["ratio"], "16:9")
        self.assertEqual(preview_body["parameters"]["duration"], 2)
        self.assertNotIn("watermark", preview_body["parameters"])
        self.assertNotIn("preset", preview_body["parameters"])
        self.assertNotIn("prompt_extend", preview_body["parameters"])
        self.assertEqual(generate_body["model"], "wan3.0-video")
        self.assertEqual(generate_body["parameters"]["resolution"], "720P")
        self.assertEqual(generate_body["parameters"]["ratio"], "adaptive")
        self.assertEqual(generate_body["parameters"]["duration"], 30)
        self.assertNotIn("watermark", generate_body["parameters"])
        self.assertNotIn("preset", generate_body["parameters"])
        self.assertEqual(negative["duration"], -1)
        self.assertEqual(negative["resolution"], "720P")
        self.assertIn("2 到 30", str(too_short.exception))
        self.assertIn("-1", str(too_short.exception))

    def test_remote_video_submit_passes_mode(self):
        from end.remote import RemoteVideoClient

        seen = []

        def fake_call(method, params, timeout=None):
            seen.append((method, dict(params)))
            return {"id": "v", "status": "submitted", "poll_url": "", "url": "", "error": ""}

        with patch("end.remote.call", fake_call):
            RemoteVideoClient().submit_video("海", duration=5, aspect_ratio="16:9")
            RemoteVideoClient().submit_video("海", mode="preview")
            RemoteVideoClient().submit_video("海", mode="")
        self.assertEqual([item[0] for item in seen], ["video.submit", "video.submit", "video.submit"])
        self.assertEqual(seen[0][1]["mode"], "generate")
        self.assertEqual(seen[0][1]["text"], "海")
        self.assertEqual(seen[0][1]["duration"], 5)
        self.assertEqual(seen[0][1]["aspect_ratio"], "16:9")
        self.assertEqual(seen[1][1]["mode"], "preview")
        self.assertEqual(seen[2][1]["mode"], "generate")

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
