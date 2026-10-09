"""Content save, image replace, logs, and pause during writing."""

import json
import struct
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from unittest.mock import patch

import web.db as db
from web.render_loop import _ffmpeg_bin, gate_for, load_run_state
from web.intake import merge_intake
from web.server import (
    Handler,
    pause_render,
    project_logs,
    remember,
    resume_render,
    run_project,
    save_asset_image,
    save_project_content,
)
from web.db import project_dir, read_project, write_project
from http.server import ThreadingHTTPServer


def tiny_png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
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


class ProjectApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
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

    def test_content_image_logs_and_pause_while_writing(self):
        project_id = "a11c0de00001"
        make_project(project_id, status="writing")
        status, body = self.request(
            "POST",
            "/api/projects/%s/content" % project_id,
            json.dumps(
                {"script": "新剧本", "storyboard": "新分镜", "assets": [{"id": "A1", "prompt": "新提示"}]},
                ensure_ascii=False,
            ).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["script"], "新剧本")
        self.assertEqual(body["storyboard"], "新分镜")
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["script"], "新剧本")
        self.assertEqual(saved["storyboard"], "新分镜")
        self.assertEqual(saved["assets"][0]["prompt"], "新提示")
        self.assertEqual(saved["assets"][1]["prompt"], "旧雨")
        self.assertEqual(read_project(project_id)["script"], "新剧本")

        png = tiny_png()
        status, body = self.request(
            "POST",
            "/api/projects/%s/assets/A1/image" % project_id,
            png,
            {"Content-Type": "image/png"},
        )
        self.assertEqual(status, 200)
        stored = project_dir(project_id) / "outputs" / "images" / "A1.png"
        self.assertEqual(stored.read_bytes(), png)
        step = load_run_state(project_dir(project_id))["steps"]["A1"]
        self.assertTrue(step["local_override"])
        self.assertEqual(step["public_url"], "")
        self.assertEqual(step["status"], "done")
        self.assertTrue(any(image["name"] == "A1.png" and "v=" in image["url"] for image in body["images"]))

        status, rejected = self.request(
            "POST",
            "/api/projects/%s/assets/A1/image" % project_id,
            b"GIF89a-not-an-image",
            {"Content-Type": "image/gif"},
        )
        self.assertEqual(status, 400)
        self.assertIn("png", rejected["error"])

        (project_dir(project_id) / "error.log").write_text(("x" * 20000) + "TRACEBACK-MARKER", encoding="utf-8")
        render_log = project_dir(project_id) / "outputs" / "render.log"
        render_log.parent.mkdir(parents=True, exist_ok=True)
        render_log.write_text("RENDER-MARKER", encoding="utf-8")
        status, logs = self.request("GET", "/api/projects/%s/logs" % project_id)
        self.assertEqual(status, 200)
        self.assertIn("TRACEBACK-MARKER", logs["error_log"])
        self.assertNotIn("x" * 20000, logs["error_log"])
        self.assertLessEqual(len(logs["error_log"]), 16000)
        self.assertEqual(logs["render_log"], "RENDER-MARKER")

        status, paused = self.request("POST", "/api/projects/%s/pause" % project_id)
        self.assertEqual(status, 200)
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
        gate_for(project_id).resume()

    def test_building_can_pause_and_drilling_cannot(self):
        project_id = "b011d0000002"
        make_project(project_id, status="building")
        status, body = self.request("POST", "/api/projects/%s/pause" % project_id)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "paused")
        self.assertEqual(body["progress"]["paused_from"], "building")
        fresh = read_project(project_id)
        fresh["status"] = "drilling"
        write_project(fresh)
        status, denied = self.request("POST", "/api/projects/%s/pause" % project_id)
        self.assertEqual(status, 409)
        self.assertIn("不能暂停", denied["error"])
        gate_for(project_id).resume()

    def test_paused_writing_saves_spec_and_resume_starts_render(self):
        project_id = "c0ffee000003"
        make_project(project_id, status="writing")
        pause_render(read_project(project_id))
        spec = {
            "title": "新标题",
            "script": "写完的剧本",
            "storyboard": "写完的分镜",
            "image_prompts": "提示",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "离开", "images": ["A1"]}],
        }
        with patch("web.server.call_deepseek", return_value=spec), patch("web.server.execute_render") as execute:
            run_project(project_id)
            execute.assert_not_called()
        saved = json.loads((project_dir(project_id) / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["script"], "写完的剧本")
        self.assertEqual(read_project(project_id)["status"], "paused")
        self.assertEqual(read_project(project_id)["script"], "写完的剧本")
        started = threading.Event()

        def fake_execute(pid, project=None):
            self.assertEqual(pid, project_id)
            started.set()

        with patch("web.server.execute_render", fake_execute):
            resume_render(read_project(project_id))
            self.assertTrue(started.wait(3))
        self.assertEqual(read_project(project_id)["status"], "rendering")
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
        status, _body = self.request(
            "POST",
            "/api/projects/%s/assets/A2/image" % project_id,
            jpeg_path.read_bytes(),
            {"Content-Type": "image/jpeg"},
        )
        self.assertEqual(status, 200, _body)
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
        boundary = "----aivideo"
        body = _multipart(
            boundary,
            [
                ("brief", "雨夜车站"),
                ("debug_llm", "0"),
                ("file", "车站.png", png),
                ("narration", "她在等最后一班车"),
            ],
        )

        def keep(project):
            write_project(project)

        with patch("web.server.start_drill", side_effect=keep):
            status, payload = self.request(
                "POST",
                "/api/projects",
                body,
                {"Content-Type": "multipart/form-data; boundary=%s" % boundary},
            )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["brief"], "雨夜车站")
        self.assertEqual(payload["materials"][0]["id"], "U1")
        self.assertEqual(payload["materials"][0]["narration"], "她在等最后一班车")
        self.assertTrue(payload["materials"][0]["url"])
        self.assertIn("她在等最后一班车", payload["messages"][0]["content"])
        folder = project_dir(payload["id"])
        self.assertEqual((folder / "outputs" / "images" / "U1.png").read_bytes(), png)
        self.assertTrue(load_run_state(folder)["steps"]["U1"]["local_override"])

    def test_merge_puts_narration_on_a_shot_for_the_uploaded_still(self):
        spec = merge_intake(
            {"title": "雨夜", "assets": [], "shots": []},
            [{"id": "U1", "title": "车站", "narration": "她在等最后一班车"}],
        )
        self.assertEqual(spec["assets"][0]["id"], "U1")
        self.assertEqual(spec["shots"][0]["images"], ["U1"])
        self.assertEqual(spec["shots"][0]["narration"], "她在等最后一班车")
        filled = merge_intake(
            {
                "assets": [{"id": "U1", "title": "车站", "prompt": "夜站"}],
                "shots": [{"id": "S01", "prompt": "她站着", "images": ["U1"], "dialogue": ""}],
            },
            [{"id": "U1", "title": "车站", "narration": "她在等最后一班车"}],
        )
        self.assertEqual(filled["shots"][0]["narration"], "她在等最后一班车")
        self.assertEqual(len(filled["shots"]), 1)


def _multipart(boundary, parts):
    chunks = []
    for part in parts:
        if len(part) == 2:
            name, value = part
            chunks.append(
                (
                    '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                    % (boundary, name, value)
                ).encode("utf-8")
            )
        else:
            name, filename, data = part
            head = (
                '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                "Content-Type: image/png\r\n\r\n" % (boundary, name, filename)
            ).encode("utf-8")
            chunks.append(head + data + b"\r\n")
    chunks.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(chunks)


if __name__ == "__main__":
    unittest.main()
