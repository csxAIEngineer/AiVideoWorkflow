#!/usr/bin/env python3
"""DeepSeek-style chat site. Each message is one video project."""

import json
import mimetypes
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web.db import DATA, connect, delete_project, list_projects, project_dir, read_project, write_project
from web.render_loop import (
    gate_for,
    is_running,
    load_run_state,
    mark_user_pause,
    project_lock,
    reset_current_step,
    resume_paused,
    run_local_pipeline,
    should_resume_project,
    thread_alive,
)
from web.pipeline import (
    DEFAULT_MODEL,
    call_deepseek,
    call_drill,
    load_skill_pack,
    model_choices,
    model_label,
    normalize_model,
)
from web.workflow_from_spec import collect_steps, task_plan

STATIC = Path(__file__).resolve().parent / "static"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
DEBUG_FILES = {
    "skill_pack": "skill_pack.txt",
    "system_prompt": "system_prompt.txt",
    "json_shape": "json_shape.txt",
    "raw_response": "raw_response.txt",
    "raw_response_repaired": "raw_response_repaired.txt",
    "user_brief": "user_brief.txt",
    "drill_skill": "drill_skill.txt",
    "drill_response": "drill_response.txt",
    "drill_response_repaired": "drill_response_repaired.txt",
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def valid_id(project_id):
    return bool(project_id) and len(project_id) == 12 and all(ch in "0123456789abcdef" for ch in project_id)


def env_debug_llm():
    value = (os.environ.get("AIVIDEO_DEBUG_LLM") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def want_debug_llm(flag=False):
    return env_debug_llm() or bool(flag)


def load_spec(project_id):
    path = project_dir(project_id) / "spec.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def can_reply(project):
    if not project or project.get("status") != "drilling":
        return False
    phase = (project.get("progress") or {}).get("phase")
    return phase == "drill_wait"


def project_debug_llm(project):
    if not project:
        return False
    if project.get("debug_llm"):
        return True
    progress = project.get("progress") or {}
    return bool(progress.get("debug_llm")) or progress.get("phase") in ("llm_debug", "image_debug")


def remember(project, status, stage, progress=None):
    with project_lock(project["id"]):
        if status == "rendering" and not is_running(project["id"]):
            fresh = read_project(project["id"])
            if fresh:
                project["status"] = fresh.get("status")
                project["stage"] = fresh.get("stage")
                project["progress"] = fresh.get("progress") or project.get("progress")
                project["error"] = fresh.get("error")
                if fresh.get("tasks"):
                    project["tasks"] = fresh["tasks"]
            return
        project["updated_at"] = now()
        events = project.setdefault("events", [])
        same = events and events[-1].get("message") == stage and project.get("status") == status
        project["status"] = status
        project["stage"] = stage
        if progress:
            stored = dict(progress)
            stored.pop("project_status", None)
            prev = project.get("progress") or {}
            debug_flag = stored["debug_llm"] if "debug_llm" in stored else prev.get("debug_llm")
            project["progress"] = {
                "percent": int(stored.get("percent") or prev.get("percent") or 0),
                "phase": stored.get("phase") or prev.get("phase") or "",
                "current": stored["current"] if "current" in stored else prev.get("current"),
                "total": stored["total"] if "total" in stored else prev.get("total"),
                "message": stored.get("message") or stage,
                "debug_llm": bool(debug_flag),
            }
            task_id = stored.get("task_id")
            task_state = stored.get("task_state")
            if task_id and task_state:
                for task in project.get("tasks") or []:
                    if task.get("id") == task_id:
                        task["state"] = task_state
                        if task_state == "done":
                            task["detail"] = "已完成"
                        elif stored.get("message"):
                            task["detail"] = stored["message"]
                        break
        if not same:
            events.append({"at": project["updated_at"], "message": stage})
        write_project(project)


def load_tasks(project):
    """Fill the task list from the saved spec when older rows have none."""
    if project.get("status") == "done" and project.get("stage") == "工作流已生成，可以下载":
        project["stage"] = "剧本已写好。出片改为 iCreat 生图 / CR MiniMax H3 / 本机拼接"
        write_project(project)
    if project.get("tasks"):
        return
    spec = load_spec(project["id"])
    if not spec:
        return
    tasks = task_plan(spec)
    if not tasks:
        return
    project["tasks"] = tasks
    write_project(project)


def public_view(project):
    load_tasks(project)
    folder = project_dir(project["id"])
    spec = load_spec(project["id"])
    assets, shots = ([], [])
    if spec:
        assets, shots = collect_steps(spec)
    debug_dir = folder / "debug"
    debug_files = [
        key for key, name in DEBUG_FILES.items() if (debug_dir / name).is_file()
    ]
    debug = project_debug_llm(project)
    return {
        "id": project["id"],
        "brief": project.get("brief", ""),
        "messages": project.get("messages") or [],
        "can_reply": can_reply(project),
        "title": project.get("title") or project.get("brief", "")[:24],
        "status": project.get("status"),
        "stage": project.get("stage", ""),
        "script": project.get("script", ""),
        "storyboard": project.get("storyboard", ""),
        "image_prompts": project.get("image_prompts", ""),
        "assets": assets,
        "shots": shots,
        "error": project.get("error"),
        "events": project.get("events") or [],
        "tasks": project.get("tasks") or [],
        "created_at": project.get("created_at"),
        "started_at": project.get("started_at") or project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "finished_at": project.get("finished_at"),
        "has_workflow": (folder / "workflow.json").is_file(),
        "has_spec": (folder / "spec.json").is_file(),
        "has_video": bool(project.get("video_file"))
        and (folder / "outputs" / str(project.get("video_file"))).is_file(),
        "video_url": (
            "/api/projects/%s/video" % project["id"]
            if project.get("video_file")
            and (folder / "outputs" / str(project.get("video_file"))).is_file()
            else ""
        ),
        "video_download_url": (
            "/api/projects/%s/video?download=1" % project["id"]
            if project.get("video_file")
            and (folder / "outputs" / str(project.get("video_file"))).is_file()
            else ""
        ),
        "prompt_id": project.get("prompt_id") or "",
        "model": normalize_model(project.get("model")),
        "model_label": model_label(project.get("model")),
        "debug_llm": debug,
        "debug_files": debug_files,
        "images": list_output_images(project["id"]),
        "clips": list_output_clips(project["id"]),
        "progress": project.get("progress") or {"percent": 0, "phase": "", "message": project.get("stage") or ""},
    }


def run_drill(project_id):
    project = read_project(project_id)
    folder = project_dir(project_id)
    debug = project_debug_llm(project)
    try:
        result = call_drill(
            project.get("messages") or [],
            debug_dir=folder / "debug" if debug else None,
            model=project.get("model"),
        )
        project = read_project(project_id) or project
        messages = list(project.get("messages") or [])
        turn = {"role": "assistant", "content": result["reply"]}
        if result.get("options"):
            turn["options"] = result["options"]
        messages.append(turn)
        project["messages"] = messages
        if result["phase"] == "ready":
            project["brief"] = result["brief"]
            project["stage"] = "已经问清，开始写剧本"
            write_project(project)
            run_project(project_id, debug_llm=debug)
            return
        remember(
            project,
            "drilling",
            "选一个方向",
            progress={
                "percent": 0,
                "phase": "drill_wait",
                "message": "选一个方向",
                "debug_llm": debug,
            },
        )
    except Exception as error:
        project = read_project(project_id) or project
        project["error"] = str(error)
        project["finished_at"] = now()
        trace = traceback.format_exc()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "error.log").write_text(trace, encoding="utf-8")
        remember(project, "error", "没问成")


def start_drill(project):
    progress = project.get("progress") or {}
    progress["phase"] = "drill_think"
    progress["message"] = "正在想方向"
    project["progress"] = progress
    project["status"] = "drilling"
    project["stage"] = "正在想方向"
    write_project(project)
    threading.Thread(target=run_drill, args=(project["id"],), daemon=True).start()


def run_project(project_id, debug_llm=False):
    project = read_project(project_id)
    folder = project_dir(project_id)
    debug = want_debug_llm(debug_llm or project_debug_llm(project))
    try:
        remember(
            project,
            "writing",
            model_label(project.get("model"))
            + " 正在写剧本、分镜和生图提示词"
            + ("（Debug：仅到生图）" if debug else ""),
            progress={
                "percent": 8,
                "phase": "writing",
                "message": model_label(project.get("model")) + " 正在写剧本、分镜和生图提示词",
                "debug_llm": debug,
            },
        )
        skill_pack = load_skill_pack()
        debug_dir = folder / "debug" if debug else None
        spec = call_deepseek(
            project["brief"],
            skill_pack,
            debug_dir=debug_dir,
            model=project.get("model"),
        )
        project["title"] = str(spec.get("title") or project["title"])
        project["script"] = str(spec.get("script") or "")
        project["storyboard"] = str(spec.get("storyboard") or "")
        project["image_prompts"] = str(spec.get("image_prompts") or "")
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

        building = "Debug：iCreat 生图" if debug else "iCreat 生图 / CR MiniMax H3 / 本机拼接"
        remember(
            project,
            "building",
            building,
            progress={
                "percent": 12,
                "phase": "building",
                "message": building,
                "debug_llm": debug,
            },
        )
        project["tasks"] = task_plan(spec)
        write_project(project)
        execute_render(project_id, project)
    except Exception as error:
        fail_project(project_id, error)


def fail_project(project_id, error):
    project = read_project(project_id)
    if project is None:
        return
    if project.get("status") == "paused":
        return
    project["error"] = str(error)
    project["finished_at"] = now()
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    remember(project, "error", "失败")


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}


def list_output_images(project_id):
    folder = project_dir(project_id) / "outputs" / "images"
    if not folder.is_dir():
        return []
    images = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        images.append(
            {
                "name": path.name,
                "url": "/api/projects/%s/images/%s" % (project_id, path.name),
            }
        )
    return images


def list_output_clips(project_id):
    folder = project_dir(project_id) / "outputs" / "clips"
    if not folder.is_dir():
        return []
    clips = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        url = "/api/projects/%s/clips/%s" % (project_id, path.name)
        clips.append(
            {
                "id": path.stem,
                "name": path.name,
                "url": url,
                "download_url": url + "?download=1",
            }
        )
    return clips


def finish_image_debug(project, result):
    project = read_project(project["id"]) or project
    project["prompt_id"] = result.get("prompt_id") or ""
    project["video_file"] = ""
    project["finished_at"] = now()
    project["error"] = None
    for task in project.get("tasks") or []:
        if task.get("kind") in ("video", "join"):
            task["state"] = "muted"
            task["detail"] = "Debug：暂不执行"
    remember(
        project,
        "done",
        "Debug：已生成图片，视频和拼接暂不执行",
        progress={
            "percent": 100,
            "phase": "image_debug",
            "message": "Debug：已停在生图。视频和拼接暂不执行",
            "debug_llm": True,
        },
    )


def execute_render(project_id, project=None):
    from web.render_loop import register_thread

    register_thread(project_id)
    project = project or read_project(project_id)
    folder = project_dir(project_id)
    spec = load_spec(project_id)
    if not isinstance(spec, dict):
        raise RuntimeError("没有 spec，不能出片")
    images_only = project_debug_llm(project)
    if not project.get("tasks"):
        project["tasks"] = task_plan(spec)
        write_project(project)

    def on_progress(payload):
        status = payload.get("project_status") or "rendering"
        message = payload.get("message") or "出片中"
        if "debug_llm" not in payload and images_only:
            payload = dict(payload)
            payload["debug_llm"] = True
        remember(project, status, message, progress=payload)

    result = run_local_pipeline(
        spec,
        folder,
        on_progress=on_progress,
        project_id=project_id,
        images_only=images_only,
    )
    if result.get("stopped_after") == "images":
        finish_image_debug(project, result)
        return
    project = read_project(project_id) or project
    project["prompt_id"] = result.get("prompt_id") or ""
    project["video_file"] = result["video_file"]
    project["finished_at"] = now()
    project["error"] = None
    remember(
        project,
        "done",
        "成片已生成，可以下载",
        progress={
            "percent": 100,
            "phase": "done",
            "message": "成片已生成，可以下载",
            "task_id": "join",
            "task_state": "done",
        },
    )


def start_render_thread(project_id):
    def target():
        try:
            execute_render(project_id)
        except Exception as error:
            fail_project(project_id, error)

    with project_lock(project_id):
        if thread_alive(project_id):
            return False
        thread = threading.Thread(target=target, daemon=True)
        from web.render_loop import register_thread

        register_thread(project_id, thread)
        thread.start()
    return True


def pause_render(project):
    if project.get("status") not in ("rendering", "paused"):
        raise RuntimeError("现在还不能暂停")
    if project.get("status") == "paused":
        return project
    state = mark_user_pause(project_dir(project["id"]), project["id"], "已暂停")
    current = state.get("current_step") or ""
    progress = {
        "phase": "paused",
        "message": "已暂停",
        "project_status": "paused",
    }
    if current:
        progress["task_id"] = current
        progress["task_state"] = "paused"
    remember(project, "paused", "已暂停", progress=progress)
    return project


def resume_render(project):
    if project.get("status") != "paused":
        raise RuntimeError("现在没有暂停")
    resume_paused(project_dir(project["id"]), project["id"])
    project["error"] = None
    remember(
        project,
        "rendering",
        "继续出片",
        progress={"phase": "images", "message": "继续出片", "project_status": "rendering"},
    )
    if not thread_alive(project["id"]):
        start_render_thread(project["id"])
    return project


def reset_render(project):
    if project.get("status") not in ("paused", "error"):
        raise RuntimeError("只有暂停或失败的步骤可以重置")
    step_id = reset_current_step(project_dir(project["id"]), project["id"])
    gate_for(project["id"]).resume()
    project["error"] = None
    project["finished_at"] = None
    remember(
        project,
        "rendering",
        "重置当前步骤：" + step_id,
        progress={
            "phase": "images",
            "message": "重置当前步骤：" + step_id,
            "task_id": step_id,
            "task_state": "pending",
            "project_status": "rendering",
        },
    )
    if not thread_alive(project["id"]):
        start_render_thread(project["id"])
    return project


def resume_rendering_projects():
    started = 0
    for project in list_projects():
        folder = project_dir(project["id"])
        if project.get("status") == "rendering" and not should_resume_project(project, folder):
            video = folder / "outputs" / "joined.mp4"
            state = load_run_state(folder)
            if state.get("phase") == "done" and video.is_file() and video.stat().st_size > 0:
                project["video_file"] = video.name
                project["error"] = None
                project["finished_at"] = project.get("finished_at") or now()
                remember(
                    project,
                    "done",
                    "成片已生成，可以下载",
                    progress={
                        "percent": 100,
                        "phase": "done",
                        "message": "成片已生成，可以下载",
                        "task_id": "join",
                        "task_state": "done",
                    },
                )
            continue
        if not should_resume_project(project, folder):
            continue
        if start_render_thread(project["id"]):
            started += 1
    return started


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, content_type="application/json; charset=utf-8", disposition=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
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
        if path == "/api/config":
            self._send(
                200,
                {
                    "debug_llm": env_debug_llm(),
                    "debug_llm_env": "AIVIDEO_DEBUG_LLM",
                    "model": DEFAULT_MODEL,
                    "models": model_choices(),
                },
            )
            return
        if path == "/api/projects":
            self._send(200, [public_view(item) for item in list_projects()])
            return
        if path.startswith("/api/projects/") and path.endswith("/video"):
            project_id = path.split("/")[3]
            if not valid_id(project_id):
                self._send(404, {"error": "还没有成片"})
                return
            project = read_project(project_id)
            name = str((project or {}).get("video_file") or "")
            video = project_dir(project_id) / "outputs" / name
            if (
                project is None
                or not name
                or name != Path(name).name
                or not video.is_file()
            ):
                self._send(404, {"error": "还没有成片"})
                return
            mime = mimetypes.guess_type(video.name)[0] or "video/mp4"
            disposition = None
            if "download" in parse_qs(parsed.query):
                disposition = 'attachment; filename="%s"' % video.name
            self._send(200, video.read_bytes(), mime, disposition)
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
            filename = "workflow-%s.json" % project_id
            self._send(
                200,
                workflow.read_bytes(),
                "application/json; charset=utf-8",
                'attachment; filename="%s"' % filename,
            )
            return
        if path.startswith("/api/projects/") and path.endswith("/spec"):
            project_id = path.split("/")[3]
            if not valid_id(project_id):
                self._send(404, {"error": "还没有 spec"})
                return
            spec_path = project_dir(project_id) / "spec.json"
            if not spec_path.is_file():
                self._send(404, {"error": "还没有 spec"})
                return
            self._send(
                200,
                spec_path.read_bytes(),
                "application/json; charset=utf-8",
                'attachment; filename="spec-%s.json"' % project_id,
            )
            return
        parts = [part for part in path.split("/") if part]
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "projects"
            and parts[3] == "clips"
        ):
            project_id = parts[2]
            name = parts[4]
            suffix = Path(name).suffix.lower()
            clip = project_dir(project_id) / "outputs" / "clips" / name
            if (
                not valid_id(project_id)
                or name != Path(name).name
                or suffix not in VIDEO_SUFFIXES
                or not clip.is_file()
            ):
                self._send(404, {"error": "没有这段视频"})
                return
            mime = mimetypes.guess_type(clip.name)[0] or "video/mp4"
            disposition = None
            if "download" in parse_qs(parsed.query):
                disposition = 'attachment; filename="%s"' % clip.name
            self._send(200, clip.read_bytes(), mime, disposition)
            return
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "projects"
            and parts[3] == "images"
        ):
            project_id = parts[2]
            name = parts[4]
            suffix = Path(name).suffix.lower()
            image = project_dir(project_id) / "outputs" / "images" / name
            if (
                not valid_id(project_id)
                or name != Path(name).name
                or suffix not in IMAGE_SUFFIXES
                or not image.is_file()
            ):
                self._send(404, {"error": "没有这张图"})
                return
            mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
            self._send(200, image.read_bytes(), mime)
            return
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "projects"
            and parts[3] == "debug"
        ):
            project_id = parts[2]
            key = parts[4]
            if not valid_id(project_id) or key not in DEBUG_FILES:
                self._send(404, {"error": "没有这个调试文件"})
                return
            file_path = project_dir(project_id) / "debug" / DEBUG_FILES[key]
            if not file_path.is_file():
                self._send(404, {"error": "没有这个调试文件"})
                return
            self._send(
                200,
                file_path.read_bytes(),
                "text/plain; charset=utf-8",
                'attachment; filename="%s"' % DEBUG_FILES[key],
            )
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
        path = urlparse(self.path).path
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "messages":
            self._post_message(parts[2])
            return
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] in ("pause", "resume", "reset"):
            self._post_control(parts[2], parts[3])
            return
        if path != "/api/projects":
            self._send(404, {"error": "没有这个接口"})
            return
        body = self._read_json()
        brief = str(body.get("brief") or "").strip()
        if not brief and not body.get("start"):
            self._send(400, {"error": "先选一个方向"})
            return
        debug = want_debug_llm(body.get("debug_llm"))
        created = now()
        project = {
            "id": uuid.uuid4().hex[:12],
            "brief": brief,
            "messages": [{"role": "user", "content": brief}] if brief else [],
            "title": (brief[:24] if brief else "新的一条"),
            "status": "drilling",
            "stage": "正在想方向",
            "script": "",
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "events": [{"at": created, "message": "正在想方向"}],
            "tasks": [],
            "progress": {
                "percent": 0,
                "phase": "drill_think",
                "message": "正在想方向",
                "debug_llm": debug,
            },
            "cloud_log": "",
            "prompt_id": "",
            "video_file": "",
            "model": normalize_model(body.get("model")),
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        }
        start_drill(project)
        self._send(201, public_view(read_project(project["id"])))

    def _post_message(self, project_id):
        if not valid_id(project_id):
            self._send(404, {"error": "没有这个项目"})
            return
        project = read_project(project_id)
        if project is None:
            self._send(404, {"error": "没有这个项目"})
            return
        if not can_reply(project):
            self._send(409, {"error": "还在想上一句，或这条片子已经开始做了"})
            return
        body = self._read_json()
        text = str(body.get("text") or "").strip()
        if not text:
            self._send(400, {"error": "先回答这一个问题"})
            return
        messages = list(project.get("messages") or [])
        messages.append({"role": "user", "content": text})
        project["messages"] = messages
        if not project.get("title") or project.get("title") == "新的一条":
            project["title"] = text[:24]
        if body.get("model"):
            project["model"] = normalize_model(body.get("model"))
        project["error"] = None
        start_drill(project)
        self._send(200, public_view(read_project(project_id)))

    def _post_control(self, project_id, action):
        if not valid_id(project_id):
            self._send(404, {"error": "没有这个项目"})
            return
        project = read_project(project_id)
        if project is None:
            self._send(404, {"error": "没有这个项目"})
            return
        try:
            if action == "pause":
                pause_render(project)
            elif action == "resume":
                resume_render(project)
            else:
                reset_render(project)
        except RuntimeError as error:
            self._send(409, {"error": str(error)})
            return
        fresh = read_project(project_id)
        self._send(200, public_view(fresh))

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/projects/"):
            self._send(404, {"error": "没有这个接口"})
            return
        project_id = path.rstrip("/").split("/")[3]
        if not valid_id(project_id):
            self._send(404, {"error": "没有这个项目"})
            return
        if read_project(project_id) is None and not project_dir(project_id).is_dir():
            self._send(404, {"error": "没有这个项目"})
            return
        if not delete_project(project_id):
            self._send(404, {"error": "没有这个项目"})
            return
        self._send(200, {"ok": True, "id": project_id})


def main():
    connect()
    DATA.mkdir(parents=True, exist_ok=True)
    resumed = resume_rendering_projects()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        "AiVideo http://%s:%s  (sqlite %s)%s"
        % (
            HOST,
            PORT,
            ROOT / "data" / "aivideo.db",
            "  DEBUG_LLM=on" if env_debug_llm() else "",
        )
        + ("  resumed=%s" % resumed if resumed else "")
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
