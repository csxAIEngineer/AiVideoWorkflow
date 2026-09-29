#!/usr/bin/env python3
"""DeepSeek-style chat site. Each message is one video project."""

import json
import mimetypes
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web.pipeline import call_deepseek, load_skill_pack, run_comfy_cloud
from web.workflow_from_spec import build_workflow

STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data" / "web_projects"
HOST = "127.0.0.1"
PORT = 8787


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def valid_id(project_id):
    return bool(project_id) and len(project_id) == 12 and all(ch in "0123456789abcdef" for ch in project_id)


def project_dir(project_id):
    return DATA / project_id


def read_project(project_id):
    path = project_dir(project_id) / "project.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_project(project):
    folder = project_dir(project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


def list_projects():
    if not DATA.is_dir():
        return []
    items = []
    for folder in DATA.iterdir():
        path = folder / "project.json"
        if path.is_file():
            items.append(json.loads(path.read_text(encoding="utf-8")))
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def public_view(project):
    return {
        "id": project["id"],
        "brief": project.get("brief", ""),
        "title": project.get("title") or project.get("brief", "")[:24],
        "status": project.get("status"),
        "stage": project.get("stage", ""),
        "script": project.get("script", ""),
        "storyboard": project.get("storyboard", ""),
        "image_prompts": project.get("image_prompts", ""),
        "error": project.get("error"),
        "cloud_log": project.get("cloud_log", ""),
        "created_at": project.get("created_at"),
        "has_workflow": (project_dir(project["id"]) / "workflow.json").is_file(),
    }


def run_project(project_id):
    project = read_project(project_id)
    folder = project_dir(project_id)
    try:
        project["status"] = "writing"
        project["stage"] = "DeepSeek V4 Pro 正在写剧本、分镜和生图提示词"
        write_project(project)
        spec = call_deepseek(project["brief"], load_skill_pack())
        project["title"] = str(spec.get("title") or project["title"])
        project["script"] = str(spec.get("script") or "")
        project["storyboard"] = str(spec.get("storyboard") or "")
        project["image_prompts"] = str(spec.get("image_prompts") or "")
        project["status"] = "building"
        project["stage"] = "正在生成 ComfyUI 工作流"
        write_project(project)
        workflow = build_workflow(spec)
        (folder / "workflow.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        project["status"] = "cloud"
        project["stage"] = "正在用 comfy-cli 提交到 Comfy Cloud"
        write_project(project)
        log = run_comfy_cloud(folder / "workflow.json", folder)
        project["status"] = "done"
        project["stage"] = "成片已提交完成"
        project["cloud_log"] = log
        project["error"] = None
        write_project(project)
    except Exception as error:
        project = read_project(project_id) or project
        project["status"] = "error"
        project["error"] = str(error)
        project["stage"] = project.get("stage") or "失败"
        trace = traceback.format_exc()
        (folder / "error.log").write_text(trace, encoding="utf-8")
        write_project(project)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 200_000:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/projects":
            self._send(200, [public_view(item) for item in list_projects()])
            return
        if path.startswith("/api/projects/") and path.endswith("/workflow"):
            project_id = path.split("/")[3]
            if not valid_id(project_id):
                self._send(404, {"error": "还没有工作流"})
                return
            workflow = project_dir(project_id) / "workflow.json"
            if not workflow.is_file():
                self._send(404, {"error": "还没有工作流"})
                return
            self._send(200, workflow.read_bytes(), "application/json; charset=utf-8")
            return
        if path.startswith("/api/projects/"):
            project_id = path.split("/")[3]
            if not valid_id(project_id):
                self._send(404, {"error": "没有这个项目"})
                return
            project = read_project(project_id)
            if project is None:
                self._send(404, {"error": "没有这个项目"})
                return
            self._send(200, public_view(project))
            return
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        file_path = (STATIC / relative).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
            self._send(404, {"error": "没有这个页面"})
            return
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._send(200, file_path.read_bytes(), mime)

    def do_POST(self):
        if urlparse(self.path).path != "/api/projects":
            self._send(404, {"error": "没有这个接口"})
            return
        body = self._read_json()
        brief = str(body.get("brief") or "").strip()
        if not brief:
            self._send(400, {"error": "请先写下视频的背景或简要"})
            return
        project = {
            "id": uuid.uuid4().hex[:12],
            "brief": brief,
            "title": brief[:24],
            "status": "queued",
            "stage": "已建立项目",
            "script": "",
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "cloud_log": "",
            "created_at": now(),
        }
        write_project(project)
        threading.Thread(target=run_project, args=(project["id"],), daemon=True).start()
        self._send(201, public_view(project))


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AiVideo http://%s:%s" % (HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
