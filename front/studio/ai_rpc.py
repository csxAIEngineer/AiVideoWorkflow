"""Studio JSON-RPC client. It does not import the AI package."""

import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "http://127.0.0.1:8791"
_PROC = None
_READY = False


class RemoteError(RuntimeError):
    pass


def _url():
    return (os.environ.get("AIVIDEO_AI_URL") or DEFAULT_URL).rstrip("/")


def _post(url, method, params, timeout):
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url + "/rpc",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RemoteError("AI 服务没有响应：%s" % error)
    if body.get("error"):
        raise RemoteError((body["error"] or {}).get("message") or "AI 调用失败")
    return body.get("result")


def _reachable(url):
    try:
        _post(url, "ping", {}, timeout=0.4)
        return True
    except RemoteError:
        return False


def _stop():
    if _PROC is not None and _PROC.poll() is None:
        _PROC.terminate()


atexit.register(_stop)


def _ensure():
    global _PROC, _READY
    url = _url()
    if _READY:
        return url
    if _reachable(url):
        _READY = True
        return url
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost"):
        raise RemoteError("AI 服务不在本机：%s" % url)
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
            _READY = True
            return url
        time.sleep(0.1)
    raise RemoteError("AI 服务没有在 20 秒内起来")


def ai(method, params=None, timeout=180):
    return _post(_ensure(), method, params or {}, timeout)
