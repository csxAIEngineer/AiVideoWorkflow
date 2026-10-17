"""Stdlib client for https://api.icreat.ai GPT Image 2. Python 3.9, no extra packages."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.icreat.ai"
SUBMIT_PATH = "/v1/task/submit/openai/gpt-image-2/text-to-image"
RESULT_PATH = "/v1/task/result"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "high"
ALLOWED_SIZES = (
    "1024x1024",
    "2048x2048",
    "2880x2880",
    "1280x720",
    "2560x1440",
    "3840x2160",
    "720x1280",
    "1440x2560",
    "2160x3840",
    "1152x864",
    "2304x1728",
    "3264x2448",
    "864x1152",
    "1728x2304",
    "2448x3264",
    "1248x832",
    "2496x1664",
    "3504x2336",
    "834x1248",
    "1664x2496",
    "2336x3504",
    "1120x896",
    "2240x1792",
    "3200x2560",
    "896x1120",
    "1792x2240",
    "2560x3200",
    "1456x624",
    "3024x1296",
    "3696x1584",
)
USER_AGENT = "AiVideo/1.0"


class ICreatError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class ImageTask(object):
    def __init__(self, payload):
        self.raw = payload
        data = payload if isinstance(payload, dict) else {}
        self.id = str(data.get("task_id") or data.get("taskId") or "")
        self.status = _task_status(data)
        self.url = _result_url(payload)
        error = data.get("error") or data.get("message") or ""
        if isinstance(error, dict):
            error = error.get("message") or ""
        self.error = str(error or "")


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


def image_size():
    size = (os.environ.get("ICREAT_IMAGE_SIZE") or "").strip() or DEFAULT_SIZE
    if size not in ALLOWED_SIZES:
        raise ICreatError("iCreat 不接受尺寸 %s。默认 1024x1024，竖屏可用 720x1280、1440x2560 或 2160x3840。" % size)
    return size


def image_quality():
    return (os.environ.get("ICREAT_IMAGE_QUALITY") or "").strip() or DEFAULT_QUALITY


def _task_status(payload):
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status") or ""
    data = payload.get("data")
    if not status and isinstance(data, dict):
        status = data.get("status") or ""
    return str(status or "")


def _result_url(payload):
    if isinstance(payload, str) and payload.startswith("http"):
        return payload
    if isinstance(payload, dict):
        for key in ("url", "download_url"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("data", "result", "results", "output", "images"):
            if key in payload:
                found = _result_url(payload.get(key))
                if found:
                    return found
        return ""
    if isinstance(payload, list):
        for item in payload:
            found = _result_url(item)
            if found:
                return found
    return ""


class ICreatClient(object):
    def __init__(self, api_key=None, env_path=None, base_url=None, opener=None):
        load_dotenv(env_path)
        self.api_key = api_key or os.environ.get("ICREAT_API_KEY", "")
        if not self.api_key:
            raise ICreatError("缺少 ICREAT_API_KEY。请写在项目根目录的 .env 里。")
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._opener = opener

    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
        body = {
            "prompt": prompt,
            "size": size or image_size(),
            "quality": quality or image_quality(),
        }
        images = _image_list(image)
        if images:
            body["image"] = images
        data = self._request("POST", SUBMIT_PATH, body, timeout=timeout)
        if not isinstance(data, dict):
            raise ICreatError("iCreat 提交没有返回任务号")
        task = ImageTask(data)
        if not task.id:
            raise ICreatError("iCreat 提交没有返回任务号")
        if not task.status:
            task.status = "SUBMITTED"
        return task

    def query_result(self, task_id, timeout=60):
        if not task_id:
            raise ICreatError("缺少 task_id")
        data = self._request("POST", RESULT_PATH, {"task_id": task_id}, timeout=timeout)
        payload = _unwrap_task(data, task_id)
        task = ImageTask(payload)
        task.id = task.id or str(task_id)
        return task

    def query_status(self, task_id, timeout=60):
        return self.query_result(task_id, timeout=timeout)

    def get_result(self, task_id, timeout=60):
        task = self.query_result(task_id, timeout=timeout)
        status = task.status.upper()
        if status in ("FAILED", "NOT_FOUND"):
            raise ICreatError(task.error or status)
        if not task.url:
            raise ICreatError("任务完成了，但没有图片地址")
        return task

    def download_image(self, url, dest, timeout=120):
        if not url:
            raise ICreatError("没有可下载的图片地址")
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        request = urllib.request.Request(url, method="GET")
        try:
            response = self._open_request(request, timeout, auth=False)
            try:
                payload = response.read()
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ICreatError("下载图片失败 %s：%s" % (error.code, detail[:500]), error.code, detail)
        except urllib.error.URLError as error:
            raise ICreatError("下载图片网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise ICreatError("下载图片网络错误：%s" % error)
        temporary.write_bytes(payload)
        temporary.replace(target)
        return target

    def _request(self, method, path, body, timeout):
        response = self._open(method, path, body, timeout)
        try:
            raw = response.read().decode("utf-8")
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if not raw:
            return {}
        return json.loads(raw)

    def _open(self, method, path, body, timeout):
        url = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return self._open_request(request, timeout, auth=True)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ICreatError("iCreat API 返回 %s：%s" % (error.code, detail[:800]), error.code, detail)
        except urllib.error.URLError as error:
            raise ICreatError("iCreat API 网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise ICreatError("iCreat API 网络错误：%s" % error)

    def _open_request(self, request, timeout, auth):
        if self._opener:
            return self._opener(request, timeout)
        return urllib.request.urlopen(request, timeout=timeout)


def _unwrap_task(data, task_id):
    if isinstance(data, list):
        return {"task_id": task_id, "status": "SUCCEEDED", "result": data}
    if not isinstance(data, dict):
        return {"task_id": task_id}
    inner = data.get("data")
    if isinstance(inner, dict) and not data.get("status") and not data.get("task_id") and not data.get("taskId"):
        merged = dict(inner)
        merged.setdefault("task_id", task_id)
        return merged
    return data


def _image_list(image):
    if not image:
        return []
    if isinstance(image, str):
        text = image.strip()
        return [text] if text else []
    values = []
    for item in image:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values
