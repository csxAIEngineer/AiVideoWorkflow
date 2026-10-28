"""JSON-RPC client. This process does not import the AI package."""

import atexit
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "http://127.0.0.1:8791"

_LOCK = threading.Lock()
_PROC = None
_READY = False
_MEMO = {}
_SERIAL = 0
_SERIAL_LOCK = threading.Lock()


class RemoteError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _url():
    return (os.environ.get("AIVIDEO_AI_URL") or DEFAULT_URL).rstrip("/")


def _autostart():
    return (os.environ.get("AIVIDEO_AI_AUTOSTART") or "1").strip().lower() not in ("0", "false", "no", "off")




def _post(url, method, params, timeout):
    global _SERIAL
    with _SERIAL_LOCK:
        _SERIAL += 1
        request_id = _SERIAL
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": request_id},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url + "/rpc",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RemoteError("AI 接口返回 %s：%s" % (error.code, detail), status=error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RemoteError("AI 服务没有响应：%s" % error)
    if not isinstance(body, dict):
        raise RemoteError("AI 没有返回 JSON")
    if body.get("error"):
        error = body["error"] or {}
        data = error.get("data") or {}
        raise RemoteError(error.get("message") or "AI 调用失败", status=data.get("status"))
    return body.get("result")


def _reachable(url):
    try:
        _post(url, "ping", {}, timeout=0.4)
        return True
    except RemoteError:
        return False


def _stop_child():
    proc = _PROC
    if proc is not None and proc.poll() is None:
        proc.terminate()


atexit.register(_stop_child)


def _spawn(url):
    global _PROC
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost"):
        raise RemoteError("AI 服务不在本机，也没有启动：%s" % url)
    env = os.environ.copy()
    env["AI_HOST"] = "127.0.0.1"
    env["AI_PORT"] = str(parsed.port or 8791)
    env["AIVIDEO_AI_AUTOSTART"] = "0"
    _PROC = subprocess.Popen([sys.executable, "-m", "AI.server"], cwd=str(ROOT), env=env)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _PROC.poll() is not None:
            raise RemoteError("AI 进程退出了，代码 %s" % _PROC.returncode)
        if _reachable(url):
            return
        time.sleep(0.1)
    raise RemoteError("AI 服务没有在 20 秒内起来")


def endpoint():
    global _READY
    url = _url()
    if _READY:
        return url
    if not _autostart() or _reachable(url):
        _READY = True
        return url
    with _LOCK:
        if _READY or _reachable(url):
            _READY = True
            return url
        _spawn(url)
        _READY = True
    return url


def call(method, params=None, timeout=60):
    url = endpoint()
    return _post(url, method, params or {}, timeout)


def _remember(key, method, params, timeout=60):
    if key in _MEMO:
        return _MEMO[key]
    value = call(method, params, timeout=timeout)
    _MEMO[key] = value
    return value


def catalog():
    return _remember("catalog", "catalog", {})


def normalize_model(model):
    key = ("model", str(model or ""))
    return _remember(key, "models.normalize", {"model": model or ""})["model"]


def model_label(model):
    key = ("model-label", str(model or ""))
    return _remember(key, "models.label", {"model": model or ""})["label"]


def model_choices():
    return catalog()["models"]


def default_model():
    return catalog()["default_model"]


def normalize_template(template):
    key = ("template", str(template or ""))
    return _remember(key, "templates.normalize", {"template": template or ""})["template"]


def template_label(template):
    key = ("template-label", str(template or ""))
    return _remember(key, "templates.label", {"template": template or ""})["label"]


def template_policy(template):
    key = ("template-policy", str(template or ""))
    return _remember(key, "templates.policy", {"template": template or ""})["policy"]


def template_choices():
    return catalog()["templates"]


def template_render(template):
    key = ("template-render", str(template or ""))
    value = _remember(key, "templates.render", {"template": template or ""})
    return dict(value or {})


def load_skill_pack(template, context="", stage="P4"):
    """Cache one stage. Track and combat in the brief change which files are read."""
    template = str(template or "")
    context = str(context or "")
    stage = str(stage or "P4")
    loaded = _remember(
        ("skill-pack", template, stage, context),
        "skills.load",
        {"template": template, "context": context, "stage": stage},
        timeout=120,
    )
    return loaded["pack"]


def json_shape(template=None):
    key = ("json-shape", str(template or ""))
    return _remember(key, "skills.shape", {"template": template or ""})["shape"]


def drill(messages, debug_dir=None, model=None, template=None, on_status=None):
    return await_job(
        "drill",
        {
            "messages": messages or [],
            "debug_dir": str(debug_dir) if debug_dir else "",
            "model": model or "",
            "template": template or "",
        },
        on_status=on_status,
    )


_SUBMIT_TIMEOUT = 30
_POLL_TIMEOUT = 15
_POLL_WAIT = 2
_STATUS_HOOK = None


def set_status_hook(hook):
    """End registers this so each poll can refresh the open project."""
    global _STATUS_HOOK
    _STATUS_HOOK = hook


def job_message(label, nodes):
    """One line for the progress panel: ``资产锁定 3/7 · name 进行中``."""
    nodes = [item for item in (nodes or []) if isinstance(item, dict)]
    done = sum(1 for item in nodes if item.get("status") == "完成")
    total = len(nodes)
    text = "%s %s/%s" % (label, done, total) if total else str(label or "")
    active = next((item for item in nodes if item.get("status") == "进行中"), None)
    if active:
        name = active.get("name") or active.get("id") or ""
        if name:
            text = "%s · %s 进行中" % (text, name)
    return text


def await_job(method, params, on_status=None, submit_timeout=_SUBMIT_TIMEOUT):
    """Submit one model call, then poll until it finishes. Each HTTP call stays short."""
    snap = call(method, params or {}, timeout=submit_timeout)
    _notify(snap, on_status)
    finished, result = _job_done(snap)
    if finished:
        return result
    while True:
        time.sleep(_POLL_WAIT)
        snap = call("llm.status", {"job_id": (snap or {}).get("job_id") or ""}, timeout=_POLL_TIMEOUT)
        _notify(snap, on_status)
        finished, result = _job_done(snap)
        if finished:
            return result


def _notify(snap, on_status):
    hook = _STATUS_HOOK
    if hook is not None:
        try:
            hook(snap)
        except Exception:
            pass
    if on_status is not None:
        on_status(snap)


def _job_done(snap):
    status = (snap or {}).get("status")
    if status == "succeeded":
        return True, snap.get("result")
    if status in ("failed", "lost"):
        raise RemoteError((snap or {}).get("error") or "AI 服务没有响应")
    return False, None


def write(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None, on_status=None):
    return await_job(
        "write",
        {
            "brief": brief or "",
            "skill_pack": skill_pack or "",
            "debug_dir": str(debug_dir) if debug_dir else "",
            "model": model or "",
            "template": template or "",
            "trace_prompts": str(trace_prompts or ""),
        },
        on_status=on_status,
    )


def ask_text(messages, model=None, timeout=45, max_output_tokens=400):
    """One short sync completion. Used by asset patch classification."""
    result = call(
        "text.ask",
        {
            "messages": messages or [],
            "model": model or "",
            "timeout": timeout,
            "max_output_tokens": max_output_tokens,
        },
        timeout=max(float(timeout or 45) + 5, 20),
    )
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    return str(result or "").strip()


def read_upload(image_path, trace_prompts=None, on_status=None):
    result = await_job(
        "vision.read",
        {"path": str(image_path), "trace_prompts": str(trace_prompts or "")},
        on_status=on_status,
    )
    if isinstance(result, dict):
        return result.get("text") or ""
    return result or ""
