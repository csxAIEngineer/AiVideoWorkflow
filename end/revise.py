"""Turn a canvas suggestion into a new prompt for one node.

Script and storyboard go through the stage StateGraph. One image or one shot
goes through a single-block StateGraph so the other blocks stay put.
The new text is saved. Downstream rerun stays on the existing button.
A feel-driven retouch lives in end.retouch and reruns on its own.
"""

import threading

from end.rerun import _worker_busy
from end.workflow_from_spec import collect_steps

_NODES = ("script", "storyboard", "asset", "shot")
_BUSY = ("writing", "building", "rendering", "drilling")
_TASK_LINE = {
    "script": "任务：剧本",
    "storyboard": "任务：分镜",
}
_KEEP = "只按修改建议改当前正文。没提到的句子、人名和顺序保持。"


def revision_brief(node, current, suggestion):
    """Brief for the script or storyboard StateGraph."""
    return "\n".join((
        _TASK_LINE[node],
        "当前正文：",
        str(current or "").strip(),
        "修改建议：",
        str(suggestion or "").strip(),
        _KEEP,
    ))


def block_brief(kind, current, suggestion):
    """User message for the one-block StateGraph."""
    title = "当前图块：" if kind == "asset" else "当前投喂单元："
    return "\n".join((
        title,
        str(current or "").strip(),
        "修改建议：",
        str(suggestion or "").strip(),
        "只按修改建议改这一块。没提到的句子保持。不要写其他图或其他镜头。",
    ))


def accept_revise(project_id, node, target_id, suggestion):
    """Queue a revision. The canvas stays where it is until the text comes back."""
    from end.task_queue import Task
    from end import server as site

    node = str(node or "")
    target_id = str(target_id or "")
    suggestion = str(suggestion or "").strip()
    if node not in _NODES:
        raise ValueError("不能从这里改")
    if not suggestion:
        raise ValueError("修改建议是空的")
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        if project.get("status") in _BUSY or _worker_busy(site, project_id):
            raise RuntimeError("正在跑，等这一轮结束再改")
        progress = dict(project.get("progress") or {})
        if progress.get("revising"):
            raise RuntimeError("正在按建议生成")
        spec = site.load_spec(project_id) or {}
        _require_current(spec, node, target_id)
        progress["revising"] = {
            "node": node,
            "target_id": target_id,
            "suggestion": suggestion,
        }
        progress.pop("revise_error", None)
        project["progress"] = progress
        project["updated_at"] = site.now()
        site.write_project(project)
        site._PIPELINE_HANDOFF[project_id] = "writer"
    try:
        queued = site.submit_agent_task(Task("revise", project_id, {}))
    except Exception:
        _clear_job(site, project_id, handoff=True)
        raise
    if not queued:
        threading.Thread(
            target=perform_revise,
            args=(project_id,),
            name="aivideo-revise-%s" % project_id,
            daemon=True,
        ).start()
    return site.read_project(project_id)


def perform_revise(project_id):
    """Run the queued suggestion and save only that node's text."""
    from end import server as site

    site.register_thread(project_id)
    job = {}
    try:
        with site.project_lock(project_id):
            project = site.read_project(project_id)
            if project is None:
                return
            job = dict(((project.get("progress") or {}).get("revising") or {}))
            if not job.get("node"):
                return
            spec = site.load_spec(project_id) or {}
            model_name = project.get("model") or ""
            site._PIPELINE_HANDOFF[project_id] = "writer"
        text, previs = generate_revision(
            job.get("node"),
            spec,
            job.get("target_id") or "",
            job.get("suggestion") or "",
            model_name,
        )
        _save_revision(site, project_id, job, text, previs)
    except Exception as error:
        _mark_failed(site, project_id, job, error)
    finally:
        with site.project_lock(project_id):
            fresh = site.read_project(project_id)
            still = bool(fresh and (fresh.get("progress") or {}).get("revising"))
            if not still and site._PIPELINE_HANDOFF.get(project_id) == "writer":
                site._PIPELINE_HANDOFF[project_id] = "yielded"


def generate_revision(node, spec, target_id, suggestion, model_name):
    """Return (new_text, previs). previs is only set for a storyboard revision."""
    node = str(node or "")
    current = _require_current(spec, node, target_id)
    if node in ("script", "storyboard"):
        parsed = run_suite_call(revision_brief(node, current, suggestion), model_name)
        if node == "script":
            text = _field(parsed, "script")
            return text, ""
        text = _field(parsed, "storyboard")
        previs = str((parsed or {}).get("previs") or "").strip()
        return text, previs
    text = run_block_call(node, current, suggestion, model_name)
    if not str(text or "").strip():
        raise RuntimeError("没有生成新的提示词")
    return str(text).strip(), ""


def run_suite_call(brief, model_name):
    """Run the stage StateGraph. Tests replace this."""
    from AI.agent.layout import get_layout

    parsed = get_layout().write(brief, "", model=model_name)
    if not isinstance(parsed, dict):
        raise RuntimeError("没有生成新的提示词")
    return parsed


def run_block_call(kind, current, suggestion, model_name):
    """Run the one-block StateGraph. Tests replace this."""
    from AI.agent.layout import get_layout
    from AI.qwen_api.revise_graph import run_block

    return run_block(
        kind,
        block_brief(kind, current, suggestion),
        get_layout().model,
        model_name=model_name,
    )


def _field(parsed, name):
    text = str((parsed or {}).get(name) or (parsed or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError("没有生成新的提示词")
    return text


def _require_current(spec, node, target_id):
    if node == "script":
        text = str((spec or {}).get("script") or "").strip()
        if not text:
            raise ValueError("提示词是空的")
        return text
    if node == "storyboard":
        text = str((spec or {}).get("storyboard") or "").strip()
        if not text:
            raise ValueError("分镜是空的")
        return text
    assets, shots = collect_steps(spec or {})
    rows = assets if node == "asset" else shots
    label = "资产" if node == "asset" else "镜头"
    found = next((item for item in rows if item.get("id") == target_id), None)
    if found is None:
        raise ValueError("没有%s %s" % (label, target_id))
    text = str(found.get("prompt") or "").strip()
    if not text:
        raise ValueError("提示词是空的")
    return text


def _save_revision(site, project_id, job, text, previs):
    node = str(job.get("node") or "")
    target_id = str(job.get("target_id") or "")
    if node == "script":
        body = {"script": text}
    elif node == "storyboard":
        body = {"storyboard": text}
    elif node == "asset":
        body = {"assets": [{"id": target_id, "prompt": text}]}
    elif node == "shot":
        body = {"shots": [{"id": target_id, "prompt": text}]}
    else:
        raise ValueError("不能从这里改")
    site.save_project_content(project_id, body)
    _clear_job(site, project_id, handoff=False)


def _mark_failed(site, project_id, job, error):
    message = str(error).strip() or "没有生成新的提示词"
    key = _job_key(job.get("node"), job.get("target_id"))
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            return
        progress = dict(project.get("progress") or {})
        progress.pop("revising", None)
        if key:
            progress["revise_error"] = {"key": key, "message": message[:200]}
        project["progress"] = progress
        project["updated_at"] = site.now()
        site.write_project(project)


def _clear_job(site, project_id, handoff):
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is not None:
            progress = dict(project.get("progress") or {})
            progress.pop("revising", None)
            progress.pop("revise_error", None)
            project["progress"] = progress
            project["updated_at"] = site.now()
            site.write_project(project)
        if handoff and site._PIPELINE_HANDOFF.get(project_id) == "writer":
            site._PIPELINE_HANDOFF[project_id] = "yielded"


def _job_key(node, target_id):
    node = str(node or "")
    if node in ("script", "storyboard"):
        return node
    if not node or not target_id:
        return ""
    return "%s:%s" % (node, target_id)
