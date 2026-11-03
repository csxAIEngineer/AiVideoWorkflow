"""MiniMax H3 video via DashScope async video-synthesis.

One model covers text, first frame, first-and-last, and reference.
Submit with X-DashScope-Async: enable.
SUCCEEDED becomes completed. FAILED becomes failed.
Frame roles and reference roles stay in separate requests.

Cloud uses the current Qwen host. Local uses MINIMAX_H3_BASE_URL
(default http://127.0.0.1:8765) when the page or MINIMAX_VIDEO selects local.
This deployment sets that variable to OmniRef Ref2VA (FastH3-Omni-FP8).
Submit, poll, and download stay on the same DashScope paths. Bearer may be
a dummy; the local server ignores it.

Local OmniRef requires at least one reference_image or reference_video.
reference_audio may ride along with those. first_frame, last_frame, and
text-only submits are rejected before the request. Cloud still accepts
text, frames, and references.

Submit parameters come in two sets, preview and generate. They share one
schema and the same defaults. Final render passes mode=generate. Omitting
mode does the same. MINIMAX_VIDEO_PREVIEW_PARAMETERS and
MINIMAX_VIDEO_GENERATE_PARAMETERS are JSON objects that override fields
on that set. ratio and duration stay empty in the set so each shot fills
them, unless that mode's JSON sets them. url and model on the same object
choose the video-synthesis host and the request model. Empty url keeps the
current Qwen host or MINIMAX_H3_BASE_URL. Empty model keeps MiniMax/MiniMax-H3.
"""

import json
import os
import urllib.parse
from pathlib import Path

from AI.qwen_api.client import (
    QwenClient,
    QwenError,
    error_text,
    extract_media_url,
    load_dotenv,
    normalize_host,
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
# Visual roles OmniRef will start a job from. Audio alone is not enough.
LOCAL_VISUAL_REFERENCE_ROLES = frozenset(("reference_image", "reference_video"))
# OmniRef accepts preset and does not switch step count because of it.
# Leave width and height unset. An explicit pair replaces the default canvas
# (short edge 480; 16:9 is 832×480). Frames stay unset. resolution stays "768P".
LOCAL_DEFAULT_PARAMETERS = {
    "preset": "fast",
}
# Alias kept for call sites that still name LOCAL_FAST_PARAMETERS.
LOCAL_FAST_PARAMETERS = dict(LOCAL_DEFAULT_PARAMETERS)
# Both modes store this shape. null ratio and duration mean "use this shot".
# preset is part of the set and is written only on the local request.
# url and model are not DashScope parameter fields. null url keeps the
# client host. model defaults to VIDEO_MODEL.
SUBMIT_PARAMETER_KEYS = ("resolution", "ratio", "duration", "watermark", "preset")
MODE_TARGET_KEYS = ("url", "model")
MODE_CONFIG_KEYS = SUBMIT_PARAMETER_KEYS + MODE_TARGET_KEYS
_REQUEST_SKIP_KEYS = frozenset(SUBMIT_PARAMETER_KEYS) | frozenset(MODE_TARGET_KEYS)
PREVIEW_PARAMETERS_ENV = "MINIMAX_VIDEO_PREVIEW_PARAMETERS"
GENERATE_PARAMETERS_ENV = "MINIMAX_VIDEO_GENERATE_PARAMETERS"
_PARAMETER_ENV = {
    "preview": PREVIEW_PARAMETERS_ENV,
    "generate": GENERATE_PARAMETERS_ENV,
}
_SUBMIT_MODE_ALIASES = {
    "preview": "preview",
    "预览": "preview",
    "generate": "generate",
    "生成": "generate",
}


def _default_submit_parameters():
    """One copy of the shared schema. preview and generate start equal."""
    return {
        "resolution": VIDEO_RESOLUTION,
        "ratio": None,
        "duration": None,
        "watermark": False,
        "preset": LOCAL_DEFAULT_PARAMETERS["preset"],
        "url": None,
        "model": VIDEO_MODEL,
    }


PREVIEW_PARAMETERS = _default_submit_parameters()
GENERATE_PARAMETERS = _default_submit_parameters()
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


def normalize_video_submit_mode(value):
    """preview or generate. Empty selects generate, the historical submit."""
    text = str(value or "").strip().lower()
    if not text:
        return "generate"
    name = _SUBMIT_MODE_ALIASES.get(text, "")
    if not name:
        raise QwenError("出镜提交模式只能是 preview 或 generate")
    return name


def _parameter_override(mode_name):
    """JSON object from the mode's env var. Missing or blank means no override."""
    env_name = _PARAMETER_ENV[mode_name]
    raw = os.environ.get(env_name)
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise QwenError("%s 必须是 JSON 对象" % env_name)
    if not isinstance(parsed, dict):
        raise QwenError("%s 必须是 JSON 对象" % env_name)
    return parsed


def video_mode_parameters(mode=None):
    """Configured parameters for preview or generate, before this shot is filled in."""
    name = normalize_video_submit_mode(mode)
    spec = dict(PREVIEW_PARAMETERS if name == "preview" else GENERATE_PARAMETERS)
    spec.update(_parameter_override(name))
    return spec


def _text_parameter(field, value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise QwenError("parameters.%s 必须是非空字符串" % field)


def _blank_target(value):
    return value is None or (isinstance(value, str) and not value.strip())


def video_submit_target(mode, fallback_url):
    """Host and model for one submit.

    An empty url keeps fallback_url (the Qwen host, or MINIMAX_H3_BASE_URL
    when the page selected local). An empty model keeps VIDEO_MODEL.
    """
    spec = video_mode_parameters(mode)
    raw_url = spec.get("url")
    if _blank_target(raw_url):
        base = str(fallback_url or "").rstrip("/")
    else:
        if not isinstance(raw_url, str) or not raw_url.strip().startswith(("http://", "https://")):
            raise QwenError("url 必须是 http 或 https 地址")
        base = normalize_host(raw_url.strip())
    raw_model = spec.get("model")
    if _blank_target(raw_model):
        model = VIDEO_MODEL
    else:
        if not isinstance(raw_model, str):
            raise QwenError("model 必须是非空字符串")
        model = raw_model.strip()
    return base, model


def _poll_host(url):
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return "%s://%s" % (parsed.scheme, parsed.netloc)


def _watermark(value):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("false", "0", "no", "off"):
        return False
    if text in ("true", "1", "yes", "on"):
        return True
    raise QwenError("parameters.watermark 必须是布尔值")


def build_video_parameters(mode, aspect_ratio, duration, local):
    """Request parameters for one submit.

    Cloud sends resolution, ratio, duration, and watermark. Local then
    update()s LOCAL_DEFAULT_PARAMETERS and writes this mode's preset.
    A JSON override replaces fields on the selected mode after that merge.
    preset is left off the cloud body. url and model stay off this object.
    Extra override keys are sent as-is.
    """
    spec = video_mode_parameters(mode)
    if spec.get("ratio") is None:
        ratio = video_ratio(aspect_ratio)
    else:
        ratio = video_ratio(spec.get("ratio"))
    if spec.get("duration") is None:
        seconds = video_duration(duration)
    else:
        seconds = video_duration(spec.get("duration"))
    parameters = {
        "resolution": _text_parameter("resolution", spec.get("resolution")),
        "ratio": ratio,
        "duration": seconds,
        "watermark": _watermark(spec.get("watermark")),
    }
    if local:
        parameters.update(LOCAL_DEFAULT_PARAMETERS)
        parameters["preset"] = _text_parameter("preset", spec.get("preset"))
    for key, value in spec.items():
        if key in _REQUEST_SKIP_KEYS or value is None:
            continue
        parameters[key] = value
    return parameters


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


def _require_local_references(media):
    """OmniRef Ref2VA: a reference image or video, never a Trim frame role."""
    if any(item["type"] in FRAME_ROLES for item in media):
        raise QwenError(
            "本地 OmniRef Ref2VA 不接受 first_frame 和 last_frame，"
            "请使用 reference_image、reference_video 或 reference_audio"
        )
    if any(item["type"] in LOCAL_VISUAL_REFERENCE_ROLES for item in media):
        return media
    if any(item["type"] == "reference_audio" for item in media):
        raise QwenError(
            "本地 OmniRef Ref2VA 的 reference_audio 需要同时提供 reference_image 或 reference_video"
        )
    raise QwenError("本地 OmniRef Ref2VA 至少需要一条 reference_image 或 reference_video")


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
        mode=None,
    ):
        """Submit one clip. mode is preview or generate. Omitted mode is generate.

        The caller model is ignored. The mode config supplies the body model,
        and VIDEO_MODEL when that field is empty.
        """
        del model, idempotency_key
        prompt = str(text or "").strip()
        if not prompt:
            raise QwenError("出镜提示词是空的")
        media = _media(images)
        if self.local:
            media = _require_local_references(media)
        base_url, model_name = video_submit_target(mode, self.client.base_url)
        parameters = build_video_parameters(mode, aspect_ratio, duration, self.local)
        body = {
            "model": model_name,
            "input": {"prompt": prompt},
            "parameters": parameters,
        }
        if media:
            body["input"]["media"] = media
        data = self.client.request(
            "POST",
            base_url + VIDEO_PATH,
            body,
            timeout=timeout,
            headers=ASYNC_HEADER,
        )
        task = self._finish(VideoTask(data), "", base_url)
        if not task.id:
            raise QwenError("千问出镜没有返回任务号")
        return task

    def poll_video(self, task, timeout=60):
        task_id = getattr(task, "id", "") or ""
        return self.poll(task_id, timeout=timeout, previous=task)

    def poll(self, task_id, timeout=60, previous=None, poll_url=None):
        url = str(poll_url or "").strip()
        if not url and previous is not None:
            url = str(getattr(previous, "poll_url", "") or "").strip()
        if url.startswith(("http://", "https://")):
            data = self.client.request("GET", url, None, timeout=timeout)
            host = _poll_host(url) or self.client.base_url
        else:
            data = self.client.get_task(task_id, timeout=timeout)
            host = self.client.base_url
        updated = self._finish(VideoTask(data), task_id, host)
        if previous is not None and not updated.poll_url:
            updated.poll_url = getattr(previous, "poll_url", "") or updated.poll_url
        return updated

    def download_video(self, task, dest, timeout=300):
        url = task if isinstance(task, str) else (getattr(task, "url", "") or "")
        return self.client.download(url, dest, timeout=timeout)

    def _finish(self, task, task_id, base_url=None):
        if not task.id:
            task.id = str(task_id or "")
        host = str(base_url or self.client.base_url).rstrip("/")
        if task.id and not task.poll_url:
            task.poll_url = host + "/api/v1/tasks/" + task.id
        return task
