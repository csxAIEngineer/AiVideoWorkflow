"""In-process jobs for model calls.

The RPC returns as soon as the job exists. A background thread runs the
call and updates each node. Callers poll ``snapshot`` instead of holding
one HTTP request open until the model finishes.
"""

import threading
import uuid
from pathlib import Path

_LOCK = threading.Lock()
_JOBS = {}
_MAX_JOBS = 32

WAITING = "等待"
RUNNING = "进行中"
DONE = "完成"
FAILED = "失败"


def node_name(relative):
    """Short label for one specialist. SKILL.md keeps its parent so names stay unique."""
    if relative == "merge":
        return "汇总"
    path = Path(str(relative or ""))
    if path.stem == "SKILL":
        parent = path.parent.name or "SKILL"
        return "%s/SKILL" % parent
    return path.stem or str(relative or "")


def submit(nodes, work, trace=""):
    """Store the job, start ``work(job_id)`` on a daemon thread, and return the waiting snapshot."""
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "running",
        "nodes": [_fresh(item) for item in nodes or []],
        "result": None,
        "error": "",
    }
    with _LOCK:
        _JOBS[job_id] = job
        _trim()
    snap = snapshot(job_id)
    thread = threading.Thread(target=_run, args=(job_id, trace, work), daemon=True)
    thread.start()
    return snap


def snapshot(job_id):
    """Current job, or ``lost`` when this process no longer has it."""
    with _LOCK:
        job = _JOBS.get(str(job_id or ""))
        if job is None:
            return {
                "job_id": str(job_id or ""),
                "status": "lost",
                "nodes": [],
                "result": None,
                "error": "任务已丢失",
            }
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "nodes": [dict(item) for item in job["nodes"]],
            "result": job["result"],
            "error": job["error"],
        }


def set_node(job_id, node_id, status):
    """Update one node. Missing jobs and unknown ids are ignored."""
    if not job_id or not node_id:
        return
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        for node in job["nodes"]:
            if node["id"] == node_id:
                node["status"] = status
                return


def _run(job_id, trace, work):
    from AI.qwen_api.graph import bind_trace_prompts

    try:
        with bind_trace_prompts(trace or ""):
            result = work(job_id)
    except Exception as error:
        _fail(job_id, error)
        return
    _succeed(job_id, result)


def _succeed(job_id, result):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "succeeded"
        job["result"] = result
        job["error"] = ""


def _fail(job_id, error):
    text = str(error or "").strip() or "这一步没有完成"
    if len(text) > 500:
        text = text[:500]
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job["status"] == "succeeded":
            return
        for node in job["nodes"]:
            if node["status"] != DONE:
                node["status"] = FAILED
        job["status"] = "failed"
        job["error"] = text


def _fresh(item):
    item = item or {}
    node_id = str(item.get("id") or "")
    return {
        "id": node_id,
        "name": str(item.get("name") or node_id),
        "status": WAITING,
    }


def _trim():
    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = [job_id for job_id, job in _JOBS.items() if job["status"] in ("succeeded", "failed")]
    while len(_JOBS) > _MAX_JOBS and finished:
        _JOBS.pop(finished.pop(0), None)
