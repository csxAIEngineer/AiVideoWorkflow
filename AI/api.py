"""JSON-RPC methods. Callers outside this process never import these modules."""

import json
import os
import urllib.error
import urllib.request

from AI.script import (
    DEFAULT_MODEL,
    call_deepseek,
    call_drill,
    model_choices,
    model_label,
    normalize_model,
    parse_drill_text,
)
from AI.skills.registry import (
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
    normalize_template,
    template_choices,
    template_files,
    template_label,
    template_policy,
    template_render,
)

_ICREAT = None
_CR = None
_MINIMAX = None


def ping(_params):
    return {"ok": True}


def catalog(_params):
    return {
        "default_model": DEFAULT_MODEL,
        "models": model_choices(),
        "default_template": normalize_template(None),
        "templates": template_choices(),
    }


def models_normalize(params):
    return {"model": normalize_model((params or {}).get("model"))}


def models_label(params):
    return {"label": model_label((params or {}).get("model"))}


def templates_normalize(params):
    return {"template": normalize_template((params or {}).get("template"))}


def templates_label(params):
    return {"label": template_label((params or {}).get("template"))}


def templates_policy(params):
    return {"policy": template_policy((params or {}).get("template"))}


def templates_files(params):
    return {"files": template_files((params or {}).get("template"))}


def templates_render(params):
    return template_render((params or {}).get("template"))


def skills_load(params):
    return {"pack": load_skill_pack((params or {}).get("template"))}


def skills_shape(params):
    return {"shape": json_shape((params or {}).get("template"))}


def drill(params):
    params = params or {}
    return call_drill(
        params.get("messages") or [],
        debug_dir=params.get("debug_dir") or None,
        model=params.get("model"),
        template=params.get("template"),
    )


def write(params):
    params = params or {}
    template = params.get("template")
    pack = params.get("skill_pack")
    if not pack:
        pack = load_skill_pack(template)
    return call_deepseek(
        params.get("brief") or "",
        pack,
        debug_dir=params.get("debug_dir") or None,
        model=params.get("model"),
        template=template,
    )


def vision_read(params):
    from AI.vision import read_upload

    return {"text": read_upload((params or {}).get("path"))}


def _icreat():
    global _ICREAT
    if _ICREAT is None:
        from AI.icreat_api import ICreatClient

        _ICREAT = ICreatClient()
    return _ICREAT


def _cr():
    global _CR
    if _CR is None:
        from AI.cr_api import CRClient

        _CR = CRClient()
    return _CR


def _minimax():
    global _MINIMAX
    if _MINIMAX is None:
        from AI.minimax_api import MiniMaxClient

        _MINIMAX = MiniMaxClient()
    return _MINIMAX


def _image(task):
    return {
        "id": getattr(task, "id", "") or "",
        "status": getattr(task, "status", "") or "",
        "url": getattr(task, "url", "") or "",
        "error": getattr(task, "error", "") or "",
    }


def _video(task):
    return {
        "id": getattr(task, "id", "") or "",
        "status": getattr(task, "status", "") or "",
        "poll_url": getattr(task, "poll_url", "") or "",
        "url": getattr(task, "url", "") or "",
        "error": getattr(task, "error", "") or "",
    }


def image_submit(params):
    params = params or {}
    task = _icreat().submit_image(params.get("prompt") or "", image=params.get("image"))
    return _image(task)


def image_query(params):
    task = _icreat().query_status((params or {}).get("task_id") or "")
    return _image(task)


def image_result(params):
    task = _icreat().get_result((params or {}).get("task_id") or "")
    return _image(task)


def image_download(params):
    params = params or {}
    path = _icreat().download_image(params.get("url") or "", params.get("dest") or "")
    return {"path": str(path)}


def video_submit(params):
    params = params or {}
    task = _cr().submit_video(
        params.get("text") or "",
        model=params.get("model") or "minimax-h3-text-to-video",
        duration=int(params.get("duration") or 4),
        aspect_ratio=params.get("aspect_ratio") or "9:16",
        images=params.get("images") or None,
        idempotency_key=params.get("idempotency_key"),
        timeout=int(params.get("timeout") or 180),
    )
    return _video(task)


def video_poll(params):
    from AI.cr_api import VideoTask

    params = params or {}
    task = VideoTask({
        "id": params.get("task_id") or "",
        "poll_url": params.get("poll_url") or "",
        "status": "submitted",
    })
    updated = _cr().poll_video(task, timeout=int(params.get("timeout") or 60))
    return _video(updated)


def video_download(params):
    from AI.cr_api import VideoTask

    params = params or {}
    task = VideoTask({
        "id": params.get("task_id") or "",
        "poll_url": params.get("poll_url") or "",
        "status": params.get("status") or "completed",
        "data": [{"url": params.get("url") or ""}],
    })
    path = _cr().download_video(task, params.get("dest") or "", timeout=int(params.get("timeout") or 300))
    return {"path": str(path)}


def audio_speak(params):
    params = params or {}
    path = _minimax().synthesize(
        params.get("text") or "",
        params.get("dest") or "",
        voice_id=params.get("voice_id"),
    )
    return {"path": str(path)}


def audio_compose(params):
    params = params or {}
    path = _minimax().generate_music(params.get("prompt") or "", params.get("dest") or "")
    return {"path": str(path)}


def layout_speak(params):
    from AI.agent.layout import get_layout

    params = params or {}
    path = get_layout().speak(params.get("text") or "", params.get("dest") or "")
    return {"path": str(path)}


def layout_compose(params):
    from AI.agent.layout import get_layout

    params = params or {}
    path = get_layout().compose(params.get("prompt") or "", params.get("dest") or "")
    return {"path": str(path)}


def _provider_chat(provider, messages):
    provider = provider or {}
    if provider.get("via") == "cr":
        from AI.cr_api import CRClient, CRError

        try:
            result = CRClient().chat(messages, model=provider.get("model") or DEFAULT_MODEL, max_tokens=8000)
        except CRError as error:
            raise RuntimeError(str(error))
        return result.content or ""
    api_key = os.environ.get(provider.get("api_key_env") or "", "")
    if not api_key:
        raise RuntimeError("缺少环境变量 %s" % (provider.get("api_key_env") or ""))
    payload = json.dumps(
        {"model": provider.get("model"), "messages": messages},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        str(provider.get("base_url") or "").rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError("接口返回 %s\n%s" % (error.code, detail[:800]))
    return data["choices"][0]["message"]["content"]


def studio_drill(params):
    """One CLI drill turn. The reply is parsed here so the studio never loads a skill."""
    params = params or {}
    template = params.get("template")
    history = list(params.get("history") or [])
    line = str(params.get("line") or "")
    messages = [{"role": "system", "content": drill_system_prompt(template)}] + history
    messages.append({"role": "user", "content": line})
    raw = _provider_chat(params.get("provider") or {}, messages)
    try:
        parsed = parse_drill_text(raw)
    except ValueError:
        return {"parsed": False, "raw": raw}
    return {
        "parsed": True,
        "raw": raw,
        "reply": parsed["reply"],
        "phase": parsed["phase"],
        "brief": parsed.get("brief") or "",
        "options": parsed.get("options") or [],
    }


def studio_write(params):
    params = params or {}
    template = params.get("template")
    locked = str(params.get("locked_brief") or "")
    prompt = build_system_prompt(load_skill_pack(template), template)
    if locked:
        prompt += "\n\n已经和用户确认的创作简报，必须原样遵守：\n%s" % locked
    history = list(params.get("history") or [])
    line = str(params.get("line") or "")
    messages = [{"role": "system", "content": prompt}] + history
    messages.append({"role": "user", "content": line})
    raw = _provider_chat(params.get("provider") or {}, messages)
    return {"raw": raw}


METHODS = {
    "ping": ping,
    "catalog": catalog,
    "models.normalize": models_normalize,
    "models.label": models_label,
    "templates.normalize": templates_normalize,
    "templates.label": templates_label,
    "templates.policy": templates_policy,
    "templates.files": templates_files,
    "templates.render": templates_render,
    "skills.load": skills_load,
    "skills.shape": skills_shape,
    "drill": drill,
    "write": write,
    "vision.read": vision_read,
    "image.submit": image_submit,
    "image.query": image_query,
    "image.result": image_result,
    "image.download": image_download,
    "video.submit": video_submit,
    "video.poll": video_poll,
    "video.download": video_download,
    "audio.speak": audio_speak,
    "audio.compose": audio_compose,
    "layout.speak": layout_speak,
    "layout.compose": layout_compose,
    "studio.drill": studio_drill,
    "studio.write": studio_write,
}
