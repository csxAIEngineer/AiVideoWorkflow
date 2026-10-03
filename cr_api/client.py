"""Stdlib client for https://api.creative-reasoning.com. Python 3.9, no extra packages."""

import json
import os
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.creative-reasoning.com"

TEXT_MODELS = {
    "gpt": ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"),
    "glm": ("glm-5.3", "glm-5.3-flash", "glm-5.2"),
    "minimax": ("MiniMax-M3",),
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4.1-flash"),
}

VIDEO_MODELS = (
    "minimax-h3-text-to-video",
    "minimax-h3-image-to-video",
    "minimax-h3-reference-to-video",
)

ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")

IMAGE_ROLES = ("first_frame", "last_frame", "reference_image")


class CRError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class ChatResult(object):
    def __init__(self, content, reasoning_content, model, raw):
        self.content = content or ""
        self.reasoning_content = reasoning_content or ""
        self.model = model
        self.raw = raw


class VideoTask(object):
    def __init__(self, payload):
        self.raw = payload
        self.id = payload.get("id")
        self.status = payload.get("status")
        self.poll_url = payload.get("poll_url") or ""
        data = payload.get("data") or []
        self.url = data[0].get("url") if data and isinstance(data[0], dict) else None
        error = payload.get("error") or {}
        self.error = error.get("message") if isinstance(error, dict) else None


def is_thinking_model(model):
    name = (model or "").lower()
    return name.startswith("glm-") or name.startswith("deepseek-")


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
    package_root = Path(__file__).resolve().parents[1]
    for candidate in (package_root / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            return candidate
    return None


class CRClient(object):
    def __init__(self, api_key=None, env_path=None):
        load_dotenv(env_path)
        self.api_key = api_key or os.environ.get("CR_API_KEY", "")
        if not self.api_key:
            raise CRError("缺少 CR_API_KEY。请写在项目根目录的 .env 里。")
        self.base_url = BASE_URL

    def chat(self, messages, model="deepseek-v4-pro", max_tokens=None, stream=False, timeout=300, response_format=None):
        if stream and model == "minimax-m3":
            raise CRError("流式输出要用大写的 MiniMax-M3，小写的 minimax-m3 不支持。")
        if max_tokens is not None and is_thinking_model(model) and int(max_tokens) < 2000:
            raise CRError("GLM 和 DeepSeek 会先思考。max_tokens 至少设 2000，或者不要传。")
        body = {"model": model, "messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if response_format is not None:
            body["response_format"] = response_format
        if stream:
            body["stream"] = True
            return self._stream(body, model, timeout)
        data = self._request("POST", "/v1/chat/completions", body, timeout=timeout)
        message = ((data.get("choices") or [{}])[0].get("message") or {})
        return ChatResult(
            content=message.get("content") or "",
            reasoning_content=message.get("reasoning_content") or "",
            model=data.get("model") or model,
            raw=data,
        )

    def submit_video(self, text, model="minimax-h3-text-to-video", duration=4, aspect_ratio="9:16", images=None, idempotency_key=None, timeout=180):
        if model not in VIDEO_MODELS:
            raise CRError("未知视频模型 %s。可用：%s" % (model, "、".join(VIDEO_MODELS)))
        try:
            seconds = int(duration)
        except (TypeError, ValueError):
            raise CRError("duration 必须是 4 到 15 的整数")
        if seconds < 4 or seconds > 15:
            raise CRError("duration 必须是 4 到 15 的整数")
        if model == "minimax-h3-text-to-video" and aspect_ratio not in ASPECT_RATIOS:
            raise CRError("文生视频比例只能是 %s" % "、".join(ASPECT_RATIOS))
        content = [{"type": "text", "text": text}]
        for image in images or []:
            role = image.get("role")
            url = image.get("url")
            if role not in IMAGE_ROLES or not url:
                raise CRError("图片需要 role（first_frame、last_frame、reference_image）和 url")
            content.append({"type": "image_url", "role": role, "image_url": {"url": url}})
        body = {
            "model": model,
            "content": content,
            "duration": seconds,
            "aspect_ratio": aspect_ratio,
        }
        data = self._request(
            "POST",
            "/unified/v1/videos/tasks",
            body,
            timeout=timeout,
            headers={"Idempotency-Key": idempotency_key or ("job-" + uuid.uuid4().hex)},
        )
        return VideoTask(data)

    def poll_video(self, task, timeout=60):
        if not task.poll_url:
            raise CRError("这个任务没有 poll_url")
        data = self._request("GET", task.poll_url, None, timeout=timeout)
        updated = VideoTask(data)
        if not updated.poll_url:
            updated.poll_url = task.poll_url
        return updated

    def wait_video(self, task, interval=10, timeout=900, on_status=None):
        deadline = time.time() + timeout
        current = task
        seen = None
        while current.status not in ("completed", "failed"):
            if on_status and current.status != seen:
                seen = current.status
                on_status(current)
            if time.time() >= deadline:
                raise CRError("视频任务超时，最后状态：%s" % current.status)
            time.sleep(interval)
            current = self.poll_video(current)
        if on_status and current.status != seen:
            on_status(current)
        if current.status == "failed":
            raise CRError(current.error or "视频任务失败")
        if not current.url:
            raise CRError("任务完成了，但没有视频地址")
        return current

    def download_video(self, task, dest, timeout=300):
        if not task.url:
            raise CRError("没有可下载的视频地址")
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(task.url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                target.write_bytes(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise CRError("下载视频失败 %s：%s" % (error.code, detail[:500]), error.code, detail)
        return target

    def _stream(self, body, model, timeout):
        response = self._open("POST", "/v1/chat/completions", body, timeout, {})
        try:
            for event in _iter_sse(response):
                choice = ((event.get("choices") or [{}])[0].get("delta") or {})
                yield ChatResult(
                    content=choice.get("content") or "",
                    reasoning_content=choice.get("reasoning_content") or "",
                    model=event.get("model") or model,
                    raw=event,
                )
        finally:
            response.close()

    def _request(self, method, path, body, timeout, headers=None):
        response = self._open(method, path, body, timeout, headers or {})
        try:
            raw = response.read().decode("utf-8")
        finally:
            response.close()
        if not raw:
            return {}
        return json.loads(raw)

    def _open(self, method, path, body, timeout, headers):
        url = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"Authorization": "Bearer " + self.api_key}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers)
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise CRError("CR API 返回 %s：%s" % (error.code, detail[:800]), error.code, detail)
        except urllib.error.URLError as error:
            raise CRError("CR API 网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise CRError("CR API 网络错误：%s" % error)


def _iter_sse(response):
    pending = b""
    while True:
        chunk = response.read(2048)
        if not chunk:
            break
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                return
            if data:
                yield json.loads(data.decode("utf-8"))
