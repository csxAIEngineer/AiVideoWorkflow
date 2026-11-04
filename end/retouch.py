"""Revise one already-rendered still or shot from a feeling, then preview it.

The model rewrites only that block. End stores the before/after text and
reruns the steps that consume it. The clip parameter profile is preview.
A full generate render stays on the existing render action.
"""

import threading
from pathlib import Path

from end.render_loop import load_run_state
from end.rerun import _worker_busy, rerun_from, rerun_ready
from end.revise import _require_current, generate_revision

_NODES = ("asset", "shot")
_BUSY = ("writing", "building", "rendering", "drilling")
_MEDIA_KINDS = ("image", "video", "join")
_MEDIA_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov")


def has_rendered(project, folder):
    """True when this project has already produced a still, a clip, or a film."""
    folder = Path(folder)
    name = str((project or {}).get("video_file") or "")
    if name and "/" not in name and ".." not in name:
        film = folder / "outputs" / name
        if film.is_file() and film.stat().st_size > 0:
            return True
    for task in (project or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if task.get("state") == "done" and task.get("kind") in _MEDIA_KINDS:
            return True
    state = load_run_state(folder)
    for step in (state.get("steps") or {}).values():
        if not isinstance(step, dict):
            continue
        if step.get("status") == "done" and step.get("kind") in _MEDIA_KINDS:
            return True
    outputs = folder / "outputs"
    if not outputs.is_dir():
        return False
    for path in outputs.rglob("*"):
        if path.is_file() and path.suffix.lower() in _MEDIA_SUFFIXES and path.stat().st_size > 0:
            return True
    return False


def retouch_failure(job, error, at):
    """Progress error for one retouch. The saved prompt and prompt_diff stay."""
    node = str((job or {}).get("node") or "")
    target_id = str((job or {}).get("target_id") or "")
    if node in _NODES and target_id:
        key = "%s:%s" % (node, target_id)
    else:
        key = node
    message = str(error).strip() or "没有生成新的提示词"
    return {"key": key, "message": message[:200], "at": at}


def normalize_grade(value):
    """Clip parameter profile. Omitted means preview. generate is the separate button."""
    if value is None or str(value).strip() == "":
        return "preview"
    text = str(value).strip().lower()
    if text not in ("preview", "generate"):
        raise ValueError("档位只能是预览或成片")
    return text


def rerun_selected(project_id, node, target_id, grade=None):
    """Rerun one already-rendered still or shot. Does not rewrite the prompt."""
    from end import server as site

    node = str(node or "")
    target_id = str(target_id or "")
    mode = normalize_grade(grade)
    if node not in _NODES:
        raise ValueError("只能从单张图或单镜头重跑")
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        progress = project.get("progress") or {}
        if (
            project.get("status") in _BUSY
            or _worker_busy(site, project_id)
            or progress.get("retouching")
            or progress.get("revising")
        ):
            raise RuntimeError("正在跑，等这一轮结束再改")
        folder = site.project_dir(project_id)
        if not has_rendered(project, folder):
            if not rerun_ready(project, folder):
                raise RuntimeError("提示词还没写完，不能从这里重跑")
            raise RuntimeError("还没出过图或片子，不能从这里重跑")
    return rerun_from(project_id, node, target_id, video_submit_mode=mode)


def accept_retouch(project_id, node, target_id, suggestion):
    """Queue one retouch. The canvas keeps the old picture until the preview lands."""
    from end.task_queue import Task
    from end import server as site

    node = str(node or "")
    target_id = str(target_id or "")
    suggestion = str(suggestion or "").strip()
    if node not in _NODES:
        raise ValueError("只能修单张图或单镜头")
    if not suggestion:
        raise ValueError("先写一下想怎么改")
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        progress = dict(project.get("progress") or {})
        if (
            project.get("status") in _BUSY
            or _worker_busy(site, project_id)
            or progress.get("retouching")
            or progress.get("revising")
        ):
            raise RuntimeError("正在跑，等这一轮结束再改")
        folder = site.project_dir(project_id)
        if not has_rendered(project, folder):
            if not rerun_ready(project, folder):
                raise RuntimeError("提示词还没写完，不能从这里重修")
            raise RuntimeError("还没出过图或片子，不能从这里重修")
        spec = site.load_spec(project_id) or {}
        _require_current(spec, node, target_id)
        progress["retouching"] = {
            "node": node,
            "target_id": target_id,
            "suggestion": suggestion,
            "phase": "prompt",
            "started_at": site.now(),
        }
        progress.pop("retouch_error", None)
        project["progress"] = progress
        project["updated_at"] = site.now()
        site.write_project(project)
        site._PIPELINE_HANDOFF[project_id] = "writer"
    try:
        queued = site.submit_agent_task(Task("retouch", project_id, {}))
    except Exception:
        _abort(site, project_id)
        raise
    if not queued:
        threading.Thread(
            target=perform_retouch,
            args=(project_id,),
            name="aivideo-retouch-%s" % project_id,
            daemon=True,
        ).start()
    return site.read_project(project_id)


def perform_retouch(project_id):
    """Rewrite one block, keep the diff, and preview the steps that use it."""
    from end import server as site

    site.register_thread(project_id)
    job = {}
    try:
        try:
            with site.project_lock(project_id):
                project = site.read_project(project_id)
                if project is None:
                    return
                job = dict((project.get("progress") or {}).get("retouching") or {})
                if job.get("phase") != "prompt" or job.get("node") not in _NODES:
                    return
                spec = site.load_spec(project_id) or {}
                model_name = project.get("model") or ""
                before = _require_current(spec, job.get("node"), job.get("target_id") or "")
                site._PIPELINE_HANDOFF[project_id] = "writer"
            text, _previs = generate_revision(
                job.get("node"),
                spec,
                job.get("target_id") or "",
                job.get("suggestion") or "",
                model_name,
            )
            _save_block(site, project_id, job.get("node"), job.get("target_id") or "", text)
            after = _saved_prompt(site, project_id, job.get("node"), job.get("target_id") or "", text)
            _commit_diff(site, project_id, job, before, after)
        except Exception as error:
            _mark_failed(site, project_id, job, error)
            return
        try:
            rerun_from(
                project_id,
                job.get("node"),
                job.get("target_id") or "",
                video_submit_mode="preview",
            )
        except Exception as error:
            _mark_failed(site, project_id, job, error)
            return
        if not _render_started(site.read_project(project_id)):
            _mark_failed(site, project_id, job, "还不能按这个感觉出片")
    finally:
        finally_handoff(site, project_id)


def _render_started(project):
    return bool(project) and project.get("status") in ("building", "rendering")


def _saved_prompt(site, project_id, node, target_id, fallback):
    """The block that landed on disk, header included when the shot unit needs one."""
    try:
        return _require_current(site.load_spec(project_id) or {}, node, target_id)
    except ValueError:
        return fallback


def _save_block(site, project_id, node, target_id, text):
    if node == "asset":
        body = {"assets": [{"id": target_id, "prompt": text}]}
    elif node == "shot":
        body = {"shots": [{"id": target_id, "prompt": text}]}
    else:
        raise ValueError("只能修单张图或单镜头")
    site.save_project_content(project_id, body)


def _commit_diff(site, project_id, job, before, after):
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            return
        progress = dict(project.get("progress") or {})
        retouching = dict(progress.get("retouching") or {})
        retouching["phase"] = "render"
        progress["retouching"] = retouching
        progress["prompt_diff"] = {
            "node": job.get("node"),
            "target_id": job.get("target_id") or "",
            "before": before,
            "after": after,
            "suggestion": job.get("suggestion") or "",
            "at": site.now(),
        }
        progress.pop("retouch_error", None)
        project["progress"] = progress
        project["updated_at"] = site.now()
        site.write_project(project)


def _mark_failed(site, project_id, job, error):
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            return
        progress = dict(project.get("progress") or {})
        progress.pop("retouching", None)
        progress["retouch_error"] = retouch_failure(job, error, site.now())
        project["progress"] = progress
        project["updated_at"] = site.now()
        site.write_project(project)
        if site._PIPELINE_HANDOFF.get(project_id) == "writer":
            site._PIPELINE_HANDOFF[project_id] = "yielded"


def _abort(site, project_id):
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is not None:
            progress = dict(project.get("progress") or {})
            progress.pop("retouching", None)
            project["progress"] = progress
            project["updated_at"] = site.now()
            site.write_project(project)
        if site._PIPELINE_HANDOFF.get(project_id) == "writer":
            site._PIPELINE_HANDOFF[project_id] = "yielded"


def finally_handoff(site, project_id):
    with site.project_lock(project_id):
        fresh = site.read_project(project_id)
        still = bool(fresh and (fresh.get("progress") or {}).get("retouching"))
        if not still and site._PIPELINE_HANDOFF.get(project_id) == "writer":
            site._PIPELINE_HANDOFF[project_id] = "yielded"
