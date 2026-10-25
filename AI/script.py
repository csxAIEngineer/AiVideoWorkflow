"""Drill and script calls. End reaches these only through the AI RPC server."""

import json
from pathlib import Path

from AI.skills.registry import (
    brief_lead,
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
    locks_ready,
    script_step_id,
    template_policy,
)

JSON_SHAPE = json_shape()

DEFAULT_MODEL = "qwen3.8-flash"
TEXT_MODEL = DEFAULT_MODEL


def model_choices():
    return [{"id": DEFAULT_MODEL, "label": "Qwen3.8 Flash", "group": "Qwen"}]


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


def _empty_body_error(_model):
    return "模型没有返回正文。请检查当前千问环境的 API Key，或把 max_tokens 调大。"


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
    if start < 0:
        _reject(_miss(cleaned, 0, "no_brace", "全文没有左花括号 {，回的是普通文本。", False))
    if end <= start:
        _reject(
            _miss(
                cleaned,
                len(cleaned),
                "unclosed",
                "从第 %s 字的 { 起像是对象，但直到文末都没有 }。回复在文末被截断。" % (start + 1),
                False,
                brace=start,
            )
        )
    blob = cleaned[start : end + 1]
    try:
        data = _loads_json_text(blob)
    except json.JSONDecodeError as error:
        _reject(_decode_failure(cleaned, start, blob, error))
    if not isinstance(data, dict):
        _reject(_shape_failure(cleaned, start, data))
    return data


OTHER_CHOICE = "其他"


def choice_options(raw):
    """Keep every concrete choice the drill listed, then always end with 其他.

    A lock can have four choices. Trimming to two hides the rest of the lock.
    """
    concrete = []
    if isinstance(raw, list):
        for item in raw:
            text = " ".join(str(item or "").split())
            if not text or text == OTHER_CHOICE or text in concrete:
                continue
            concrete.append(text)
    concrete = concrete[:8]
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
    if template_policy(template) == "drama_still" and not locks_ready(brief):
        return None
    return {
        "phase": "ready",
        "reply": "方向已经够了，开始写提示词。",
        "brief": brief,
        "options": [],
    }


def parse_drill_text(text, template=None):
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
    if template_policy(template) == "drama_still" and not locks_ready(brief):
        return {
            "phase": "ask",
            "reply": reply,
            "brief": "",
            "options": choice_options(data.get("options")),
        }
    return {"phase": "ready", "reply": reply, "brief": brief, "options": []}


def call_drill(messages, debug_dir=None, model=None, template=None, job_id=""):
    """One drill-me turn. messages are user/assistant text, not the JSON wrapper."""
    from AI.agent.layout import get_layout

    return get_layout().drill(messages, debug_dir=debug_dir, model=model, template=template, job_id=job_id)


_SPEC_KEYS = (
    "script",
    "assets",
    "shots",
    "image_prompts",
    "storyboard",
    "feed",
    "previs",
    "intel",
    "internal",
)


class TruncatedJSON(ValueError):
    """The model opened a JSON object and stopped before it was complete."""


def _coords(text, pos):
    pos = max(0, min(int(pos or 0), len(text)))
    line = text.count("\n", 0, pos) + 1
    last = text.rfind("\n", 0, pos)
    return line, pos - last


def _flat(text, limit):
    chunk = str(text or "").replace("\n", "↵")
    if len(chunk) <= limit:
        return chunk
    return chunk[:limit] + "…"


def _window(text, pos, radius=48):
    if not text:
        return ""
    pos = max(0, min(int(pos or 0), len(text)))
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    chunk = text[start:end].replace("\n", "↵")
    if start:
        chunk = "…" + chunk
    if end < len(text):
        chunk += "…"
    return chunk


def _format_json_miss(failure):
    text = failure.get("text") or ""
    pos = int(failure.get("pos") or 0)
    line, column = _coords(text, pos)
    length = len(text)
    shown = min(pos + 1, length) if length else 0
    lines = [
        "模型没有返回 JSON。",
        "原因：%s" % (failure.get("detail") or "没有解析成规格 JSON"),
        "定位：第 %s 行第 %s 列，第 %s/%s 字。" % (line, column, shown, length),
        "开头：%s" % _flat(text, 60),
        "附近：%s" % _window(text, min(pos, length)),
    ]
    if failure.get("reason") in ("unclosed", "cut"):
        tail = "…" + _flat(text[-80:], 80) if length > 80 else _flat(text, 80)
        if tail and tail not in lines[-1]:
            lines.append("文末：%s" % tail)
    brace = failure.get("brace")
    if brace is not None and failure.get("reason") in ("unclosed", "cut", "syntax"):
        lines.append("对象开头：第 %s/%s 字。" % (int(brace) + 1, length))
    if failure.get("decoder"):
        lines.append("解析器：%s" % failure["decoder"])
    if failure.get("keys") is not None:
        lines.append("对象里的键：%s。" % ("、".join(failure["keys"]) or "（无）"))
    if failure.get("escaped"):
        lines.append("说明：定位落在把字符串里的换行转义之后的文本上。")
    return "\n".join(lines)


def _reject(failure):
    message = _format_json_miss(failure)
    if failure.get("truncated"):
        raise TruncatedJSON(message)
    raise ValueError(message)


def _miss(text, pos, reason, detail, truncated, brace=None, decoder="", keys=None, escaped=False):
    return {
        "reason": reason,
        "truncated": truncated,
        "detail": detail,
        "pos": pos,
        "text": text,
        "brace": brace,
        "decoder": decoder,
        "keys": keys,
        "escaped": escaped,
    }


def _strip_fence(text):
    cleaned = str(text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _quote_closes_string(text, index):
    """A quote ends the string when the next token is a JSON delimiter."""
    cursor = index + 1
    size = len(text)
    while cursor < size and text[cursor] in " \t\r\n":
        cursor += 1
    return cursor >= size or text[cursor] in ",}]:"


def _escape_controls_in_strings(text):
    """Escape raw quotes and newlines that appear inside JSON strings."""
    text = str(text or "")
    pieces = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if not in_string:
            pieces.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            pieces.append(char)
            escaped = False
            continue
        if char == "\\":
            pieces.append(char)
            escaped = True
            continue
        if char == '"':
            if _quote_closes_string(text, index):
                pieces.append(char)
                in_string = False
            else:
                pieces.append('\\"')
            continue
        if char == "\n":
            pieces.append("\\n")
            continue
        if char == "\r":
            pieces.append("\\r")
            continue
        pieces.append(char)
    return "".join(pieces)


def _starts_object(text, start):
    return text[start + 1 :].lstrip().startswith('"')


def _decode_was_cut(error, blob):
    """A cut-off object is incomplete. A trailing comma or an extra brace is not."""
    message = str(getattr(error, "msg", "") or "")
    position = int(getattr(error, "pos", 0) or 0)
    at_end = position >= max(0, len(blob) - 1)
    if "Unterminated string" in message:
        return True
    return message.startswith("Expecting value") and at_end


def _loads_json_text(blob):
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        repaired = _escape_controls_in_strings(blob)
        if repaired == blob:
            raise
        return json.loads(repaired)


def _debug_log(hypothesis, location, message, data):
    # region agent log
    try:
        import time

        payload = {
            "sessionId": "ee5fa7",
            "hypothesisId": hypothesis,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


def _loads_spec(blob, brief=""):
    data = _loads_json_text(blob)
    step = script_step_id(brief)
    text = str(data.get("text") or "").strip() if isinstance(data, dict) else ""
    if step and step != "5" and text:
        return data
    if not isinstance(data, dict) or not any(data.get(key) for key in _SPEC_KEYS):
        # region agent log
        keys = [str(key) for key in list(data)[:12]] if isinstance(data, dict) else []
        _debug_log(
            "A",
            "AI/script.py:_loads_spec",
            "parsed json rejected",
            {
                "isDict": isinstance(data, dict),
                "keys": keys,
                "textLen": len(str(data.get("text") or "")) if isinstance(data, dict) else 0,
                "specKeyHits": [key for key in _SPEC_KEYS if isinstance(data, dict) and data.get(key)],
            },
        )
        # endregion
        error = ValueError("模型没有返回 JSON")
        error.spec_data = data
        raise error
    return data


def _shape_failure(cleaned, start, data):
    if not isinstance(data, dict):
        return _miss(
            cleaned,
            start,
            "not_dict",
            "最外层 JSON 是 %s，不是对象。" % type(data).__name__,
            False,
            brace=start,
        )
    keys = [str(key) for key in list(data)[:12]]
    return _miss(
        cleaned,
        start,
        "missing_keys",
        "JSON 能解析，但没有规格字段（script、assets、shots、image_prompts、storyboard、feed、previs、intel、internal）。",
        False,
        brace=start,
        keys=keys,
    )


def _decode_failure(cleaned, start, blob, error):
    repaired = _escape_controls_in_strings(blob)
    escaped = repaired != blob
    local = int(getattr(error, "pos", 0) or 0)
    if escaped:
        failure_text = repaired
        failure_pos = local
        brace = None
    else:
        failure_text = cleaned
        failure_pos = start + local
        brace = start
    truncated = _decode_was_cut(error, blob)
    detail = "JSON 没写完。解析器停在这里：%s。" % (error.msg or error)
    if not truncated:
        detail = "JSON 语法不对。解析器停在这里：%s。" % (error.msg or error)
    return _miss(
        failure_text,
        failure_pos,
        "cut" if truncated else "syntax",
        detail,
        truncated,
        brace=brace,
        decoder="%s（第 %s 行第 %s 列）" % (error.msg, error.lineno, error.colno),
        escaped=escaped,
    )


def _extract_json(text, brief=""):
    cleaned = _strip_fence(text)
    start = cleaned.find("{")
    if start < 0:
        _reject(_miss(cleaned, 0, "no_brace", "全文没有左花括号 {，回的是普通文本。", False))
    if not _starts_object(cleaned, start):
        rest = cleaned[start + 1 :].lstrip()
        detail = "第一个 { 后面不是 \"键名\"，不能当成规格 JSON。"
        if not rest:
            detail = "有一个 {，但后面没有 \"键名\"，对象没写完。"
        _reject(_miss(cleaned, start, "not_object", detail, False, brace=start))
    end = cleaned.rfind("}")
    if end <= start:
        _reject(
            _miss(
                cleaned,
                len(cleaned),
                "unclosed",
                "从第 %s 字的 { 起像是对象，但直到文末都没有 }。回复在文末被截断。" % (start + 1),
                True,
                brace=start,
            )
        )
    blob = cleaned[start : end + 1]
    try:
        return _loads_spec(blob, brief)
    except json.JSONDecodeError as error:
        _reject(_decode_failure(cleaned, start, blob, error))
    except ValueError as error:
        data = getattr(error, "spec_data", None)
        if data is None:
            raise
        _reject(_shape_failure(cleaned, start, data))


def _named_pieces(result):
    if result is None:
        return []
    if isinstance(result, str):
        text = result.strip()
        return [("文本", text)] if text else []
    found = []
    for label, piece in (
        ("正文", getattr(result, "content", None)),
        ("推理", getattr(result, "reasoning_content", None)),
    ):
        text = str(piece or "").strip()
        if text:
            found.append((label, text))
    return found


def _result_pieces(result):
    return [text for _label, text in _named_pieces(result)]


def _reason_line(error):
    for line in str(error).splitlines():
        if line.startswith("原因："):
            return line[len("原因：") :]
    text = str(error).strip()
    return text.splitlines()[0][:80] if text else "没有说明"


def _explain_failure(label, error, failures):
    lines = [str(error).strip(), "这一次用的是：%s。" % label]
    others = [(name, item) for name, item in failures if item is not error]
    if others:
        lines.append("其他尝试：")
        for name, item in others:
            kind = "截断" if isinstance(item, TruncatedJSON) else "无效"
            lines.append("- %s：%s。%s" % (name, kind, _reason_line(item)))
    return "\n".join(lines)


def explain_parse_failure(error, step, why, title="模型没有返回 JSON。"):
    body = str(error).strip()
    if body.startswith(title):
        rest = body[len(title) :].lstrip("\n")
        message = "%s\n这一步：%s。%s\n%s" % (title, step, why, rest)
    else:
        message = "%s\n这一步：%s。%s\n%s" % (title, step, why, body)
    return message


def _truncated_body(result):
    fallback = ""
    for text in _result_pieces(result):
        if not fallback:
            fallback = text
        try:
            _extract_json(text)
        except TruncatedJSON:
            return text
        except ValueError:
            continue
    return fallback


def _stitch_json(original, extra):
    base = str(original or "")
    extra = _strip_fence(extra).strip()
    if not extra:
        return base
    if not base:
        return extra
    cap = min(len(base), len(extra))
    size = cap
    while size >= 8:
        if base.endswith(extra[:size]):
            return base + extra[size:]
        size -= 1
    return base + extra


def _raise_attempts(failures, truncated):
    if truncated is not None:
        label, error = truncated
        raise TruncatedJSON(_explain_failure(label, error, failures)) from error
    if failures:
        label, error = failures[-1]
        raise ValueError(_explain_failure(label, error, failures)) from error
    raise ValueError("模型没有返回 JSON。\n原因：正文和推理都是空的。")


def _continued_spec(original, result, brief=""):
    pieces = _named_pieces(result)
    attempts = []
    seen = []
    for label, piece in pieces:
        if _strip_fence(piece).lstrip().startswith("{"):
            attempts.append(("%s（以 { 开头）" % label, piece))
            seen.append(piece)
    for label, piece in pieces:
        stitched = _stitch_json(original, piece)
        if stitched and stitched not in seen:
            attempts.append(("原文 + %s" % label, stitched))
            seen.append(stitched)
    for label, piece in pieces:
        if piece not in seen:
            attempts.append((label, piece))
            seen.append(piece)
    truncated = None
    failures = []
    for label, text in attempts:
        try:
            spec = _extract_json(text, brief)
        except TruncatedJSON as error:
            truncated = (label, error)
            failures.append((label, error))
        except ValueError as error:
            failures.append((label, error))
        else:
            return spec
    _raise_attempts(failures, truncated)


def _spec_from_result(result, brief=""):
    truncated = None
    failures = []
    for label, text in _named_pieces(result):
        try:
            return _extract_json(text, brief)
        except TruncatedJSON as error:
            truncated = (label, error)
            failures.append((label, error))
        except ValueError as error:
            failures.append((label, error))
    _raise_attempts(failures, truncated)


def _reply_text(result):
    return ((result.content or "") + "\n" + (result.reasoning_content or "")).strip()


def write_llm_debug(folder, brief, skill_pack, result=None, repaired=None, template=None):
    """Persist prompts and raw model text for skill / response inspection."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill_pack.txt").write_text(skill_pack or "", encoding="utf-8")
    (folder / "json_shape.txt").write_text(json_shape(template, brief), encoding="utf-8")
    (folder / "system_prompt.txt").write_text(
        build_system_prompt(skill_pack, template, brief),
        encoding="utf-8",
    )
    (folder / "user_brief.txt").write_text(brief or "", encoding="utf-8")
    if result is not None:
        (folder / "raw_response.txt").write_text(_reply_text(result), encoding="utf-8")
    if repaired is not None:
        (folder / "raw_response_repaired.txt").write_text(_reply_text(repaired), encoding="utf-8")


def call_deepseek(brief, skill_pack, debug_dir=None, model=None, template=None, job_id=""):
    from AI.agent.layout import get_layout

    return get_layout().write(
        brief,
        skill_pack,
        debug_dir=debug_dir,
        model=model,
        template=template,
        job_id=job_id,
    )
