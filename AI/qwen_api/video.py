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

Platform, model id, and parameter profile are separate registries in
AI.qwen_api.video_catalog. preview and generate are profiles. qwen and
omniref are platforms. MiniMax/MiniMax-H3 is a model id. A route binds
one profile to one platform and one model. Omitting the profile selects
generate. An empty route keeps this client's host and MiniMax/MiniMax-H3.

Overrides live in data/video_catalog.json next to .env. See
data/video_catalog.example.json. A missing file keeps these defaults.
API keys stay in .env.
"""

import os
import urllib.parse
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
from AI.qwen_api.video_catalog import (
    DEFAULT_MODEL_ID,
    DASH_SCOPE_SUBMIT_PATH,
    describe_profile,
    enforce_media,
    platform_credentials,
    resolve_model_id,
    resolve_plan,
)

VIDEO_MODEL = DEFAULT_MODEL_ID
VIDEO_PATH = DASH_SCOPE_SUBMIT_PATH
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
# Parameter profiles share this shape. url and model are route fields, not
# DashScope parameters. The legacy view below still shows them so older
# callers can read one dict per profile.
SUBMIT_PARAMETER_KEYS = ("resolution", "ratio", "duration", "watermark", "preset")
MODE_TARGET_KEYS = ("url", "model")
MODE_CONFIG_KEYS = SUBMIT_PARAMETER_KEYS + MODE_TARGET_KEYS
_REQUEST_SKIP_KEYS = frozenset(SUBMIT_PARAMETER_KEYS) | frozenset(("url", "model", "platform"))


def _legacy_profile_view():
    """Parameter profile plus empty route fields. preview and generate start equal."""
    from AI.qwen_api.video_catalog import default_profile

    view = default_profile()
    view["preset"] = LOCAL_DEFAULT_PARAMETERS["preset"]
    view["url"] = None
    view["model"] = VIDEO_MODEL
    return view


PREVIEW_PARAMETERS = _legacy_profile_view()
GENERATE_PARAMETERS = _legacy_profile_view()
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


def video_duration(duration, model=None):
    """Shot length for the selected model.

    MiniMax stays 4–15. A model record may set duration_min, duration_max,
    and duration_extra (wan3.0-video uses 2–30 or -1).
    """
    record = model or {}
    has_bounds = "duration_min" in record or "duration_max" in record
    lo = int(record.get("duration_min", 4))
    hi = int(record.get("duration_max", 15))
    extra = []
    for item in record.get("duration_extra") or []:
        try:
            extra.append(int(item))
        except (TypeError, ValueError):
            continue
    if extra:
        message = "duration 必须是 %s 到 %s 的整数，或 %s" % (
            lo,
            hi,
            "、".join(str(item) for item in extra),
        )
    else:
        message = "duration 必须是 %s 到 %s 的整数" % (lo, hi)
    if not has_bounds and not extra:
        message = "duration 必须是 4 到 15 的整数"
        lo, hi = 4, 15
    try:
        seconds = int(duration)
    except (TypeError, ValueError):
        raise QwenError(message)
    if seconds in extra:
        return seconds
    if seconds < lo or seconds > hi:
        raise QwenError(message)
    return seconds


def normalize_video_submit_mode(value, env_path=None):
    """Profile name. Empty selects generate, the historical submit."""
    from AI.qwen_api.video_catalog import normalize_profile

    return normalize_profile(value, env_path=env_path)


def video_mode_parameters(mode=None, env_path=None):
    """Legacy combined view: profile fields plus that profile's route url and model."""
    _name, parameters, route = describe_profile(mode, env_path=env_path)
    spec = dict(parameters)
    spec["url"] = route.get("url") if route.get("url") else None
    spec["model"] = resolve_model_id(route.get("model"), env_path=env_path)
    return spec


def _text_parameter(field, value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise QwenError("parameters.%s 必须是非空字符串" % field)


def video_submit_target(mode, fallback_url, local=False, fallback_key="", env_path=None):
    """Host and model id for one submit. The parameter profile is chosen separately."""
    plan = resolve_plan(
        mode,
        local=local,
        fallback_url=fallback_url,
        fallback_key=fallback_key,
        env_path=env_path,
    )
    return plan.base_url, plan.model_id


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


def build_video_parameters(mode, aspect_ratio, duration, local, env_path=None):
    """Wire parameters for one profile. The model decides which keys are sent."""
    plan = resolve_plan(mode, local=local, fallback_url="", fallback_key="", env_path=env_path)
    return wire_parameters(
        plan.parameters,
        plan.emit_local_defaults,
        aspect_ratio,
        duration,
        allowed=plan.model.get("emit"),
        model=plan.model,
    )


# Keys the model emit list can turn on or off. Other profile fields still pass through.
_EMIT_KEYS = frozenset(("resolution", "ratio", "duration", "watermark", "preset", "prompt_extend"))


def _allows(allowed, key):
    if allowed is None or key in allowed:
        return True
    return key not in _EMIT_KEYS


def wire_parameters(spec, emit_local_defaults, aspect_ratio, duration, allowed=None, model=None):
    """Fill ratio and duration from the shot. Route fields never enter the body.

    allowed is the model emit list. MiniMax sends watermark and, on OmniRef,
    preset. wan3.0-video sends resolution, ratio, duration, and prompt_extend
    only when that field is present on the profile.
    """
    if spec.get("ratio") is None:
        ratio = video_ratio(aspect_ratio)
    else:
        ratio = video_ratio(spec.get("ratio"))
    if spec.get("duration") is None:
        seconds = video_duration(duration, model)
    else:
        seconds = video_duration(spec.get("duration"), model)
    parameters = {}
    if _allows(allowed, "resolution"):
        parameters["resolution"] = _text_parameter("resolution", spec.get("resolution"))
    if _allows(allowed, "ratio"):
        parameters["ratio"] = ratio
    if _allows(allowed, "duration"):
        parameters["duration"] = seconds
    if _allows(allowed, "watermark") and spec.get("watermark") is not None:
        parameters["watermark"] = _watermark(spec.get("watermark"))
    if emit_local_defaults:
        parameters.update(LOCAL_DEFAULT_PARAMETERS)
        parameters["preset"] = _text_parameter("preset", spec.get("preset"))
    for key, value in spec.items():
        if key in _REQUEST_SKIP_KEYS or value is None:
            continue
        if not _allows(allowed, key):
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
    """OmniRef Ref2VA media rules. The reference policy owns the message text."""
    return enforce_media(media, "reference")


class QwenVideoClient(object):
    def __init__(self, client=None, api_key=None, opener=None, base_url=None, env_path=None):
        self.env_path = env_path
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
        """Submit one clip. mode selects a parameter profile. Omitted mode is generate.

        The caller model is ignored. The route supplies the model id and the
        platform supplies the host, bearer, and protocol.
        """
        del model, idempotency_key
        prompt = str(text or "").strip()
        if not prompt:
            raise QwenError("出镜提示词是空的")
        media = _media(images)
        plan = resolve_plan(
            mode,
            local=self.local,
            fallback_url=self.client.base_url,
            fallback_key=self.client.api_key,
            env_path=self.env_path,
        )
        enforce_media(media, plan.media)
        parameters = wire_parameters(
            plan.parameters,
            plan.emit_local_defaults,
            aspect_ratio,
            duration,
            allowed=plan.model.get("emit"),
            model=plan.model,
        )
        body = {
            "model": plan.model_id,
            "input": {"prompt": prompt},
            "parameters": parameters,
        }
        if media:
            body["input"]["media"] = media
        transport = self._transport(plan.api_key, plan.base_url)
        data = transport.request(
            "POST",
            plan.base_url + plan.submit_path,
            body,
            timeout=timeout,
            headers=plan.headers,
        )
        task = self._finish(VideoTask(data), "", plan.base_url, plan.task_prefix)
        if plan.explicit:
            task.platform = plan.platform
        if not task.id:
            raise QwenError("千问出镜没有返回任务号")
        return task

    def poll_video(self, task, timeout=60):
        task_id = getattr(task, "id", "") or ""
        return self.poll(task_id, timeout=timeout, previous=task)

    def poll(self, task_id, timeout=60, previous=None, poll_url=None, platform_name=None):
        url = str(poll_url or "").strip()
        if not url and previous is not None:
            url = str(getattr(previous, "poll_url", "") or "").strip()
        platform = str(platform_name or "").strip()
        if not platform and previous is not None:
            platform = str(getattr(previous, "platform", "") or "")
        client = self.for_platform(platform).client if platform else self.client
        if url.startswith(("http://", "https://")):
            data = client.request("GET", url, None, timeout=timeout)
            host = _poll_host(url) or client.base_url
        else:
            data = client.get_task(task_id, timeout=timeout)
            host = client.base_url
        updated = self._finish(VideoTask(data), task_id, host)
        if url.startswith(("http://", "https://")) and not str((data or {}).get("poll_url") or ""):
            updated.poll_url = url
        elif previous is not None and not updated.poll_url:
            updated.poll_url = getattr(previous, "poll_url", "") or updated.poll_url
        if platform:
            updated.platform = platform
        return updated

    def for_platform(self, name):
        """A client whose bearer matches that platform. The same client when it already does."""
        if not str(name or "").strip():
            return self
        host, key = platform_credentials(name, self.env_path)
        if not key or key == self.client.api_key:
            return self
        other = QwenVideoClient(
            client=QwenClient(
                api_key=key,
                base_url=host or self.client.base_url,
                opener=getattr(self.client, "_opener", None),
            )
        )
        other.local = self.local
        return other

    def _transport(self, api_key, base_url):
        if not api_key or api_key == self.client.api_key:
            return self.client
        return QwenClient(
            api_key=api_key,
            base_url=base_url or self.client.base_url,
            opener=getattr(self.client, "_opener", None),
        )

    def download_video(self, task, dest, timeout=300):
        url = task if isinstance(task, str) else (getattr(task, "url", "") or "")
        return self.client.download(url, dest, timeout=timeout)

    def _finish(self, task, task_id, base_url=None, task_prefix=None):
        if not task.id:
            task.id = str(task_id or "")
        host = str(base_url or self.client.base_url).rstrip("/")
        prefix = task_prefix or "/api/v1/tasks/"
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        if not prefix.endswith("/"):
            prefix += "/"
        if task.id and not task.poll_url:
            task.poll_url = host + prefix + task.id
        return task
