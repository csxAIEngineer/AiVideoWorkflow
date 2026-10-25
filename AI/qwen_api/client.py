"""Shared Qwen HTTP client. Test uses QWEN_API_KEY. Token Plan uses TOKE_PLAN_QWEN_API_KEY."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

QWEN_ENVS = {
    "test": "https://maas.qianwenaiapi.com",
    "prod": "https://token-plan.maas.qianwenaiapi.com",
}
DEFAULT_QWEN_ENV = "test"
DEFAULT_BASE_URL = QWEN_ENVS[DEFAULT_QWEN_ENV]
QWEN_KEY_VARS = {
    "test": "QWEN_API_KEY",
    "prod": "TOKE_PLAN_QWEN_API_KEY",
}
_HOST_SUFFIXES = ("/compatible-mode/v1", "/api/v1")
_ENV_ALIASES = {
    "test": "test",
    "testing": "test",
    "测试": "test",
    "prod": "prod",
    "production": "prod",
    "生产": "prod",
}


class QwenError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


def load_dotenv(path=None, override=False):
    """Read KEY=VALUE lines into the process environment. Existing values stay unless override."""
    env_path = Path(path) if path else _find_env()
    if env_path is None or not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
    return env_path


def _find_env():
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
        if (parent / ".git").exists():
            break
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env
    return None


def normalize_qwen_env(value):
    """test or prod. Empty when the value is not one of those names."""
    return _ENV_ALIASES.get(str(value or "").strip().lower(), "")


def qwen_choice_path(env_path=None):
    """data/qwen_env next to the .env that configured this process."""
    if env_path is not None:
        candidate = Path(env_path)
        root = candidate.parent if candidate.is_file() else None
    else:
        found = _find_env()
        root = found.parent if found else None
    if root is None:
        return None
    return root / "data" / "qwen_env"


def saved_qwen_env(env_path=None):
    path = qwen_choice_path(env_path)
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    first = text.splitlines()[0] if text.splitlines() else ""
    return normalize_qwen_env(first)


def active_qwen_env(env_path=None):
    """The named environment, or custom when only QWEN_BASE_URL is set."""
    saved = saved_qwen_env(env_path)
    if saved:
        return saved
    named = normalize_qwen_env(os.environ.get("QWEN_ENV", ""))
    if named:
        return named
    if str(os.environ.get("QWEN_BASE_URL") or "").strip():
        return "custom"
    return DEFAULT_QWEN_ENV


def resolve_qwen_host(env_path=None):
    """Host for this process. A saved page choice wins, then QWEN_ENV, then QWEN_BASE_URL."""
    saved = saved_qwen_env(env_path)
    if saved:
        return QWEN_ENVS[saved]
    named = normalize_qwen_env(os.environ.get("QWEN_ENV", ""))
    if named:
        return QWEN_ENVS[named]
    explicit = str(os.environ.get("QWEN_BASE_URL") or "").strip()
    if explicit:
        return normalize_host(explicit)
    return DEFAULT_BASE_URL


def describe_qwen_target(env_path=None):
    labels = {"test": "测试", "prod": "生产", "custom": "自定义"}
    name = active_qwen_env(env_path)
    return "千问 %s %s/compatible-mode/v1" % (labels.get(name, name), resolve_qwen_host(env_path))


def qwen_key_var(env_path=None):
    """Env var for the active host. Token Plan is production."""
    name = active_qwen_env(env_path)
    return QWEN_KEY_VARS.get(name, "QWEN_API_KEY")


def qwen_api_key(env_path=None, api_key=None):
    """The key for this environment, plus the variable name used when it is missing."""
    var = qwen_key_var(env_path)
    if api_key is not None:
        return str(api_key).strip(), var
    return str(os.environ.get(var) or "").strip(), var


def normalize_host(value):
    """Host only. A compatible-mode or /api/v1 suffix is stripped so callers can append one path."""
    root = str(value or "").strip().rstrip("/")
    if not root:
        return DEFAULT_BASE_URL
    for suffix in _HOST_SUFFIXES:
        if root.endswith(suffix):
            root = root[: -len(suffix)].rstrip("/")
    return root or DEFAULT_BASE_URL


def extract_media_url(payload):
    """First http(s) URL in a task payload, including DashScope results and message content."""
    if isinstance(payload, str) and payload.startswith(("http://", "https://")):
        return payload
    if isinstance(payload, list):
        for item in payload:
            found = extract_media_url(item)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("url", "video_url", "download_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    image = payload.get("image")
    if isinstance(image, str) and image.startswith(("http://", "https://")):
        return image
    for key in ("audio", "results", "data", "choices", "content", "message", "output"):
        if key in payload:
            found = extract_media_url(payload.get(key))
            if found:
                return found
    return ""


def error_text(payload):
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    if isinstance(error, str):
        return error
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    status = str(output.get("task_status") or payload.get("task_status") or "").upper()
    if status in ("FAILED", "CANCELED", "UNKNOWN"):
        return str(output.get("message") or payload.get("message") or output.get("code") or "")
    return ""


class QwenClient(object):
    """Bearer auth against one MaaS host. Async jobs are GET /api/v1/tasks/{task_id}."""

    def __init__(self, api_key=None, env_path=None, base_url=None, opener=None):
        load_dotenv(env_path)
        self.api_key, key_var = qwen_api_key(env_path, api_key)
        if not self.api_key:
            raise QwenError("未配置 %s" % key_var)
        if base_url is not None:
            self.base_url = normalize_host(base_url)
        else:
            self.base_url = resolve_qwen_host(env_path)
        self._opener = opener

    @property
    def compatible_base(self):
        return self.base_url + "/compatible-mode/v1"

    def get_task(self, task_id, timeout=60):
        if not str(task_id or "").strip():
            raise QwenError("缺少 task_id")
        return self.request("GET", "/api/v1/tasks/%s" % task_id, None, timeout=timeout)

    def request(self, method, path, body=None, timeout=60, headers=None):
        response = self._open(method, path, body, timeout, headers or {}, auth=True)
        try:
            raw = response.read().decode("utf-8")
        finally:
            self._close(response)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise QwenError("千问没有返回 JSON")
        if isinstance(data, dict) and data.get("code") and not data.get("output"):
            raise QwenError(str(data.get("message") or data.get("code")))
        return data if isinstance(data, dict) else {"data": data}

    def download(self, url, dest, timeout=120):
        if not url:
            raise QwenError("没有可下载的地址")
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        request = urllib.request.Request(str(url), method="GET")
        try:
            response = self._open_request(request, timeout)
            try:
                payload = response.read()
            finally:
                self._close(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise QwenError("下载失败 %s：%s" % (error.code, detail[:500]), error.code, detail)
        except urllib.error.URLError as error:
            raise QwenError("下载网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise QwenError("下载网络错误：%s" % error)
        temporary.write_bytes(payload)
        temporary.replace(target)
        return target

    def _open(self, method, path, body, timeout, headers, auth):
        url = path if str(path).startswith("http") else self.base_url + "/" + str(path).lstrip("/")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {}
        if auth:
            request_headers["Authorization"] = "Bearer " + self.api_key
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            return self._open_request(request, timeout)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise QwenError("千问返回 %s：%s" % (error.code, detail[:800]), error.code, detail)
        except urllib.error.URLError as error:
            raise QwenError("千问网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise QwenError("千问网络错误：%s" % error)

    def _open_request(self, request, timeout):
        opener = self._opener or urllib.request.urlopen
        # region agent log
        try:
            body = getattr(request, "data", None)
            payload = {
                "sessionId": "eab7ec",
                "runId": "post-fix",
                "hypothesisId": "A",
                "location": "AI/qwen_api/client.py:_open_request",
                "message": "request body before urlopen",
                "data": {
                    "bodyType": type(body).__name__,
                    "timeout": timeout if isinstance(timeout, (int, float)) else type(timeout).__name__,
                    "method": request.get_method(),
                },
                "timestamp": int(__import__("time").time() * 1000),
            }
            with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass
        # endregion
        return opener(request, timeout=timeout)

    @staticmethod
    def _close(response):
        close = getattr(response, "close", None)
        if close:
            close()
