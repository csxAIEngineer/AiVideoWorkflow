"""Content save, image replace, logs, and pause during writing."""

import base64
import json
import os
import struct
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.render_loop import _ffmpeg_bin, gate_for, invalidate_steps, load_run_state, save_run_state
from tests.test_render_loop import FakeICreat, _noop_sleep, run_local_pipeline
from end.intake import enrich_brief, load_intake, merge_intake
from AI.vision import QwenVisionError
from end.server import (
    Handler,
    pause_render,
    project_logs,
    remember,
    resume_render,
    run_project,
    save_asset_image,
    save_project_content,
)
from end.db import project_dir, read_project, write_project
from http.server import ThreadingHTTPServer


def tiny_png():
    return png_size(1, 1)


def png_size(width, height):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


SPEC = {
    "title": "雨夜",
    "script": "旧剧本",
    "storyboard": "旧分镜",
    "assets": [
        {"id": "A1", "title": "主角", "prompt": "旧提示"},
        {"id": "A2", "title": "雨", "prompt": "旧雨", "depends_on": "A1"},
    ],
    "shots": [{"id": "S01", "duration": 5, "prompt": "离开", "images": ["A1"]}],
}


def make_project(project_id, status="writing", **extra):
    created = "2026-01-01T00:00:00+08:00"
    project = {
        "id": project_id,
        "brief": "雨夜",
        "messages": [{"role": "user", "content": "雨夜的方向"}],
        "title": "雨夜",
        "status": status,
        "stage": "正在写剧本",
        "script": "旧剧本",
        "storyboard": "旧分镜",
        "image_prompts": "",
        "error": "UNIQUE_CANVAS_ERROR" if status == "error" else None,
        "events": [{"at": created, "message": "正在写剧本"}],
        "tasks": extra.pop("tasks", []),
        "progress": {"percent": 8, "phase": status, "message": "正在写剧本", "debug_llm": False},
        "cloud_log": "",
        "prompt_id": "",
        "video_file": "",
        "model": "deepseek-v4-pro",
        "created_at": created,
        "started_at": created,
        "updated_at": created,
        "finished_at": None,
    }
    project.update(extra)
    write_project(project)
    folder = project_dir(project_id)
    (folder / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


PUBLIC_BASE = "http://203.0.113.10:8787"


class ProjectApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        self._env = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = PUBLIC_BASE
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        url = "http://127.0.0.1:%s%s" % (self.port, path)
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = response.read()
                content_type = response.headers.get("Content-Type") or ""
                if "json" in content_type:
                    return response.status, json.loads(payload.decode("utf-8"))
                return response.status, payload
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                parsed = {"error": raw.decode("utf-8", "replace")}
            return error.code, parsed

    def rpc(self, method, params=None):
        status, body = self.request(
            "POST",
            "/rpc",
            json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1},
                ensure_ascii=False,
            ).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        return body

    def test_content_image_logs_and_pause_while_writing(self):
        project_id = "a11c0de00001"
        make_project(project_id, status="writing")
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/rpc.js", page)
        self.assertIn("Debug：只到意图解析".encode(), page)
        self.assertIn("Debug：只到提示词".encode(), page)
        self.assertIn(b"/app.js?v=79", page)
        self.assertIn(b"id=\"crew\"", page)
        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"projects.create", script)
        self.assertIn("参考图".encode(), script)
        self.assertIn("资产图".encode(), script)
        self.assertIn(b"mode: chosen", script)
        self.assertIn(b"debug_stop", script)
        self.assertIn(b"isIntentDebug", script)
        self.assertIn(b"isPromptDebug", script)
        body = self.rpc(
            "projects.save_content",
            {
                "id": project_id,
                "script": "新剧本",
                "storyboard": "新分镜",
                "assets": [{"id": "A1", "prompt": "新提示"}],
            },
        )["result"]
        self.assertEqual(body["script"], "新剧本")
        self.assertEqual(body["storyboard"], "新分镜")
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["script"], "新剧本")
        self.assertEqual(saved["storyboard"], "新分镜")
        self.assertEqual(saved["assets"][0]["prompt"], "新提示")
        self.assertEqual(saved["assets"][1]["prompt"], "旧雨")
        self.assertEqual(read_project(project_id)["script"], "新剧本")
        status, spec_body = self.request("GET", "/media/projects/%s/spec" % project_id)
        self.assertEqual(status, 200)
        self.assertEqual(spec_body["script"], "新剧本")

        png = tiny_png()
        body = self.rpc(
            "projects.set_asset_image",
            {
                "id": project_id,
                "asset_id": "A1",
                "data_base64": base64.b64encode(png).decode("ascii"),
            },
        )["result"]
        stored = project_dir(project_id) / "outputs" / "images" / "A1.png"
        self.assertEqual(stored.read_bytes(), png)
        step = load_run_state(project_dir(project_id))["steps"]["A1"]
        self.assertTrue(step["local_override"])
        self.assertTrue(step["public_url"].startswith(PUBLIC_BASE))
        # 1×1 is under wan2.7-image's 240px floor, so the stored URL is the enlarged copy.
        self.assertEqual(
            step["public_url"],
            "%s/media/projects/%s/images/A1_ref.png" % (PUBLIC_BASE, project_id),
        )
        self.assertEqual(step["status"], "done")
        image = next(item for item in body["images"] if item["name"] == "A1.png")
        self.assertTrue(image["url"].startswith("/media/projects/%s/images/A1.png?" % project_id))
        self.assertIn("v=", image["url"])
        status, raw = self.request("GET", image["url"])
        self.assertEqual(status, 200)
        self.assertEqual(raw, png)

        rejected = self.rpc(
            "projects.set_asset_image",
            {
                "id": project_id,
                "asset_id": "A1",
                "data_base64": base64.b64encode(b"GIF89a-not-an-image").decode("ascii"),
            },
        )
        self.assertEqual(rejected["error"]["code"], 400)
        self.assertIn("png", rejected["error"]["message"])

        (project_dir(project_id) / "error.log").write_text(("x" * 20000) + "TRACEBACK-MARKER", encoding="utf-8")
        render_log = project_dir(project_id) / "outputs" / "render.log"
        render_log.parent.mkdir(parents=True, exist_ok=True)
        render_log.write_text("RENDER-MARKER", encoding="utf-8")
        logs = self.rpc("projects.logs", {"id": project_id})["result"]
        self.assertIn("TRACEBACK-MARKER", logs["error_log"])
        self.assertNotIn("x" * 20000, logs["error_log"])
        self.assertLessEqual(len(logs["error_log"]), 16000)
        self.assertEqual(logs["render_log"], "RENDER-MARKER")

        paused = self.rpc("projects.pause", {"id": project_id})["result"]
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["progress"]["paused_from"], "writing")
        remember(
            read_project(project_id),
            "writing",
            "仍在写",
            progress={"phase": "writing", "message": "仍在写", "percent": 9},
        )
        self.assertEqual(read_project(project_id)["status"], "paused")
        remember(
            read_project(project_id),
            "building",
            "仍在准备",
            progress={"phase": "building", "message": "仍在准备"},
        )
        self.assertEqual(read_project(project_id)["status"], "paused")
        debug_dir = project_dir(project_id) / "debug"
        debug_dir.mkdir()
        (debug_dir / "user_brief.txt").write_text("BRIEF-MARKER", encoding="utf-8")
        status, debug_body = self.request("GET", "/media/projects/%s/debug/user_brief" % project_id)
        self.assertEqual(status, 200)
        self.assertEqual(debug_body, b"BRIEF-MARKER")
        (project_dir(project_id) / "workflow.json").write_text('{"nodes":[]}', encoding="utf-8")
        status, workflow = self.request("GET", "/media/projects/%s/workflow" % project_id)
        self.assertEqual(status, 200)
        self.assertEqual(workflow, {"nodes": []})
        clip_dir = project_dir(project_id) / "outputs" / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "S01.mp4").write_bytes(b"clip-bytes")
        status, clip = self.request("GET", "/media/projects/%s/clips/S01.mp4" % project_id)
        self.assertEqual(status, 200)
        self.assertEqual(clip, b"clip-bytes")
        film = read_project(project_id)
        film["video_file"] = "joined.mp4"
        write_project(film)
        (project_dir(project_id) / "outputs" / "joined.mp4").write_bytes(b"film-bytes")
        view = self.rpc("projects.get", {"id": project_id})["result"]
        self.assertEqual(view["video_url"], "/media/projects/%s/video" % project_id)
        self.assertTrue(view["clips"][0]["url"].startswith("/media/projects/%s/clips/S01.mp4" % project_id))
        status, film_body = self.request("GET", view["video_url"])
        self.assertEqual(status, 200)
        self.assertEqual(film_body, b"film-bytes")
        status, download = self.request("GET", view["video_download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(download, b"film-bytes")
        gate_for(project_id).resume()

    def test_building_can_pause_and_drilling_cannot(self):
        project_id = "b011d0000002"
        make_project(project_id, status="building")
        body = self.rpc("projects.pause", {"id": project_id})["result"]
        self.assertEqual(body["status"], "paused")
        self.assertEqual(body["progress"]["paused_from"], "building")
        fresh = read_project(project_id)
        fresh["status"] = "drilling"
        write_project(fresh)
        denied = self.rpc("projects.pause", {"id": project_id})
        self.assertEqual(denied["error"]["code"], 409)
        self.assertIn("不能暂停", denied["error"]["message"])
        gate_for(project_id).resume()

    def test_paused_writing_saves_spec_and_resume_stays_at_prompts(self):
        project_id = "c0ffee000003"
        make_project(project_id, status="writing")
        pause_render(read_project(project_id))
        spec = {
            "title": "新标题",
            "script": "写完的剧本",
            "storyboard": "写完的分镜",
            "image_prompts": "提示",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人"}],
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "\n".join([
                    "integrated_multimodal_description: A woman in a blue coat stands on a wet platform. [Shot 1] She turns and walks toward the train.",
                    "overall_soundscape: Rain and a distant train brake.",
                    "non_diegetic_music: Low strings, slow.",
                    "duration_seconds: 5",
                ]),
                "images": ["A1"],
            }],
        }
        with patch("end.server.call_deepseek", return_value=spec), patch("end.server.execute_render") as execute:
            run_project(project_id)
            execute.assert_not_called()
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["script"], "写完的剧本")
        self.assertEqual(read_project(project_id)["status"], "paused")
        self.assertEqual(read_project(project_id)["script"], "写完的剧本")
        with patch("end.server.execute_render") as execute, patch("end.server.submit_agent_task"):
            resume_render(read_project(project_id))
        execute.assert_not_called()
        self.assertNotEqual(read_project(project_id)["status"], "rendering")
        self.assertNotEqual(read_project(project_id)["status"], "building")
        gate_for(project_id).resume()

    def test_chinese_asset_replacement_uploads_and_stores_the_public_url(self):
        project_id = "c0de1c000006"
        make_project(project_id)
        spec = {
            "title": "深夜",
            "script": "旧剧本",
            "storyboard": "旧分镜",
            "assets": [{"id": "图1", "title": "小君", "prompt": "深夜工作间"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "她抬头", "images": ["图1"]}],
        }
        (project_dir(project_id) / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False),
            encoding="utf-8",
        )
        png = png_size(240, 240)
        saved_env = {
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
            "AIVIDEO_COS_BUCKET": os.environ.get("AIVIDEO_COS_BUCKET"),
            "AIVIDEO_COS_DOMAIN": os.environ.get("AIVIDEO_COS_DOMAIN"),
        }
        os.environ["AIVIDEO_IMAGE_STORE"] = "cos"
        os.environ["AIVIDEO_COS_BUCKET"] = "demo-1250000000"
        os.environ["AIVIDEO_COS_DOMAIN"] = "https://demo-1250000000.cos.ap-guangzhou.myqcloud.com"
        calls = []
        real_open = urllib.request.urlopen

        class _Response(object):
            status = 200

            def read(self, _limit=0):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake_open(request, timeout=None):
            target = str(getattr(request, "full_url", "") or "")
            if "myqcloud.com" in target:
                calls.append(request)
                return _Response()
            if timeout is None:
                return real_open(request)
            return real_open(request, timeout=timeout)

        public = (
            "https://demo-1250000000.cos.ap-guangzhou.myqcloud.com/projects/%s/images/%s"
            % (project_id, urllib.parse.quote("图1.png"))
        )
        view_patches = (
            patch("end.server.normalize_model", side_effect=lambda model: model or ""),
            patch("end.server.model_label", return_value=""),
            patch("end.server.normalize_template", side_effect=lambda template: template or ""),
            patch("end.server.template_label", return_value=""),
        )
        for item in view_patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in view_patches])
        try:
            with patch("end.image_store.store.urllib.request.urlopen", fake_open):
                body = self.rpc(
                    "projects.set_asset_image",
                    {
                        "id": project_id,
                        "asset_id": "图1",
                        "data_base64": base64.b64encode(png).decode("ascii"),
                    },
                )
                self.assertIn("result", body, body)
                stored = project_dir(project_id) / "outputs" / "images" / "图1.png"
                self.assertEqual(stored.read_bytes(), png)
                step = load_run_state(project_dir(project_id))["steps"]["图1"]
                self.assertTrue(step["local_override"])
                self.assertTrue(step["asset_still"])
                self.assertEqual(step["status"], "done")
                self.assertEqual(step["public_url"], public)
                self.assertEqual(step["output_file"], "images/图1.png")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].get_method(), "PUT")
                self.assertEqual(calls[0].full_url, public)
                self.assertEqual(calls[0].data, png)
                self.assertFalse(calls[0].has_header("Authorization"))
                image = next(item for item in body["result"]["images"] if item["name"] == "图1.png")
                status, raw = self.request("GET", urllib.parse.quote(image["url"], safe="/?=&"))
                self.assertEqual(status, 200)
                self.assertEqual(raw, png)
                state = load_run_state(project_dir(project_id))
                state["steps"]["图1"]["status"] = "running"
                state["steps"]["图1"]["remote_task_id"] = "task-already"
                save_run_state(project_dir(project_id), state)
                rejected = self.rpc(
                    "projects.set_asset_image",
                    {
                        "id": project_id,
                        "asset_id": "图1",
                        "data_base64": base64.b64encode(png).decode("ascii"),
                    },
                )
                self.assertEqual(rejected["error"]["code"], 400)
                self.assertIn("已经提交", rejected["error"]["message"])
                self.assertEqual(len(calls), 1)
                kept = load_run_state(project_dir(project_id))["steps"]["图1"]
                self.assertEqual(kept["public_url"], public)
                self.assertEqual(kept["status"], "running")
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _quiet_view(self):
        view_patches = (
            patch("end.server.normalize_model", side_effect=lambda model: model or ""),
            patch("end.server.model_label", return_value=""),
            patch("end.server.normalize_template", side_effect=lambda template: template or ""),
            patch("end.server.template_label", return_value=""),
        )
        for item in view_patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in view_patches])

    def _use_cos(self, bucket="demo-1250000000"):
        saved = {
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
            "AIVIDEO_COS_BUCKET": os.environ.get("AIVIDEO_COS_BUCKET"),
            "AIVIDEO_COS_DOMAIN": os.environ.get("AIVIDEO_COS_DOMAIN"),
        }
        domain = "https://%s.cos.ap-guangzhou.myqcloud.com" % bucket
        os.environ["AIVIDEO_IMAGE_STORE"] = "cos"
        os.environ["AIVIDEO_COS_BUCKET"] = bucket
        os.environ["AIVIDEO_COS_DOMAIN"] = domain

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        return domain

    def _cos_open(self, calls):
        real_open = urllib.request.urlopen

        class _Response(object):
            status = 200

            def read(self, _limit=0):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake_open(request, timeout=None):
            target = str(getattr(request, "full_url", "") or "")
            if "myqcloud.com" in target:
                calls.append(request)
                return _Response()
            if timeout is None:
                return real_open(request)
            return real_open(request, timeout=timeout)

        return fake_open

    def _figure_spec(self, project_id):
        spec = {
            "title": "深夜",
            "script": "旧剧本",
            "storyboard": "旧分镜",
            "assets": [{"id": "图1", "title": "小君", "prompt": "深夜工作间"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "她抬头", "images": ["图1"]}],
        }
        (project_dir(project_id) / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False),
            encoding="utf-8",
        )
        return spec

    def test_asset_replace_of_figure_one_survives_invalidate_and_rerender(self):
        project_id = "a55e71000007"
        make_project(project_id)
        self._figure_spec(project_id)
        self._quiet_view()
        png = png_size(240, 240)
        domain = self._use_cos()
        calls = []
        public = "%s/projects/%s/images/%s" % (
            domain,
            project_id,
            urllib.parse.quote("图1.png"),
        )
        with patch("end.image_store.store.urllib.request.urlopen", self._cos_open(calls)), patch(
            "end.server.read_upload"
        ) as read:
            body = self.rpc(
                "projects.set_asset_image",
                {
                    "id": project_id,
                    "asset_id": "图1",
                    "mode": "资产图",
                    "data_base64": base64.b64encode(png).decode("ascii"),
                },
            )
            self.assertIn("result", body, body)
            read.assert_not_called()
            folder = project_dir(project_id)
            stored = folder / "outputs" / "images" / "图1.png"
            self.assertEqual(stored.read_bytes(), png)
            step = load_run_state(folder)["steps"]["图1"]
            self.assertTrue(step["asset_still"])
            self.assertTrue(step["local_override"])
            self.assertEqual(step["public_url"], public)
            self.assertEqual(calls[0].full_url, public)
            self.assertEqual(calls[0].data, png)
            invalidate_steps(folder, project_id, [("图1", "image"), ("S01", "video")])
            kept = load_run_state(folder)
            self.assertEqual(kept["steps"]["图1"]["public_url"], public)
            self.assertTrue(kept["steps"]["图1"]["asset_still"])
            self.assertTrue(kept["steps"]["图1"]["local_override"])
            self.assertEqual(kept["steps"]["图1"]["status"], "done")
            self.assertEqual(stored.read_bytes(), png)
            self.assertEqual(kept["steps"]["S01"]["status"], "pending")
            self.assertEqual(kept["steps"]["S01"]["public_url"], "")
            icreat = FakeICreat()
            spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
            run_local_pipeline(
                spec,
                folder,
                project_id=project_id,
                icreat=icreat,
                sleeper=_noop_sleep,
                images_only=True,
                brief="雨夜",
            )
        self.assertEqual(icreat.submits, [])
        fresh = load_run_state(folder)["steps"]["图1"]
        self.assertEqual(fresh["public_url"], public)
        self.assertTrue(fresh["asset_still"])
        self.assertEqual(stored.read_bytes(), png)
        self.assertEqual(len(calls), 1)
        gate_for(project_id).resume()

    def test_reference_replace_understands_then_generates_a_new_still(self):
        project_id = "0ef000000008"
        make_project(project_id)
        self._figure_spec(project_id)
        self._quiet_view()
        png = png_size(240, 240)
        domain = self._use_cos()
        calls = []
        reference = "%s/projects/%s/images/%s" % (
            domain,
            project_id,
            urllib.parse.quote("图1_upload.png"),
        )
        with patch("end.image_store.store.urllib.request.urlopen", self._cos_open(calls)), patch(
            "end.server.read_upload", return_value="主体是银色的车"
        ) as read:
            rejected = self.rpc(
                "projects.set_asset_image",
                {
                    "id": project_id,
                    "asset_id": "图1",
                    "mode": "poster",
                    "data_base64": base64.b64encode(png).decode("ascii"),
                },
            )
            self.assertEqual(rejected["error"]["code"], 400)
            self.assertIn("参考图", rejected["error"]["message"])
            body = self.rpc(
                "projects.set_asset_image",
                {
                    "id": project_id,
                    "asset_id": "图1",
                    "mode": "参考图",
                    "data_base64": base64.b64encode(png).decode("ascii"),
                },
            )
            self.assertIn("result", body, body)
            read.assert_called_once()
            folder = project_dir(project_id)
            upload = folder / "outputs" / "images" / "图1_upload.png"
            asset_file = folder / "outputs" / "images" / "图1.png"
            self.assertEqual(upload.read_bytes(), png)
            self.assertFalse(asset_file.is_file())
            state = load_run_state(folder)
            self.assertEqual(state["steps"]["图1_upload"]["public_url"], reference)
            self.assertEqual(state["steps"]["图1_upload"]["reading"], "主体是银色的车")
            self.assertTrue(state["steps"]["图1_upload"]["local_override"])
            self.assertFalse(state["steps"]["图1_upload"].get("asset_still"))
            asset = state["steps"]["图1"]
            self.assertEqual(asset["status"], "pending")
            self.assertEqual(asset["public_url"], "")
            self.assertFalse(asset["asset_still"])
            self.assertFalse(asset["local_override"])
            saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["assets"][0]["references"], ["图1_upload"])
            self.assertEqual(calls[0].full_url, reference)
            self.assertTrue(urllib.parse.unquote(calls[0].full_url).endswith("图1_upload.png"))
            invalidate_steps(folder, project_id, [("图1", "image"), ("S01", "video")])
            self.assertEqual(upload.read_bytes(), png)
            self.assertFalse(load_run_state(folder)["steps"]["图1"].get("asset_still"))
            icreat = FakeICreat()
            spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
            run_local_pipeline(
                spec,
                folder,
                project_id=project_id,
                icreat=icreat,
                sleeper=_noop_sleep,
                images_only=True,
                brief="雨夜",
            )
            read.assert_called_once()
        self.assertEqual(len(icreat.submits), 1)
        self.assertEqual(icreat.submits[0]["image"], [reference])
        generated = load_run_state(folder)["steps"]["图1"]
        self.assertEqual(generated["public_url"], "https://cdn.example/img-1.png")
        self.assertNotEqual(generated["public_url"], reference)
        self.assertFalse(generated.get("asset_still"))
        self.assertEqual(upload.read_bytes(), png)
        self.assertNotEqual((folder / "outputs" / "images" / "图1.png").read_bytes(), png)
        state = load_run_state(folder)
        state["steps"]["图1"]["status"] = "running"
        state["steps"]["图1"]["remote_task_id"] = "task-already"
        save_run_state(folder, state)
        with patch("end.server.read_upload", return_value="不该再读") as again:
            blocked = self.rpc(
                "projects.set_asset_image",
                {
                    "id": project_id,
                    "asset_id": "图1",
                    "mode": "reference",
                    "data_base64": base64.b64encode(png).decode("ascii"),
                },
            )
        self.assertEqual(blocked["error"]["code"], 400)
        self.assertIn("已经提交", blocked["error"]["message"])
        again.assert_not_called()
        self.assertEqual(load_run_state(folder)["steps"]["图1"]["public_url"], "https://cdn.example/img-1.png")
        gate_for(project_id).resume()

    def test_jpeg_upload_becomes_png(self):
        ffmpeg = _ffmpeg_bin()
        if not ffmpeg:
            self.skipTest("本机没有 ffmpeg")
        project_id = "d00d11e00004"
        make_project(project_id)
        png_path = self.root / "dot.png"
        png_path.write_bytes(tiny_png())
        jpeg_path = self.root / "dot.jpg"
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(png_path), str(jpeg_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0 or not jpeg_path.is_file():
            self.skipTest("ffmpeg 没能写出 jpeg")
        payload = self.rpc(
            "projects.set_asset_image",
            {
                "id": project_id,
                "asset_id": "A2",
                "data_base64": base64.b64encode(jpeg_path.read_bytes()).decode("ascii"),
            },
        )
        self.assertIn("result", payload, payload)
        stored = (project_dir(project_id) / "outputs" / "images" / "A2.png").read_bytes()
        self.assertTrue(stored.startswith(b"\x89PNG"))

    def test_save_content_rejects_unknown_asset(self):
        project_id = "e11a5e000005"
        make_project(project_id)
        with self.assertRaises(ValueError):
            save_project_content(project_id, {"assets": [{"id": "NOPE", "prompt": "x"}]})
        with self.assertRaises(ValueError):
            save_asset_image(project_id, "../A1", tiny_png())

    def test_logs_require_a_project(self):
        with self.assertRaises(KeyError):
            project_logs("abc123abc123")

    def test_opening_upload_keeps_the_still_and_narration(self):
        png = tiny_png()

        def keep(project):
            write_project(project)

        with patch("end.server.start_drill", side_effect=keep), patch(
            "end.server.read_upload", return_value="尚未读图"
        ) as read:
            payload = self.rpc(
                "projects.create",
                {
                    "brief": "雨夜车站",
                    "debug_llm": False,
                    "materials": [
                        {
                            "filename": "车站.png",
                            "narration": "她在等最后一班车",
                            "data_base64": base64.b64encode(png).decode("ascii"),
                        }
                    ],
                },
            )["result"]
        read.assert_called_once()
        self.assertEqual(payload["brief"], "雨夜车站")
        self.assertEqual(payload["materials"][0]["id"], "U1")
        self.assertEqual(payload["materials"][0]["narration"], "她在等最后一班车")
        self.assertTrue(payload["materials"][0]["url"].startswith("/media/projects/"))
        self.assertIn("她在等最后一班车", payload["messages"][0]["content"])
        self.assertIn("素材读图", payload["messages"][0]["content"])
        self.assertIn("尚未读图", payload["messages"][0]["content"])
        folder = project_dir(payload["id"])
        self.assertEqual((folder / "outputs" / "images" / "U1.png").read_bytes(), png)
        step = load_run_state(folder)["steps"]["U1"]
        self.assertTrue(step["local_override"])
        self.assertTrue(step["public_url"].startswith(PUBLIC_BASE))
        # 1×1 is under wan2.7-image's 240px floor, so the stored URL is the enlarged copy.
        self.assertEqual(
            step["public_url"],
            "%s/media/projects/%s/images/U1_ref.png" % (PUBLIC_BASE, payload["id"]),
        )

    def test_opening_upload_stores_the_reading_for_the_writer(self):
        png = tiny_png()

        def keep(project):
            write_project(project)

        with patch("end.server.read_upload", return_value="主体是海边的银色SUV") as read, patch(
            "end.server.start_drill", side_effect=keep
        ):
            payload = self.rpc(
                "projects.create",
                {
                    "brief": "基于这些素材做广告",
                    "materials": [
                        {
                            "filename": "海边.png",
                            "narration": "上路吧",
                            "data_base64": base64.b64encode(png).decode("ascii"),
                        }
                    ],
                },
            )["result"]
        read.assert_called_once()
        folder = project_dir(payload["id"])
        saved = json.loads((folder / "intake.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["reading"], "主体是海边的银色SUV")
        self.assertEqual(saved[0]["id"], "U1")
        loaded = load_intake(folder)
        self.assertEqual(loaded[0]["reading"], "主体是海边的银色SUV")
        self.assertIn("素材读图", payload["messages"][0]["content"])
        self.assertIn("银色SUV", payload["messages"][0]["content"])
        brief = enrich_brief(payload["brief"], loaded)
        self.assertIn("银色SUV", brief)
        self.assertIn("身份跟图", brief)
        self.assertIn("海边的汽车不能被说成两轮车", brief)
        self.assertIn('["U1"]', brief)

    def test_vision_failure_stops_before_drill(self):
        png = tiny_png()
        with patch("end.server.read_upload", side_effect=QwenVisionError("读图失败")), patch(
            "end.server.start_drill"
        ) as drill:
            payload = self.rpc(
                "projects.create",
                {
                    "brief": "雨夜车站",
                    "materials": [
                        {
                            "filename": "车站.png",
                            "narration": "她在等",
                            "data_base64": base64.b64encode(png).decode("ascii"),
                        }
                    ],
                },
            )
        self.assertEqual(payload["error"]["code"], 400)
        self.assertIn("读图失败", payload["error"]["message"])
        drill.assert_not_called()

    def test_merge_puts_narration_on_the_shot_that_uses_the_generated_asset(self):
        spec = merge_intake(
            {"title": "雨夜", "assets": [], "shots": []},
            [{"id": "U1", "title": "车站", "narration": "她在等最后一班车"}],
        )
        self.assertEqual(len(spec["assets"]), 1)
        asset = spec["assets"][0]
        self.assertNotEqual(asset["id"], "U1")
        self.assertEqual(asset["references"], ["U1"])
        self.assertEqual(asset["title"], "车站")
        self.assertEqual(spec["shots"][0]["images"], [asset["id"]])
        self.assertNotIn("U1", spec["shots"][0]["images"])
        self.assertEqual(spec["shots"][0]["narration"], "她在等最后一班车")
        filled = merge_intake(
            {
                "assets": [{"id": "U1", "title": "车站", "prompt": "夜站"}],
                "shots": [{"id": "S01", "prompt": "她站着", "images": ["U1"], "dialogue": ""}],
            },
            [{"id": "U1", "title": "车站", "narration": "她在等最后一班车"}],
        )
        self.assertEqual(filled["shots"][0]["id"], "S01")
        self.assertEqual(filled["shots"][0]["narration"], "她在等最后一班车")
        self.assertEqual(len(filled["shots"]), 1)
        self.assertNotIn("U1", [item["id"] for item in filled["assets"]])
        self.assertNotIn("U1", filled["shots"][0]["images"])
        used = filled["shots"][0]["images"][0]
        match = next(item for item in filled["assets"] if item["id"] == used)
        self.assertEqual(match["references"], ["U1"])
        self.assertEqual(match["prompt"], "夜站")

    def test_create_remembers_soundtrack_after_progress_updates(self):
        def keep(project):
            write_project(project)

        for mode, stored in (("off", "off"), ("music", "off"), ("narration", "narration"), ("music_narration", "narration")):
            with patch("end.server.start_drill", side_effect=keep):
                payload = self.rpc(
                    "projects.create",
                    {"brief": "雨夜的车", "debug_llm": False, "soundtrack": mode},
                )["result"]
            self.assertEqual(payload["progress"]["soundtrack"], stored)
            project = read_project(payload["id"])
            remember(
                project,
                "writing",
                "正在写",
                progress={"phase": "writing", "message": "正在写", "percent": 8, "debug_llm": False},
            )
            saved = read_project(payload["id"])["progress"]
            self.assertEqual(saved["soundtrack"], stored)
            self.assertEqual(saved["phase"], "writing")
        with patch("end.server.start_drill", side_effect=keep):
            payload = self.rpc(
                "projects.create",
                {"brief": "只有音乐", "soundtrack": "music"},
            )["result"]
        self.assertEqual(payload["progress"]["soundtrack"], "off")
        noted = read_project(payload["id"])
        noted["progress"]["soundtrack"] = "music_narration"
        write_project(noted)
        remember(
            read_project(payload["id"]),
            "rendering",
            "没有旁白，保留镜头原声",
            progress={
                "phase": "audio",
                "message": "没有旁白，保留镜头原声",
                "soundtrack_note": "没有旁白",
            },
        )
        remember(
            read_project(payload["id"]),
            "rendering",
            "正在烧字幕",
            progress={"phase": "subtitles", "message": "正在烧字幕", "percent": 90},
        )
        progress = read_project(payload["id"])["progress"]
        self.assertEqual(progress["soundtrack"], "narration")
        self.assertEqual(progress["soundtrack_note"], "没有旁白")
        self.assertEqual(progress["phase"], "subtitles")


if __name__ == "__main__":
    unittest.main()
