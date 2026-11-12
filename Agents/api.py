"""JSON-RPC methods served by Agents.

The method names, parameters, and result shapes match ``AI.api.METHODS``.
Methods that are not a stage loop call that module in this process. ``write``
for an asset brief (``任务：资产锁定``, and not a repair) runs the P2
generator → critic → ``check_asset_blocks.py`` loop. Other writes, including
asset repairs, stay on the existing text graphs.

Jobs are stored in ``AI.qwen_api.jobs``. ``llm.status`` reads that same store,
so End can submit and poll exactly as it does today.
"""

from AI.api import METHODS as _BACKEND
from AI.skills.registry import suite_task_for

_ASSET_NODES = (
    {"id": "generate", "name": "生成"},
    {"id": "critique", "name": "审查"},
    {"id": "machine_check", "name": "机检"},
)


def ping(params):
    return _BACKEND["ping"](params)


def text_ask(params):
    return _BACKEND["text.ask"](params)


def catalog(params):
    return _BACKEND["catalog"](params)


def models_normalize(params):
    return _BACKEND["models.normalize"](params)


def models_label(params):
    return _BACKEND["models.label"](params)


def templates_normalize(params):
    return _BACKEND["templates.normalize"](params)


def templates_label(params):
    return _BACKEND["templates.label"](params)


def templates_policy(params):
    return _BACKEND["templates.policy"](params)


def templates_files(params):
    return _BACKEND["templates.files"](params)


def templates_render(params):
    return _BACKEND["templates.render"](params)


def skills_load(params):
    return _BACKEND["skills.load"](params)


def skills_route(params):
    return _BACKEND["skills.route"](params)


def skills_shape(params):
    return _BACKEND["skills.shape"](params)


def drill(params):
    return _BACKEND["drill"](params)


def write(params):
    """Asset stage uses the P2 loop. Every other brief uses the existing writer."""
    params = params or {}
    if suite_task_for(params.get("brief") or "") == "asset":
        return _submit_asset(params)
    return _BACKEND["write"](params)


def llm_status(params):
    return _BACKEND["llm.status"](params)


def vision_read(params):
    return _BACKEND["vision.read"](params)


def image_submit(params):
    return _BACKEND["image.submit"](params)


def image_query(params):
    return _BACKEND["image.query"](params)


def image_result(params):
    return _BACKEND["image.result"](params)


def image_download(params):
    return _BACKEND["image.download"](params)


def video_submit(params):
    return _BACKEND["video.submit"](params)


def video_poll(params):
    return _BACKEND["video.poll"](params)


def video_download(params):
    return _BACKEND["video.download"](params)


def audio_speak(params):
    return _BACKEND["audio.speak"](params)


def audio_compose(params):
    return _BACKEND["audio.compose"](params)


def layout_speak(params):
    return _BACKEND["layout.speak"](params)


def layout_compose(params):
    return _BACKEND["layout.compose"](params)


def studio_drill(params):
    return _BACKEND["studio.drill"](params)


def studio_write(params):
    return _BACKEND["studio.write"](params)


def _submit_asset(params):
    from AI.qwen_api.jobs import submit

    def work(job_id):
        return _run_asset(job_id, params)

    return submit(list(_ASSET_NODES), work, trace=params.get("trace_prompts") or "")


def _run_asset(job_id, params):
    from AI.qwen_api.jobs import DONE, RUNNING, set_node

    from Agents.machine_check import check_asset_draft
    from Agents.p2_asset import run_p2_asset_loop
    from Agents.text import complete_text

    model = params.get("model") or None

    def complete(role, messages):
        if role == "critic":
            set_node(job_id, "generate", DONE)
            set_node(job_id, "critique", RUNNING)
        else:
            set_node(job_id, "generate", RUNNING)
        return complete_text(role, messages, model=model)

    def checker(text, min_blocks):
        set_node(job_id, "generate", DONE)
        set_node(job_id, "critique", DONE)
        set_node(job_id, "machine_check", RUNNING)
        return check_asset_draft(text, min_blocks=min_blocks)

    result = run_p2_asset_loop(
        params.get("brief") or "",
        skill_pack=params.get("skill_pack") or "",
        model=model,
        complete=complete,
        checker=checker,
    )
    for node in _ASSET_NODES:
        set_node(job_id, node["id"], DONE)
    return _asset_payload(result)


def _asset_payload(result):
    """Shape End stores for an asset stage: ``image_prompts`` plus ``partials``."""
    result = result or {}
    machine = result.get("machine_report") or {}
    return {
        "image_prompts": result.get("image_prompts") or result.get("draft") or "",
        "partials": [],
        "p2": {
            "status": result.get("status") or "",
            "passed": bool(result.get("passed")),
            "stop_reason": result.get("stop_reason") or "",
            "attempts": result.get("attempts") or 0,
            "critic_report": result.get("critic_report") or {},
            "machine_report": {
                "ok": bool(machine.get("ok")),
                "exit_code": machine.get("exit_code"),
                "errors": list(machine.get("errors") or []),
                "warnings": list(machine.get("warnings") or []),
            },
        },
    }


METHODS = {
    "ping": ping,
    "text.ask": text_ask,
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
