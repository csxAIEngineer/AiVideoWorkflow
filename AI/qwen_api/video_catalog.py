"""Video platforms, models, and parameter profiles are separate registries.

A submit picks one platform (host, auth, protocol, media rules), one model
id, and one parameter profile. None of the three stores the other two.
preview and generate are profiles. qwen and omniref are platforms. The
MiniMax-H3 id is a model. Routes name which pair a profile uses.

Add a protocol by register_adapter. Add a host, model id, profile, or
route in data/video_catalog.json beside the process .env. See
data/video_catalog.example.json. A missing file or a missing field keeps
the built-in default. API keys stay in .env.
"""

import json
import os
from pathlib import Path

from AI.qwen_api.client import QwenError, normalize_host, qwen_api_key, resolve_qwen_host

DEFAULT_MODEL_KEY = "minimax-h3"
DEFAULT_MODEL_ID = "MiniMax/MiniMax-H3"
DASH_SCOPE_SUBMIT_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
DASH_SCOPE_TASK_PREFIX = "/api/v1/tasks/"

CATALOG_FILE = "video_catalog.json"
CATALOG_LABEL = "data/video_catalog.json"

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


def catalog_root(env_path=None):
    """Project root whose data/video_catalog.json is read.

    An explicit env_path that is not a file or directory does not fall
    through to the repo, so tests can opt out of a local catalog.
    """
    if env_path is not None:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate.parent
        if candidate.is_dir():
            return candidate
        return None
    from AI.qwen_api.client import qwen_choice_path

    choice = qwen_choice_path(None)
    if choice is None:
        return Path.cwd()
    return choice.parent.parent


def catalog_path(env_path=None):
    root = catalog_root(env_path)
    if root is None:
        return None
    return root / "data" / CATALOG_FILE


def _load_catalog(env_path=None):
    path = catalog_path(env_path)
    if path is None or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    text = text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise QwenError("%s 必须是 JSON 对象" % CATALOG_LABEL)
    if not isinstance(parsed, dict):
        raise QwenError("%s 必须是 JSON 对象" % CATALOG_LABEL)
    return parsed


def _plain(value):
    """Drop keys that document the file. They are not configuration."""
    return {key: item for key, item in value.items() if not str(key).startswith("_")}


def _section(catalog, name):
    value = catalog.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise QwenError("%s 的 %s 必须是对象" % (CATALOG_LABEL, name))
    return _plain(value)


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


def parameter_profiles(env_path=None):
    """Profile name -> parameter dict. No host and no model id."""
    table = {
        "preview": default_profile(),
        "generate": default_profile(),
    }
    for name, value in _section(_load_catalog(env_path), "profiles").items():
        if not isinstance(value, dict):
            raise QwenError("%s 的 profiles.%s 必须是对象" % (CATALOG_LABEL, name))
        base = dict(table.get(name) or default_profile())
        params, _route = _split_legacy(_plain(value))
        base.update(params)
        table[str(name)] = base
    return table


def models(env_path=None):
    """Model key -> {id}. The id is the request body model. No parameters."""
    table = {DEFAULT_MODEL_KEY: {"id": DEFAULT_MODEL_ID}}
    for name, value in _section(_load_catalog(env_path), "models").items():
        if isinstance(value, str) and value.strip():
            table[str(name)] = {"id": value.strip()}
            continue
        if isinstance(value, dict) and isinstance(value.get("id"), str) and value.get("id").strip():
            table[str(name)] = {"id": value.get("id").strip()}
            continue
        raise QwenError("%s 的 models.%s 需要 id" % (CATALOG_LABEL, name))
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


def platforms(env_path=None):
    """Platform name -> host, auth, protocol, and media rules. No model id."""
    table = _builtin_platforms()
    for name, value in _section(_load_catalog(env_path), "platforms").items():
        if not isinstance(value, dict):
            raise QwenError("%s 的 platforms.%s 必须是对象" % (CATALOG_LABEL, name))
        value = _plain(value)
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


def routes(env_path=None):
    """Profile -> {platform, model, url}. Empty platform keeps the page host."""
    names = set(parameter_profiles(env_path))
    table = {}
    for name in names:
        table[name] = {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None}
    for name, value in _section(_load_catalog(env_path), "routes").items():
        if not isinstance(value, dict):
            raise QwenError("%s 的 routes.%s 必须是对象" % (CATALOG_LABEL, name))
        params, route = _split_legacy(_plain(value))
        if params:
            raise QwenError("%s 的 routes.%s 只接受 platform、model、url" % (CATALOG_LABEL, name))
        _apply_route(table, str(name), route, CATALOG_LABEL)
    return table


def _apply_route(table, name, route, env_name):
    current = dict(table.get(name) or {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None})
    for key in _ROUTE_KEYS:
        if key not in route:
            continue
        current[key] = _check_route_value(key, route.get(key), env_name)
    table[name] = current


def known_profiles(env_path=None):
    names = set(parameter_profiles(env_path))
    names.update(routes(env_path))
    return names


def normalize_profile(value, env_path=None):
    """Parameter profile name. Empty selects generate."""
    text = str(value or "").strip()
    lowered = text.lower()
    if not lowered:
        return "generate"
    alias = _PROFILE_ALIASES.get(lowered) or _PROFILE_ALIASES.get(text)
    if alias:
        return alias
    names = known_profiles(env_path)
    if text in names:
        return text
    if lowered in names:
        return lowered
    raise QwenError("出镜提交模式只能是 preview 或 generate")


def resolve_model_id(token, env_path=None):
    """Registry key or a raw model id. Parameters are not consulted."""
    if token is None or (isinstance(token, str) and not token.strip()):
        return DEFAULT_MODEL_ID
    if not isinstance(token, str):
        raise QwenError("model 必须是非空字符串")
    text = token.strip()
    found = models(env_path).get(text)
    if found:
        return found["id"]
    return text


def profile_parameters(name, env_path=None):
    table = parameter_profiles(env_path)
    if name not in table:
        return default_profile()
    return dict(table[name])


def describe_profile(mode=None, env_path=None):
    """Profile parameters plus the route's url and model, still not mixed into one registry."""
    name = normalize_profile(mode, env_path=env_path)
    route = dict(routes(env_path).get(name) or {"platform": "", "model": DEFAULT_MODEL_KEY, "url": None})
    return name, profile_parameters(name, env_path), route


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


def resolve_plan(mode, local, fallback_url, fallback_key, env_path=None):
    """Combine one profile, one platform, and one model id for a submit."""
    name, parameters, route = describe_profile(mode, env_path=env_path)
    table = platforms(env_path)
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
        model_id=resolve_model_id(route.get("model"), env_path=env_path),
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


def platform_credentials(name, env_path=None):
    """Host and bearer for a platform name. Used when a later poll names it."""
    table = platforms(env_path)
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


def video_registry(env_path=None):
    """The three registries plus routes, for callers that want to see the split."""
    return {
        "adapters": sorted(ADAPTERS),
        "platforms": platforms(env_path),
        "models": models(env_path),
        "profiles": parameter_profiles(env_path),
        "routes": routes(env_path),
    }
