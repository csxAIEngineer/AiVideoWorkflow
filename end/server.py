#!/usr/bin/env python3
"""Project site. The browser talks to this process over JSON-RPC.

Model work is Front -> End -> AI. End keeps the queue and the render loop.
AI is a separate process: LangGraph, the agent, and the provider clients.
"""

import base64
import contextvars
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from end.ai_client import (
    default_model,
    job_message,
    load_skill_pack,
    model_choices,
    model_label,
    normalize_model,
    normalize_template,
    read_upload,
    set_status_hook,
    template_choices,
    template_label,
    template_policy,
    drill as ai_drill,
    write as ai_write,
)
from end.db import DATA, connect, delete_project, list_projects, project_dir, read_project, write_project
from end.image_store import get_image_store
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
from end.image_node import prepare_image_node, video_nodes
from end.delivery import list_deliverables
from end.locks import (
    absorb_model_output,
    apply_selection,
    copy_locks,
    corpus,
    load_style_cards,
    locked_aspect,
    locks_ready,
    pending_question,
    prepare_locks,
    read_locks,
    ready_to_write,
    style_question,
    wants_render,
    write_locks,
)
from end.prompt_gate import build_board, feed_text, project_markdown, validate_feed
from end.repair_units import choice_action, next_asset_question
from end.stage_runner import (
    accept_pipeline,
    detect_only,
    execute,
    initial_pipeline,
    question_message,
    ASSET_REVISE,
    STORYBOARD_KEEP,
    script_revise_question,
    storyboard_has_draft,
)
from end.soundtrack import normalize_soundtrack, project_soundtrack, soundtrack_enabled
from end.workflow_from_spec import _safe_id, collect_steps, task_plan
from end.task_queue import Task, TaskQueue

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


def _phase(project):
    return (project.get("progress") or {}).get("phase") or ""


def prompts_ready(project):
    return bool(project) and project.get("status") == "done" and _phase(project) == "prompts_ready"


def can_reply(project):
    if not project:
        return False
    phase = _phase(project)
    if project.get("status") == "drilling":
        return phase in ("drill_wait", "aspect_wait", "lock_wait")
    if project.get("status") == "gate":
        return phase.startswith("gate_")
    return prompts_ready(project)


def normalize_debug_stop(value):
    name = str(value or "").strip().lower()
    if name in ("intent", "意图", "意图解析"):
        return "intent"
    if name in ("prompts", "prompt", "提示词"):
        return "prompts"
    if name in ("images", "image", "生图"):
        return "images"
    return ""


def project_debug_llm(project):
    if not project:
        return False
    if project.get("debug_llm"):
        return True
    progress = project.get("progress") or {}
    return bool(progress.get("debug_llm")) or progress.get("phase") in ("llm_debug", "image_debug")


def project_stops_at_intent(project):
    if not project:
        return False
    progress = project.get("progress") or {}
    if normalize_debug_stop(progress.get("debug_stop") or project.get("debug_stop")) == "intent":
        return True
    return progress.get("phase") == "intent_debug"


def project_stops_at_prompts(project):
    if not project:
        return False
    progress = project.get("progress") or {}
    if normalize_debug_stop(progress.get("debug_stop") or project.get("debug_stop")) == "prompts":
        return True
    return progress.get("phase") == "prompt_debug"


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
            if "debug_stop" in stored:
                debug_stop = normalize_debug_stop(stored.get("debug_stop"))
            else:
                debug_stop = normalize_debug_stop(prev.get("debug_stop"))
            soundtrack = stored["soundtrack"] if "soundtrack" in stored else prev.get("soundtrack")
            project["progress"] = {
                "percent": int(stored.get("percent") or prev.get("percent") or 0),
                "phase": stored.get("phase") or prev.get("phase") or "",
                "current": stored["current"] if "current" in stored else prev.get("current"),
                "total": stored["total"] if "total" in stored else prev.get("total"),
                "message": stored.get("message") or stage,
                "debug_llm": bool(debug_flag),
                "debug_stop": debug_stop,
                "soundtrack": normalize_soundtrack(soundtrack),
            }
            if "soundtrack_note" in stored:
                note = str(stored.get("soundtrack_note") or "").strip()
                if note:
                    project["progress"]["soundtrack_note"] = note
            elif prev.get("soundtrack_note"):
                project["progress"]["soundtrack_note"] = prev.get("soundtrack_note")
            if "lock_machine" in stored:
                project["progress"]["lock_machine"] = bool(stored.get("lock_machine"))
            elif prev.get("lock_machine"):
                project["progress"]["lock_machine"] = True
            if "pipeline" in stored:
                project["progress"]["pipeline"] = stored.get("pipeline") or {}
            elif prev.get("pipeline"):
                project["progress"]["pipeline"] = prev.get("pipeline")
            if "question" in stored:
                project["progress"]["question"] = stored.get("question") or None
            elif prev.get("question"):
                project["progress"]["question"] = prev.get("question")
            if "calls" in stored:
                calls = stored.get("calls") or []
                project["progress"]["calls"] = list(calls) if isinstance(calls, list) else []
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
        project["stage"] = "提示词已写好。出片改为千问生图 / MiniMax H3 出镜 / 本机拼接 / ffmpeg 烧字幕"
        write_project(project)
    if project.get("tasks"):
        return
    spec = load_spec(project["id"])
    if not spec:
        return
    tasks = task_plan(spec, soundtrack=project_soundtrack(project))
    if not tasks:
        return
    project["tasks"] = tasks
    write_project(project)


def _shown_messages(project):
    """A failed machine check offers one revision from the report, even if an older reply still says 继续."""
    messages = [dict(item) if isinstance(item, dict) else item for item in (project.get("messages") or [])]
    progress = project.get("progress") or {}
    pipeline = progress.get("pipeline") or {}
    if project.get("status") != "gate" or pipeline.get("passed"):
        return messages
    if not messages or not isinstance(messages[-1], dict) or messages[-1].get("role") != "assistant":
        return messages
    options = list(messages[-1].get("options") or [])
    if options == ["继续"]:
        options = [ASSET_REVISE]
    if (
        pipeline.get("current") == "storyboard"
        and options == [ASSET_REVISE]
        and storyboard_has_draft(project_dir(project["id"]))
    ):
        options = [ASSET_REVISE, STORYBOARD_KEEP]
    if options != list(messages[-1].get("options") or []):
        messages[-1]["options"] = options
    return messages


def public_view(project):
    from end.trace import list_traces, trace_enabled

    load_tasks(project)
    folder = project_dir(project["id"])
    spec = load_spec(project["id"])
    assets, shots = ([], [])
    if spec:
        from end.board_shots import apply_storyboard_shots

        filled = apply_storyboard_shots(spec)
        if filled.get("shots") != spec.get("shots"):
            spec = filled
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "spec.json").write_text(
                json.dumps(spec, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        assets, shots = collect_steps(spec)
    debug_dir = folder / "debug"
    debug_files = [
        key for key, name in DEBUG_FILES.items() if (debug_dir / name).is_file()
    ]
    debug = project_debug_llm(project)
    return {
        "id": project["id"],
        "brief": project.get("brief", ""),
        "messages": _shown_messages(project),
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
        "has_board": (folder / "board.html").is_file(),
        "board_url": (
            "/media/projects/%s/board" % project["id"]
            if (folder / "board.html").is_file()
            else ""
        ),
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
        "template": normalize_template(project.get("template")),
        "template_label": template_label(project.get("template")),
        "debug_llm": debug,
        "debug_stop": normalize_debug_stop((project.get("progress") or {}).get("debug_stop")),
        "debug_files": debug_files,
        "debug_trace": trace_enabled(),
        "traces": list_traces(folder),
        "nodes": video_nodes(spec, soundtrack=project_soundtrack(project)),
        "images": list_output_images(project["id"]),
        "clips": list_output_clips(project["id"]),
        "materials": public_materials(project["id"]),
        "progress": project.get("progress") or {"percent": 0, "phase": "", "message": project.get("stage") or ""},
        "question": (project.get("progress") or {}).get("question") or None,
        "deliverables": list_deliverables(folder),
    }


_JOB_PROJECT = contextvars.ContextVar("aivideo_job_project", default="")
_CALL_LABEL = {
    "script": "剧本",
    "asset": "资产锁定",
    "storyboard": "分镜",
    "feed": "提示词封装",
    "recon": "对标",
    "qc": "独立质检",
    "board": "离线看板",
    "route": "赛道分流",
}


def _bind_job(project_id):
    return _JOB_PROJECT.set(str(project_id or ""))


def _call_label(project, calls):
    names = [item.get("name") for item in calls or []]
    if names == ["读图"]:
        return "读图"
    if names == ["旁白"]:
        return "旁白"
    current = ((project.get("progress") or {}).get("pipeline") or {}).get("current") or ""
    if current in _CALL_LABEL:
        return _CALL_LABEL[current]
    if project.get("status") == "drilling":
        return "立项锁定"
    return project.get("stage") or "写稿"


def _publish_job(snap):
    project_id = _JOB_PROJECT.get()
    if not project_id or not isinstance(snap, dict):
        return
    project = read_project(project_id)
    if project is None:
        return
    calls = []
    for item in snap.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        calls.append(
            {
                "name": item.get("name") or item.get("id") or "",
                "status": item.get("status") or "",
            }
        )
    label = _call_label(project, calls)
    progress = dict(project.get("progress") or {})
    progress["calls"] = calls
    progress["message"] = job_message(label, calls)
    remember(
        project,
        project.get("status") or "writing",
        project.get("stage") or label,
        progress=progress,
    )


set_status_hook(_publish_job)


def call_drill(messages, debug_dir=None, model=None, template=None):
    return ai_drill(messages, debug_dir=debug_dir, model=model, template=template)


def call_deepseek(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
    return ai_write(
        brief,
        skill_pack,
        debug_dir=debug_dir,
        model=model,
        template=template,
        trace_prompts=trace_prompts,
    )


def _lock_inputs(project):
    folder = project_dir(project["id"])
    brief = project.get("brief") or ""
    return folder, prepare_locks(
        read_locks(folder),
        brief,
        material_text=corpus(brief, project.get("messages")),
        has_images=bool(load_intake(folder)),
    )


def _open_question(locks, brief):
    question = pending_question(locks)
    if question and question.get("id") == "style":
        question = style_question(load_style_cards(brief))
    return question


def _ask_lock(project, question):
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    remember(
        project,
        "drilling",
        "立项锁定",
        progress={
            "percent": 0,
            "phase": "lock_wait",
            "message": (question or {}).get("reply") or "立项锁定",
            "lock_machine": True,
            "question": question,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _begin_writing(project):
    if project_stops_at_intent(project):
        project["finished_at"] = now()
        remember(
            project,
            "done",
            "Debug：已停在意图解析",
            progress={
                "percent": 100,
                "phase": "intent_debug",
                "message": "Debug：已停在意图解析。不写提示词，不生图。",
                "debug_llm": False,
                "debug_stop": "intent",
                "lock_machine": True,
                "question": None,
            },
        )
        return public_view(read_project(project["id"]) or project)
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    if pipeline.get("accepted"):
        folder = project_dir(project["id"])
        pipeline, _action = accept_pipeline(pipeline, read_locks(folder))
        label = "独立质检" if pipeline.get("action") == "finish" else "下一段"
        _enqueue_stage(project, pipeline, label)
        return public_view(read_project(project["id"]) or project)
    pipeline = initial_pipeline(detect_only(project.get("brief") or ""))
    remember(
        project,
        "writing",
        "赛道分流",
        progress={
            "percent": 8,
            "phase": "writing",
            "message": "赛道分流",
            "lock_machine": True,
            "pipeline": pipeline,
            "question": None,
        },
    )
    submit_agent_task(Task("write", project["id"], {"debug_llm": bool(progress.get("debug_llm"))}))
    return public_view(read_project(project["id"]) or project)


def _enqueue_stage(project, pipeline, label):
    progress = project.get("progress") or {}
    remember(
        project,
        "writing",
        label,
        progress={
            "percent": int(progress.get("percent") or 12),
            "phase": "writing",
            "message": label,
            "lock_machine": True,
            "pipeline": pipeline,
            "question": None,
        },
    )
    submit_agent_task(Task("write", project["id"], {"debug_llm": bool(progress.get("debug_llm"))}))


def run_lock_turn(project_id):
    project = read_project(project_id)
    if project is None:
        return
    folder, locks = _lock_inputs(project)
    write_locks(folder, locks)
    if not ready_to_write(locks):
        _ask_lock(project, _open_question(locks, project.get("brief") or ""))
        return
    _begin_writing(project)


def answer_lock(project, text):
    folder, locks = _lock_inputs(project)
    question = (project.get("progress") or {}).get("question") or pending_question(locks)
    if question and question.get("id") == "style" and not question.get("cards"):
        question = style_question(load_style_cards(project.get("brief") or ""))
    if (question or {}).get("id") in ("model", "reference_mode"):
        updated = copy_locks(locks)
        updated["model"] = "h3"
        updated["reference_mode"] = ""
        error = ""
    else:
        updated, error = apply_selection(locks, question, text, cards=(question or {}).get("cards"))
    if error:
        refused = dict(question or {})
        refused["reply"] = error
        return _ask_lock(project, refused)
    brief = project.get("brief") or ""
    updated = prepare_locks(
        updated,
        brief,
        material_text=corpus(brief, project.get("messages")),
        has_images=bool(load_intake(folder)),
    )
    write_locks(folder, updated)
    absorb_model_output(folder, {"ignored": True})
    project = read_project(project["id"]) or project
    if not ready_to_write(updated):
        return _ask_lock(project, _open_question(updated, brief))
    return _begin_writing(project)


def _ask_script_revise(project, pipeline):
    question = script_revise_question()
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    remember(
        project,
        "gate",
        "剧本门",
        progress={
            "percent": int((project.get("progress") or {}).get("percent") or 20),
            "phase": "gate_script",
            "message": "选一个改法",
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _asset_text(project):
    path = project_dir(project["id"]) / "_内部_" / "assets.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _ask_asset_choice(project, pipeline, question):
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    pipeline = dict(pipeline or {})
    pipeline["action"] = "wait"
    remember(
        project,
        "gate",
        "资产门",
        progress={
            "percent": int((project.get("progress") or {}).get("percent") or 40),
            "phase": "gate_asset",
            "message": "选一个改法",
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _open_asset_choices(project, pipeline):
    pipeline = dict(pipeline or {})
    pipeline["repair_choices"] = {}
    question = next_asset_question(_asset_text(project), {})
    if question is None:
        pipeline["action"] = "rewrite"
        pipeline["repair_round"] = True
        pipeline["repair"] = pipeline.get("report") or pipeline.get("repair") or "按块修补"
        _enqueue_stage(project, pipeline, "资产门")
        return public_view(read_project(project["id"]) or project)
    return _ask_asset_choice(project, pipeline, question)


def _take_asset_choice(project, pipeline, body, question):
    picked = choice_action(body)
    options = list((question or {}).get("options") or [])
    if picked is None or body not in options:
        return _ask_asset_choice(project, pipeline, question)
    choices = dict(pipeline.get("repair_choices") or {})
    choices[str(question.get("unit_id") or "")] = picked
    pipeline = dict(pipeline or {})
    pipeline["repair_choices"] = choices
    nxt = next_asset_question(_asset_text(project), choices)
    if nxt:
        return _ask_asset_choice(project, pipeline, nxt)
    pipeline["action"] = "rewrite"
    pipeline["repair_round"] = True
    pipeline["repair"] = pipeline.get("report") or pipeline.get("repair") or "按块修补"
    _enqueue_stage(project, pipeline, "资产门")
    return public_view(read_project(project["id"]) or project)


def answer_gate(project, text):
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    current = pipeline.get("current") or ""
    body = " ".join(str(text or "").split())
    passed = bool(pipeline.get("passed"))
    question = progress.get("question") or {}
    if question.get("id") == "script_revise" and body not in ("继续", "接受", "确认"):
        note = "" if body == "其他" else body
        if not note:
            return _ask_script_revise(project, pipeline)
        pipeline["revise_note"] = note
        pipeline["action"] = "rewrite"
        pipeline["current"] = "script"
        _enqueue_stage(project, pipeline, "剧本")
        return public_view(read_project(project["id"]) or project)
    if question.get("id") == "asset_repair_choice":
        return _take_asset_choice(project, pipeline, body, question)
    if body == "精修" and current == "script" and passed:
        pipeline["action"] = "wait"
        return _ask_script_revise(project, pipeline)
    if body == ASSET_REVISE and (passed or not current):
        question = progress.get("question") or {}
        if question:
            messages = list(project.get("messages") or [])
            messages.append(question_message(question))
            project["messages"] = messages
            write_project(project)
        return public_view(read_project(project["id"]) or project)
    if body not in ("继续", "接受", "确认", ASSET_REVISE, STORYBOARD_KEEP):
        question = progress.get("question") or {}
        if question:
            messages = list(project.get("messages") or [])
            messages.append(question_message(question))
            project["messages"] = messages
            write_project(project)
        return public_view(read_project(project["id"]) or project)
    report_text = str(pipeline.get("report") or pipeline.get("repair") or "")
    if not passed and (
        "文件不存在" in report_text
        or "资产稿是空的" in report_text
        or "资产提示词是空的" in report_text
    ):
        # Missing assets.md cannot be figure-repaired; rewrite the whole stage.
        pipeline["retry_write"] = True
    if not passed and pipeline.get("retry_write"):
        pipeline["action"] = "write"
        pipeline["retry_write"] = False
        pipeline["passed"] = True
        pipeline["repair"] = ""
        pipeline["report"] = ""
        # region agent log
        try:
            import json as _json
            import time as _time

            with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                handle.write(
                    _json.dumps(
                        {
                            "sessionId": "ee5fa7",
                            "hypothesisId": "H9",
                            "location": "end/server.py:answer_gate",
                            "message": "retry unfinished write",
                            "data": {"current": current, "action": "write"},
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
        # endregion
        _enqueue_stage(project, pipeline, project.get("stage") or "再写这一段")
        return public_view(read_project(project["id"]) or project)
    if body == ASSET_REVISE and current == "asset" and not passed:
        return _open_asset_choices(project, pipeline)
    if body == STORYBOARD_KEEP and current == "storyboard" and not passed:
        folder = project_dir(project["id"])
        if not storyboard_has_draft(folder):
            question = progress.get("question") or {}
            if question:
                messages = list(project.get("messages") or [])
                messages.append(question_message(question))
                project["messages"] = messages
                write_project(project)
            return public_view(read_project(project["id"]) or project)
        pipeline["passed"] = True
        pipeline["repair"] = ""
        pipeline["report"] = ""
        passed = True
    if not passed:
        pipeline["action"] = "rewrite"
        pipeline["repair"] = pipeline.get("report") or pipeline.get("repair") or ""
        _enqueue_stage(project, pipeline, project.get("stage") or "重写这一段")
        return public_view(read_project(project["id"]) or project)
    folder, locks = _lock_inputs(project)
    if current == "recon":
        locks["intel_confirmed"] = True
        locks = prepare_locks(
            locks,
            project.get("brief") or "",
            material_text=corpus(project.get("brief") or "", project.get("messages")),
            has_images=bool(load_intake(folder)),
        )
        write_locks(folder, locks)
        accepted = list(pipeline.get("accepted") or [])
        if current not in accepted:
            accepted.append(current)
        pipeline["accepted"] = accepted
        pipeline["action"] = "wait"
        progress["pipeline"] = pipeline
        if not ready_to_write(locks):
            project = read_project(project["id"]) or project
            project["progress"] = progress
            return _ask_lock(project, _open_question(locks, project.get("brief") or ""))
    pipeline, _action = accept_pipeline(pipeline, locks)
    label = "独立质检" if pipeline.get("action") == "finish" else "下一段"
    _enqueue_stage(project, pipeline, label)
    return public_view(read_project(project["id"]) or project)


def run_project_gated(project_id, debug_llm=False):
    register_thread(project_id)
    with project_lock(project_id):
        _PIPELINE_HANDOFF[project_id] = "writer"
    try:
        project = read_project(project_id)
        if project is None or not is_running(project_id):
            return
        folder = project_dir(project_id)
        locks = read_locks(folder)
        outcome = execute(
            project,
            folder,
            locks,
            write_model=call_deepseek,
            load_pack=load_skill_pack,
        )
        absorb_model_output(folder, outcome.get("spec") or {})
        if outcome.get("spec"):
            saved = _save_written_spec(project_id, outcome["spec"])
            if saved is None or not is_running(project_id):
                return
        if not is_running(project_id):
            return
        project = read_project(project_id) or project
        if outcome.get("message"):
            messages = list(project.get("messages") or [])
            messages.append(outcome["message"])
            project["messages"] = messages
        if outcome.get("terminal") == "ready":
            if (read_locks(folder).get("model") or "") != "h3":
                note = "提示词已就绪。Seedance 只交付提示词。"
            else:
                note = "提示词已就绪。要出图出片，点按钮或发送「出图」。"
            messages = list(project.get("messages") or [])
            messages.append({"role": "assistant", "content": note})
            project["messages"] = messages
            if project_stops_at_prompts(project):
                finish_prompt_debug(project)
                return
            finish_prompts_ready(project)
            return
        progress = dict(outcome.get("progress") or {})
        progress["lock_machine"] = True
        remember(
            project,
            outcome.get("status") or "gate",
            outcome.get("stage") or "停在门上",
            progress=progress,
        )
    except Exception as error:
        fail_project(project_id, error)
    finally:
        with project_lock(project_id):
            if _PIPELINE_HANDOFF.get(project_id) == "writer":
                _PIPELINE_HANDOFF[project_id] = "yielded"


def run_drill(project_id):
    token = _bind_job(project_id)
    try:
        _run_drill(project_id)
    finally:
        _JOB_PROJECT.reset(token)


def _run_drill(project_id):
    project = read_project(project_id)
    if project and (project.get("progress") or {}).get("lock_machine"):
        run_lock_turn(project_id)
        return
    folder = project_dir(project_id)
    debug = project_debug_llm(project)
    intent_stop = project_stops_at_intent(project)
    prompt_stop = project_stops_at_prompts(project)
    try:
        result = call_drill(
            project.get("messages") or [],
            debug_dir=folder / "debug" if (debug or intent_stop or prompt_stop) else None,
            model=project.get("model"),
            template=project.get("template"),
        )
        project = read_project(project_id) or project
        messages = list(project.get("messages") or [])
        turn = {"role": "assistant", "content": result["reply"]}
        if result.get("options"):
            turn["options"] = result["options"]
        messages.append(turn)
        project["messages"] = messages
        if result["phase"] == "ready" and (
            template_policy(project.get("template")) != "drama_still"
            or locks_ready(result.get("brief") or "")
        ):
            project["brief"] = result["brief"]
            if project_stops_at_intent(project):
                project["finished_at"] = now()
                remember(
                    project,
                    "done",
                    "Debug：已停在意图解析",
                    progress={
                        "percent": 100,
                        "phase": "intent_debug",
                        "message": "Debug：已停在意图解析。不写提示词，不生图。",
                        "debug_llm": False,
                        "debug_stop": "intent",
                    },
                )
                return
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
    stored = read_project(project_id)
    policy = template_policy((stored or {}).get("template"))
    spec = prepare_image_node(
        spec,
        locked_ids=[item["id"] for item in items],
        items=items,
        policy=policy,
    )
    spec = merge_intake(spec, items)
    aspect = ""
    if stored:
        aspect = read_locks(folder).get("aspect") or ""
    if aspect not in ("16:9", "9:16", "21:9"):
        aspect = locked_aspect((stored or {}).get("brief") or "")
    if aspect:
        spec = dict(spec)
        spec["aspect_ratio"] = aspect
    (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    with project_lock(project_id):
        fresh = read_project(project_id)
        if fresh is None:
            return None
        fresh["title"] = str(spec.get("title") or fresh.get("title") or "")
        fresh["script"] = str(spec.get("script") or "")
        fresh["storyboard"] = str(spec.get("storyboard") or "")
        fresh["image_prompts"] = str(spec.get("image_prompts") or "")
        fresh["tasks"] = task_plan(spec, soundtrack=project_soundtrack(fresh))
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


def finish_prompt_debug(project):
    project = read_project(project["id"]) or project
    project["finished_at"] = now()
    project["error"] = None
    for task in project.get("tasks") or []:
        task["state"] = "muted"
        task["detail"] = "Debug：不生图"
    remember(
        project,
        "done",
        "Debug：已停在提示词",
        progress={
            "percent": 100,
            "phase": "prompt_debug",
            "message": "Debug：已停在提示词。不生图。",
            "debug_llm": False,
            "debug_stop": "prompts",
        },
    )


WRITE_STAGES = (
    ("P1", "写剧本", 20),
    ("P2", "资产提示词", 36),
    ("P3", "分镜", 52),
    ("P4", "投喂稿", 68),
)


def _merge_spec(base, incoming):
    merged = dict(base or {})
    incoming = incoming or {}
    for key in ("title", "script", "storyboard", "image_prompts", "feed", "previs"):
        if str(incoming.get(key) or "").strip():
            merged[key] = incoming.get(key)
    if incoming.get("assets"):
        merged["assets"] = incoming["assets"]
    if incoming.get("shots"):
        merged["shots"] = incoming["shots"]
    return merged


def _stage_brief(base, stage, label, carried):
    lines = [str(base or "").strip(), "", "当前阶段：%s %s" % (stage, label)]
    if carried:
        lines.extend(["", "上一阶段正文：", carried])
    return "\n".join(lines).strip()


def _carried_text(spec):
    spec = spec or {}
    chunks = [
        str(spec.get("script") or "").strip(),
        str(spec.get("image_prompts") or "").strip(),
        str(spec.get("storyboard") or "").strip(),
        feed_text(spec),
    ]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _write_prompt_files(project_id, spec, brief):
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    feed = feed_text(spec)
    if not feed:
        raise RuntimeError("投喂稿是空的")
    (folder / "feed.md").write_text(feed + "\n", encoding="utf-8")
    (folder / "project.md").write_text(project_markdown(spec, brief), encoding="utf-8")
    return folder / "feed.md", folder / "project.md"


def finish_prompts_ready(project):
    project = read_project(project["id"]) or project
    project["finished_at"] = now()
    project["error"] = None
    for task in project.get("tasks") or []:
        task["state"] = "muted"
        task["detail"] = "停在提示词"
    remember(
        project,
        "done",
        "提示词已就绪",
        progress={
            "percent": 100,
            "phase": "prompts_ready",
            "message": "提示词已就绪。要出图出片，点按钮或发送「出图」。",
            "debug_llm": False,
        },
    )


def _store_aspect(project, aspect):
    brief = str(project.get("brief") or "")
    if re.search(r"画幅\s*[:：]", brief):
        brief = re.sub(
            r"(画幅\s*[:：]\s*)(?:21:9|16:9|9:16)",
            lambda match: match.group(1) + aspect,
            brief,
            count=1,
        )
    else:
        brief = (brief.rstrip() + "\n画幅：" + aspect).strip()
    project["brief"] = brief
    # #region agent log
    try:
        _kept = re.search(r"画幅\s*[:：]\s*(\S+)", brief)
        _line = json.dumps({"sessionId": "77976e", "runId": "post-fix", "hypothesisId": "G", "location": "end/server.py:_store_aspect", "message": "aspect stored", "data": {"aspect": aspect, "kept": _kept.group(1) if _kept else ""}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False)
        with open("/Users/shaneson/AiVideo/.cursor/debug-77976e.log", "a", encoding="utf-8") as _handle:
            _handle.write(_line + "\n")
    except Exception:
        pass
    # #endregion
    write_project(project)
    spec = load_spec(project["id"]) or {}
    spec["aspect_ratio"] = aspect
    folder = project_dir(project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec


def _ask_aspect(project):
    messages = list(project.get("messages") or [])
    messages.append(
        {
            "role": "assistant",
            "content": "这支片子还没有锁定画幅。选 16:9、9:16 或 21:9。没有锁定之前不会默认竖屏，也不会出图。",
            "options": ["16:9", "9:16", "21:9", "其他"],
        }
    )
    project["messages"] = messages
    project["finished_at"] = None
    project["error"] = None
    remember(
        project,
        "drilling",
        "先锁定画幅",
        progress={
            "percent": 100,
            "phase": "aspect_wait",
            "message": "先锁定画幅",
            "debug_llm": False,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _seedance_only(project):
    messages = list(project.get("messages") or [])
    messages.append({"role": "assistant", "content": "Seedance 只交付提示词。不出图。"})
    project["messages"] = messages
    write_project(project)
    return public_view(read_project(project["id"]) or project)


def _start_explicit_render(project):
    project_id = project["id"]
    spec = load_spec(project_id)
    if not isinstance(spec, dict):
        raise RpcError(409, "还没有提示词")
    locks = read_locks(project_dir(project_id))
    if (project.get("progress") or {}).get("lock_machine") and locks.get("model") not in ("", "h3"):
        return _seedance_only(project)
    aspect = locks.get("aspect") or locked_aspect(project.get("brief") or "") or locked_aspect(str(spec.get("aspect_ratio") or ""))
    if not aspect:
        return _ask_aspect(project)
    _store_aspect(project, aspect)
    project = read_project(project_id) or project
    project["tasks"] = task_plan(spec, soundtrack=project_soundtrack(project))
    project["error"] = None
    project["finished_at"] = None
    write_project(project)
    remember(
        project,
        "building",
        "出图出片",
        progress={
            "percent": 12,
            "phase": "building",
            "message": "出图出片",
            "debug_llm": project_debug_llm(project),
        },
    )
    if _yield_if_paused(project_id):
        return public_view(read_project(project_id) or project)
    with project_lock(project_id):
        if not is_running(project_id):
            _PIPELINE_HANDOFF[project_id] = "yielded"
            return public_view(read_project(project_id) or project)
        _PIPELINE_HANDOFF[project_id] = "render"
    start_render_thread(project_id, replace=True)
    return public_view(read_project(project_id) or project)


def run_project(project_id, debug_llm=False):
    token = _bind_job(project_id)
    try:
        _run_project(project_id, debug_llm=debug_llm)
    finally:
        _JOB_PROJECT.reset(token)


def _run_project(project_id, debug_llm=False):
    project = read_project(project_id)
    if project and (project.get("progress") or {}).get("lock_machine"):
        run_project_gated(project_id, debug_llm=debug_llm)
        return
    register_thread(project_id)
    with project_lock(project_id):
        _PIPELINE_HANDOFF[project_id] = "writer"
    project = read_project(project_id)
    folder = project_dir(project_id)
    prompt_stop = project_stops_at_prompts(project)
    debug = False if prompt_stop else want_debug_llm(debug_llm or project_debug_llm(project))
    try:
        writing = model_label(project.get("model")) + " 正在写剧本"
        if prompt_stop:
            writing += "（Debug：只到提示词）"
        progress = {
            "percent": 8,
            "phase": "writing",
            "message": writing,
            "debug_llm": debug,
        }
        if prompt_stop:
            progress["debug_stop"] = "prompts"
        remember(project, "writing", writing, progress=progress)
        template = project.get("template")
        brief = enrich_brief(project["brief"], load_intake(folder))
        debug_dir = folder / "debug" if (debug or prompt_stop) else None
        spec = {}
        for stage, label, percent in WRITE_STAGES:
            stage_brief = _stage_brief("" if stage != "P1" else brief, stage, label, _carried_text(spec))
            remember(
                read_project(project_id) or project,
                "writing",
                label,
                progress={
                    "percent": percent,
                    "phase": "writing",
                    "message": label,
                    "debug_llm": debug,
                    "debug_stop": "prompts" if prompt_stop else "",
                },
            )
            pack = load_skill_pack(template, stage_brief, stage=stage)
            written = call_deepseek(
                stage_brief,
                pack,
                debug_dir=debug_dir,
                model=project.get("model"),
                template=template,
            )
            spec = _merge_spec(spec, written)
            fresh = _save_written_spec(project_id, spec)
            if fresh is None or _yield_if_paused(project_id):
                return
        feed_path, markdown_path = _write_prompt_files(project_id, spec, brief)
        aspect = locked_aspect(brief)
        code, report = validate_feed(feed_path, aspect)
        if code != 0:
            remember(
                read_project(project_id) or project,
                "writing",
                "机检",
                progress={"percent": 82, "phase": "writing", "message": "机检", "debug_llm": debug},
            )
            repair_brief = _stage_brief("", "P4", "投喂稿", _carried_text(spec))
            repair_brief += "\n\n机检报告，按它重修一次，画幅和模型不要改：\n" + (report or "")[-8000:]
            pack = load_skill_pack(template, repair_brief, stage="P4")
            spec = _merge_spec(
                spec,
                call_deepseek(
                    repair_brief,
                    pack,
                    debug_dir=debug_dir,
                    model=project.get("model"),
                    template=template,
                ),
            )
            fresh = _save_written_spec(project_id, spec)
            if fresh is None or _yield_if_paused(project_id):
                return
            feed_path, markdown_path = _write_prompt_files(project_id, spec, brief)
            code, report = validate_feed(feed_path, aspect)
            if code != 0:
                raise RuntimeError("投喂稿机检未通过：%s" % ((report or "")[-800:]))
        remember(
            read_project(project_id) or project,
            "writing",
            "看板",
            progress={"percent": 92, "phase": "writing", "message": "看板", "debug_llm": debug},
        )
        build_board(markdown_path, project_dir(project_id) / "board.html")
        fresh = read_project(project_id)
        if fresh is None or _yield_if_paused(project_id):
            return
        if prompt_stop or project_stops_at_prompts(fresh):
            finish_prompt_debug(fresh)
            return
        finish_prompts_ready(fresh)
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
    progress = None
    if "投喂稿机检未通过" in str(error):
        progress = {"phase": "error", "message": "机检未通过"}
    remember(project, "error", "失败", progress=progress)


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
        if task.get("kind") in ("video", "join", "audio", "subtitles"):
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
    token = _bind_job(project_id)
    try:
        _execute_render(project_id, project=project)
    finally:
        _JOB_PROJECT.reset(token)


def _execute_render(project_id, project=None):
    from end.render_loop import register_thread

    register_thread(project_id)
    project = project or read_project(project_id)
    folder = project_dir(project_id)
    spec = load_spec(project_id)
    if not isinstance(spec, dict):
        raise RuntimeError("没有 spec，不能出片")
    images_only = project_debug_llm(project)
    if not project.get("tasks"):
        project["tasks"] = task_plan(spec, soundtrack=project_soundtrack(project))
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
        soundtrack=project_soundtrack(project),
        brief=project.get("brief") or "",
        template=project.get("template"),
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
        spec = load_spec(project_id)
        board_ready = bool(spec) and (folder / "board.html").is_file()
        explicit = paused_from in ("rendering", "building")
        action = "render"
        if not explicit:
            action = "ready" if board_ready else "rewrite"
        elif not spec:
            mark_user_pause(folder, project_id, "已暂停")
            raise RuntimeError("提示词还没写完，不能继续出片")
        else:
            aspect = locked_aspect(fresh.get("brief") or "") or locked_aspect(str(spec.get("aspect_ratio") or ""))
            if not aspect:
                action = "aspect"
            else:
                _PIPELINE_HANDOFF[project_id] = "render"
                fresh["error"] = None
    if action == "ready":
        return finish_prompts_ready(fresh)
    if action == "rewrite":
        submit_agent_task(Task("write", project_id, {"debug_llm": project_debug_llm(fresh)}))
        return read_project(project_id) or fresh
    if action == "aspect":
        return _ask_aspect(fresh)
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
        from end.trace import begin_trace, finish_trace

        prompts = begin_trace(folder, "vision", repair=False, brief="读图")
        token = _bind_job(project_id)
        try:
            if prompts:
                reading = read_upload(image_path, trace_prompts=prompts)
            else:
                reading = read_upload(image_path)
        except Exception as error:
            if prompts:
                finish_trace(folder, {}, error=str(error))
            raise
        finally:
            _JOB_PROJECT.reset(token)
        material["reading"] = reading
        if prompts:
            finish_trace(
                folder,
                {"readings": [{"id": material["id"], "reading": reading}]},
            )
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
    from end.qwen_env import read_qwen_env
    from end.trace import trace_enabled

    return {
        "debug_llm": env_debug_llm(),
        "debug_llm_env": "AIVIDEO_DEBUG_LLM",
        "debug_trace": trace_enabled(),
        "debug_trace_env": "AIVIDEO_DEBUG_TRACE",
        "qwen_env": read_qwen_env(),
        "model": default_model(),
        "models": model_choices(),
        "template": normalize_template(None),
        "templates": template_choices(),
    }


def rpc_set_qwen_env(params):
    from end.qwen_env import write_qwen_env

    try:
        name = write_qwen_env((params or {}).get("qwen_env"))
    except ValueError as error:
        raise RpcError(400, str(error))
    return {"qwen_env": name}


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
    stop = normalize_debug_stop(params.get("debug_stop"))
    debug = want_debug_llm(flag)
    if stop in ("intent", "prompts"):
        debug = False
    elif debug:
        stop = "images"
    soundtrack = normalize_soundtrack(params.get("soundtrack"))
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
            "debug_stop": stop,
            "soundtrack": soundtrack,
            "lock_machine": True,
        },
        "cloud_log": "",
        "prompt_id": "",
        "video_file": "",
        "model": normalize_model(params.get("model")),
        "template": normalize_template(params.get("template")),
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
    write_project(project)
    project = read_project(project["id"]) or project
    if _phase(project) == "lock_wait":
        return answer_lock(project, text)
    if project.get("status") == "gate":
        return answer_gate(project, text)
    if _phase(project) == "aspect_wait":
        picked = text if text in ("16:9", "9:16", "21:9") else locked_aspect(text)
        if not picked:
            messages.append(
                {
                    "role": "assistant",
                    "content": "先选一个画幅：16:9、9:16 或 21:9。",
                    "options": ["16:9", "9:16", "21:9", "其他"],
                }
            )
            project["messages"] = messages
            write_project(project)
            return public_view(project)
        _store_aspect(project, picked)
        project = read_project(project["id"]) or project
        if wants_render(text):
            return _start_explicit_render(project)
        finish_prompts_ready(project)
        return public_view(read_project(project["id"]))
    if prompts_ready(project):
        if wants_render(text):
            return _start_explicit_render(project)
        messages.append(
            {
                "role": "assistant",
                "content": "提示词已经就绪。要出图出片，点按钮或发送「出图」。",
            }
        )
        project["messages"] = messages
        write_project(project)
        return public_view(project)
    start_drill(project)
    return public_view(read_project(project["id"]))


def rpc_render(params):
    project = _request_project(params)
    if not prompts_ready(project) and not (
        project.get("status") == "drilling" and _phase(project) == "aspect_wait"
    ):
        raise RpcError(409, "提示词还没就绪")
    return _start_explicit_render(project)


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


def rpc_replay(params):
    """Restore one saved process and start the next stage."""
    from end.stage_runner import _GATE_LABEL, _GATE_PHASE, _PERCENT, _gate_copy
    from end.trace import restore_trace

    project = _request_project(params)
    if project.get("status") in ("writing", "building", "rendering"):
        raise RpcError(409, "这一步还在写，完成后再重放")
    trace_id = str((params or {}).get("trace_id") or "")
    folder = project_dir(project["id"])
    with project_lock(project["id"]):
        fresh = read_project(project["id"])
        if fresh is None:
            raise RpcError(404, "没有这个项目")
        if fresh.get("status") in ("writing", "building", "rendering"):
            raise RpcError(409, "这一步还在写，完成后再重放")
        locks = read_locks(folder)
        pipeline = dict((fresh.get("progress") or {}).get("pipeline") or {})
        try:
            plan = restore_trace(folder, trace_id, locks, pipeline.get("only") or "")
        except ValueError as error:
            raise RpcError(400, str(error))
        spec = load_spec(fresh["id"]) or {}
        fresh["script"] = str(spec.get("script") or "")
        fresh["storyboard"] = str(spec.get("storyboard") or "")
        fresh["image_prompts"] = str(spec.get("image_prompts") or "")
        if spec.get("title"):
            fresh["title"] = str(spec.get("title"))
        fresh["error"] = None
        fresh["finished_at"] = None
        messages = list(fresh.get("messages") or [])
        pipeline["accepted"] = list(plan.get("accepted") or [])
        pipeline["current"] = plan.get("current") or ""
        pipeline["action"] = plan.get("action") or "wait"
        if plan.get("script_from"):
            pipeline["script_from"] = plan["script_from"]
        else:
            pipeline.pop("script_from", None)
        pipeline["passed"] = True
        pipeline["report"] = ""
        pipeline["repair"] = ""
        pipeline["retry_write"] = False
        pipeline["repairs"] = 0
        pipeline["check"] = ""
        pipeline["revise_note"] = ""
        kept_progress = fresh.get("progress") or {}
        if plan.get("action") == "write":
            messages.append(
                {
                    "role": "assistant",
                    "content": "已恢复「%s」。接下来%s。" % (plan.get("label") or "这一次", plan.get("write_label") or "写下一段"),
                }
            )
            fresh["messages"] = messages
            gate_for(fresh["id"]).resume()
            remember(
                fresh,
                "writing",
                plan.get("write_label") or "下一段",
                progress={
                    "percent": _PERCENT.get(plan.get("next") or "", 50),
                    "phase": "writing",
                    "message": plan.get("write_label") or "下一段",
                    "lock_machine": True,
                    "pipeline": pipeline,
                    "question": None,
                    "debug_llm": bool(kept_progress.get("debug_llm")),
                    "debug_stop": kept_progress.get("debug_stop") or "",
                },
            )
            submit_agent_task(Task("write", fresh["id"], {"debug_llm": bool(kept_progress.get("debug_llm"))}))
        else:
            gate_stage = plan.get("stage") if plan.get("stage") in _GATE_PHASE else "script"
            reply, options = _gate_copy(gate_stage, True, "", 0)
            messages.append(
                {
                    "role": "assistant",
                    "content": "已恢复「%s」。%s" % (plan.get("label") or "这一次", reply),
                    "options": list(options or []),
                }
            )
            fresh["messages"] = messages
            remember(
                fresh,
                "gate",
                _GATE_LABEL.get(gate_stage, "停在门上"),
                progress={
                    "percent": _PERCENT.get(gate_stage, 76),
                    "phase": _GATE_PHASE.get(gate_stage, "gate_fail"),
                    "message": reply,
                    "lock_machine": True,
                    "pipeline": pipeline,
                    "question": None,
                    "debug_llm": bool(kept_progress.get("debug_llm")),
                    "debug_stop": kept_progress.get("debug_stop") or "",
                },
            )
    return public_view(read_project(project["id"]) or project)


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
    if method == "config.set_qwen_env":
        return rpc_set_qwen_env(params)
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
    if method == "projects.render":
        return rpc_render(params)
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
    if method == "projects.replay":
        return rpc_replay(params)
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
        if path.startswith("/media/projects/") and path.endswith("/board"):
            project_id = path.split("/")[3]
            board = project_dir(project_id) / "board.html"
            if not valid_id(project_id) or not board.is_file():
                self._send(404, {"error": "还没有看板"})
                return
            self._send(200, board.read_bytes(), "text/html; charset=utf-8")
            return
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
            raw_name = parts[4]
            name = unquote(raw_name)
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
    from end.ai_client import endpoint

    connect()
    DATA.mkdir(parents=True, exist_ok=True)
    ai_url = endpoint()
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
        "AiVideo http://%s:%s  (sqlite %s)  ai=%s  agent_workers=%s%s"
        % (HOST, PORT, ROOT / "data" / "aivideo.db", ai_url, agent_queue().workers, note)
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
