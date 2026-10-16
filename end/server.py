#!/usr/bin/env python3
"""DeepSeek-style chat site. Each message is one video project.

Model work is a task: Front -> End -> Task Queue -> Agent Layout.
The layout is LangGraph -> Agent -> LLM Model. Rendering stays on its own thread.
"""

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from end.db import DATA, connect, delete_project, list_projects, project_dir, read_project, write_project
from image_store import get_image_store
from end.render_loop import (
    _ffmpeg_bin,
    gate_for,
    is_running,
    load_run_state,
    mark_user_pause,
    note_local_image,
    project_lock,
    register_thread,
    reset_current_step,
    resume_paused,
    run_local_pipeline,
    should_resume_project,
    thread_alive,
)
from end.intake import (
    MAX_MATERIALS,
    enrich_brief,
    intake_note,
    load_intake,
    merge_intake,
    new_material,
    save_intake,
)
from end.qwen_vision import read_upload
from agent.layout import get_layout
from end.pipeline import (
    DEFAULT_MODEL,
    load_skill_pack,
    model_choices,
    model_label,
    normalize_model,
)
from end.image_node import prepare_image_node, video_nodes
from end.workflow_from_spec import _safe_id, collect_steps, task_plan
from task_queue import Task, TaskQueue

STATIC = ROOT / "front"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
PAUSABLE_STATUSES = ("writing", "building", "rendering")
MAX_CONTENT_BYTES = 1_000_000
MAX_ASSET_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CREATE_BYTES = MAX_MATERIALS * MAX_ASSET_IMAGE_BYTES + MAX_CONTENT_BYTES
# JSON-RPC bodies carry base64, which is four characters for every three bytes.
MAX_RPC_BYTES = ((MAX_CREATE_BYTES + 2) // 3) * 4 + 2_000_000
LOG_TAIL_CHARS = 16000
# writer: a queue worker is inside the script call. render: the render thread owns 出片.
# yielded: the writer stopped before 出片, so resume must start it.
_PIPELINE_HANDOFF = {}
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
        if status in ("rendering", "writing", "building") and not is_running(project["id"]):
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
            if status == "paused":
                paused_from = stored.get("paused_from") or prev.get("paused_from") or ""
                if paused_from:
                    project["progress"]["paused_from"] = paused_from
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
        project["stage"] = "提示词已写好。出片改为 iCreat 生图 / CR MiniMax H3 / 本机拼接 / ffmpeg 烧字幕"
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
            "/media/projects/%s/video" % project["id"]
            if project.get("video_file")
            and (folder / "outputs" / str(project.get("video_file"))).is_file()
            else ""
        ),
        "video_download_url": (
            "/media/projects/%s/video?download=1" % project["id"]
            if project.get("video_file")
            and (folder / "outputs" / str(project.get("video_file"))).is_file()
            else ""
        ),
        "prompt_id": project.get("prompt_id") or "",
        "model": normalize_model(project.get("model")),
        "model_label": model_label(project.get("model")),
        "debug_llm": debug,
        "debug_files": debug_files,
        "nodes": video_nodes(spec),
        "images": list_output_images(project["id"]),
        "clips": list_output_clips(project["id"]),
        "materials": public_materials(project["id"]),
        "progress": project.get("progress") or {"percent": 0, "phase": "", "message": project.get("stage") or ""},
    }


def call_drill(messages, debug_dir=None, model=None):
    return get_layout().drill(messages, debug_dir=debug_dir, model=model)


def call_deepseek(brief, skill_pack, debug_dir=None, model=None):
    return get_layout().write(brief, skill_pack, debug_dir=debug_dir, model=model)


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
            remember(
                project,
                "writing",
                "已经问清，开始写提示词",
                progress={
                    "percent": 8,
                    "phase": "writing",
                    "message": "已经问清，开始写提示词",
                    "debug_llm": debug,
                },
            )
            submit_agent_task(Task("write", project_id, {"debug_llm": debug}))
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
    submit_agent_task(Task("drill", project["id"]))


def _save_written_spec(project_id, spec):
    """Persist the script the model just returned without touching pause state."""
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    items = load_intake(folder)
    spec = prepare_image_node(spec, locked_ids=[item["id"] for item in items], items=items)
    spec = merge_intake(spec, items)
    (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    with project_lock(project_id):
        fresh = read_project(project_id)
        if fresh is None:
            return None
        fresh["title"] = str(spec.get("title") or fresh.get("title") or "")
        fresh["script"] = str(spec.get("script") or "")
        fresh["storyboard"] = str(spec.get("storyboard") or "")
        fresh["image_prompts"] = str(spec.get("image_prompts") or "")
        fresh["tasks"] = task_plan(spec)
        fresh["updated_at"] = now()
        write_project(fresh)
        return fresh


def _yield_if_paused(project_id):
    """Stop before 出片. The writer thread will not call execute_render."""
    with project_lock(project_id):
        if is_running(project_id):
            return False
        if _PIPELINE_HANDOFF.get(project_id) != "render":
            _PIPELINE_HANDOFF[project_id] = "yielded"
        return True


def run_project(project_id, debug_llm=False):
    register_thread(project_id)
    with project_lock(project_id):
        _PIPELINE_HANDOFF[project_id] = "writer"
    project = read_project(project_id)
    folder = project_dir(project_id)
    debug = want_debug_llm(debug_llm or project_debug_llm(project))
    try:
        remember(
            project,
            "writing",
            model_label(project.get("model"))
            + " 正在写提示词、分镜和生图提示词"
            + ("（Debug：仅到生图）" if debug else ""),
            progress={
                "percent": 8,
                "phase": "writing",
                "message": model_label(project.get("model")) + " 正在写提示词、分镜和生图提示词",
                "debug_llm": debug,
            },
        )
        skill_pack = load_skill_pack()
        debug_dir = folder / "debug" if debug else None
        spec = call_deepseek(
            enrich_brief(project["brief"], load_intake(folder)),
            skill_pack,
            debug_dir=debug_dir,
            model=project.get("model"),
        )
        fresh = _save_written_spec(project_id, spec)
        if fresh is None or _yield_if_paused(project_id):
            return
        building = "Debug：iCreat 生图" if debug else "iCreat 生图 / CR MiniMax H3 / 本机拼接 / ffmpeg 烧字幕"
        remember(
            fresh,
            "building",
            building,
            progress={
                "percent": 12,
                "phase": "building",
                "message": building,
                "debug_llm": debug,
            },
        )
        if _yield_if_paused(project_id):
            return
        with project_lock(project_id):
            if not is_running(project_id):
                _PIPELINE_HANDOFF[project_id] = "yielded"
                return
            _PIPELINE_HANDOFF[project_id] = "render"
        start_render_thread(project_id, replace=True)
    except Exception as error:
        fail_project(project_id, error)
    finally:
        with project_lock(project_id):
            if _PIPELINE_HANDOFF.get(project_id) == "writer":
                _PIPELINE_HANDOFF[project_id] = "yielded"


def handle_agent_task(task):
    if task.kind == "drill":
        run_drill(task.project_id)
        return
    if task.kind == "write":
        run_project(task.project_id, debug_llm=bool((task.payload or {}).get("debug_llm")))


_AGENT_QUEUE = None


def agent_queue():
    global _AGENT_QUEUE
    if _AGENT_QUEUE is None:
        _AGENT_QUEUE = TaskQueue(handle_agent_task)
    return _AGENT_QUEUE


def submit_agent_task(task):
    return agent_queue().submit(task)


def start_agent_workers():
    agent_queue().start()


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
                "url": "/media/projects/%s/images/%s?v=%s" % (project_id, path.name, int(path.stat().st_mtime)),
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
        url = "/media/projects/%s/clips/%s" % (project_id, path.name)
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
        if task.get("kind") in ("video", "join", "subtitles"):
            task["state"] = "muted"
            task["detail"] = "Debug：暂不执行"
    remember(
        project,
        "done",
        "Debug：已生成图片，视频、拼接和字幕暂不执行",
        progress={
            "percent": 100,
            "phase": "image_debug",
            "message": "Debug：已停在生图。视频、拼接和字幕暂不执行",
            "debug_llm": True,
        },
    )


def execute_render(project_id, project=None):
    from end.render_loop import register_thread

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
            "task_id": "subtitles",
            "task_state": "done",
        },
    )


def start_render_thread(project_id, replace=False):
    def target():
        try:
            execute_render(project_id)
        except Exception as error:
            fail_project(project_id, error)

    with project_lock(project_id):
        if thread_alive(project_id) and not replace:
            return False
        thread = threading.Thread(target=target, daemon=True)
        register_thread(project_id, thread)
        thread.start()
    return True


def pause_render(project):
    status = project.get("status")
    if status == "paused":
        return project
    if status not in PAUSABLE_STATUSES:
        raise RuntimeError("现在还不能暂停")
    state = mark_user_pause(project_dir(project["id"]), project["id"], "已暂停")
    current = state.get("current_step") or ""
    prev = project.get("progress") or {}
    progress = {
        "phase": "paused",
        "message": "已暂停",
        "project_status": "paused",
        "paused_from": status,
        "percent": prev.get("percent") or 0,
        "debug_llm": bool(prev.get("debug_llm")),
    }
    if current:
        progress["task_id"] = current
        progress["task_state"] = "paused"
    remember(project, "paused", "已暂停", progress=progress)
    return project


def _resume_in_place(project, paused_from):
    """The original thread is still in the script call or blocked in the render loop."""
    if paused_from in ("writing", "building") and _PIPELINE_HANDOFF.get(project["id"]) == "writer":
        status = paused_from
    else:
        status = "rendering"
    message = {
        "writing": "继续写提示词",
        "building": "继续准备出片",
    }.get(status, "继续出片")
    phase = status if status in ("writing", "building") else "images"
    project["error"] = None
    remember(
        project,
        status,
        message,
        progress={
            "phase": phase,
            "message": message,
            "project_status": status,
            "percent": (project.get("progress") or {}).get("percent") or 0,
        },
    )
    return read_project(project["id"]) or project


def resume_render(project):
    if project.get("status") != "paused":
        raise RuntimeError("现在没有暂停")
    project_id = project["id"]
    folder = project_dir(project_id)
    with project_lock(project_id):
        fresh = read_project(project_id) or project
        if fresh.get("status") != "paused":
            raise RuntimeError("现在没有暂停")
        paused_from = (fresh.get("progress") or {}).get("paused_from") or ""
        handoff = _PIPELINE_HANDOFF.get(project_id)
        alive = thread_alive(project_id)
        resume_paused(folder, project_id)
        if alive and handoff in ("writer", "render"):
            return _resume_in_place(fresh, paused_from if handoff == "writer" else "rendering")
        if not load_spec(project_id):
            mark_user_pause(folder, project_id, "已暂停")
            raise RuntimeError("提示词还没写完，不能继续出片")
        _PIPELINE_HANDOFF[project_id] = "render"
        fresh["error"] = None
    remember(
        fresh,
        "rendering",
        "继续出片",
        progress={"phase": "images", "message": "继续出片", "project_status": "rendering"},
    )
    start_render_thread(project_id, replace=True)
    return read_project(project_id) or fresh


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


def _spec_asset(spec, asset_id):
    for item in spec.get("assets") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or "")
        if raw == asset_id or _safe_id(raw, "") == asset_id:
            return item
    return None


def save_project_content(project_id, body):
    """Write script, storyboard, and asset prompts onto the project and spec.json."""
    if not isinstance(body, dict):
        raise ValueError("内容格式不对")
    with project_lock(project_id):
        project = read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        spec = load_spec(project_id) or {}
        changed = False
        if "script" in body:
            project["script"] = "" if body.get("script") is None else str(body.get("script"))
            spec["script"] = project["script"]
            changed = True
        if "storyboard" in body:
            project["storyboard"] = "" if body.get("storyboard") is None else str(body.get("storyboard"))
            spec["storyboard"] = project["storyboard"]
            changed = True
        if "assets" in body:
            incoming = body.get("assets")
            if not isinstance(incoming, list):
                raise ValueError("资产格式不对")
            if not isinstance(spec.get("assets"), list):
                raise ValueError("还没有资产")
            for item in incoming:
                if not isinstance(item, dict) or "id" not in item:
                    raise ValueError("资产格式不对")
                target = _spec_asset(spec, str(item.get("id") or ""))
                if target is None:
                    raise ValueError("没有资产 " + str(item.get("id") or ""))
                if "prompt" in item:
                    target["prompt"] = "" if item.get("prompt") is None else str(item.get("prompt"))
            changed = True
        if not changed:
            raise ValueError("没有要保存的内容")
        folder = project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        project["updated_at"] = now()
        write_project(project)
        return project


def _image_kind(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _ffmpeg_png_bytes(data, kind):
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg，不能把图片转成 png")
    suffix = ".jpg" if kind == "jpeg" else ".webp"
    folder = Path(tempfile.mkdtemp(prefix="aivideo-still-"))
    source = folder / ("source" + suffix)
    temporary = folder / "out.png"
    try:
        source.write_bytes(data)
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(source), str(temporary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("ffmpeg 转 png 失败：%s" % ((proc.stdout or "")[-300:]))
        return temporary.read_bytes()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _png_bytes(data):
    if not data:
        raise ValueError("没有图片")
    if len(data) > MAX_ASSET_IMAGE_BYTES:
        raise ValueError("图片太大")
    kind = _image_kind(data)
    if not kind:
        raise ValueError("只接受 png、jpeg、webp")
    if kind == "png":
        return data
    return _ffmpeg_png_bytes(data, kind)


def save_asset_image(project_id, asset_id, data):
    """Store a replacement still and record this service's public URL.

    local_override stays true so a later asset does not reuse the upload as a reference.
    """
    asset_id = str(asset_id or "")
    if not asset_id or asset_id != Path(asset_id).name or not all(ch.isalnum() or ch in "-_" for ch in asset_id):
        raise ValueError("没有这个资产")
    with project_lock(project_id):
        project = read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        spec = load_spec(project_id) or {}
        if _spec_asset(spec, asset_id) is None:
            raise ValueError("没有这个资产")
        state = load_run_state(project_dir(project_id))
        step = (state.get("steps") or {}).get(asset_id) or {}
        if step.get("remote_task_id") and step.get("status") in ("running", "retrying"):
            raise RuntimeError("这一步已经提交，完成前不能替换图片")
        public_url = get_image_store().save(project_id, asset_id, _png_bytes(data))
        note_local_image(project_dir(project_id), project_id, asset_id, public_url)
        return project


def store_materials(project_id, files):
    """Save the stills from the opening composer and remember each narration."""
    if len(files) > MAX_MATERIALS:
        raise ValueError("一次最多上传 8 张图")
    items = []
    folder = project_dir(project_id)
    store = get_image_store()
    for index, item in enumerate(files, start=1):
        material = new_material(index, item.get("narration"), item.get("filename"))
        public_url = store.save(project_id, material["id"], _png_bytes(item.get("data") or b""))
        note_local_image(folder, project_id, material["id"], public_url)
        image_path = folder / "outputs" / "images" / (material["id"] + ".png")
        material["reading"] = read_upload(image_path)
        items.append(material)
    save_intake(folder, items)
    return items


def public_materials(project_id):
    items = load_intake(project_dir(project_id))
    images = list_output_images(project_id)
    result = []
    for item in items:
        image = next((info for info in images if str(info.get("name") or "").startswith(item["id"] + ".")), None)
        result.append(
            {
                "id": item["id"],
                "title": item["title"],
                "narration": item["narration"],
                "url": image["url"] if image else "",
            }
        )
    return result


def read_log_tail(path, limit=LOG_TAIL_CHARS):
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def project_logs(project_id):
    project = read_project(project_id)
    if project is None:
        raise KeyError("没有这个项目")
    folder = project_dir(project_id)
    return {
        "error": project.get("error") or "",
        "error_log": read_log_tail(folder / "error.log"),
        "render_log": read_log_tail(folder / "outputs" / "render.log"),
    }


def resume_agent_tasks():
    """Put interrupted drill and script calls back on the queue after a restart."""
    started = 0
    for project in list_projects():
        status = project.get("status")
        phase = (project.get("progress") or {}).get("phase") or ""
        if status == "drilling" and phase == "drill_think":
            if submit_agent_task(Task("drill", project["id"])):
                started += 1
            continue
        if status == "writing":
            if submit_agent_task(
                Task("write", project["id"], {"debug_llm": project_debug_llm(project)})
            ):
                started += 1
    return started


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
                finished_task = "join"
                if any(task.get("id") == "subtitles" for task in project.get("tasks") or []):
                    finished_task = "subtitles"
                remember(
                    project,
                    "done",
                    "成片已生成，可以下载",
                    progress={
                        "percent": 100,
                        "phase": "done",
                        "message": "成片已生成，可以下载",
                        "task_id": finished_task,
                        "task_state": "done",
                    },
                )
            continue
        if not should_resume_project(project, folder):
            continue
        if start_render_thread(project["id"]):
            started += 1
    return started


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)


def _rpc_body(request_id, result=None, error=None):
    if error is not None:
        return {
            "jsonrpc": "2.0",
            "error": {"code": error.code, "message": error.message},
            "id": request_id,
        }
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def decode_image_b64(value):
    text = str(value or "").strip()
    if text.startswith("data:"):
        marker = text.find(",")
        if marker < 0:
            raise ValueError("图片格式不对")
        text = text[marker + 1 :].strip()
    if not text:
        raise ValueError("没有图片")
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        raise ValueError("图片格式不对")


def _request_project(params):
    project_id = str((params or {}).get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目")
    return project


def rpc_config(_params):
    return {
        "debug_llm": env_debug_llm(),
        "debug_llm_env": "AIVIDEO_DEBUG_LLM",
        "model": DEFAULT_MODEL,
        "models": model_choices(),
    }


def rpc_list_projects(_params):
    return [public_view(item) for item in list_projects()]


def rpc_get_project(params):
    return public_view(_request_project(params))


def _materials_from_params(params):
    raw = params.get("materials") or []
    if not raw:
        return []
    if not isinstance(raw, list):
        raise RpcError(400, "内容格式不对")
    files = []
    for item in raw:
        if not isinstance(item, dict):
            raise RpcError(400, "内容格式不对")
        try:
            data = decode_image_b64(item.get("data_base64"))
        except ValueError as error:
            raise RpcError(400, str(error))
        if len(data) > MAX_ASSET_IMAGE_BYTES:
            raise RpcError(413, "图片太大")
        files.append(
            {
                "filename": str(item.get("filename") or ""),
                "narration": "" if item.get("narration") is None else str(item.get("narration")),
                "data": data,
            }
        )
    return files


def rpc_create_project(params):
    brief = str(params.get("brief") or "").strip()
    if not brief and not params.get("start"):
        raise RpcError(400, "先选一个方向")
    flag = params.get("debug_llm")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("1", "true", "yes", "on")
    debug = want_debug_llm(flag)
    files = _materials_from_params(params)
    created = now()
    project_id = uuid.uuid4().hex[:12]
    try:
        materials = store_materials(project_id, files) if files else []
    except (ValueError, RuntimeError) as error:
        folder = project_dir(project_id)
        if folder.is_dir():
            shutil.rmtree(folder)
        raise RpcError(400, str(error))
    note = intake_note(materials)
    content = brief if not note else brief + "\n\n" + note
    project = {
        "id": project_id,
        "brief": brief,
        "messages": [{"role": "user", "content": content}] if content else [],
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
        "model": normalize_model(params.get("model")),
        "created_at": created,
        "started_at": created,
        "updated_at": created,
        "finished_at": None,
    }
    start_drill(project)
    return public_view(read_project(project["id"]))


def rpc_delete_project(params):
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    if read_project(project_id) is None and not project_dir(project_id).is_dir():
        raise RpcError(404, "没有这个项目")
    if not delete_project(project_id):
        raise RpcError(404, "没有这个项目")
    return {"ok": True, "id": project_id}


def rpc_send_message(params):
    project = _request_project(params)
    if not can_reply(project):
        raise RpcError(409, "还在想上一句，或这条片子已经开始做了")
    text = str(params.get("text") or "").strip()
    if not text:
        raise RpcError(400, "先回答这一个问题")
    messages = list(project.get("messages") or [])
    messages.append({"role": "user", "content": text})
    project["messages"] = messages
    if not project.get("title") or project.get("title") == "新的一条":
        project["title"] = text[:24]
    if params.get("model"):
        project["model"] = normalize_model(params.get("model"))
    project["error"] = None
    start_drill(project)
    return public_view(read_project(project["id"]))


def rpc_control(action, params):
    project = _request_project(params)
    try:
        if action == "pause":
            pause_render(project)
        elif action == "resume":
            resume_render(project)
        else:
            reset_render(project)
    except RuntimeError as error:
        raise RpcError(409, str(error))
    return public_view(read_project(project["id"]))


def rpc_save_content(params):
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    body = {}
    for key in ("script", "storyboard", "assets"):
        if key in params:
            body[key] = params[key]
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_CONTENT_BYTES:
        raise RpcError(413, "内容太大")
    try:
        save_project_content(project_id, body)
    except KeyError as error:
        raise RpcError(404, str(error))
    except ValueError as error:
        raise RpcError(400, str(error))
    return public_view(read_project(project_id))


def rpc_set_asset_image(params):
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    try:
        data = decode_image_b64(params.get("data_base64"))
    except ValueError as error:
        raise RpcError(400, str(error))
    if len(data) > MAX_ASSET_IMAGE_BYTES:
        raise RpcError(413, "图片太大")
    try:
        save_asset_image(project_id, str(params.get("asset_id") or ""), data)
    except KeyError as error:
        raise RpcError(404, str(error))
    except (ValueError, RuntimeError) as error:
        raise RpcError(400, str(error))
    return public_view(read_project(project_id))


def rpc_logs(params):
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    try:
        return project_logs(project_id)
    except KeyError as error:
        raise RpcError(404, str(error))


def rpc_call(method, params):
    if method == "config.get":
        return rpc_config(params)
    if method == "projects.list":
        return rpc_list_projects(params)
    if method == "projects.get":
        return rpc_get_project(params)
    if method == "projects.create":
        return rpc_create_project(params)
    if method == "projects.delete":
        return rpc_delete_project(params)
    if method == "projects.send_message":
        return rpc_send_message(params)
    if method == "projects.pause":
        return rpc_control("pause", params)
    if method == "projects.resume":
        return rpc_control("resume", params)
    if method == "projects.reset":
        return rpc_control("reset", params)
    if method == "projects.save_content":
        return rpc_save_content(params)
    if method == "projects.set_asset_image":
        return rpc_set_asset_image(params)
    if method == "projects.logs":
        return rpc_logs(params)
    raise RpcError(-32601, "没有这个接口")


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

    def _read_limited(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            self.close_connection = True
            return None
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/media/projects/") and path.endswith("/video"):
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
        if path.startswith("/media/projects/") and path.endswith("/workflow"):
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
        if path.startswith("/media/projects/") and path.endswith("/spec"):
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
            and parts[0] == "media"
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
            and parts[0] == "media"
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
            and parts[0] == "media"
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
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        file_path = (STATIC / relative).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
            self._send(404, {"error": "没有这个页面"})
            return
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._send(200, file_path.read_bytes(), mime)

    def do_POST(self):
        if urlparse(self.path).path != "/rpc":
            self._send(404, {"error": "没有这个接口"})
            return
        self._post_rpc()

    def _post_rpc(self):
        request_id = None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(200, _rpc_body(None, error=RpcError(-32700, "Parse error")))
            return
        if length > MAX_RPC_BYTES:
            self._send(200, _rpc_body(None, error=RpcError(413, "内容太大")))
            return
        raw = self._read_limited(MAX_RPC_BYTES)
        if raw is None:
            self._send(200, _rpc_body(None, error=RpcError(413, "内容太大")))
            return
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(200, _rpc_body(None, error=RpcError(-32700, "Parse error")))
            return
        if (
            not isinstance(payload, dict)
            or payload.get("jsonrpc") != "2.0"
            or not isinstance(payload.get("method"), str)
            or "id" not in payload
        ):
            if isinstance(payload, dict) and "id" in payload:
                request_id = payload.get("id")
            self._send(200, _rpc_body(request_id, error=RpcError(-32600, "Invalid Request")))
            return
        request_id = payload.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int, float, type(None))):
            self._send(200, _rpc_body(None, error=RpcError(-32600, "Invalid Request")))
            return
        params = payload.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._send(200, _rpc_body(request_id, error=RpcError(-32602, "Invalid params")))
            return
        try:
            result = rpc_call(payload["method"], params)
        except RpcError as error:
            self._send(200, _rpc_body(request_id, error=error))
            return
        except Exception:
            traceback.print_exc()
            self._send(200, _rpc_body(request_id, error=RpcError(-32603, "Internal error")))
            return
        self._send(200, _rpc_body(request_id, result=result))


def main():
    connect()
    DATA.mkdir(parents=True, exist_ok=True)
    start_agent_workers()
    resumed = resume_rendering_projects()
    agent_resumed = resume_agent_tasks()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    note = ""
    if env_debug_llm():
        note += "  DEBUG_LLM=on"
    if resumed:
        note += "  resumed=%s" % resumed
    if agent_resumed:
        note += "  agent_resumed=%s" % agent_resumed
    print(
        "AiVideo http://%s:%s  (sqlite %s)  agent_workers=%s%s"
        % (HOST, PORT, ROOT / "data" / "aivideo.db", agent_queue().workers, note)
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
