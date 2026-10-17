"""Drill and script calls. End reaches these only through the AI RPC server."""

import json
from pathlib import Path

from AI.cr_api import TEXT_MODELS
from AI.skills.registry import (
    brief_lead,
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
)

JSON_SHAPE = json_shape()

DEFAULT_MODEL = "deepseek-v4-pro"
TEXT_MODEL = DEFAULT_MODEL
# 这两个 DeepSeek 走官方 API。其余文本模型走 CR API。
OFFICIAL_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
GROUP_ORDER = ("deepseek", "gpt", "glm")
GROUP_LABELS = {
    "deepseek": "DeepSeek",
    "gpt": "GPT",
    "glm": "GLM",
}
MODEL_LABELS = {
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.4": "GPT 5.4",
    "gpt-5.4-mini": "GPT 5.4 Mini",
    "gpt-5.4-nano": "GPT 5.4 Nano",
    "glm-5.3": "GLM 5.3",
    "glm-5.3-flash": "GLM 5.3 Flash",
    "glm-5.2": "GLM 5.2",
}


def model_choices():
    choices = []
    for group in GROUP_ORDER:
        for name in TEXT_MODELS.get(group, ()):
            choices.append({
                "id": name,
                "label": MODEL_LABELS.get(name, name),
                "group": GROUP_LABELS.get(group, group),
            })
    return choices


def normalize_model(model):
    name = str(model or "").strip()
    allowed = {item["id"] for item in model_choices()}
    if name in allowed:
        return name
    return DEFAULT_MODEL


def model_label(model):
    name = normalize_model(model)
    for item in model_choices():
        if item["id"] == name:
            return item["label"]
    return name


def _empty_body_error(model):
    if normalize_model(model) in OFFICIAL_MODELS:
        return "模型没有返回正文。请检查 DEEPSEEK_API_KEY，或把 max_tokens 调大。"
    return "模型没有返回正文。请检查 CR_API_KEY，或把 max_tokens 调大。"


def _extract_object(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有返回 JSON")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        raise ValueError("没有返回 JSON")
    if not isinstance(data, dict):
        raise ValueError("没有返回 JSON")
    return data


OTHER_CHOICE = "其他"


def choice_options(raw):
    """Keep one or two concrete futures, then always end with 其他."""
    concrete = []
    if isinstance(raw, list):
        for item in raw:
            text = " ".join(str(item or "").split())
            if not text or text == OTHER_CHOICE or text in concrete:
                continue
            concrete.append(text)
    concrete = concrete[:2]
    if not concrete:
        concrete = ["沿刚才的方向继续"]
    return concrete + [OTHER_CHOICE]


def conversation_brief(messages, template=None):
    """Turn the background and the chosen directions into the brief for the writer."""
    user_lines = []
    for item in messages or []:
        if item.get("role") != "user":
            continue
        text = " ".join(str(item.get("content") or "").split())
        if text:
            user_lines.append(text)
    if len(user_lines) < 2:
        return ""
    lines = [
        brief_lead(template),
        "背景：" + user_lines[0],
        "选定方向：",
    ]
    for text in user_lines[1:]:
        lines.append("- " + text)
    return "\n".join(lines)


def ready_from_conversation(messages, template=None):
    brief = conversation_brief(messages, template)
    if not brief:
        return None
    return {
        "phase": "ready",
        "reply": "方向已经够了，开始写提示词。",
        "brief": brief,
        "options": [],
    }


def parse_drill_text(text):
    data = _extract_object(text)
    phase = str(data.get("phase") or "").strip().lower()
    reply = str(data.get("reply") or "").strip()
    brief = str(data.get("brief") or "").strip()
    if phase not in ("ask", "ready") or not reply:
        raise ValueError("drill 回复缺少 phase 或 reply")
    if phase != "ready":
        return {
            "phase": "ask",
            "reply": reply,
            "brief": "",
            "options": choice_options(data.get("options")),
        }
    if not brief:
        raise ValueError("确认后的简报是空的")
    return {"phase": "ready", "reply": reply, "brief": brief, "options": []}


def call_drill(messages, debug_dir=None, model=None, template=None):
    """One drill-me turn. messages are user/assistant text, not the JSON wrapper."""
    from AI.agent.layout import get_layout

    return get_layout().drill(messages, debug_dir=debug_dir, model=model, template=template)


def _extract_json(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek 没有返回 JSON")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        raise ValueError("DeepSeek 没有返回 JSON")
    if not isinstance(data, dict) or not (data.get("script") or data.get("assets")):
        raise ValueError("DeepSeek 没有返回 JSON")
    return data


def _spec_from_result(result):
    for text in (result.content, result.reasoning_content):
        try:
            return _extract_json(text)
        except ValueError:
            continue
    raise ValueError("DeepSeek 没有返回 JSON")


def _reply_text(result):
    return ((result.content or "") + "\n" + (result.reasoning_content or "")).strip()


def write_llm_debug(folder, brief, skill_pack, result=None, repaired=None, template=None):
    """Persist prompts and raw model text for skill / response inspection."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill_pack.txt").write_text(skill_pack or "", encoding="utf-8")
    (folder / "json_shape.txt").write_text(json_shape(template), encoding="utf-8")
    (folder / "system_prompt.txt").write_text(build_system_prompt(skill_pack, template), encoding="utf-8")
    (folder / "user_brief.txt").write_text(brief or "", encoding="utf-8")
    if result is not None:
        (folder / "raw_response.txt").write_text(_reply_text(result), encoding="utf-8")
    if repaired is not None:
        (folder / "raw_response_repaired.txt").write_text(_reply_text(repaired), encoding="utf-8")


def call_deepseek(brief, skill_pack, debug_dir=None, model=None, template=None):
    from AI.agent.layout import get_layout

    return get_layout().write(brief, skill_pack, debug_dir=debug_dir, model=model, template=template)
