"""JSON-RPC methods. Callers outside this process never import these modules."""

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
    suite_task_for,
    normalize_template,
    skill_pack_extra,
    template_choices,
    template_files,
    template_label,
    template_policy,
    template_render,
)

_IMAGE = None
_VIDEO = None


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
    params = params or {}
    template = params.get("template")
    context = params.get("context") or ""
    stage = params.get("stage") or "P4"
    return {
        "pack": load_skill_pack(template, context=context, stage=stage),
        "extra": skill_pack_extra(template, context),
    }


def skills_route(params):
    params = params or {}
    return {"extra": skill_pack_extra(params.get("template"), params.get("context") or "")}


def skills_shape(params):
    return {"shape": json_shape((params or {}).get("template"))}


def drill(params):
    from AI.qwen_api.jobs import node_name, submit
    from AI.skills.registry import suite_files

    params = params or {}
    files = suite_files("lock", "")
    nodes = [{"id": path, "name": node_name(path)} for path in files]
    nodes.append({"id": "merge", "name": "汇总"})

    def work(job_id):
        return call_drill(
            params.get("messages") or [],
            debug_dir=params.get("debug_dir") or None,
            model=params.get("model"),
            template=params.get("template"),
            job_id=job_id,
        )

    return submit(nodes, work)


def write(params):
    from AI.qwen_api.jobs import node_name, set_node, submit
    from AI.skills.registry import suite_files

    params = params or {}
    template = params.get("template")
    brief = params.get("brief") or ""
    pack = params.get("skill_pack")
    task = suite_task_for(brief)
    if not pack and not task:
        pack = load_skill_pack(
            template,
            context=brief,
            stage=params.get("stage") or "P4",
        )
    if task == "repair":
        from AI.qwen_api.repair_graph import repair_job_nodes

        nodes = repair_job_nodes(brief)
    elif task:
        files = suite_files(task, brief)
        nodes = [{"id": path, "name": node_name(path)} for path in files]
        nodes.append({"id": "merge", "name": "汇总"})
    else:
        nodes = [{"id": "write", "name": "写稿"}]
    trace = params.get("trace_prompts") or ""

    def work(job_id):
        if not task:
            set_node(job_id, "write", "进行中")
        result = call_deepseek(
            brief,
            pack,
            debug_dir=params.get("debug_dir") or None,
            model=params.get("model"),
            template=template,
            job_id=job_id,
        )
        if not task:
            set_node(job_id, "write", "完成")
        return result

    return submit(nodes, work, trace=trace)


def vision_read(params):
    from AI.qwen_api.jobs import set_node, submit
    from AI.vision import read_upload

    params = params or {}

    def work(job_id):
        set_node(job_id, "vision", "进行中")
        text = read_upload(params.get("path"))
        set_node(job_id, "vision", "完成")
        return {"text": text}

    return submit([{"id": "vision", "name": "读图"}], work, trace=params.get("trace_prompts") or "")


def _client_host(client):
    inner = getattr(client, "client", None)
    return str(getattr(inner, "base_url", "") or "")


def _client_key(client):
    inner = getattr(client, "client", None)
    return str(getattr(inner, "api_key", "") or "")


def _image_client():
    global _IMAGE
    from AI.qwen_api.client import load_dotenv, qwen_api_key, resolve_qwen_host
    from AI.qwen_api.image import QwenImageClient

    load_dotenv()
    host = resolve_qwen_host()
    key, _var = qwen_api_key()
    if _IMAGE is None or _client_host(_IMAGE) != host or _client_key(_IMAGE) != key:
        _IMAGE = QwenImageClient()
    return _IMAGE


def _video_client():
    global _VIDEO
    from AI.qwen_api.client import load_dotenv, qwen_api_key, resolve_qwen_host
    from AI.qwen_api.video import QwenVideoClient

    load_dotenv()
    host = resolve_qwen_host()
    key, _var = qwen_api_key()
    if _VIDEO is None or _client_host(_VIDEO) != host or _client_key(_VIDEO) != key:
        _VIDEO = QwenVideoClient()
    return _VIDEO


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
    task = _image_client().submit_image(
        params.get("prompt") or "",
        image=params.get("image"),
        size=params.get("size"),
    )
    return _image(task)


def image_query(params):
    task = _image_client().query_status((params or {}).get("task_id") or "")
    return _image(task)


def image_result(params):
    task = _image_client().get_result((params or {}).get("task_id") or "")
    return _image(task)


def image_download(params):
    params = params or {}
    path = _image_client().download_image(params.get("url") or "", params.get("dest") or "")
    return {"path": str(path)}


def video_submit(params):
    params = params or {}
    task = _video_client().submit_video(
        params.get("text") or "",
        duration=int(params.get("duration") or 4),
        aspect_ratio=params.get("aspect_ratio") or "",
        images=params.get("images") or None,
        idempotency_key=params.get("idempotency_key"),
        timeout=int(params.get("timeout") or 180),
    )
    return _video(task)


def video_poll(params):
    params = params or {}
    updated = _video_client().poll(params.get("task_id") or "", timeout=int(params.get("timeout") or 60))
    return _video(updated)


def video_download(params):
    params = params or {}
    path = _video_client().download_video(
        params.get("url") or "",
        params.get("dest") or "",
        timeout=int(params.get("timeout") or 300),
    )
    return {"path": str(path)}


def audio_speak(params):
    return _speech_job(params)


def _speech_job(params):
    from AI.qwen_api.audio import synthesize
    from AI.qwen_api.jobs import set_node, submit

    params = params or {}

    def work(job_id):
        set_node(job_id, "speech", "进行中")
        path = synthesize(
            params.get("text") or "",
            params.get("dest") or "",
            voice_id=params.get("voice_id"),
        )
        set_node(job_id, "speech", "完成")
        return {"path": str(path)}

    return submit([{"id": "speech", "name": "旁白"}], work)


def audio_compose(params):
    del params
    from AI.qwen_api.audio import reject_music

    reject_music()


def layout_speak(params):
    return _speech_job(params)


def layout_compose(params):
    from AI.agent.layout import get_layout

    params = params or {}
    path = get_layout().compose(params.get("prompt") or "", params.get("dest") or "")
    return {"path": str(path)}


def _qwen_text(messages, kind, provider):
    from AI.qwen_api.graph import generate_text, understand_text

    model = normalize_model((provider or {}).get("model"))
    call = understand_text if kind == "drill" else generate_text
    return call(messages, model=model, timeout=180, max_output_tokens=4000 if kind == "drill" else 16000)


def llm_status(params):
    from AI.qwen_api.jobs import snapshot

    return snapshot((params or {}).get("job_id") or "")


def studio_drill(params):
    """One CLI drill turn. The reply is parsed here so the studio never loads a skill."""
    params = params or {}
    template = params.get("template")
    history = list(params.get("history") or [])
    line = str(params.get("line") or "")
    context = history + [{"role": "user", "content": line}]
    messages = [{"role": "system", "content": drill_system_prompt(template, context)}] + history
    messages.append({"role": "user", "content": line})
    raw = _qwen_text(messages, "drill", params.get("provider") or {})
    try:
        parsed = parse_drill_text(raw, template)
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
    prompt = build_system_prompt(
        load_skill_pack(template, context=locked, stage=params.get("stage") or "P4"),
        template,
        locked,
    )
    if locked:
        prompt += "\n\n已经和用户确认的创作简报，必须原样遵守：\n%s" % locked
    history = list(params.get("history") or [])
    line = str(params.get("line") or "")
    messages = [{"role": "system", "content": prompt}] + history
    messages.append({"role": "user", "content": line})
    raw = _qwen_text(messages, "write", params.get("provider") or {})
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
    "skills.route": skills_route,
    "skills.shape": skills_shape,
    "drill": drill,
    "write": write,
    "llm.status": llm_status,
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
