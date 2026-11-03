"""Video platforms, models, and parameter profiles are separate registries.

A submit picks one platform (host, auth, protocol, media rules), one model
id, and one parameter profile. None of the three stores the other two.
preview and generate are profiles. qwen and omniref are platforms. The
MiniMax-H3 id is a model. Routes name which pair a profile uses.

Add a protocol by register_adapter. Add a host that already speaks a
registered protocol with VIDEO_PLATFORMS. Add a model id with VIDEO_MODELS.
Add or edit parameter profiles with VIDEO_PROFILES. Bind them with
VIDEO_ROUTES.

MINIMAX_VIDEO_PREVIEW_PARAMETERS and MINIMAX_VIDEO_GENERATE_PARAMETERS
remain aliases: parameter fields merge into that profile, and url, model,
and platform merge into that profile's route.
"""

import json
import os

from AI.qwen_api.client import QwenError, normalize_host, qwen_api_key, resolve_qwen_host

DEFAULT_MODEL_KEY = "minimax-h3"
DEFAULT_MODEL_ID = "MiniMax/MiniMax-H3"
DASH_SCOPE_SUBMIT_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
DASH_SCOPE_TASK_PREFIX = "/api/v1/tasks/"

PREVIEW_PARAMETERS_ENV = "MINIMAX_VIDEO_PREVIEW_PARAMETERS"
GENERATE_PARAMETERS_ENV = "MINIMAX_VIDEO_GENERATE_PARAMETERS"
PLATFORMS_ENV = "VIDEO_PLATFORMS"
MODELS_ENV = "VIDEO_MODELS"
PROFILES_ENV = "VIDEO_PROFILES"
ROUTES_ENV = "VIDEO_ROUTES"

_PROFILE_ENV = {
    "preview": PREVIEW_PARAMETERS_ENV,
    "generate": GENERATE_PARAMETERS_ENV,
}
_PROFILE_ALIASES = {
    "preview": "preview",
    "预览": "preview",
    "generate": "generate",
    "生成": "generate",
}
_ROUTE_KEYS = ("url", "model", "platform")
_FRAME_ROLES = frozenset(("first_frame", "last_frame"))
_VISUAL_REFERENCE_ROLES = frozenset(("reference_image", "reference_video"))

MEDIA_POLICIES = {
    "open": {},
    "reference": {
        "reject_frame_roles": True,
        "reject_frame_message": (
            "本地 OmniRef Ref2VA 不接受 first_frame 和 last_frame，"
            "请使用 reference_image、reference_video 或 reference_audio"
        ),
        "require_visual_reference": True,
        "audio_only_message": (
            "本地 OmniRef Ref2VA 的 reference_audio 需要同时提供 reference_image 或 reference_video"
        ),
        "missing_message": "本地 OmniRef Ref2VA 至少需要一条 reference_image 或 reference_video",
    },
}


class DashScopeVideoAdapter(object):
    """DashScope async video-synthesis. Qwen cloud and OmniRef both speak it."""

    name = "dashscope"
    submit_path = DASH_SCOPE_SUBMIT_PATH
    headers = {"X-DashScope-Async": "enable"}
    task_prefix = DASH_SCOPE_TASK_PREFIX


ADAPTERS = {}


def register_adapter(adapter):
    """Register a submit/poll protocol. Platforms point at adapter.name."""
    name = str(getattr(adapter, "name", "") or "").strip()
    if not name:
        raise QwenError("视频协议缺少 name")
    ADAPTERS[name] = adapter
    return adapter


def unregister_adapter(name):
    ADAPTERS.pop(str(name or ""), None)


register_adapter(DashScopeVideoAdapter())


class SubmissionPlan(object):
    """One combination of platform, model id, and parameter profile."""

    def __init__(self, profile, platform, model_id, base_url, api_key, parameters, emit_local_defaults, media, submit_path, headers, task_prefix, url_override, explicit):
        self.profile = profile
        self.platform = platform
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = api_key
        self.parameters = parameters
        self.emit_local_defaults = emit_local_defaults
        self.media = media
        self.submit_path = submit_path
        self.headers = headers
        self.task_prefix = task_prefix
        self.url_override = url_override
        self.explicit = explicit


def default_profile():
    """Parameter fields only. preview and generate each get their own copy."""
    return {
        "resolution": "768P",
        "ratio": None,
        "duration": None,
        "watermark": False,
        "preset": "fast",
    }


def _json_object(env_name):
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


def _legacy_blob(profile):
    env_name = _PROFILE_ENV.get(profile)
    if not env_name:
        return {}
    return _json_object(env_name)


def _split_legacy(blob):
    parameters = {}
    route = {}
    for key, value in blob.items():
        if key in _ROUTE_KEYS:
            route[key] = value
        else:
            parameters[key] = value
    return parameters, route


def _check_route_value(key, value, env_name):
    if key == "model":
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        raise QwenError("model 必须是非空字符串")
    if key == "url":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or not value.strip().startswith(("http://", "https://")):
            raise QwenError("url 必须是 http 或 https 地址")
        return value.strip()
    if key == "platform":
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        if not isinstance(value, str):
            raise QwenError("%s 的 platform 必须是平台名" % env_name)
        return value.strip()
    return value


def parameter_profiles():
    """Profile name -> parameter dict. No host and no model id."""
    table = {
        "preview": default_profile(),
        "generate": default_profile(),
    }
    for name, value in _json_object(PROFILES_ENV).items():
        if not isinstance(value, dict):
            raise QwenError("%s.%s 必须是对象" % (PROFILES_ENV, name))
        base = dict(table.get(name) or default_profile())
        params, _route = _split_legacy(value)
        base.update(params)
        table[str(name)] = base
    for name in ("preview", "generate"):
        params, _route = _split_legacy(_legacy_blob(name))
        if params:
            merged = dict(table[name])
            merged.update(params)
            table[name] = merged
    return table


def models():
    """Model key -> {id}. The id is the request body model. No parameters."""
    table = {DEFAULT_MODEL_KEY: {"id": DEFAULT_MODEL_ID}}
    for name, value in _json_object(MODELS_ENV).items():
        if isinstance(value, str) and value.strip():
            table[str(name)] = {"id": value.strip()}
            continue
        if isinstance(value, dict) and isinstance(value.get("id"), str) and value.get("id").strip():
            table[str(name)] = {"id": value.get("id").strip()}
            continue
        raise QwenError("%s.%s 需要 id" % (MODELS_ENV, name))
    return table


def _builtin_platforms():
    return {
        "qwen": {
            "adapter": "dashscope",
            "host": "qwen",
            "auth": "qwen",
            "media": "open",
            "emit_local_defaults": False,
        },
        "omniref": {
            "adapter": "dashscope",
            "host": "minimax_h3",
            "auth": "local",
            "media": "reference",
            "emit_local_defaults": True,
        },
    }


def platforms():
    """Platform name -> host, auth, protocol, and media rules. No model id."""
    table = _builtin_platforms()
    for name, value in _json_object(PLATFORMS_ENV).items():
        if not isinstance(value, dict):
            raise QwenError("%s.%s 必须是对象" % (PLATFORMS_ENV, name))
        base = dict(table.get(name) or {
            "adapter": "dashscope",
            "host": "",
            "auth": "client",
            "media": "open",
            "emit_local_defaults": False,
        })
        base.update(value)
        table[str(name)] = base
    return table


def routes():
    """Profile -> {platform, model, url}. Empty platform keeps the page host."""
    names = set(parameter_profiles())
    table = {}
    for name in names:
        table[name] = {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None}
    for name in ("preview", "generate"):
        _params, route = _split_legacy(_legacy_blob(name))
        _apply_route(table, name, route, _PROFILE_ENV[name])
    for name, value in _json_object(ROUTES_ENV).items():
        if not isinstance(value, dict):
            raise QwenError("%s.%s 必须是对象" % (ROUTES_ENV, name))
        _apply_route(table, str(name), value, ROUTES_ENV)
    return table


def _apply_route(table, name, route, env_name):
    current = dict(table.get(name) or {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None})
    for key in _ROUTE_KEYS:
        if key not in route:
            continue
        current[key] = _check_route_value(key, route.get(key), env_name)
    table[name] = current


def known_profiles():
    names = set(parameter_profiles())
    names.update(routes())
    return names


def normalize_profile(value):
    """Parameter profile name. Empty selects generate."""
    text = str(value or "").strip()
    lowered = text.lower()
    if not lowered:
        return "generate"
    alias = _PROFILE_ALIASES.get(lowered) or _PROFILE_ALIASES.get(text)
    if alias:
        return alias
    names = known_profiles()
    if text in names:
        return text
    if lowered in names:
        return lowered
    raise QwenError("出镜提交模式只能是 preview 或 generate")


def resolve_model_id(token):
    """Registry key or a raw model id. Parameters are not consulted."""
    if token is None or (isinstance(token, str) and not token.strip()):
        return DEFAULT_MODEL_ID
    if not isinstance(token, str):
        raise QwenError("model 必须是非空字符串")
    text = token.strip()
    found = models().get(text)
    if found:
        return found["id"]
    return text


def profile_parameters(name):
    table = parameter_profiles()
    if name not in table:
        return default_profile()
    return dict(table[name])


def describe_profile(mode=None):
    """Profile parameters plus the route's url and model, still not mixed into one registry."""
    name = normalize_profile(mode)
    route = dict(routes().get(name) or {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None})
    return name, profile_parameters(name), route


def _platform_host(platform):
    host = str(platform.get("host") or "").strip()
    if not host or host == "client":
        return ""
    if host.startswith(("http://", "https://")):
        return normalize_host(host)
    if host == "qwen":
        return resolve_qwen_host()
    if host == "minimax_h3":
        from AI.qwen_api.video import local_video_base_url

        return local_video_base_url()
    raise QwenError("未知的视频主机 %s" % host)


def _platform_key(platform, fallback_key):
    auth = str(platform.get("auth") or "client").strip() or "client"
    if auth == "client":
        return fallback_key
    if auth == "qwen":
        key, var = qwen_api_key()
        if not key:
            raise QwenError("未配置 %s" % var)
        return key
    if auth == "local":
        from AI.qwen_api.video import local_video_api_key

        return local_video_api_key()
    if auth == "env":
        var = str(platform.get("key_env") or "").strip()
        key = str(os.environ.get(var) or "").strip() if var else ""
        if not key:
            raise QwenError("未配置 %s" % (var or "视频平台密钥"))
        return key
    raise QwenError("未知的视频鉴权 %s" % auth)


def _binding(platform):
    adapter_name = str(platform.get("adapter") or "dashscope").strip() or "dashscope"
    adapter = ADAPTERS.get(adapter_name)
    if adapter is None:
        raise QwenError("未知的视频协议 %s" % adapter_name)
    submit_path = str(platform.get("submit_path") or getattr(adapter, "submit_path", "") or "")
    if not submit_path.startswith("/"):
        submit_path = "/" + submit_path
    headers = dict(getattr(adapter, "headers", {}) or {})
    extra = platform.get("headers") or {}
    if extra and not isinstance(extra, dict):
        raise QwenError("平台 headers 必须是对象")
    for key, value in (extra or {}).items():
        headers[str(key)] = value
    task_prefix = str(platform.get("task_prefix") or getattr(adapter, "task_prefix", DASH_SCOPE_TASK_PREFIX))
    if not task_prefix.startswith("/"):
        task_prefix = "/" + task_prefix
    if not task_prefix.endswith("/"):
        task_prefix += "/"
    return submit_path, headers, task_prefix


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
    return bool(value)


def resolve_plan(mode, local, fallback_url, fallback_key):
    """Combine one profile, one platform, and one model id for a submit."""
    name, parameters, route = describe_profile(mode)
    table = platforms()
    explicit = str(route.get("platform") or "").strip()
    if explicit:
        if explicit not in table:
            raise QwenError("未知的视频平台 %s" % explicit)
        platform = table[explicit]
        platform_name = explicit
        base = _platform_host(platform) or str(fallback_url or "").rstrip("/")
        api_key = _platform_key(platform, fallback_key)
    else:
        platform_name = "omniref" if local else "qwen"
        platform = table[platform_name]
        base = str(fallback_url or "").rstrip("/")
        api_key = fallback_key
    override = route.get("url")
    if override:
        base = normalize_host(override)
    submit_path, headers, task_prefix = _binding(platform)
    return SubmissionPlan(
        profile=name,
        platform=platform_name,
        model_id=resolve_model_id(route.get("model")),
        base_url=base,
        api_key=api_key,
        parameters=parameters,
        emit_local_defaults=_as_bool(platform.get("emit_local_defaults")),
        media=platform.get("media") or "open",
        submit_path=submit_path,
        headers=headers,
        task_prefix=task_prefix,
        url_override=override if override else None,
        explicit=bool(explicit),
    )


def platform_credentials(name):
    """Host and bearer for a platform name. Used when a later poll names it."""
    table = platforms()
    if name not in table:
        raise QwenError("未知的视频平台 %s" % name)
    platform = table[name]
    return _platform_host(platform), _platform_key(platform, "")


def enforce_media(media, policy):
    """Apply a platform's media rules. The rules are data, not a platform name check."""
    if isinstance(policy, str):
        found = MEDIA_POLICIES.get(policy)
        if found is None:
            raise QwenError("未知的素材规则 %s" % policy)
        policy = found
    if not isinstance(policy, dict):
        raise QwenError("素材规则必须是对象")
    roles = [item.get("type") for item in media or []]
    if policy.get("reject_frame_roles") and any(role in _FRAME_ROLES for role in roles):
        raise QwenError(policy.get("reject_frame_message") or "该平台不接受首尾帧")
    if policy.get("require_visual_reference"):
        if any(role in _VISUAL_REFERENCE_ROLES for role in roles):
            return media
        if any(role == "reference_audio" for role in roles):
            raise QwenError(policy.get("audio_only_message") or "reference_audio 需要画面参考")
        raise QwenError(policy.get("missing_message") or "至少需要一条画面参考")
    return media


def video_registry():
    """The three registries plus routes, for callers that want to see the split."""
    return {
        "adapters": sorted(ADAPTERS),
        "platforms": platforms(),
        "models": models(),
        "profiles": parameter_profiles(),
        "routes": routes(),
    }
