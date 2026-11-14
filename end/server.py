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

from end.product_names import (
    DRAFT_NAME,
    EMPTY_DRAFT,
    MACHINE_BLOCKED,
    MACHINE_GATE,
    STAGE_NAME,
    report_says_empty_draft,
)

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
from end.crew import attach_crew
from end.db import DATA, connect, delete_project, list_projects, project_dir, read_project, write_project
from end.image_store import get_image_store
from end.render_loop import (
    _ffmpeg_bin,
    _unlink_render_output,
    gate_for,
    is_running,
    load_run_state,
    lock_asset_still,
    mark_user_pause,
    note_local_image,
    output_paths,
    project_lock,
    register_thread,
    release_asset_for_generation,
    reset_current_step,
    resume_paused,
    run_local_pipeline,
    save_run_state,
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
    _PERCENT,
    accept_pipeline,
    auto_gate_action,
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
from end.workflow_from_spec import _safe_id, task_plan
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


def _public_semantic(project, spec):
    from end.script_review import public_semantic

    try:
        return public_semantic(project_dir(project["id"]), spec, project.get("brief") or "")
    except Exception as error:
        return {"script_status": "failed", "script_reason": str(error), "confirmed": False, "delivery_status": "failed"}


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
            for key in ("prompt_diff", "retouching", "retouch_error", "video_submit_mode"):
                if key in stored:
                    if stored.get(key):
                        project["progress"][key] = stored.get(key)
                elif prev.get(key):
                    project["progress"][key] = prev.get(key)
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


def _storyboard_question(project):
    """Expose 补写分镜 or 补全 on the existing question. Does not write or call a model."""
    question = (project.get("progress") or {}).get("question") or None
    if not isinstance(question, dict):
        return question
    progress = project.get("progress") or {}
    pipeline = progress.get("pipeline") or {}
    if project.get("status") != "gate" or pipeline.get("passed"):
        return question
    if pipeline.get("current") != "storyboard":
        return question
    from end.storyboard_contract import COMPLETE, FILL, needs_human

    report = str(pipeline.get("report") or "")
    if not needs_human(report):
        return question
    shown = dict(question)
    folder = project_dir(project["id"])
    if report.strip() == "分镜是空的" or not storyboard_has_draft(folder):
        shown["options"] = [FILL]
    else:
        shown["options"] = [COMPLETE]
    return shown


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
    from end.storyboard_contract import COMPLETE, FILL, needs_human

    report = str(pipeline.get("report") or "")
    if pipeline.get("current") == "storyboard" and needs_human(report):
        folder = project_dir(project["id"])
        if report.strip() == "分镜是空的" or not storyboard_has_draft(folder):
            options = [FILL]
        else:
            options = [COMPLETE]
    elif (
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
        from end.shot_contract import annotate_shots
        from end.workflow_from_spec import assemble_steps

        assets, shots, sources = assemble_steps(spec)
        shots = annotate_shots(shots, sources, folder, project["id"])
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
        "semantic": _public_semantic(project, spec),
        "progress": attach_crew(project),
        "question": _storyboard_question(project),
        "semantic_scope": str(((project.get("progress") or {}).get("pipeline") or {}).get("semantic_scope") or ""),
        "semantic_status": str(((project.get("progress") or {}).get("pipeline") or {}).get("semantic_status") or ""),
        "delivery_semantic_review": "retired",
        "delivery_blocks_render": False,
        "generation_hold": _generation_hold(project),
        "render_recovery": "projects.render" if _explicit_render_recovery(project) else "",
        "option_effects": _option_effects(project),
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
        pipeline, _action = accept_pipeline(
            pipeline,
            read_locks(folder),
            folder=folder,
            brief=project.get("brief") or "",
        )
        label = MACHINE_GATE if pipeline.get("action") == "finish" else "下一段"
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


def _enqueue_script_repair(project, pipeline):
    """Semantic script revision. Not the generic full-stage rewrite."""
    remember(
        project,
        "writing",
        "剧本修订",
        progress={
            "percent": int((project.get("progress") or {}).get("percent") or 40),
            "phase": "writing",
            "message": "剧本修订",
            "lock_machine": True,
            "pipeline": pipeline,
            "question": None,
        },
    )
    submit_agent_task(Task("script_repair", project["id"], {"model": project.get("model") or ""}))


def _present_script_candidate(project, row):
    from end.script_repair import candidate_question

    reply, options = candidate_question(row)
    question = {"id": "script_candidate", "kind": "single", "reply": reply, "options": options, "cards": []}
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline["action"] = "wait"
    pipeline["current"] = "script"
    pipeline["repair"] = ""
    remember(
        project,
        "gate",
        "剧本候选",
        progress={
            "percent": 48,
            "phase": "gate_script",
            "message": reply.split("\n", 1)[0],
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _answer_script_candidate(project, body, brief):
    from end.script_repair import MAX_ATTEMPTS, load_candidate, promote_candidate

    folder = project_dir(project["id"])
    if body == "采用这版剧本":
        ok, reason = promote_candidate(folder, brief or project.get("brief") or "", project_id=project["id"])
        if not ok:
            return _repeat_gate(project, reason)
        fresh = read_project(project["id"]) or project
        return _present_script_gate(fresh, force=True)
    if body == "再修一次":
        from end.script_repair import repair_budget_open

        if not repair_budget_open(folder, brief or project.get("brief") or ""):
            return _repeat_gate(project, "同一原稿和问题的修订已到上限，正式剧本没有改")
        pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
        pipeline["action"] = "script_repair"
        pipeline["current"] = "script"
        pipeline["repair"] = ""
        _enqueue_script_repair(project, pipeline)
        return public_view(read_project(project["id"]) or project)
    if body == "保持原稿":
        from end.script_repair import repair_budget_open

        reply = "保持原稿。正式剧本没有改。"
        options = ["根据建议修改"]
        if not repair_budget_open(folder, brief or project.get("brief") or ""):
            reply += "这一原稿和问题的模型修订次数已用完，不会因此清零。"
            saved = str((load_candidate(folder) or {}).get("rejected_text") or "").strip()
            options = ["查看未采用候选"] if saved else []
            if saved:
                reply += "未采用的候选还在，可以查看。"
        question = {
            "id": "script",
            "kind": "single",
            "reply": reply,
            "options": options,
            "cards": [],
        }
        messages = list(project.get("messages") or [])
        messages.append(question_message(question))
        project["messages"] = messages
        pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
        pipeline["action"] = "wait"
        pipeline["current"] = "script"
        pipeline["repair"] = ""
        remember(
            project,
            "gate",
            "剧本",
            progress={
                "percent": 48,
                "phase": "gate_script",
                "message": reply.split("\n", 1)[0],
                "lock_machine": True,
                "pipeline": pipeline,
                "question": question,
            },
        )
        return public_view(read_project(project["id"]) or project)
    return _repeat_gate(project, "这一步只接受采用候选或保持原稿")


def run_script_repair(project_id):
    """Semantic repair entry. It stores a candidate and does not render."""
    from end.script_repair import default_complete, run_script_repair as propose

    project = read_project(project_id)
    if project is None:
        return
    folder = project_dir(project_id)
    try:
        row = propose(
            folder,
            project.get("brief") or "",
            default_complete(project.get("model") or ""),
            model=project.get("model") or "",
        )
    except Exception as error:
        fail_project(project_id, error)
        return
    fresh = read_project(project_id) or project
    _present_script_candidate(fresh, row)


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


def _repeat_gate(project, reason):
    """Show the current gate again. Does not delete drafts or media."""
    progress = dict(project.get("progress") or {})
    question = dict(progress.get("question") or {})
    reply = str(reason or "还不能继续")
    if question.get("reply"):
        reply = reply + "\n" + question["reply"]
    question["reply"] = reply
    message = question_message(question)
    messages = list(project.get("messages") or [])
    messages.append(message)
    project["messages"] = messages
    progress["question"] = question
    progress["message"] = reply.split("\n", 1)[0]
    phase = progress.get("phase") or "gate_script"
    if not str(phase).startswith("gate_"):
        phase = "gate_script"
    progress["phase"] = phase
    remember(project, "gate", project.get("stage") or "停在门上", progress=progress)
    return public_view(read_project(project["id"]) or project)


def _present_script_gate(project, force=False):
    """Format gate plus semantic review. The script text is not rewritten."""
    from end.script_review import apply_script_review_gate
    from end.stage_runner import gate_result

    project = read_project(project["id"]) or project
    folder = project_dir(project["id"])
    spec = load_spec(project["id"]) or {}
    text = str(spec.get("script") or project.get("script") or "").strip()
    script_path = folder / "_内部_" / "剧本.md"
    if text and not script_path.is_file():
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(text + "\n", encoding="utf-8")
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline.setdefault("only", "")
    pipeline.setdefault("accepted", [])
    pipeline["current"] = "script"
    pipeline["action"] = "wait"
    pipeline["passed"] = True
    outcome = gate_result("script", True, "", pipeline)
    outcome = apply_script_review_gate(
        folder,
        spec,
        read_locks(folder),
        project.get("brief") or "",
        outcome,
        force=force,
    )
    previous = project.get("progress") or {}
    outcome["progress"]["lock_machine"] = True
    outcome["progress"]["pipeline"]["semantic_scope"] = "script"
    if previous.get("debug_stop"):
        outcome["progress"]["debug_stop"] = previous.get("debug_stop")
    outcome["progress"]["debug_llm"] = bool(previous.get("debug_llm"))
    _settle_writer_outcome(project, outcome)
    return read_project(project["id"]) or project


def _hold_technical(project, decision):
    """Stop an explicit render on a file or prompt check. No semantic model and no new render."""
    from end.script_review import public_semantic

    project = read_project(project["id"]) or project
    folder = project_dir(project["id"])
    owner = decision.get("owner") or "feed"
    if owner not in ("script", "asset", "storyboard", "feed"):
        owner = "feed"
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline.pop("semantic_scope", None)
    pipeline["current"] = owner
    pipeline["action"] = "wait"
    pipeline["passed"] = False
    pipeline["retry_write"] = False
    pipeline["check"] = "machine"
    pipeline["generation_hold"] = "technical"
    pipeline["semantic_status"] = "failed"
    pipeline["semantic_owner"] = owner
    pipeline["report"] = decision.get("reason") or ""
    pipeline["repair"] = pipeline["report"]
    reply = "出图前的技术校验没过。没有调用出片语义审查，也没有继续出图。\n" + (decision.get("reason") or "")
    options = ["根据建议修改"]
    question = {"id": owner, "kind": "single", "reply": reply, "options": options, "cards": []}
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    project["finished_at"] = None
    project["error"] = None
    progress = dict(project.get("progress") or {})
    progress.pop("semantic_scope", None)
    remember(
        project,
        "gate",
        "技术校验",
        progress={
            "percent": int(progress.get("percent") or 80),
            "phase": "gate_machine",
            "message": reply.split("\n", 1)[0],
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
            "semantic": public_semantic(folder, load_spec(project["id"]), project.get("brief") or ""),
        },
    )
    return public_view(read_project(project["id"]) or project)


def _pipeline_of(project, pipeline=None):
    if pipeline is not None:
        return pipeline
    return (project.get("progress") or {}).get("pipeline") or {}


def _retired_delivery_gate(project, pipeline=None):
    pipeline = _pipeline_of(project, pipeline)
    scope = str(pipeline.get("semantic_scope") or (project.get("progress") or {}).get("semantic_scope") or "")
    if scope != "delivery":
        return False
    if str(pipeline.get("check") or "") == "machine":
        return False
    if (project.get("progress") or {}).get("phase") == "gate_machine":
        return False
    if str(pipeline.get("generation_hold") or "") == "technical":
        return False
    return project.get("status") == "gate"


def _technical_generation_hold(project, pipeline=None):
    pipeline = _pipeline_of(project, pipeline)
    if project.get("status") != "gate":
        return False
    return str(pipeline.get("generation_hold") or "") == "technical"


def _generation_hold(project):
    if _technical_generation_hold(project):
        return "technical"
    return ""


def _explicit_render_recovery(project):
    return _retired_delivery_gate(project) or _technical_generation_hold(project)


def _message_options(project):
    labels = []
    for message in project.get("messages") or []:
        if not isinstance(message, dict):
            continue
        for option in message.get("options") or []:
            text = " ".join(str(option or "").split())
            if text and text not in labels:
                labels.append(text)
    question = (project.get("progress") or {}).get("question") or {}
    if isinstance(question, dict):
        for option in question.get("options") or []:
            text = " ".join(str(option or "").split())
            if text and text not in labels:
                labels.append(text)
    return labels


def _option_effects(project):
    reject = {
        "effect": "reject",
        "reason": "delivery_review_retired",
        "render": False,
        "recovery": "projects.render",
    }
    if _retired_delivery_gate(project):
        effects = {}
        for label in _message_options(project) + ["再审一次", "接受可疑项并出片", "根据建议修改", "继续", "接受", "确认"]:
            effects.setdefault(label, dict(reject))
        effects["*"] = dict(reject)
        return effects
    pipeline = _pipeline_of(project)
    scope = str(pipeline.get("semantic_scope") or (project.get("progress") or {}).get("semantic_scope") or "")
    if _technical_generation_hold(project):
        return {
            "再审一次": {
                "effect": "reject",
                "reason": "technical_hold",
                "render": False,
                "recovery": "projects.render",
            },
            "接受可疑项并出片": dict(reject),
            "根据建议修改": {"effect": "technical_rewrite", "render": False},
        }
    if project.get("status") == "gate" and (
        str(pipeline.get("check") or "") == "machine"
        or (project.get("progress") or {}).get("phase") == "gate_machine"
    ):
        effects = {"根据建议修改": {"effect": "technical_rewrite", "render": False}}
        if scope == "delivery":
            effects["再审一次"] = dict(reject)
            effects["接受可疑项并出片"] = dict(reject)
        return effects
    return {}


def _reject_retired_delivery(project, label):
    raise RpcError(
        409,
        "出图前的语义审查已经取消。「%s」不会再审，也不会出图。要生成请明确调用 projects.render。" % label,
        {
            "reason": "delivery_review_retired",
            "semantic_scope": "delivery",
            "render": False,
            "recovery": "projects.render",
            "option": label,
        },
    )


def _rereview_semantic(project, pipeline):
    scope = str((pipeline or {}).get("semantic_scope") or (project.get("progress") or {}).get("semantic_scope") or "script")
    if scope == "delivery":
        return _reject_retired_delivery(project, "再审一次")
    from end.script_review import content_hash, script_is_settled, story_context
    from end.script_repair import official_script

    folder = project_dir(project["id"])
    official = official_script(folder)
    if official and script_is_settled(folder, content_hash(official), content_hash(story_context(folder, project.get("brief") or ""))):
        return _repeat_gate(project, "正文和原要求没变，不再审这一稿。")
    return _present_script_gate(project, force=True)


def answer_gate(project, text):
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    current = pipeline.get("current") or ""
    body = " ".join(str(text or "").split())
    passed = bool(pipeline.get("passed"))
    question = progress.get("question") or {}
    if _retired_delivery_gate(project, pipeline):
        return _reject_retired_delivery(project, body)
    if question.get("id") == "script_revise" and body not in ("继续", "接受", "确认"):
        note = "" if body == "其他" else body
        if not note:
            return _ask_script_revise(project, pipeline)
        pipeline["revise_note"] = note
        pipeline["action"] = "rewrite"
        pipeline["current"] = "script"
        pipeline["repairs"] = 0
        pipeline["auto_retries"] = 0
        _enqueue_stage(project, pipeline, "剧本")
        return public_view(read_project(project["id"]) or project)
    folder = project_dir(project["id"])
    brief = project.get("brief") or ""
    if body == "查看未采用候选":
        from end.script_repair import load_candidate

        return _present_script_candidate(project, load_candidate(folder))
    if body == "再审一次":
        if _retired_delivery_gate(project, pipeline):
            return _reject_retired_delivery(project, "再审一次")
        if _technical_generation_hold(project, pipeline):
            raise RpcError(
                409,
                "这是技术校验停点。「再审一次」不会调用语义审查，也不会出图。",
                {"reason": "technical_hold", "render": False, "technical": True, "recovery": "projects.render", "option": "再审一次"},
            )
        return _rereview_semantic(project, pipeline)
    explicit_script_accept = False
    if body == "接受可疑项并继续" and current == "script":
        from end.script_review import confirm_script

        ok, reason = confirm_script(folder, accept_suspects=True, brief=brief)
        if not ok:
            return _repeat_gate(project, reason)
        explicit_script_accept = True
        body = "继续"
        passed = True
    if (
        body == ASSET_REVISE
        and current == "script"
        and pipeline.get("semantic_scope") == "script"
        and pipeline.get("semantic_status") in ("suspect", "blocked", "failed")
    ):
        from end.script_review import load_reviews, save_reviews

        state = load_reviews(folder)
        state.pop("confirmed_script_hash", None)
        state.pop("confirmed_context_hash", None)
        row = dict(state.get("script") or {})
        for key in ("accepted_suspects", "accepted_by", "accepted_script_hash", "accepted_context_hash"):
            row.pop(key, None)
        if row:
            state["script"] = row
        from end.script_repair import repair_budget_open

        if not str(brief or "").strip():
            return _repeat_gate(project, "缺少创作要求，不能判断主旨，需要人工确认")
        if not repair_budget_open(folder, brief):
            return _repeat_gate(project, "同一原稿和问题的修订已到上限。正式剧本没有改。")
        save_reviews(folder, state)
        pipeline["passed"] = False
        pipeline["action"] = "script_repair"
        pipeline["current"] = "script"
        pipeline["retry_write"] = False
        pipeline["repairs"] = 0
        pipeline["auto_retries"] = 0
        pipeline["machine_repairs"] = 0
        pipeline["report"] = str(pipeline.get("report") or (question.get("reply") if isinstance(question, dict) else "") or "")
        pipeline["repair"] = ""
        _enqueue_script_repair(project, pipeline)
        return public_view(read_project(project["id"]) or project)
    if question.get("id") == "script_candidate":
        return _answer_script_candidate(project, body, brief)
    if body == "接受可疑项并出片":
        return _reject_retired_delivery(project, "接受可疑项并出片")
    if body == ASSET_REVISE and _retired_delivery_gate(project, pipeline):
        return _reject_retired_delivery(project, ASSET_REVISE)
    if question.get("id") == "asset_repair_choice":
        return _take_asset_choice(project, pipeline, body, question)
    if body == "精修" and current == "script" and passed:
        pipeline["action"] = "wait"
        return _ask_script_revise(project, pipeline)
    from end.storyboard_contract import needs_human

    if (
        current == "storyboard"
        and not passed
        and body in ("补写分镜", "补全", ASSET_REVISE)
        and needs_human(str(pipeline.get("report") or ""))
    ):
        report = str(pipeline.get("report") or "")
        named = bool(re.search(r"S\d+\s*缺", report))
        pipeline["retry_write"] = False
        pipeline["repairs"] = 0
        pipeline["auto_retries"] = 0
        pipeline["machine_repairs"] = 0
        pipeline.pop("worker_exhausted", None)
        if named and body in ("补全", ASSET_REVISE):
            pipeline["action"] = "rewrite"
            pipeline["passed"] = False
            pipeline["repair"] = report
            pipeline["report"] = report
            _enqueue_stage(project, pipeline, "补全分镜")
            return public_view(read_project(project["id"]) or project)
        pipeline["action"] = "write"
        pipeline["passed"] = True
        pipeline["repair"] = ""
        pipeline["report"] = ""
        _enqueue_stage(project, pipeline, body if body in ("补写分镜", "补全") else "补写分镜")
        return public_view(read_project(project["id"]) or project)
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
        or report_says_empty_draft(report_text)
        or "预演故事板是空的" in report_text
    ):
        # Missing assets.md cannot be figure-repaired; rewrite the whole stage.
        pipeline["retry_write"] = True
    if not passed and pipeline.get("retry_write"):
        pipeline["action"] = "write"
        pipeline["retry_write"] = False
        pipeline["passed"] = True
        pipeline["repair"] = ""
        pipeline["report"] = ""
        pipeline["repairs"] = 0
        pipeline["auto_retries"] = 0
        pipeline["machine_repairs"] = 0
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
        pipeline["repairs"] = 0
        pipeline["auto_retries"] = 0
        pipeline["machine_repairs"] = 0
        pipeline.pop("repair_choices", None)
        pipeline.pop("repair_round", None)
        pipeline["action"] = "rewrite"
        pipeline["repair"] = pipeline.get("report") or pipeline.get("repair") or ""
        _enqueue_stage(project, pipeline, project.get("stage") or "重写这一段")
        return public_view(read_project(project["id"]) or project)
    folder, locks = _lock_inputs(project)
    if (
        current == "script"
        and pipeline.get("semantic_scope") == "script"
        and pipeline.get("semantic_status") == "suspect"
        and body in ("继续", "接受", "确认")
        and not explicit_script_accept
    ):
        return _repeat_gate(project, "有可疑项。普通继续不能代替「接受可疑项并继续」。")
    if current == "script" and passed and body in ("继续", "接受", "确认") and not explicit_script_accept:
        from end.script_review import confirm_script

        ok, reason = confirm_script(folder, accept_suspects=False, brief=brief)
        if not ok:
            return _repeat_gate(project, reason)
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
    pipeline, _action = accept_pipeline(pipeline, locks, folder=folder, brief=brief)
    if pipeline.get("action") == "wait" and pipeline.get("current") == "script":
        return _repeat_gate(project, pipeline.get("report") or "剧本还不能进入下游")
    label = MACHINE_GATE if pipeline.get("action") == "finish" else "下一段"
    _enqueue_stage(project, pipeline, label)
    return public_view(read_project(project["id"]) or project)


_AUTO_LABEL = {
    "recon": "对标情报",
    "script": "剧本",
    "asset": "资产锁定",
    "storyboard": "分镜",
    "feed": "提示词封装",
}
_AUTO_CHAIN_LIMIT = 24


def _mark_writing(project, pipeline, label):
    previous = project.get("progress") or {}
    progress = {
        "percent": _PERCENT.get(pipeline.get("current") or "", int(previous.get("percent") or 12)),
        "phase": "writing",
        "message": label,
        "lock_machine": True,
        "pipeline": pipeline,
        "question": None,
        "debug_llm": bool(previous.get("debug_llm")),
        "debug_stop": previous.get("debug_stop") or "",
    }
    remember(project, "writing", label, progress=progress)
    return read_project(project["id"]) or project


def _settle_writer_outcome(project, outcome):
    project = read_project(project["id"]) or project
    folder = project_dir(project["id"])
    if outcome.get("terminal") == "ready":
        if (read_locks(folder).get("model") or "") != "h3":
            note = "提示词已就绪。Seedance 只交付提示词。"
        else:
            note = "提示词已就绪。要出图出片，点按钮或发送「出图」。"
        messages = list(project.get("messages") or [])
        messages.append({"role": "assistant", "content": note})
        project["messages"] = messages
        write_project(project)
        if project_stops_at_prompts(project):
            finish_prompt_debug(project)
            return
        finish_prompts_ready(project)
        return
    if outcome.get("message"):
        messages = list(project.get("messages") or [])
        messages.append(outcome["message"])
        project["messages"] = messages
    progress = dict(outcome.get("progress") or {})
    progress["lock_machine"] = True
    remember(
        project,
        outcome.get("status") or "gate",
        outcome.get("stage") or "停在门上",
        progress=progress,
    )


_DRAFT_NAME = {"asset": "assets.md", "storyboard": "分镜.md", "feed": "feed.md"}
_SPEC_FIELD = {"asset": "image_prompts", "storyboard": "storyboard", "feed": "feed"}


def _draft_path(folder, stage):
    name = _DRAFT_NAME.get(stage)
    if not name:
        return None
    return Path(folder) / "_内部_" / name


def _sync_spec_field(folder, stage, text):
    key = _SPEC_FIELD.get(stage)
    path = Path(folder) / "spec.json"
    if not key or not path.is_file():
        return
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(spec, dict):
        return
    spec[key] = str(text or "").strip()
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")


def _recheck_after_rules(folder, locks, pipeline, path, title):
    stage = str(pipeline.get("current") or "")
    if str(pipeline.get("check") or "") == "machine" and stage == "feed":
        from end.stage_runner import check_prompt

        code, report = check_prompt(path, (locks or {}).get("model") or "h3", (locks or {}).get("track") or "1")
        return code == 0, report or ""
    from end.stage_runner import check_stage

    return check_stage(folder, stage, locks, title or "")


def _rule_repair(project, folder, locks, pipeline):
    """Apply matching rules and re-check. None when no rule changed the draft.

    A rule that clears the failure does not increment ``repairs``. A rule that
    leaves a failure updates the report and still leaves the AI attempt unused.
    """
    stage = str(pipeline.get("current") or "")
    if stage not in _DRAFT_NAME or pipeline.get("passed"):
        return None
    report = str(pipeline.get("report") or pipeline.get("repair") or "")
    path = _draft_path(folder, stage)
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    from end.repair_rules import run_rules

    updated, applied = run_rules(
        stage,
        report,
        text,
        {"folder": str(folder), "locks": locks or {}, "stage": stage, "report": report},
    )
    if not applied:
        return None
    body = updated if updated.endswith("\n") else updated + "\n"
    path.write_text(body, encoding="utf-8")
    _sync_spec_field(folder, stage, body)
    pipeline = dict(pipeline)
    pipeline["rule_repairs"] = int(pipeline.get("rule_repairs") or 0) + len(applied)
    passed, new_report = _recheck_after_rules(folder, locks, pipeline, path, project.get("title") or "")
    if passed:
        pipeline["passed"] = True
        pipeline["report"] = ""
        pipeline["repair"] = ""
        pipeline["check"] = ""
        return pipeline
    pipeline["passed"] = False
    pipeline["report"] = new_report or report
    pipeline["repair"] = pipeline["report"]
    return pipeline


def _follow_auto_gate(project, folder, locks, outcome):
    """Chain the next write for automatic stages. None leaves the human gate in place.

    A human acceptance queues one stage. That stage may still use its existing
    repair budget, then stops on its own gate. It does not start the next stage.
    """
    action = auto_gate_action(outcome)
    pipeline = dict(((outcome.get("progress") or {}).get("pipeline")) or {})
    if pipeline.get("human_release") and action in ("stop", "advance"):
        pipeline.pop("human_release", None)
        progress = dict(outcome.get("progress") or {})
        progress["pipeline"] = pipeline
        outcome["progress"] = progress
        return None
    if action == "stop":
        return None
    current = str(pipeline.get("current") or "")
    if action == "retry_write":
        pipeline["auto_retries"] = int(pipeline.get("auto_retries") or 0) + 1
        pipeline["action"] = "write"
        pipeline["retry_write"] = False
        pipeline["passed"] = True
        pipeline["repair"] = ""
        pipeline["report"] = ""
        pipeline["check"] = ""
        pipeline["repairs"] = 0
        return _mark_writing(project, pipeline, _AUTO_LABEL.get(current, "再写这一段"))
    if action == "rewrite":
        ruled = _rule_repair(project, folder, locks, pipeline)
        if ruled is not None:
            pipeline = ruled
            if ruled.get("passed"):
                action = "advance"
        if action == "rewrite":
            if str(pipeline.get("check") or "") == "machine":
                pipeline["machine_repairs"] = int(pipeline.get("machine_repairs") or 0) + 1
                pipeline["repairs"] = 0
            pipeline["action"] = "rewrite"
            pipeline["repair"] = pipeline.get("report") or pipeline.get("repair") or ""
            pipeline["check"] = ""
            pipeline["retry_write"] = False
            pipeline.pop("repair_choices", None)
            pipeline.pop("repair_round", None)
            return _mark_writing(project, pipeline, _AUTO_LABEL.get(current, "重写这一段"))
    if current == "recon":
        locks = copy_locks(locks)
        locks["intel_confirmed"] = True
        brief = project.get("brief") or ""
        locks = prepare_locks(
            locks,
            brief,
            material_text=corpus(brief, project.get("messages")),
            has_images=bool(load_intake(folder)),
        )
        write_locks(folder, locks)
        accepted = list(pipeline.get("accepted") or [])
        if current not in accepted:
            accepted.append(current)
        pipeline["accepted"] = accepted
        pipeline["passed"] = True
        pipeline["current"] = current
        if not ready_to_write(locks):
            progress = dict(project.get("progress") or {})
            progress["pipeline"] = pipeline
            progress["lock_machine"] = True
            project = read_project(project["id"]) or project
            project["progress"] = progress
            _ask_lock(project, _open_question(locks, brief))
            return "lock"
        messages = list(project.get("messages") or [])
        messages.append({"role": "assistant", "content": "对标情报已确认，开始写剧本。"})
        project["messages"] = messages
    pipeline, step = accept_pipeline(pipeline, locks, folder=folder, brief=project.get("brief") or "")
    if step == "wait":
        return None
    label = MACHINE_GATE if step == "finish" else _AUTO_LABEL.get(pipeline.get("current") or "", "下一段")
    return _mark_writing(project, pipeline, label)


def run_project_gated(project_id, debug_llm=False):
    register_thread(project_id)
    with project_lock(project_id):
        _PIPELINE_HANDOFF[project_id] = "writer"
    try:
        project = read_project(project_id)
        if project is None or not is_running(project_id):
            return
        outcome = None
        for _step in range(_AUTO_CHAIN_LIMIT):
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
                project = saved
            if not is_running(project_id):
                return
            project = read_project(project_id) or project
            if outcome.get("terminal") == "ready":
                _settle_writer_outcome(project, outcome)
                return
            follow = _follow_auto_gate(project, folder, locks, outcome)
            if follow == "lock":
                return
            if not isinstance(follow, dict):
                _settle_writer_outcome(project, outcome)
                return
            project = follow
        if outcome is not None:
            _settle_writer_outcome(read_project(project_id) or project, outcome)
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
    ("P4", STAGE_NAME, 68),
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
        raise RuntimeError(EMPTY_DRAFT)
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


def _start_explicit_render(project, keep_done=None, video_submit_mode="generate"):
    from end.render_loop import normalize_video_submit_mode

    project_id = project["id"]
    mode = normalize_video_submit_mode(video_submit_mode)
    spec = load_spec(project_id)
    if not isinstance(spec, dict):
        raise RpcError(409, "还没有提示词")
    locks = read_locks(project_dir(project_id))
    if (project.get("progress") or {}).get("lock_machine") and locks.get("model") not in ("", "h3"):
        return _seedance_only(project)
    aspect = locks.get("aspect") or locked_aspect(project.get("brief") or "") or locked_aspect(str(spec.get("aspect_ratio") or ""))
    if not aspect:
        return _ask_aspect(project)
    from end.script_review import record_generation_technical, technical_generation_block, user_image_ids

    def ids_for(shot, _index):
        return user_image_ids(project_dir(project_id), shot)

    block = technical_generation_block(
        project_dir(project_id),
        spec,
        brief=project.get("brief") or "",
        template=project.get("template"),
        soundtrack=project_soundtrack(project),
        user_ids_for=ids_for,
    )
    if block:
        data = {"reason": "technical_block", "owner": block["owner"], "render": False, "technical": True}
        if block.get("reason") == "提示词是空的":
            _hold_technical(read_project(project_id) or project, block)
            data["generation_hold"] = "technical"
            data["recovery"] = "projects.render"
        raise RpcError(409, block["reason"], data)
    record_generation_technical(
        project_dir(project_id),
        spec,
        brief=project.get("brief") or "",
        template=project.get("template"),
        soundtrack=project_soundtrack(project),
        user_ids_for=ids_for,
    )
    _store_aspect(project, aspect)
    project = read_project(project_id) or project
    tasks = task_plan(spec, soundtrack=project_soundtrack(project))
    kept = {str(step_id) for step_id in (keep_done or ())}
    for task in tasks:
        if str(task.get("id") or "") in kept:
            task["state"] = "done"
            task["detail"] = "已完成"
    project["tasks"] = tasks
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
            "video_submit_mode": mode,
        },
    )
    if _yield_if_paused(project_id):
        return public_view(read_project(project_id) or project)
    with project_lock(project_id):
        if not is_running(project_id):
            _PIPELINE_HANDOFF[project_id] = "yielded"
            return public_view(read_project(project_id) or project)
        _PIPELINE_HANDOFF[project_id] = "render"
    if mode == "preview":
        start_render_thread(project_id, replace=True, video_submit_mode="preview")
    else:
        start_render_thread(project_id, replace=True)
    return public_view(read_project(project_id) or project)


def run_project(project_id, debug_llm=False):
    token = _bind_job(project_id)
    try:
        _run_project(project_id, debug_llm=debug_llm)
    finally:
        _JOB_PROJECT.reset(token)


def _run_project(project_id, debug_llm=False):
    with project_lock(project_id):
        project = read_project(project_id)
        gated = bool(project and (project.get("progress") or {}).get("lock_machine"))
    if gated:
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
            if stage == "P1":
                _present_script_gate(fresh)
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
            repair_brief = _stage_brief("", "P4", DRAFT_NAME, _carried_text(spec))
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
                raise RuntimeError("%s：%s" % (MACHINE_BLOCKED, (report or "")[-800:]))
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
        return
    if task.kind == "script_repair":
        run_script_repair(task.project_id)
        return
    if task.kind == "script_review":
        run_script_revision_review(task.project_id)
        return
    if task.kind == "opinion_revise":
        from end.script_opinion import run_opinion_task

        run_opinion_task(task.project_id, (task.payload or {}).get("opinion_id"), (task.payload or {}).get("generation"))
        return
    if task.kind == "revise":
        from end.revise import perform_revise

        perform_revise(task.project_id)
        return
    if task.kind == "retouch":
        from end.retouch import perform_retouch

        perform_retouch(task.project_id)
        return


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
    if MACHINE_BLOCKED in str(error) or "投喂稿机检未通过" in str(error):
        progress = {"phase": "error", "message": "机检未通过"}
    job = (project.get("progress") or {}).get("retouching") or {}
    if job:
        from end.retouch import retouch_failure

        progress = dict(progress or {})
        progress.setdefault("phase", "error")
        progress.setdefault("message", "重修失败")
        progress["retouching"] = None
        progress["retouch_error"] = retouch_failure(job, error, now())
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


def execute_render(project_id, project=None, video_submit_mode="generate"):
    token = _bind_job(project_id)
    try:
        _execute_render(project_id, project=project, video_submit_mode=video_submit_mode)
    finally:
        _JOB_PROJECT.reset(token)


def _execute_render(project_id, project=None, video_submit_mode="generate"):
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

    from end.render_loop import normalize_video_submit_mode

    mode = normalize_video_submit_mode(
        video_submit_mode or (project.get("progress") or {}).get("video_submit_mode")
    )
    from end.script_review import GenerationBlocked

    try:
        result = run_local_pipeline(
            spec,
            folder,
            on_progress=on_progress,
            project_id=project_id,
            images_only=images_only,
            soundtrack=project_soundtrack(project),
            brief=project.get("brief") or "",
            template=project.get("template"),
            video_submit_mode=mode,
        )
    except GenerationBlocked as blocked:
        _hold_technical(read_project(project_id) or project, blocked.decision or {"reason": str(blocked), "owner": "feed", "technical": True})
        return
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
            "retouching": None,
            "retouch_error": None,
        },
    )


def start_render_thread(project_id, replace=False, video_submit_mode="generate"):
    mode = video_submit_mode

    def target():
        try:
            execute_render(project_id, video_submit_mode=mode)
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
        from end.storyboard_contract import rendering_conflict

        conflict = rendering_conflict(folder)
        if conflict:
            raise RuntimeError(conflict)
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
    kept_mode = (fresh.get("progress") or {}).get("video_submit_mode") or "generate"
    remember(
        fresh,
        "rendering",
        "继续出片",
        progress={"phase": "images", "message": "继续出片", "project_status": "rendering"},
    )
    start_render_thread(project_id, replace=True, video_submit_mode=kept_mode)
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
    """Write canvas text through the rerun persist path, including one shot unit."""
    from end.rerun import apply_saved_content, persist_edits

    if not isinstance(body, dict):
        raise ValueError("内容格式不对")
    with project_lock(project_id):
        project = read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        spec = apply_saved_content(load_spec(project_id) or {}, body)
        folder = project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        from end.script_repair import official_script

        previous_script = official_script(folder) if "script" in body else ""
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        persist_edits(folder, spec, body)
        if "script" in body:
            project["script"] = spec.get("script") or ""
        if "storyboard" in body:
            project["storyboard"] = spec.get("storyboard") or ""
        if "assets" in body:
            project["image_prompts"] = spec.get("image_prompts") or ""
        project["updated_at"] = now()
        write_project(project)
        if "script" in body:
            _refresh_gate_after_script_save(project, previous_script, spec.get("script") or "")
        return read_project(project_id) or project


def _refresh_gate_after_script_save(project, previous_script, new_script):
    """A real manuscript edit leaves the repair gate. It does not adopt the old candidate or add attempts."""
    from end.script_repair import load_candidate
    from end.script_review import content_hash

    if str(previous_script or "").strip() == str(new_script or "").strip():
        return
    row = load_candidate(project_dir(project["id"]))
    if not row:
        return
    if row.get("source_hash") == content_hash(str(new_script or "").strip()):
        return
    question = (project.get("progress") or {}).get("question") or {}
    if question.get("id") not in ("script_candidate", "script"):
        return
    reply = "稿件已保存。上一版修订候选没有采用，修订次数也没有清零。新稿要先重新审查，确认后才能继续。保存本身不会再给模型修订次数。"
    fresh = {"id": "script", "kind": "single", "reply": reply, "options": ["再审一次"], "cards": []}
    messages = list(project.get("messages") or [])
    messages.append(question_message(fresh))
    project["messages"] = messages
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline["action"] = "wait"
    pipeline["current"] = "script"
    pipeline["repair"] = ""
    pipeline["passed"] = False
    remember(
        project,
        "gate",
        "剧本",
        progress={
            "percent": int((project.get("progress") or {}).get("percent") or 48),
            "phase": "gate_script",
            "message": reply.split("\n", 1)[0],
            "lock_machine": True,
            "pipeline": pipeline,
            "question": fresh,
        },
    )


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


def _replace_mode(value):
    """参考图 understands the upload and generates a still. 资产图 is the still."""
    text = str(value or "").strip()
    if text in ("", "asset", "资产图"):
        return "asset"
    if text in ("reference", "参考图"):
        return "reference"
    raise ValueError("替换方式只能是参考图或资产图")


def _reference_still_id(asset_id):
    """Stable object id for a reference upload. It is not the asset file 图N.png."""
    suffix = "_upload"
    base = str(asset_id or "")
    candidate = base + suffix
    if len(candidate) <= 64:
        return candidate
    return base[: 64 - len(suffix)] + suffix


def _asset_id_ok(asset_id):
    text = str(asset_id or "")
    if not text or text != Path(text).name:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in text)


def _reject_busy_replace(project_id, asset_id):
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
    return project, spec, step


def _attach_asset_reference(folder, asset_id, reference_id):
    path = Path(folder) / "spec.json"
    spec = {}
    if path.is_file():
        spec = json.loads(path.read_text(encoding="utf-8"))
    assets = spec.get("assets") if isinstance(spec, dict) else None
    if not isinstance(assets, list):
        raise ValueError("还没有资产")
    found = False
    for item in assets:
        if not isinstance(item, dict) or str(item.get("id") or "") != asset_id:
            continue
        found = True
        refs = []
        for ref in item.get("references") or []:
            text = str(ref).strip()
            if text and text != asset_id and text not in refs:
                refs.append(text)
        if reference_id not in refs:
            refs.append(reference_id)
        item["references"] = refs
        break
    if not found:
        raise ValueError("没有这个资产")
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec


def _note_reference_reading(folder, project_id, reference_id, reading):
    with project_lock(project_id):
        state = load_run_state(folder)
        step = (state.get("steps") or {}).get(reference_id)
        if not step:
            return None
        step["reading"] = str(reading or "")
        save_run_state(folder, state)
        return step


def _retire_asset_file(folder, asset_id, output_file):
    """Archive the previous 资产图 so the card no longer shows that upload as 图N."""
    targets = []
    recorded = None
    text = str(output_file or "").strip().lstrip("/")
    if text and ".." not in Path(text).parts:
        recorded = Path(folder) / "outputs" / text
    canonical, _relative = output_paths(folder, asset_id, "image")
    for path in (recorded, canonical):
        if path is None or path in targets:
            continue
        targets.append(path)
    for path in targets:
        _unlink_render_output(path, folder, "reference-replace")


def save_asset_image(project_id, asset_id, data, mode=None):
    """Replace one still.

    资产图 uploads the file and stores that public URL on the asset. The still
    stays through a later invalidate. 参考图 stores the file as a reference,
    reads it, and leaves the asset pending so the next image pass generates
    a new still. The upload is not that still.
    """
    chosen = _replace_mode(mode)
    asset_id = str(asset_id or "")
    if not _asset_id_ok(asset_id):
        raise ValueError("没有这个资产")
    png = _png_bytes(data)
    if chosen == "reference":
        return _save_reference_image(project_id, asset_id, png)
    return _save_direct_asset(project_id, asset_id, png)


def _save_direct_asset(project_id, asset_id, png):
    with project_lock(project_id):
        project, _spec, _step = _reject_busy_replace(project_id, asset_id)
        folder = project_dir(project_id)
        public_url = get_image_store().save(project_id, asset_id, png)
        note_local_image(folder, project_id, asset_id, public_url)
        lock_asset_still(folder, project_id, asset_id)
        return project


def _save_reference_image(project_id, asset_id, png):
    reference_id = _reference_still_id(asset_id)
    folder = project_dir(project_id)
    with project_lock(project_id):
        project, _spec, step = _reject_busy_replace(project_id, asset_id)
        was_asset = bool(step.get("asset_still"))
        output_file = step.get("output_file") or ""
        public_url = get_image_store().save(project_id, reference_id, png)
        note_local_image(folder, project_id, reference_id, public_url)
    image_path = folder / "outputs" / "images" / (reference_id + ".png")
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
    if prompts:
        finish_trace(folder, {"readings": [{"id": reference_id, "reading": reading}]})
    with project_lock(project_id):
        _reject_busy_replace(project_id, asset_id)
        _note_reference_reading(folder, project_id, reference_id, reading)
        _attach_asset_reference(folder, asset_id, reference_id)
        release_asset_for_generation(folder, project_id, asset_id)
        if was_asset:
            _retire_asset_file(folder, asset_id, output_file)
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


def _present_missing_repair_context(project, row):
    """An empty brief is not a new repair. Do not queue the model or clear the saved rejection."""
    saved = str((row or {}).get("rejected_text") or "").strip()
    reply = "缺少创作要求，不能判断主旨，需要人工确认。正式剧本没有改。没有因此重新排队修订。"
    options = ["查看未采用候选", "保持原稿"] if saved else ["保持原稿"]
    if (row or {}).get("reason") and not str(row.get("reason")).startswith("缺少创作要求"):
        reply += "\n先前的记录还在：" + str(row.get("reason"))
    question = {"id": "script_candidate", "kind": "single", "reply": reply, "options": options, "cards": []}
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline["action"] = "wait"
    pipeline["current"] = "script"
    pipeline["repair"] = ""
    remember(
        project,
        "gate",
        "剧本候选",
        progress={
            "percent": 48,
            "phase": "gate_script",
            "message": reply.split("\n", 1)[0],
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
        },
    )
    return public_view(read_project(project["id"]) or project)


def _present_stale_manual_review(project):
    """The manuscript no longer matches the manual save. Do not queue or rewrite it."""
    reply = "正式稿已经变了，上次保存对应的审稿不能自动继续。没有改这一稿，也没有重排审稿。请重新审查。"
    question = {"id": "script", "kind": "single", "reply": reply, "options": ["再审一次"], "cards": []}
    messages = list(project.get("messages") or [])
    messages.append(question_message(question))
    project["messages"] = messages
    pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
    pipeline["action"] = "wait"
    pipeline["current"] = "script"
    pipeline["repair"] = ""
    remember(
        project,
        "gate",
        "剧本",
        progress={
            "percent": int((project.get("progress") or {}).get("percent") or 48),
            "phase": "gate_script",
            "message": reply.split("\n", 1)[0],
            "lock_machine": True,
            "pipeline": pipeline,
            "question": question,
        },
    )
    return read_project(project["id"]) or project


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
            pipeline = (project.get("progress") or {}).get("pipeline") or {}
            if pipeline.get("action") == "script_review":
                from end.script_revision import manual_resume_decision

                # list_projects can go stale before submit. The handler writes
                # the gate under this lock and only then drops the queue key,
                # so hold the lock from the fresh read through submit.
                with project_lock(project["id"]):
                    fresh = read_project(project["id"]) or project
                    fresh_pipeline = (fresh.get("progress") or {}).get("pipeline") or {}
                    if fresh.get("status") != "writing" or fresh_pipeline.get("action") != "script_review":
                        continue
                    if manual_resume_decision(fresh) == "hold":
                        _present_stale_manual_review(fresh)
                        started += 1
                        continue
                    if submit_agent_task(Task("script_review", fresh["id"], {})):
                        started += 1
                continue
            if pipeline.get("action") == "script_repair":
                from end.script_repair import budget_identity, issue_rows, load_candidate, official_script, same_budget

                folder = project_dir(project["id"])
                row = load_candidate(folder)
                if not str(project.get("brief") or "").strip():
                    _present_missing_repair_context(project, row or {})
                    started += 1
                    continue
                identity = budget_identity(
                    official_script(folder),
                    project.get("brief") or "",
                    issue_rows(folder),
                )
                if row and same_budget(row, identity) and row.get("status") in ("passed", "blocked", "error"):
                    _present_script_candidate(project, row)
                    started += 1
                    continue
                if submit_agent_task(Task("script_repair", project["id"], {"model": project.get("model") or ""})):
                    started += 1
                continue
            if pipeline.get("human_release"):
                with project_lock(project["id"]):
                    fresh = read_project(project["id"]) or project
                    fresh_pipeline = (fresh.get("progress") or {}).get("pipeline") or {}
                    if fresh.get("status") != "writing" or fresh_pipeline.get("action") != "write":
                        continue
                    from end.script_accept import confirmed_continue
                    from end.script_repair import official_script
                    from end.script_review import content_hash

                    folder = project_dir(fresh["id"])
                    if not confirmed_continue(folder, content_hash(official_script(folder))):
                        continue
                    if submit_agent_task(Task("write", fresh["id"], {"debug_llm": project_debug_llm(fresh)})):
                        started += 1
                continue
            if submit_agent_task(
                Task("write", project["id"], {"debug_llm": project_debug_llm(project)})
            ):
                started += 1
    return started


def resume_rendering_projects():
    started = 0
    for project in list_projects():
        folder = project_dir(project["id"])
        from end.storyboard_contract import rendering_conflict

        conflict = rendering_conflict(folder)
        if conflict:
            if project.get("error") != conflict:
                project["error"] = conflict
                remember(
                    project,
                    "paused",
                    conflict,
                    progress={
                        "phase": "paused",
                        "message": conflict,
                        "project_status": "paused",
                        "paused_from": "rendering",
                        "percent": (project.get("progress") or {}).get("percent") or 0,
                    },
                )
            continue
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
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data


def _rpc_body(request_id, result=None, error=None):
    if error is not None:
        body = {"code": error.code, "message": error.message}
        if getattr(error, "data", None):
            body["data"] = error.data
        return {
            "jsonrpc": "2.0",
            "error": body,
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
    from end.minimax_video import local_base_url, read_minimax_video
    from end.qwen_env import read_qwen_env
    from end.trace import trace_enabled

    return {
        "debug_llm": env_debug_llm(),
        "debug_llm_env": "AIVIDEO_DEBUG_LLM",
        "debug_trace": trace_enabled(),
        "debug_trace_env": "AIVIDEO_DEBUG_TRACE",
        "qwen_env": read_qwen_env(),
        "minimax_video": read_minimax_video(),
        "minimax_base_url": local_base_url(),
        "model": default_model(),
        "models": model_choices(),
        "template": normalize_template(None),
        "templates": template_choices(),
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
    ) and not _explicit_render_recovery(project):
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


def rpc_retouch(params):
    """Revise one rendered still or shot from a feeling, then preview the affected steps."""
    from end.retouch import accept_retouch

    project_id = str((params or {}).get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    try:
        project = accept_retouch(
            project_id,
            (params or {}).get("node"),
            (params or {}).get("target_id"),
            (params or {}).get("suggestion"),
        )
    except KeyError as error:
        raise RpcError(404, str(error))
    except ValueError as error:
        raise RpcError(400, str(error))
    except RuntimeError as error:
        raise RpcError(409, str(error))
    return public_view(project or read_project(project_id))


def rpc_rerun(params):
    """Rerun one still or shot. Omitted grade is preview; 出成片档 sends generate."""
    from end.retouch import rerun_selected

    project_id = str((params or {}).get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目")
    try:
        view = rerun_selected(
            project_id,
            (params or {}).get("node"),
            (params or {}).get("target_id"),
            (params or {}).get("grade") if "grade" in (params or {}) else None,
        )
    except KeyError as error:
        raise RpcError(404, str(error))
    except ValueError as error:
        raise RpcError(400, str(error))
    except RuntimeError as error:
        raise RpcError(409, str(error))
    if view is None:
        return public_view(read_project(project_id))
    return view


def _revision_call(fn):
    from end.script_revision import RevisionError

    try:
        return fn()
    except RevisionError as error:
        data = {
            "reason": error.reason,
            "retryable": error.retryable,
            "saved": error.saved,
            "retry_same_key": error.retry_same_key,
            "official_saved": error.official_saved,
        }
        if getattr(error, "model_remaining", None) is not None:
            data["model_remaining"] = error.model_remaining
        raise RpcError(error.code, error.message, data)


def rpc_script_revision_get(params):
    project_id = str((params or {}).get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    from end.script_revision import revision_get

    return revision_get(project)


def rpc_script_revision_save(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    from end.script_revision import revision_save

    return _revision_call(lambda: revision_save(
        project,
        params.get("text"),
        params.get("base_token"),
        params.get("idempotency_key"),
        params.get("source") or "",
        params.get("opinion_id"),
        params.get("opinion_revision"),
    ))


def rpc_script_revision_retry(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    from end.script_revision import revision_retry

    return _revision_call(lambda: revision_retry(project, params.get("base_token")))


def rpc_script_opinion(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    from end.script_opinion import submit_opinion

    return _revision_call(lambda: submit_opinion(
        project, params.get("base"), params.get("opinion"), params.get("base_token"), params.get("idempotency_key"),
    ))


def rpc_script_opinion_retry(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False})
    from end.script_opinion import retry_opinion

    return _revision_call(lambda: retry_opinion(project, params.get("base_token")))


def _continue_accepted_script(project):
    """Queue the next prompt stage. Does not render and does not mark the review passed."""
    from end.script_revision import RevisionError
    from end.stage_runner import accept_pipeline

    project = read_project(project["id"]) or project
    folder, locks = _lock_inputs(project)
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    semantic = pipeline.get("semantic_status")
    pipeline["human_release"] = True
    pipeline["passed"] = True
    pipeline["current"] = "script"
    pipeline["action"] = "wait"
    pipeline["retry_write"] = False
    if semantic:
        pipeline["semantic_status"] = semantic
    pipeline, _action = accept_pipeline(pipeline, locks, folder=folder, brief=project.get("brief") or "")
    if pipeline.get("action") == "wait" and pipeline.get("current") == "script":
        raise RevisionError(409, "这一稿还不能进入下一段", "continue_not_queued", True, True, True, False)
    stage = "finish" if pipeline.get("action") == "finish" else str(pipeline.get("current") or "")
    label = MACHINE_GATE if pipeline.get("action") == "finish" else "下一段"
    remember(
        project,
        "writing",
        label,
        progress={
            "percent": int(progress.get("percent") or 48),
            "phase": "writing",
            "message": label,
            "lock_machine": True,
            "pipeline": pipeline,
            "question": None,
        },
    )
    queued = submit_agent_task(Task("write", project["id"], {"debug_llm": bool(progress.get("debug_llm"))}))
    if not queued:
        return {"continue_stage": stage, "continue_paid": False, "queued": False, "already": True}
    return {"continue_stage": stage, "continue_paid": False, "queued": True, "already": False}


def rpc_script_accept(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False, "official_saved": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False, "official_saved": False})
    from end.script_accept import accept_as_is

    return _revision_call(lambda: accept_as_is(project, params, _continue_accepted_script))


def rpc_script_accept_retry(params):
    params = params or {}
    project_id = str(params.get("id") or "")
    if not valid_id(project_id):
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False, "official_saved": False})
    project = read_project(project_id)
    if project is None:
        raise RpcError(404, "没有这个项目", {"reason": "not_found", "retryable": False, "saved": False, "retry_same_key": False, "official_saved": False})
    from end.script_accept import accept_retry

    return _revision_call(lambda: accept_retry(project, params.get("base_token"), _continue_accepted_script))


def run_script_revision_review(project_id):
    """Full script review for a human manuscript. Does not repair or generate."""
    project = read_project(project_id)
    if project is None:
        return
    try:
        _present_script_gate(read_project(project_id) or project, force=True)
    except Exception as error:
        fail_project(project_id, error)


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
        save_asset_image(
            project_id,
            str(params.get("asset_id") or ""),
            data,
            params.get("mode"),
        )
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
    if method == "projects.script_revision.get":
        return rpc_script_revision_get(params)
    if method == "projects.script_revision.save":
        return rpc_script_revision_save(params)
    if method == "projects.script_revision.retry_review":
        return rpc_script_revision_retry(params)
    if method == "projects.script_revision.opinion":
        return rpc_script_opinion(params)
    if method == "projects.script_revision.opinion_retry":
        return rpc_script_opinion_retry(params)
    if method == "projects.script_revision.accept_as_is":
        return rpc_script_accept(params)
    if method == "projects.script_revision.accept_retry":
        return rpc_script_accept_retry(params)
    if method == "projects.retouch":
        return rpc_retouch(params)
    if method == "projects.rerun":
        return rpc_rerun(params)
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
