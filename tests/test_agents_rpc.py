"""Agents /rpc matches the AI JSON-RPC contract End and the studio already call.

Live model and media hosts are not contacted. Those methods are exercised
through fakes, plus the real error paths that fail before any HTTP call
(empty text.ask, music refusal).

    python3 -m unittest tests.test_agents_rpc
"""

import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from AI.api import METHODS as AI_METHODS
from AI.qwen_api.audio import MUSIC_UNAVAILABLE
from AI.qwen_api.client import QwenError
from Agents.api import METHODS as AGENT_METHODS
from Agents.server import MAX_BYTES, Handler

ROOT = Path(__file__).resolve().parents[1]
PASS_ROLE = "\n".join((
    "#### 图1 · 林浅",
    "16:9",
    "全图共 4 格",
    "五官 10 项锁定",
    "声纹",
    "≤8 词",
    "fifth panel",
    "different face",
))
SENTINEL = "CRITIC-REWRITE-SENTINEL"
ASSET_BRIEF = "任务：资产锁定\n\n林浅在雨夜车站等一班车。"
SCRIPT_BRIEF = "任务：剧本\n\n林浅在雨夜车站等一班车。"
REPAIR_BRIEF = "任务：建议修补\n阶段：asset\n失败项：\n[]\n"
METHOD_NAMES = (
    "ping",
    "text.ask",
    "text.complete",
    "catalog",
    "models.normalize",
    "models.label",
    "templates.normalize",
    "templates.label",
    "templates.policy",
    "templates.files",
    "templates.render",
    "skills.load",
    "skills.route",
    "skills.shape",
    "drill",
    "write",
    "llm.status",
    "vision.read",
    "image.submit",
    "image.query",
    "image.result",
    "image.download",
    "video.submit",
    "video.poll",
    "video.download",
    "audio.speak",
    "audio.compose",
    "layout.speak",
    "layout.compose",
    "studio.drill",
    "studio.write",
)


class _ImageTask(object):
    def __init__(self, task_id="img-1"):
        self.id = task_id
        self.status = "SUCCEEDED"
        self.url = "http://example.test/a.png"
        self.error = ""


class _ImageClient(object):
    def __init__(self):
        self.calls = []

    def submit_image(self, prompt, image=None, size=None):
        self.calls.append(("submit", prompt, image, size))
        return _ImageTask()

    def query_status(self, task_id):
        self.calls.append(("query", task_id))
        return _ImageTask(task_id or "img-1")

    def get_result(self, task_id):
        self.calls.append(("result", task_id))
        return _ImageTask(task_id or "img-1")

    def download_image(self, url, dest):
        self.calls.append(("download", url, dest))
        return dest or "/tmp/a.png"


class _VideoTask(object):
    def __init__(self):
        self.id = "vid-1"
        self.status = "completed"
        self.poll_url = "http://example.test/poll"
        self.url = "http://example.test/a.mp4"
        self.error = ""
        self.platform = "token-plan"


class _VideoClient(object):
    def __init__(self):
        self.calls = []

    def submit_video(self, text, **kwargs):
        self.calls.append(("submit", text, dict(kwargs)))
        return _VideoTask()

    def poll(self, task_id, timeout=60, poll_url="", platform_name=""):
        self.calls.append(("poll", task_id, poll_url, platform_name))
        return _VideoTask()

    def for_platform(self, platform):
        self.calls.append(("platform", platform))
        return self

    def download_video(self, url, dest, timeout=300):
        self.calls.append(("download", url, dest))
        return dest or "/tmp/a.mp4"


def _asset_complete(role, messages, **_kwargs):
    if role == "specialist":
        return "节点正文"
    if role in ("generator", "director"):
        return PASS_ROLE
    return json.dumps(
        {"verdict": "accept", "issues": [], "draft": "#### 图9 · 改写\n%s\n" % SENTINEL},
        ensure_ascii=False,
    )


class RpcContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["AGENTS_RPC_QUIET"] = "1"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, name="agents-rpc-test", daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def rpc(self, method, params=None, timeout=30):
        body, status = self._post(
            json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": "case-%s" % method},
                ensure_ascii=False,
            ).encode("utf-8"),
            timeout=timeout,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("jsonrpc"), "2.0")
        self.assertEqual(body.get("id"), "case-%s" % method)
        self.assertNotIn("error", body, body.get("error"))
        return body["result"]

    def rpc_error(self, method, params=None, timeout=30):
        body, status = self._post(
            json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 7},
                ensure_ascii=False,
            ).encode("utf-8"),
            timeout=timeout,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("id"), 7)
        self.assertNotIn("result", body)
        return body["error"]

    def _post(self, payload, timeout=30, path="/rpc", headers=None):
        request = urllib.request.Request(
            "http://127.0.0.1:%s%s" % (self.port, path),
            data=payload,
            headers=headers or {"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}, response.status
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            return parsed, error.code

    def _wait(self, job_id):
        deadline = time.time() + 5
        last = None
        while time.time() < deadline:
            last = self.rpc("llm.status", {"job_id": job_id})
            if last.get("status") in ("succeeded", "failed", "lost"):
                return last
            time.sleep(0.02)
        self.fail("job %s did not finish: %s" % (job_id, last))

    def _job_keys(self, snap):
        self.assertEqual(
            set(snap),
            {"job_id", "status", "nodes", "result", "error"},
        )
        for node in snap["nodes"]:
            self.assertEqual(set(node), {"id", "name", "status"})

    def test_method_table_matches_the_ai_server(self):
        self.assertEqual(tuple(AGENT_METHODS), METHOD_NAMES)
        self.assertEqual(set(AGENT_METHODS), set(AI_METHODS))

    def test_ping(self):
        self.assertEqual(self.rpc("ping"), {"ok": True})
        self.assertEqual(self.rpc("ping"), AI_METHODS["ping"]({}))

    def test_catalog(self):
        result = self.rpc("catalog")
        self.assertEqual(result, AI_METHODS["catalog"]({}))
        self.assertEqual(result["default_model"], "deepseek-v4-flash-0731")
        self.assertEqual({item["id"] for item in result["models"]}, {"deepseek-v4-flash-0731"})
        self.assertEqual(result["default_template"], "short-drama-director")
        self.assertTrue(result["templates"])

    def test_models_normalize(self):
        result = self.rpc("models.normalize", {"model": "qwen3.8-flash"})
        self.assertEqual(result, {"model": "deepseek-v4-flash-0731"})
        self.assertEqual(result, AI_METHODS["models.normalize"]({"model": "nope"}))

    def test_models_label(self):
        result = self.rpc("models.label", {"model": "deepseek-v4-flash-0731"})
        self.assertEqual(result, AI_METHODS["models.label"]({"model": "deepseek-v4-flash-0731"}))
        self.assertTrue(result["label"])

    def test_templates_normalize(self):
        result = self.rpc("templates.normalize", {"template": "apple-product"})
        self.assertEqual(result, {"template": "short-drama-director"})

    def test_templates_label(self):
        result = self.rpc("templates.label", {"template": ""})
        self.assertEqual(result, AI_METHODS["templates.label"]({}))
        self.assertTrue(result["label"])

    def test_templates_policy(self):
        result = self.rpc("templates.policy", {})
        self.assertEqual(result, {"policy": "drama_still"})

    def test_templates_files(self):
        result = self.rpc("templates.files", {})
        self.assertEqual(result, AI_METHODS["templates.files"]({}))
        self.assertIsInstance(result["files"], list)
        self.assertTrue(result["files"])

    def test_templates_render(self):
        result = self.rpc("templates.render", {})
        self.assertEqual(set(result), {
            "image",
            "image_from",
            "video_lock",
            "video_next",
            "video_tail",
            "video_from",
        })

    def test_skills_load(self):
        params = {"template": "short-drama-director", "context": "任务：资产锁定", "stage": "P2"}
        result = self.rpc("skills.load", params, timeout=60)
        direct = AI_METHODS["skills.load"](params)
        self.assertEqual(set(result), {"pack", "extra"})
        self.assertEqual(result["extra"], direct["extra"])
        self.assertIsInstance(result["pack"], str)
        self.assertIn("sd-asset-engine", result["pack"])

    def test_skills_route(self):
        params = {"template": "short-drama-director", "context": ""}
        result = self.rpc("skills.route", params)
        self.assertEqual(result, AI_METHODS["skills.route"](params))
        self.assertIn("extra", result)

    def test_skills_shape(self):
        result = self.rpc("skills.shape", {"template": "short-drama-director"})
        self.assertEqual(result, AI_METHODS["skills.shape"]({"template": "short-drama-director"}))
        self.assertIn("image_prompts", result["shape"])

    def test_text_ask(self):
        with patch("AI.qwen_api.graph.generate_text", return_value="  角色  ") as fake:
            result = self.rpc("text.ask", {"messages": [{"role": "user", "content": "分类"}], "model": "other"})
        self.assertEqual(result, {"text": "角色"})
        self.assertEqual(fake.call_args.kwargs["model"], "deepseek-v4-flash-0731")

    def test_text_ask_rejects_empty_messages(self):
        error = self.rpc_error("text.ask", {"messages": []})
        self.assertEqual(error["code"], -32000)
        self.assertIn("messages 不能为空", error["message"])
        self.assertIn("代码：", error["message"])

    def test_text_ask_keeps_provider_status(self):
        def explode(*_args, **_kwargs):
            raise QwenError("未配置 TOKE_PLAN_QWEN_API_KEY", status=401)

        with patch("AI.qwen_api.graph.generate_text", side_effect=explode):
            error = self.rpc_error("text.ask", {"messages": [{"role": "user", "content": "写"}]})
        self.assertEqual(error["code"], -32000)
        self.assertEqual(error["data"]["status"], 401)
        self.assertIn("未配置 TOKE_PLAN_QWEN_API_KEY", error["message"])

    def test_text_complete_is_one_node_and_not_a_suite(self):
        def fake(role, messages, **_kwargs):
            self.assertEqual(role, "director")
            self.assertEqual(messages[-1]["content"], "汇总这一节点")
            return "一节点"

        def explode(*_args, **_kwargs):
            raise AssertionError("text.complete 不能进入套件")

        with patch("Agents.text.complete_text", side_effect=fake), patch(
            "AI.qwen_api.suite_graph.run_suite", side_effect=explode
        ), patch("AI.agent.layout.run_suite", side_effect=explode), patch(
            "AI.api.write", side_effect=explode
        ):
            snap = self.rpc(
                "text.complete",
                {
                    "role": "director",
                    "messages": [{"role": "user", "content": "汇总这一节点"}],
                    "model": "deepseek-v4-flash-0731",
                },
            )
        self._job_keys(snap)
        self.assertEqual(len(snap["nodes"]), 1)
        self.assertEqual(snap["nodes"][0]["id"], "director")
        finished = self._wait(snap["job_id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["result"], {"text": "一节点"})

    def test_text_complete_rejects_empty_messages(self):
        error = self.rpc_error("text.complete", {"messages": [], "role": "critic"})
        self.assertEqual(error["code"], -32000)
        self.assertIn("messages 不能为空", error["message"])

    def test_drill_submits_a_job_and_llm_status_returns_the_body(self):
        def fake_suite(*_args, **_kwargs):
            return {"phase": "ask", "reply": "选一个", "options": ["甲"], "brief": ""}

        with patch("AI.agent.layout.run_suite", side_effect=fake_suite):
            snap = self.rpc("drill", {"messages": [{"role": "user", "content": "雨夜"}], "model": "qwen3.8-flash"})
        self._job_keys(snap)
        self.assertEqual(snap["status"], "running")
        self.assertEqual(snap["result"], None)
        self.assertEqual(snap["nodes"][-1]["name"], "汇总")
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["error"], "")
        self.assertEqual(done["result"]["phase"], "ask")
        self.assertEqual(done["result"]["reply"], "选一个")

    def test_write_script_stays_on_the_existing_writer(self):
        def fake_suite(*_args, **_kwargs):
            return {"script": "正式剧本", "partials": []}

        def forbid_p2(*_args, **_kwargs):
            raise AssertionError("script write must not enter the P2 loop")

        with patch("AI.agent.layout.run_suite", side_effect=fake_suite), patch(
            "Agents.workers.asset.run_p2_asset_loop", side_effect=forbid_p2
        ):
            snap = self.rpc("write", {"brief": SCRIPT_BRIEF, "skill_pack": "", "model": "qwen3.8-flash"})
        self.assertEqual(snap["status"], "running")
        self.assertEqual(snap["nodes"][-1]["name"], "汇总")
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["script"], "正式剧本")
        self.assertNotIn("p2", done["result"])

    def test_write_asset_uses_the_p2_loop_and_drops_critic_rewrites(self):
        def forbid_suite(*_args, **_kwargs):
            raise AssertionError("asset write must not enter the suite graph")

        with patch("Agents.text.complete_text", side_effect=_asset_complete), patch(
            "AI.agent.layout.run_suite", side_effect=forbid_suite
        ):
            snap = self.rpc("write", {"brief": ASSET_BRIEF, "skill_pack": "资产引擎摘录", "model": ""})
        self._job_keys(snap)
        self.assertEqual([node["name"] for node in snap["nodes"]], ["生成", "审查", "机检"])
        self.assertTrue(all(node["status"] == "等待" for node in snap["nodes"]))
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertTrue(all(node["status"] == "完成" for node in done["nodes"]))
        self.assertIn("#### 图1 · 林浅", done["result"]["image_prompts"])
        self.assertNotIn(SENTINEL, done["result"]["image_prompts"])
        self.assertEqual(done["result"]["partials"], [])
        self.assertTrue(done["result"]["p2"]["passed"])
        self.assertEqual(done["result"]["p2"]["stop_reason"], "machine_check_pass")
        self.assertEqual(done["result"]["p2"]["attempts"], 1)

    def test_write_repair_stays_on_the_repair_graph(self):
        def fake_repair(*_args, **_kwargs):
            return {"repairs": [{"id": "图-0", "text": "改过的块"}]}

        def forbid_p2(*_args, **_kwargs):
            raise AssertionError("repair must not enter the P2 loop")

        with patch("AI.qwen_api.repair_graph.run_repair", side_effect=fake_repair), patch(
            "Agents.workers.asset.run_p2_asset_loop", side_effect=forbid_p2
        ):
            snap = self.rpc("write", {"brief": REPAIR_BRIEF})
        self.assertEqual(snap["nodes"][-1]["id"], "merge")
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["repairs"][0]["id"], "图-0")

    def test_llm_status_lost(self):
        lost = self.rpc("llm.status", {"job_id": "missing-job"})
        self.assertEqual(lost["status"], "lost")
        self.assertEqual(lost["nodes"], [])
        self.assertEqual(lost["result"], None)
        self.assertEqual(lost["error"], "任务已丢失")
        self.assertEqual(lost, AI_METHODS["llm.status"]({"job_id": "missing-job"}))

    def test_vision_read_is_a_job(self):
        with patch("AI.vision.read_upload", return_value="药盒，白色") as fake:
            snap = self.rpc("vision.read", {"path": "/tmp/upload.png", "trace_prompts": ""})
        self.assertEqual(snap["nodes"], [{"id": "vision", "name": "读图", "status": "等待"}])
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"], {"text": "药盒，白色"})
        fake.assert_called_once_with("/tmp/upload.png")

    def test_image_submit_query_result_download(self):
        client = _ImageClient()
        with patch("AI.api._image_client", return_value=client):
            submitted = self.rpc("image.submit", {"prompt": "雨夜", "image": None, "size": "9:16"})
            queried = self.rpc("image.query", {"task_id": "img-9"})
            resulted = self.rpc("image.result", {"task_id": "img-9"})
            saved = self.rpc("image.download", {"url": "http://example.test/a.png", "dest": "/tmp/hero.png"})
        self.assertEqual(set(submitted), {"id", "status", "url", "error"})
        self.assertEqual(submitted["id"], "img-1")
        self.assertEqual(submitted["status"], "SUCCEEDED")
        self.assertEqual(queried["id"], "img-9")
        self.assertEqual(resulted["url"], "http://example.test/a.png")
        self.assertEqual(saved, {"path": "/tmp/hero.png"})
        self.assertEqual(client.calls[0][0], "submit")
        self.assertEqual(client.calls[0][1], "雨夜")
        self.assertEqual(client.calls[0][3], "9:16")

    def test_image_submit_error_uses_the_rpc_error_envelope(self):
        class Broken(object):
            def submit_image(self, prompt, image=None, size=None):
                raise QwenError("生图提示词是空的")

        with patch("AI.api._image_client", return_value=Broken()):
            error = self.rpc_error("image.submit", {"prompt": ""})
        self.assertEqual(error["code"], -32000)
        self.assertIn("生图提示词是空的", error["message"])

    def test_video_submit_poll_download(self):
        client = _VideoClient()
        with patch("AI.api._video_client", return_value=client):
            submitted = self.rpc(
                "video.submit",
                {"text": "她推门", "duration": 6, "aspect_ratio": "9:16", "images": ["http://example.test/a.png"]},
            )
            polled = self.rpc(
                "video.poll",
                {"task_id": "vid-1", "poll_url": "http://example.test/poll", "platform": "local", "timeout": 5},
            )
            saved = self.rpc("video.download", {"url": "http://example.test/a.mp4", "dest": "/tmp/shot.mp4", "timeout": 5})
        self.assertEqual(
            set(submitted),
            {"id", "status", "poll_url", "url", "error", "platform"},
        )
        self.assertEqual(submitted["id"], "vid-1")
        self.assertEqual(submitted["platform"], "token-plan")
        self.assertNotIn("mode", client.calls[0][2])
        self.assertEqual(client.calls[0][2]["duration"], 6)
        self.assertEqual(client.calls[0][2]["aspect_ratio"], "9:16")
        self.assertEqual(polled["status"], "completed")
        self.assertEqual(client.calls[1], ("platform", "local"))
        self.assertEqual(saved, {"path": "/tmp/shot.mp4"})

    def test_video_submit_forwards_an_explicit_mode(self):
        client = _VideoClient()
        with patch("AI.api._video_client", return_value=client):
            self.rpc("video.submit", {"text": "她推门", "mode": "preview", "duration": 4})
        self.assertEqual(client.calls[0][2]["mode"], "preview")

    def test_audio_speak_is_a_job(self):
        with patch("AI.qwen_api.audio.synthesize", return_value="/tmp/voice.mp3") as fake:
            snap = self.rpc("audio.speak", {"text": "你好", "dest": "/tmp/voice.mp3", "voice_id": "longanhuan_v3.6"})
        self.assertEqual(snap["nodes"][0]["name"], "旁白")
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"], {"path": "/tmp/voice.mp3"})
        self.assertEqual(fake.call_args.args[0], "你好")

    def test_audio_compose_refuses_without_a_provider_call(self):
        error = self.rpc_error("audio.compose", {"prompt": "钢琴", "dest": "/tmp/music.mp3"})
        self.assertEqual(error["code"], -32000)
        self.assertIn(MUSIC_UNAVAILABLE, error["message"])

    def test_layout_speak_is_a_job(self):
        with patch("AI.qwen_api.audio.synthesize", return_value="/tmp/layout.mp3"):
            snap = self.rpc("layout.speak", {"text": "旁白", "dest": "/tmp/layout.mp3"})
        done = self._wait(snap["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"], {"path": "/tmp/layout.mp3"})

    def test_layout_compose_refuses_without_a_provider_call(self):
        error = self.rpc_error("layout.compose", {"prompt": "钢琴", "dest": "/tmp/music.mp3"})
        self.assertEqual(error["code"], -32000)
        self.assertIn(MUSIC_UNAVAILABLE, error["message"])

    def test_studio_drill_parsed_and_unparsed(self):
        payload = {"phase": "ask", "reply": "选画幅", "options": ["16:9", "9:16"], "brief": ""}
        with patch("AI.qwen_api.graph.understand_text", return_value=json.dumps(payload, ensure_ascii=False)):
            parsed = self.rpc("studio.drill", {"template": "short-drama-director", "history": [], "line": "开始"})
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["phase"], "ask")
        self.assertEqual(parsed["reply"], "选画幅")
        self.assertEqual(parsed["options"], ["16:9", "9:16", "其他"])
        self.assertIn("raw", parsed)
        with patch("AI.qwen_api.graph.understand_text", return_value="不是 JSON"):
            raw = self.rpc("studio.drill", {"line": "再来"})
        self.assertEqual(raw, {"parsed": False, "raw": "不是 JSON"})

    def test_studio_write(self):
        with patch("AI.qwen_api.graph.generate_text", return_value="#### 图1 · 林浅") as fake:
            result = self.rpc(
                "studio.write",
                {
                    "template": "short-drama-director",
                    "history": [],
                    "line": "写资产",
                    "locked_brief": "画幅：9:16",
                    "stage": "P2",
                    "provider": {"model": "qwen3.8-flash"},
                },
                timeout=60,
            )
        self.assertEqual(set(result), {"raw"})
        self.assertEqual(result["raw"], "#### 图1 · 林浅")
        self.assertTrue(fake.called)

    def test_unknown_method_and_bad_json(self):
        error = self.rpc_error("no.such")
        self.assertEqual(error["code"], -32601)
        self.assertEqual(error["message"], "没有这个接口")
        body, status = self._post(b"{")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], -32700)
        self.assertIsNone(body["id"])
        missing, status = self._post(b"", headers={"Content-Type": "application/json", "Content-Length": "0"})
        self.assertEqual(status, 413)
        self.assertEqual(missing["error"]["code"], -32600)
        gone, status = self._post(b"{}", path="/nope")
        self.assertEqual(status, 404)
        self.assertEqual(gone, {"error": "没有这个接口"})

    def test_oversized_request_is_rejected_before_the_body_matters(self):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            "/rpc",
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": str(MAX_BYTES + 1)},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], -32600)

    def test_end_client_calls_this_server(self):
        import end.ai_client as client

        previous_ready = client._READY
        previous_memo = dict(client._MEMO)
        client._READY = True
        client._MEMO.clear()
        try:
            with patch.dict(
                os.environ,
                {"AIVIDEO_AI_URL": "http://127.0.0.1:%s" % self.port, "AIVIDEO_AI_AUTOSTART": "0"},
            ):
                self.assertEqual(client.call("ping"), {"ok": True})
                info = client.call("catalog")
            self.assertEqual(info["default_model"], "deepseek-v4-flash-0731")
        finally:
            client._READY = previous_ready
            client._MEMO.clear()
            client._MEMO.update(previous_memo)

    def test_end_and_studio_autostart_agents(self):
        end_text = (ROOT / "end" / "ai_client.py").read_text(encoding="utf-8")
        studio_text = (ROOT / "front" / "studio" / "ai_rpc.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        needle = '[sys.executable, "-m", "Agents.server"]'
        self.assertIn(needle, end_text)
        self.assertIn(needle, studio_text)
        self.assertNotIn('"AI.server"', end_text)
        self.assertNotIn('"AI.server"', studio_text)
        self.assertIn("Agents.server", compose)
        self.assertNotIn("AI.server", compose)


if __name__ == "__main__":
    unittest.main()
