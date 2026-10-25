"""Turn an over-long truncated write into closed batches and merge them.

Short truncations stay on the character continuation in agent.py.
This module only runs when the body was cut for length.
"""

import json
import re

from AI.script import (
    TruncatedJSON,
    _SPEC_KEYS,
    _loads_json_text,
    _reply_text,
    _spec_from_result,
    _strip_fence,
    explain_parse_failure,
    write_llm_debug,
)
from AI.skills.registry import _slice_module, json_shape, script_step_id

CATALOG_TOKENS = 16000
PREVIEW_CHARS = 1500
MAX_BATCHES = 6
MODEL_CALL_CAP = 8
WRITE_TIMEOUT = 1200
TEXT_KEYS = (
    "text",
    "image_prompts",
    "storyboard",
    "previs",
    "script",
    "feed",
    "intel",
    "internal",
)
LIST_KEYS = ("assets", "shots")
_ITEM_SPLIT = re.compile(r"[、\n；;]+")

CATALOG_LEAD = "\n".join(
    [
        "上一次输出超长，在中途被截断。不要续写，不要补已经写过的字符。",
        "按结构切开，不要按字数硬切。",
        "资产按角色、场景、道具。分镜和预演按段。剧本按场。投喂稿按段或分组。",
        "只输出一个闭合 JSON 对象，不要 Markdown 围栏。",
        '形如 {"batches":[{"id":"cast","field":"image_prompts","ask":"只写角色顾行和女主","items":["顾行","女主"]}]}。',
        "field 只能是 script、assets、shots、image_prompts、storyboard、feed、previs、intel、internal。",
        "最多 6 批。ask 写这一批要写什么。items 是这一批里的单项；没有就给空数组。",
        "不要写正文。",
    ]
)
REPAIR_CATALOG_LEAD = "\n".join(
    [
        "这次是返修。上一次输出超长，在中途被截断。不要续写，不要补已经写过的字符。",
        "只切简报里写明要改的失败项。锁定参考里的剧本和资产不要另开一批。",
        "分镜和预演按段。资产按被点名的图块。投喂稿按被点名的段。",
        "只输出一个闭合 JSON 对象，不要 Markdown 围栏。",
        '形如 {"batches":[{"id":"previs-1","field":"previs","ask":"只补段1缺的六问","items":["段1"]}]}。',
        "field 只能是 script、assets、shots、image_prompts、storyboard、feed、previs、intel、internal。",
        "最多 6 批。ask 写这一批要改的失败项。items 是这一批里的单项；没有就给空数组。",
        "不要写正文。",
    ]
)


def overlong_chars():
    return int(_slice_module().STAGE_OUTPUT_CHARS["P2"])


def batch_tokens():
    return CATALOG_TOKENS


def incomplete_reason(result):
    reason = getattr(result, "incomplete_reason", "") or ""
    if reason:
        return str(reason)
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        return str(raw.get("incompleteReason") or "")
    return ""


def is_overlong(result, body):
    if incomplete_reason(result) == "max_output_tokens":
        return True
    return len(str(body or "")) >= overlong_chars()


def begin_batches(state, body):
    state = dict(state)
    state["continue_write"] = False
    state["batch_mode"] = True
    state["batch_phase"] = "catalog"
    state["batch_queue"] = []
    state["batch_done"] = []
    state["pending_batch"] = None
    state["request"] = catalog_request(state, body)
    return state


def catalog_request(state, body):
    brief = state.get("brief") or ""
    repairing = "这次是返修" in brief[:2500]
    return {
        "kind": "write",
        "messages": [
            {"role": "system", "content": _catalog_lead(brief, repairing)},
            {"role": "user", "content": _catalog_user(brief, body)},
        ],
        "model": state.get("model_name"),
        "max_tokens": CATALOG_TOKENS,
        "timeout": WRITE_TIMEOUT,
    }


def _catalog_lead(brief, repairing):
    lead = REPAIR_CATALOG_LEAD if repairing else CATALOG_LEAD
    step = script_step_id(brief)
    if step == "5":
        return lead + "\n这一步是正式剧本，field 用 script。"
    if step:
        return lead + "\n这一步的正文 field 用 text。"
    return lead


def read_catalog(state):
    state = dict(state)
    try:
        queue = parse_catalog(_reply_text(state.get("result")))
    except (TruncatedJSON, ValueError) as error:
        _save(state, state.get("result"))
        raise RuntimeError(
            explain_parse_failure(error, "分批目录", "超长截断后没有得到批次目录。")
        ) from error
    return _arm(state, queue)


def read_item(state):
    state = dict(state)
    pending = dict(state.get("pending_batch") or {})
    result = state.get("result")
    try:
        spec = _spec_from_result(result, state.get("brief") or "")
    except TruncatedJSON:
        return _retry_split(state, pending)
    except ValueError as error:
        _save(state, result)
        raise RuntimeError(
            explain_parse_failure(
                error,
                "分批",
                "批次 %s 没有收成闭合 JSON。" % _batch_label(pending),
            )
        ) from error
    done = list(state.get("batch_done") or [])
    done.append(spec)
    state["batch_done"] = done
    queue = list(state.get("batch_queue") or [])
    if queue:
        return _arm(state, queue)
    state["output"] = merge_specs(done)
    state["request"] = None
    state["pending_batch"] = None
    _save(state, result)
    return state


def parse_catalog(text):
    data = _catalog_object(text)
    rows = data.get("batches")
    if not isinstance(rows, list) or not rows:
        raise ValueError("分批目录没有 batches。")
    found = []
    for index, row in enumerate(rows):
        if len(found) >= MAX_BATCHES:
            break
        found.append(_one_batch(row, index))
    if not found:
        raise ValueError("分批目录没有可用批次。")
    return found


def split_batch(batch):
    if batch.get("split"):
        return []
    names = _item_names(batch.get("items"))
    if not names:
        names = _names_from_ask(batch.get("ask") or "")
    if len(names) < 2:
        return []
    parent = str(batch.get("id") or "")
    pieces = []
    for index, name in enumerate(names):
        pieces.append(
            {
                "id": "%s-%s" % (parent, index + 1),
                "field": batch.get("field") or "",
                "ask": "只写这一项：%s" % name,
                "items": [name],
                "split": True,
                "split_from": parent,
            }
        )
    return pieces


def merge_specs(parts):
    merged = {}
    for key in TEXT_KEYS:
        chunks = _text_chunks(parts, key)
        if chunks:
            merged[key] = "\n\n".join(chunks)
    for key in LIST_KEYS:
        rows = _merge_rows(parts, key)
        if rows:
            merged[key] = rows
    title = _first_title(parts)
    if title:
        merged["title"] = title
    # region agent log
    try:
        import time

        payload = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "D",
            "location": "AI/agent/batch.py:merge_specs",
            "message": "merged batch fields",
            "data": {
                "keys": [key for key in list(merged)[:12]],
                "textLen": len(str(merged.get("text") or "")),
                "internalLen": len(str(merged.get("internal") or "")),
                "scriptLen": len(str(merged.get("script") or "")),
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    if not any(merged.get(key) for key in TEXT_KEYS + LIST_KEYS):
        raise RuntimeError("分批没有写出规格字段。")
    return merged


def _catalog_user(brief, body):
    preview = str(body or "")[:PREVIEW_CHARS]
    return "简报：\n%s\n\n截断正文的前%s字：\n%s" % (brief or "", PREVIEW_CHARS, preview)


def _catalog_object(text):
    cleaned = _strip_fence(text)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("分批目录没有左花括号。")
    end = cleaned.rfind("}")
    if end <= start:
        raise TruncatedJSON("分批目录不是闭合 JSON。")
    try:
        data = _loads_json_text(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise ValueError("分批目录 JSON 没有闭合。%s" % error.msg) from error
    if not isinstance(data, dict):
        raise ValueError("分批目录不是对象。")
    return data


def _one_batch(row, index):
    if not isinstance(row, dict):
        raise ValueError("批次 %s 不是对象。" % (index + 1))
    batch_id = str(row.get("id") or ("batch-%s" % (index + 1))).strip()
    field = str(row.get("field") or "").strip()
    if field not in _SPEC_KEYS and field != "text":
        raise ValueError("批次 %s 的 field 不是规格字段。" % batch_id)
    ask = str(row.get("ask") or "").strip()
    if not ask:
        raise ValueError("批次 %s 没有 ask。" % batch_id)
    return {
        "id": batch_id,
        "field": field,
        "ask": ask,
        "items": _item_names(row.get("items")),
        "split": False,
        "split_from": "",
    }


def _item_names(value):
    if isinstance(value, str):
        value = _ITEM_SPLIT.split(value)
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _names_from_ask(ask):
    names = _item_names(_ITEM_SPLIT.split(str(ask or "")))
    if len(names) < 2:
        return []
    return names


def _batch_system(template, brief, batch):
    field = batch.get("field") or ""
    return "\n".join(
        [
            json_shape(template, brief),
            "这一次只写一批。回复必须是一个闭合的 JSON 对象，不要 Markdown 围栏。",
            "正文只放在 %s。其他规格字段用空字符串或空数组。" % field,
            "reply 里不要写长文。",
            "这一批：%s" % (batch.get("ask") or ""),
        ]
    )


def _batch_request(state, batch):
    brief = state.get("brief") or ""
    return {
        "kind": "write",
        "messages": [
            {"role": "system", "content": _batch_system(state.get("template"), brief, batch)},
            {"role": "user", "content": brief},
        ],
        "model": state.get("model_name"),
        "max_tokens": batch_tokens(),
        "timeout": WRITE_TIMEOUT,
    }


def _arm(state, queue):
    pending = dict(queue[0])
    state = dict(state)
    state["batch_queue"] = [dict(item) for item in queue[1:]]
    state["pending_batch"] = pending
    state["batch_phase"] = "item"
    state["request"] = _batch_request(state, pending)
    return state


def _retry_split(state, pending):
    label = _batch_label(pending)
    pieces = [] if pending.get("split") else split_batch(pending)
    if not pieces:
        _save(state, state.get("result"))
        raise RuntimeError("批次 %s 再次被截断。" % label)
    queue = pieces + [dict(item) for item in (state.get("batch_queue") or [])]
    return _arm(state, queue)


def _batch_label(batch):
    return str(batch.get("split_from") or batch.get("id") or "")


def _text_chunks(parts, key):
    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get(key)
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return chunks


def _merge_rows(parts, key):
    rows = []
    index = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        incoming = part.get(key)
        if not isinstance(incoming, list):
            continue
        for row in incoming:
            if not isinstance(row, dict):
                continue
            copied = dict(row)
            item_id = str(copied.get("id") or "").strip()
            if item_id and item_id in index:
                rows[index[item_id]] = copied
                continue
            if item_id:
                index[item_id] = len(rows)
            rows.append(copied)
    return rows


def _first_title(parts):
    for part in parts:
        if not isinstance(part, dict):
            continue
        title = part.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def _save(state, repaired):
    debug_dir = state.get("debug_dir")
    if not debug_dir:
        return
    write_llm_debug(
        debug_dir,
        state.get("brief") or "",
        state.get("skill_pack") or "",
        result=state.get("first_result"),
        repaired=repaired,
        template=state.get("template"),
    )
