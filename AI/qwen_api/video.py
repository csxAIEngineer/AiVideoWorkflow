"""MiniMax H3 video via DashScope async video-synthesis.

One model covers text, first frame, first-and-last, and reference.
Submit with X-DashScope-Async: enable.
SUCCEEDED becomes completed. FAILED becomes failed.
Frame roles and reference roles stay in separate requests.

Cloud uses the current Qwen host. Local uses MINIMAX_H3_BASE_URL
(default http://127.0.0.1:8765) when the page or MINIMAX_VIDEO selects local.
The submit, poll, and download calls stay the same. Bearer may be a dummy.
"""

import os
from pathlib import Path

from AI.qwen_api.client import (
    QwenClient,
    QwenError,
    error_text,
    extract_media_url,
    load_dotenv,
    qwen_api_key,
    qwen_choice_path,
    resolve_qwen_host,
)

VIDEO_MODEL = "MiniMax/MiniMax-H3"
VIDEO_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
ASYNC_HEADER = {"X-DashScope-Async": "enable"}
VIDEO_RESOLUTION = "768P"
# MiniMax H3 documents these ratios. Anything else stays adaptive.
DOCUMENTED_RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
FRAME_ROLES = frozenset(("first_frame", "last_frame"))
REFERENCE_ROLES = frozenset(("reference_image", "reference_video", "reference_audio"))
MEDIA_ROLES = FRAME_ROLES | REFERENCE_ROLES
DEFAULT_LOCAL_VIDEO_URL = "http://127.0.0.1:8765"
# Local preview only. Cloud bodies omit these. h3-local-api accepts
# parameters.preset == "fast" and runs this argv. It still checks 768P, ratio,
# and duration 4–15, but those do not choose the canvas or length:
# --width 512 --height 512 --render-width 320 --render-height 320
# --frames 22 --steps 4 --layers 50 --reuse 1 --ssd-streaming
# Keep the cloud fields so that check passes.
LOCAL_FAST_PARAMETERS = {
    "preset": "fast",
    "width": 512,
    "height": 512,
    "render_width": 320,
    "render_height": 320,
    "frames": 22,
    "steps": 4,
    "layers": 50,
    "reuse": 1,
    "ssd_streaming": True,
}
LOCAL_DUMMY_KEY = "local"
_CHOICE_FILE = "minimax_video"
_HOST_SUFFIXES = ("/compatible-mode/v1", "/api/v1")
_MODE_ALIASES = {
    "cloud": "cloud",
    "qwen": "cloud",
    "dashscope": "cloud",
    "云端": "cloud",
    "local": "local",
    "localhost": "local",
    "本地": "local",
}


def normalize_minimax_video(value):
    """cloud or local. Empty when the value is not one of those names."""
    return _MODE_ALIASES.get(str(value or "").strip().lower(), "")


def minimax_choice_path(env_path=None):
    """data/minimax_video beside .env. Without .env, the process working directory."""
    if env_path is not None:
        candidate = Path(env_path)
        if not candidate.is_file():
            return None
        return candidate.parent / "data" / _CHOICE_FILE
    qwen_path = qwen_choice_path(None)
    if qwen_path is not None:
        return qwen_path.parent / _CHOICE_FILE
    return Path.cwd() / "data" / _CHOICE_FILE


def saved_minimax_video(env_path=None):
    path = minimax_choice_path(env_path)
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    first = lines[0] if lines else ""
    return normalize_minimax_video(first)


def active_minimax_video(env_path=None):
    """cloud or local. A saved page choice wins over MINIMAX_VIDEO."""
    saved = saved_minimax_video(env_path)
    if saved:
        return saved
    named = normalize_minimax_video(os.environ.get("MINIMAX_VIDEO", ""))
    return named or "cloud"


def local_video_base_url():
    """Local H3 host. MINIMAX_H3_BASE_URL overrides the localhost default."""
    explicit = str(os.environ.get("MINIMAX_H3_BASE_URL") or "").strip().rstrip("/")
    if not explicit:
        return DEFAULT_LOCAL_VIDEO_URL
    for suffix in _HOST_SUFFIXES:
        if explicit.endswith(suffix):
            explicit = explicit[: -len(suffix)].rstrip("/")
    return explicit or DEFAULT_LOCAL_VIDEO_URL


def local_video_api_key():
    """Bearer for the local server. Unset sends a dummy the server may ignore."""
    explicit = str(os.environ.get("MINIMAX_H3_API_KEY") or "").strip()
    return explicit or LOCAL_DUMMY_KEY


def video_client_identity(env_path=None):
    """Host and bearer the default QwenVideoClient would use."""
    load_dotenv(env_path)
    if active_minimax_video(env_path) == "local":
        return local_video_base_url(), local_video_api_key()
    key, _var = qwen_api_key(env_path)
    return resolve_qwen_host(env_path), key


def map_video_status(status):
    name = str(status or "")
    upper = name.upper()
    if upper == "SUCCEEDED" or name == "completed":
        return "completed"
    if upper in ("FAILED", "CANCELED", "UNKNOWN") or name == "failed":
        return "failed"
    return name


def video_ratio(aspect):
    value = str(aspect or "").strip() or "adaptive"
    if value in DOCUMENTED_RATIOS:
        return value
    return "adaptive"


def video_duration(duration):
    try:
        seconds = int(duration)
    except (TypeError, ValueError):
        raise QwenError("duration 必须是 4 到 15 的整数")
    if seconds < 4 or seconds > 15:
        raise QwenError("duration 必须是 4 到 15 的整数")
    return seconds


class VideoTask(object):
    def __init__(self, payload=None):
        data = payload if isinstance(payload, dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        source = output or data
        self.raw = payload
        self.id = str(source.get("task_id") or data.get("id") or data.get("task_id") or "")
        raw_status = source.get("task_status") or data.get("status") or source.get("status") or ""
        self.status = map_video_status(raw_status)
        self.poll_url = str(data.get("poll_url") or "")
        self.url = extract_media_url(source) or extract_media_url(data)
        error = data.get("error")
        if isinstance(error, dict):
            self.error = error.get("message")
        elif isinstance(error, str) and error:
            self.error = error
        else:
            self.error = error_text(data) or None


def _media(images):
    seen = []
    media = []
    for image in images or []:
        if not isinstance(image, dict):
            raise QwenError("素材需要 role（first_frame、last_frame、reference_image、reference_video、reference_audio）和 url")
        role = image.get("role")
        url = image.get("url")
        if role not in MEDIA_ROLES or not url:
            raise QwenError("素材需要 role（first_frame、last_frame、reference_image、reference_video、reference_audio）和 url")
        seen.append(role)
        media.append({"type": role, "url": url})
    if FRAME_ROLES.intersection(seen) and REFERENCE_ROLES.intersection(seen):
        raise QwenError("同一次提交不能同时使用首尾帧和参考素材")
    return media


class QwenVideoClient(object):
    def __init__(self, client=None, api_key=None, opener=None, base_url=None, env_path=None):
        self.local = False
        if client is not None:
            self.client = client
            return
        load_dotenv(env_path)
        if active_minimax_video(env_path) == "local":
            self.local = True
            self.client = QwenClient(
                api_key=local_video_api_key() if api_key is None else api_key,
                opener=opener,
                base_url=local_video_base_url() if base_url is None else base_url,
                env_path=env_path,
            )
            return
        self.client = QwenClient(api_key=api_key, opener=opener, base_url=base_url, env_path=env_path)

    def submit_video(
        self,
        text,
        model=None,
        duration=4,
        aspect_ratio=None,
        images=None,
        idempotency_key=None,
        timeout=180,
    ):
        del model, idempotency_key
        prompt = str(text or "").strip()
        if not prompt:
            raise QwenError("出镜提示词是空的")
        media = _media(images)
        parameters = {
            "resolution": VIDEO_RESOLUTION,
            "ratio": video_ratio(aspect_ratio),
            "duration": video_duration(duration),
            "watermark": False,
        }
        if self.local:
            parameters.update(LOCAL_FAST_PARAMETERS)
        body = {
            "model": VIDEO_MODEL,
            "input": {"prompt": prompt},
            "parameters": parameters,
        }
        if media:
            body["input"]["media"] = media
        data = self.client.request("POST", VIDEO_PATH, body, timeout=timeout, headers=ASYNC_HEADER)
        task = self._finish(VideoTask(data), "")
        if not task.id:
            raise QwenError("千问出镜没有返回任务号")
        return task

    def poll_video(self, task, timeout=60):
        task_id = getattr(task, "id", "") or ""
        return self.poll(task_id, timeout=timeout, previous=task)

    def poll(self, task_id, timeout=60, previous=None):
        data = self.client.get_task(task_id, timeout=timeout)
        updated = self._finish(VideoTask(data), task_id)
        if previous is not None and not updated.poll_url:
            updated.poll_url = getattr(previous, "poll_url", "") or updated.poll_url
        return updated

    def download_video(self, task, dest, timeout=300):
        url = task if isinstance(task, str) else (getattr(task, "url", "") or "")
        return self.client.download(url, dest, timeout=timeout)

    def _finish(self, task, task_id):
        if not task.id:
            task.id = str(task_id or "")
        if task.id and not task.poll_url:
            task.poll_url = self.client.base_url + "/api/v1/tasks/" + task.id
        return task
