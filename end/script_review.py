"""Script semantic review, plus technical checks before a submit.

The script gate runs after the final script is on disk and before a person
confirms it. That review still calls ``complete_review``. Tests replace it
with ``set_reviewer``.

The old delivery semantic review does not run. ``review_delivery`` only
checks files, confirmation, and prompt presence, then records
``generation_technical``. It does not call a model and does not overwrite a
stored ``delivery`` row. A missing or historical delivery row is not a pass.
Nothing here deletes drafts or media.
"""

import hashlib
import json
import re
from pathlib import Path

MAX_ATTEMPTS = 3
_REVIEWER = None

_CATEGORIES = {
    "causal": "causal",
    "motivation": "motivation",
    "continuity": "continuity",
    "dialogue": "dialogue",
    "order": "order",
    "setup": "setup",
    "preference": "preference",
    "因果": "causal",
    "动机": "motivation",
    "连续性": "continuity",
    "对白": "dialogue",
    "台词": "dialogue",
    "顺序": "order",
    "铺垫": "setup",
    "偏好": "preference",
}
_SEVERITIES = {
    "block": "block",
    "suspect": "suspect",
    "preference": "preference",
    "阻断": "block",
    "重大": "block",
    "可疑": "suspect",
    "偏好": "preference",
}
_OWNERS = {
    "script": "script",
    "asset": "asset",
    "storyboard": "storyboard",
    "feed": "feed",
    "剧本": "script",
    "编剧": "script",
    "资产": "asset",
    "分镜": "storyboard",
    "投喂": "feed",
}
_FIELDS = ("location", "evidence", "category", "severity", "reason", "owner", "revision")
_STORY = {"causal", "motivation", "continuity"}
_STATUSES = {"passed", "blocked", "suspect", "failed"}
_SAVE_KEYS = {"script", "feed", "storyboard", "image_prompts", "text", "rewrite", "replacement", "previs"}
_PRIOR = re.compile(r"上一集|前一集|前集|上集")


class GenerationBlocked(RuntimeError):
    """Generation must not submit. Existing files stay where they are."""

    def __init__(self, reason, decision=None):
        super().__init__(reason)
        self.decision = decision or {}


def set_reviewer(fn):
    """Install a fake completion. ``None`` restores the JSON-RPC call."""
    global _REVIEWER
    _REVIEWER = fn


def complete_review(messages):
    """One review completion. Tests inject this. Production uses ``text.ask``."""
    if _REVIEWER is not None:
        return _REVIEWER(messages)
    from end.ai_client import ask_text

    return ask_text(messages, timeout=90, max_output_tokens=1800)


def content_hash(text):
    body = str(text or "").replace("\r\n", "\n").strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def issue_fingerprint(issue):
    """Identity of one review issue. Wording outside these four fields does not match."""
    issue = issue or {}
    body = "\n".join([
        str(issue.get("category") or "").strip(),
        str(issue.get("severity") or "").strip(),
        str(issue.get("evidence") or "").strip(),
        str(issue.get("reason") or "").strip(),
    ])
    return content_hash(body)


def reviews_path(folder):
    return Path(folder) / "reviews.json"


def load_reviews(folder):
    path = reviews_path(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_reviews(folder, state):
    path = reviews_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def _read(path):
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _spec(folder, spec=None):
    if isinstance(spec, dict):
        return spec
    path = Path(folder) / "spec.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def current_script(folder, spec=None):
    """The script under review. Two disagreeing copies are a block, not a merge."""
    spec = _spec(folder, spec)
    spec_text = str(spec.get("script") or "").strip()
    disk = _read(Path(folder) / "_内部_" / "剧本.md")
    if disk and spec_text and disk != spec_text:
        return "", "剧本.md 和 spec.json 的剧本不一致，不能审查通过，也不会自动删掉任何一份"
    return disk or spec_text, ""


def story_context(folder, brief=""):
    """Only text already stored. Missing previous episodes stay missing."""
    folder = Path(folder)
    parts = []
    kept = []
    for line in str(brief or "").splitlines():
        if re.match(r"^\s*画幅\s*[:：]", line):
            continue
        kept.append(line)
    brief = "\n".join(kept).strip()
    if brief:
        parts.append("用户立项原文：\n" + brief)
    for name in ("对标情报卡.md", "剧本过程.md"):
        body = _read(folder / "_内部_" / name)
        if body:
            parts.append(name + "：\n" + body)
    chain = folder / "_内部_" / "剧本链"
    if chain.is_dir():
        for path in sorted(chain.glob("*.md")):
            body = _read(path)
            if body:
                parts.append(path.name + "：\n" + body)
    if not parts:
        parts.append("没有前集、故事圣经或对标卡。跨集连续性无法从已有材料核对。")
    parts.append("上下文里没有写到的前集、前史和设定，标为不确定，不要编造。")
    return "\n\n".join(parts)


def _system(kind):
    common = "\n".join((
        "你只做审查，不改写故事，不输出新剧本、新分镜或新投喂稿。",
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        "格式：{\"issues\":[...]}。没有问题时 issues 是空数组。",
        "每条必须有 location、evidence、category、severity、reason、owner、revision。",
        "category 只能是 causal、motivation、continuity、dialogue、order、setup、preference。",
        "severity 只能是 block、suspect、preference。",
        "owner 只能是 script、asset、storyboard、feed。",
        "evidence 必须引用给定正文里的原句。revision 只写要改什么，不要写出改完的全文。",
        "重大因果、动机或连续性矛盾用 block。证据不足或仅可能有问题用 suspect。主观好恶用 preference。",
        "给定材料里没有的前集和设定，不要补写。相关条目标 suspect，reason 写明上下文不足。",
    ))
    if kind == "script":
        return common + "\n审查对象是最终剧本。问题的 owner 用 script。查因果链、人物动机和人物所知、时间地点、人物状态、道具、跨集设定是否连续。"
    return common + "\n".join((
        "",
        "审查对象是已确认剧本，对照资产提示词、分镜和即将提交的最终提示词。",
        "最终提示词已经包含出片时追加的约束。遗漏或篡改关键铺垫、对白、动作顺序、动机、连续性，owner 用 asset、storyboard 或 feed。",
        "若矛盾在已确认剧本自身，owner 用 script，不要改剧本。",
        "只增强镜头表达、不改剧情的差异用 preference，不要用 block。",
    ))


def _user(kind, script, context, bundle=""):
    lines = ["可用上下文：", context, "", "最终剧本：", script or "（空）"]
    if kind == "delivery":
        lines.extend(["", "即将提交的资产、分镜和最终提示词：", bundle or "（空）"])
    return "\n".join(lines)


def parse_issues(raw):
    """Return a list, or None when the body cannot be trusted."""
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    if any(key in data for key in _SAVE_KEYS):
        return None
    rows = data.get("issues")
    if not isinstance(rows, list):
        return None
    issues = []
    for item in rows:
        if not isinstance(item, dict):
            return None
        category = _CATEGORIES.get(str(item.get("category") or "").strip())
        severity = _SEVERITIES.get(str(item.get("severity") or "").strip())
        owner = _OWNERS.get(str(item.get("owner") or "").strip())
        fields = {
            "location": str(item.get("location") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
            "category": category or "",
            "severity": severity or "",
            "reason": str(item.get("reason") or "").strip(),
            "owner": owner or "",
            "revision": str(item.get("revision") or "").strip(),
        }
        if not category or not severity or not owner:
            return None
        if not all(fields[name] for name in _FIELDS):
            return None
        issues.append(fields)
    return issues


def _status_of(issues):
    severities = {item["severity"] for item in issues}
    if "block" in severities:
        return "blocked"
    if "suspect" in severities:
        return "suspect"
    return "passed"


def _force_story_owner(issues):
    """Causal, motive, and continuity problems belong to the script, whatever owner the model named."""
    for item in issues or []:
        if item.get("category") in _STORY:
            item["owner"] = "script"
    return issues


def _prior_gap(script, context):
    """A prior-episode claim without a stored prior fact is uncertain, not a pass."""
    match = _PRIOR.search(script or "")
    if not match:
        return None
    body = str(context or "")
    if "前集事实：" in body or "上一集事实：" in body:
        return None
    return {
        "location": "跨集",
        "evidence": match.group(0),
        "category": "continuity",
        "severity": "suspect",
        "reason": "上下文不足，不能确定跨集连续性，也不补写前集",
        "owner": "script",
        "revision": "补上已有的前集材料后再确认，或删掉没有来源的跨集断言",
        "uncertain": True,
    }


def _primary_owner(issues, default):
    for severity in ("block", "suspect"):
        for item in issues:
            if item["severity"] == severity:
                return item["owner"]
    return default


def format_issues(issues):
    lines = []
    for item in issues:
        if item["severity"] == "preference":
            lines.append("偏好（不阻断）%s：%s" % (item["location"], item["reason"]))
            continue
        label = {"block": "阻断", "suspect": "可疑"}.get(item["severity"], item["severity"])
        lines.append(
            "[%s] %s · %s · 负责：%s\n证据：%s\n原因：%s\n修订：%s"
            % (label, item["location"], item["category"], item["owner"], item["evidence"], item["reason"], item["revision"])
        )
    return "\n".join(lines)


def _record(status, reason, issues, **hashes):
    row = {"status": status, "reason": reason, "issues": issues}
    row.update(hashes)
    return row


def _cached(row, attempts_key, force):
    if force or not isinstance(row, dict):
        return None
    if row.get("cache_key") != attempts_key:
        return None
    if row.get("status") not in _STATUSES:
        return None
    return row


def _run(kind, messages, previous, cache_key, force, complete):
    previous = previous or {}
    cached = _cached(previous, cache_key, force)
    if cached is not None:
        return cached, False
    attempts = int(previous.get("attempts") or 0) if previous.get("cache_key") == cache_key else 0
    if attempts >= MAX_ATTEMPTS:
        row = _record("failed", "同一内容的语义复审已达 %d 次，不再自动调用模型" % MAX_ATTEMPTS, [])
        row["cache_key"] = cache_key
        row["attempts"] = attempts
        return row, False
    try:
        raw = complete(messages)
    except Exception as error:
        row = _record("failed", "语义审查没有完成：%s" % error, [])
        row["cache_key"] = cache_key
        row["attempts"] = attempts + 1
        return row, True
    issues = parse_issues(raw)
    if issues is None:
        row = _record("failed", "语义审查结果不是合格的 JSON，不能当作通过", [])
        row["cache_key"] = cache_key
        row["attempts"] = attempts + 1
        return row, True
    row = _record(_status_of(issues), "", issues)
    row["cache_key"] = cache_key
    row["attempts"] = attempts + 1
    row["reason"] = format_issues(issues)
    return row, True


def script_is_settled(folder, script_hash, context_hash):
    """True when a human already accepted this exact script and requirements context."""
    state = load_reviews(folder)
    accept = state.get("human_accept") if isinstance(state.get("human_accept"), dict) else None
    return bool(
        accept
        and accept.get("acknowledged") is True
        and accept.get("script_hash") == script_hash
        and accept.get("context_hash") == context_hash
    )


def settled_script_note(folder, script_hash, context_hash):
    """Tell a downstream reviewer the script is settled. Not a waiver list."""
    if not script_is_settled(folder, script_hash, context_hash):
        return ""
    state = load_reviews(folder)
    accept = state.get("human_accept") or {}
    lines = [
        "这份剧本已经人工接受，正文摘要和原要求摘要都没变。",
        "不要把这一次审查做成对剧本的第二次裁判，也不要再要求先改剧本。",
        "请对照这份已定稿，检查资产、分镜和最终提示词有没有偏离：缺了已定稿里的关键情节、对白或动作，或写了和已定稿冲突的新内容。",
        "新的偏离仍要列出。不要因为措辞相近就把它当成已经接受的旧结论而放过，也不要因为类别相同就整类放过。",
    ]
    noted = [item for item in (accept.get("issues") or []) if isinstance(item, dict)]
    if noted:
        lines.append("人工接受时记下的审稿结论只作背景，不是豁免名单：")
        for item in noted:
            lines.append("- %s / %s / %s / %s" % (
                item.get("category") or "",
                item.get("severity") or "",
                item.get("evidence") or "",
                item.get("reason") or "",
            ))
    return "\n".join(lines)


def review_script(folder, spec=None, brief="", force=False, complete=None):
    """Review the final script. Does not write the script.

    A human acceptance of this exact script and requirements context does not
    call the model again, even when force is set. Changing the script or the
    requirements leaves this path and reviews normally.
    """
    folder = Path(folder)
    before = _read(folder / "_内部_" / "剧本.md")
    script, clash = current_script(folder, spec)
    state = load_reviews(folder)
    if not clash and script:
        base_hash = content_hash(story_context(folder, brief))
        if script_is_settled(folder, content_hash(script), base_hash):
            row = state.get("script") if isinstance(state.get("script"), dict) else {}
            if row.get("script_hash") == content_hash(script) and row.get("context_hash") == base_hash:
                kept = dict(row)
            else:
                kept = {
                    "status": "unreviewed",
                    "reason": "正文和原要求没变，不再审这一稿",
                    "issues": [],
                    "script_hash": content_hash(script),
                    "context_hash": base_hash,
                }
            kept["skipped_repeat"] = True
            return kept
    if clash or not script:
        reason = clash or "剧本是空的，不能通过语义审查"
        row = _record("failed", reason, [])
        row["script_hash"] = ""
        row["context_hash"] = ""
        state["script"] = row
        save_reviews(folder, state)
        return row
    context = story_context(folder, brief)
    script_hash = content_hash(script)
    from end.script_opinion import intent_for_script

    intent = intent_for_script(folder, script_hash)
    if intent:
        context = context + (
            "\n\n用户本轮有意变更：%s\n这条意见优先于原要求里的主题和机制。"
            "不要把有意改掉的主题或机制压回旧要求，也不要因为意见原文不在旧稿里就判无依据。"
            % intent.get("opinion")
        )
    context_hash = content_hash(context)
    cache_key = script_hash + ":" + context_hash
    messages = [
        {"role": "system", "content": _system("script")},
        {"role": "user", "content": _user("script", script, context)},
    ]
    row, _called = _run("script", messages, state.get("script"), cache_key, force, complete or complete_review)
    issues = list(row.get("issues") or [])
    gap = _prior_gap(script, context)
    if gap and not any(item.get("uncertain") for item in issues):
        issues.append(gap)
    _force_story_owner(issues)
    for item in issues:
        item["owner"] = "script"
    row["issues"] = issues
    if issues or row.get("status") == "failed":
        if row.get("status") != "failed":
            row["status"] = _status_of(issues)
        row["reason"] = format_issues(issues) or row.get("reason") or ""
    row["script_hash"] = script_hash
    row["context_hash"] = context_hash
    state = load_reviews(folder)
    state["script"] = row
    save_reviews(folder, state)
    if _read(folder / "_内部_" / "剧本.md") != before:
        raise RuntimeError("语义审查改写了剧本文件")
    return row


def confirm_script(folder, accept_suspects=False, spec=None, brief=""):
    """Record a human confirmation. Does not change the script text."""
    folder = Path(folder)
    script, clash = current_script(folder, spec)
    if clash or not script:
        return False, clash or "剧本是空的"
    state = load_reviews(folder)
    row = state.get("script") or {}
    script_hash = content_hash(script)
    context_hash = content_hash(story_context(folder, brief))
    if row.get("script_hash") != script_hash or row.get("context_hash") != context_hash:
        return False, "剧本或上下文已变化，旧审查失效，需要重新审查"
    status = row.get("status")
    if status == "passed":
        state["confirmed_script_hash"] = script_hash
        state["confirmed_context_hash"] = context_hash
        save_reviews(folder, state)
        return True, ""
    if status == "suspect" and accept_suspects:
        state["confirmed_script_hash"] = script_hash
        state["confirmed_context_hash"] = context_hash
        row = dict(row)
        row["accepted_suspects"] = True
        row["accepted_script_hash"] = script_hash
        row["accepted_context_hash"] = context_hash
        row["accepted_by"] = "human"
        state["script"] = row
        save_reviews(folder, state)
        return True, ""
    if status == "suspect":
        return False, "有可疑项，需要明确接受后才能确认剧本"
    if status == "blocked":
        return False, row.get("reason") or "语义审查有阻断项，不能确认"
    return False, row.get("reason") or "语义审查未通过，不能确认"


def downstream_allowed(folder, spec=None, brief=""):
    """True only when this exact script was reviewed and then confirmed."""
    script, clash = current_script(folder, spec)
    if clash:
        return False, clash
    if not script:
        return False, "还没有剧本"
    state = load_reviews(folder)
    script_hash = content_hash(script)
    context_hash = content_hash(story_context(folder, brief))
    accept = state.get("human_accept") if isinstance(state.get("human_accept"), dict) else None
    if (
        accept
        and accept.get("acknowledged") is True
        and accept.get("script_hash") == script_hash
        and accept.get("context_hash") == context_hash
    ):
        return True, ""
    if state.get("confirmed_script_hash") != script_hash or state.get("confirmed_context_hash") != context_hash:
        return False, "剧本尚未人工确认，或确认后正文/上下文已变"
    row = state.get("script") or {}
    if row.get("script_hash") != script_hash or row.get("context_hash") != context_hash:
        return False, "剧本审查与当前正文不一致"
    if row.get("status") == "passed":
        return True, ""
    if row.get("status") == "suspect" and row.get("accepted_by") == "human":
        if (
            row.get("accepted_script_hash") == script_hash
            and row.get("accepted_context_hash") == context_hash
        ):
            return True, ""
        return False, "对可疑项的接受已经过期"
    return False, row.get("reason") or "剧本语义审查未通过"


def _shot_spoken(spec, shots, index):
    from end.render_loop import assign_dialogues

    groups = assign_dialogues(spec, shots)
    if index < len(groups):
        return groups[index]
    return []


def planned_image_texts(asset, template=None):
    """Every image prompt this asset might submit, including the reference lock."""
    from end.render_loop import image_prompt

    asset = asset or {}
    plain = image_prompt(asset, reference=False, template=template)
    texts = [plain]
    if asset.get("references") or asset.get("depends_on"):
        locked = image_prompt(asset, reference=True, template=template)
        if locked != plain:
            texts.append(locked)
    return texts


def planned_video_text(spec, shot, index, template=None, soundtrack="off", user_image_ids=(), has_tail=None):
    """Clip prompt for one tail state. ``has_tail`` defaults to later shots only."""
    from end.render_loop import choose_video_mode, delivery_prompt

    if has_tail is None:
        has_tail = index > 0
    has_tail = bool(has_tail)
    mode = choose_video_mode(shot, user_image_ids=user_image_ids, continues=has_tail)
    _assets, shots = _assets_shots(spec)
    spoken = _shot_spoken(spec, shots, index)
    return delivery_prompt(
        shot,
        spoken,
        index,
        has_tail,
        mode,
        soundtrack=soundtrack,
        template=template,
    )


def _binding(shot_id, mode, has_tail, images, tail_from, reference=False):
    return {
        "shot_id": str(shot_id or ""),
        "mode": str(mode or ""),
        "has_tail": bool(has_tail),
        "images": [str(item) for item in (images or [])],
        "tail_from": str(tail_from or ""),
        "reference": bool(reference),
    }


def _payload(text, binding):
    blob = json.dumps({"binding": binding, "text": text}, ensure_ascii=False, sort_keys=True)
    return {"text": text, "binding": binding, "hash": content_hash(blob)}


def video_payloads(spec, shot, index, template=None, soundtrack="off", user_image_ids=()):
    """Both tail states for a later shot. Shot 1 has no previous frame."""
    from end.render_loop import choose_video_mode

    _assets, shots = _assets_shots(spec)
    flags = (False, True) if index else (False,)
    rows = []
    images = list((shot or {}).get("images") or [])
    tail_from = shots[index - 1]["id"] if index else ""
    for has_tail in flags:
        mode = choose_video_mode(shot, user_image_ids=user_image_ids, continues=has_tail)
        text = planned_video_text(
            spec,
            shot,
            index,
            template=template,
            soundtrack=soundtrack,
            user_image_ids=user_image_ids,
            has_tail=has_tail,
        )
        rows.append(_payload(text, _binding(
            shot.get("id"),
            mode,
            has_tail,
            images,
            tail_from if has_tail else "",
        )))
    return rows


def _assets_shots(spec):
    from end.workflow_from_spec import collect_steps

    return collect_steps(spec)


def disk_source_hash(folder, brief=""):
    """Hash the manuscript that is on disk right now, not a cached object."""
    spec = _spec(folder, None)
    script, _clash = current_script(folder, spec)
    blob = json.dumps(
        {
            "assets": spec.get("assets") or [],
            "context": story_context(folder, brief),
            "feed": spec.get("feed") or "",
            "image_prompts": spec.get("image_prompts") or "",
            "script": script,
            "shots": spec.get("shots") or [],
            "storyboard": spec.get("storyboard") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return content_hash(blob)


def generation_bundle(spec, template=None, soundtrack="off", user_ids_for=None):
    """Final image and video prompts, plus the manuscript they must follow."""
    spec = spec or {}
    assets, shots = _assets_shots(spec)
    user_ids_for = user_ids_for or (lambda _shot, _index: ())
    items = []
    for asset in assets:
        plain = planned_image_texts(asset, template)
        for text in plain:
            reference = text != plain[0]
            items.append({
                "kind": "image",
                "id": asset["id"],
                "text": text,
                "hash": _payload(text, _binding(asset["id"], "image", False, [], "", reference))["hash"],
                "binding": _binding(asset["id"], "image", False, [], "", reference),
            })
    for index, shot in enumerate(shots):
        for payload in video_payloads(
            spec,
            shot,
            index,
            template=template,
            soundtrack=soundtrack,
            user_image_ids=user_ids_for(shot, index),
        ):
            items.append({
                "kind": "video",
                "id": shot["id"],
                "text": payload["text"],
                "hash": payload["hash"],
                "binding": payload["binding"],
            })
    manuscript = "\n\n".join((
        str(spec.get("image_prompts") or ""),
        str(spec.get("storyboard") or ""),
        str(spec.get("feed") or ""),
        json.dumps(items, ensure_ascii=False, sort_keys=True),
    ))
    return items, manuscript


def technical_generation_block(folder, spec=None, brief="", template=None, soundtrack="off", user_ids_for=None):
    """File, prompt, and script-confirmation checks. Does not call a model."""
    allowed, reason = downstream_allowed(folder, spec, brief)
    if not allowed:
        return {"ok": False, "status": "failed", "reason": reason, "owner": "script", "technical": True}
    spec = _spec(folder, spec)
    _script, clash = current_script(folder, spec)
    if clash:
        return {"ok": False, "status": "failed", "reason": clash, "owner": "script", "technical": True}
    shots = [item for item in (spec.get("shots") or []) if isinstance(item, dict)]
    if shots and not any(str(item.get("prompt") or "").strip() for item in shots):
        return {
            "ok": False,
            "status": "failed",
            "reason": "提示词是空的",
            "owner": "feed",
            "technical": True,
        }
    items, _manuscript = generation_bundle(spec, template, soundtrack, user_ids_for)
    if not items:
        return {
            "ok": False,
            "status": "failed",
            "reason": "没有可提交的资产或镜头提示词",
            "owner": "feed",
            "technical": True,
        }
    if any(not str(item.get("text") or "").strip() for item in items):
        return {
            "ok": False,
            "status": "failed",
            "reason": "提示词是空的",
            "owner": "feed",
            "technical": True,
        }
    return None


def _snapshot_prompt_hashes(items):
    hashes = []
    for item in items or []:
        text = str(item.get("text") or "")
        payload_hash = item.get("hash") or content_hash(text)
        if payload_hash not in hashes:
            hashes.append(payload_hash)
        if text.strip():
            digest = content_hash(text)
            if digest not in hashes:
                hashes.append(digest)
    return hashes


def record_generation_technical(folder, spec=None, brief="", template=None, soundtrack="off", user_ids_for=None):
    """Snapshot the prompts for this explicit render. Does not touch delivery history."""
    folder = Path(folder)
    spec = _spec(folder, spec)
    script, _clash = current_script(folder, spec)
    items, manuscript = generation_bundle(spec, template, soundtrack, user_ids_for)
    source_hash = disk_source_hash(folder, brief)
    state = load_reviews(folder)
    previous = state.get("delivery")
    state["generation_technical"] = {
        "source_hash": source_hash,
        "prompt_hashes": _snapshot_prompt_hashes(items),
        "bundle_hash": content_hash(str(script or "") + "\n" + manuscript + "\n" + source_hash),
    }
    if previous is not None:
        state["delivery"] = previous
    save_reviews(folder, state)
    return state["generation_technical"]


def review_delivery(folder, spec=None, brief="", template=None, soundtrack="off", user_ids_for=None, force=False, complete=None):
    """Retired semantic delivery review. Technical snapshot only. Does not call a model."""
    del force, complete
    folder = Path(folder)
    script_before = _read(folder / "_内部_" / "剧本.md")
    block = technical_generation_block(folder, spec, brief, template, soundtrack, user_ids_for)
    if _read(folder / "_内部_" / "剧本.md") != script_before:
        raise RuntimeError("出片检查改写了剧本文件")
    if block:
        return {
            "status": "failed",
            "reason": block["reason"],
            "owner": block["owner"],
            "issues": [],
            "semantic_review": "retired",
            "technical": True,
        }
    record_generation_technical(folder, spec, brief, template, soundtrack, user_ids_for)
    return {"status": "technical_ok", "reason": "", "owner": "", "issues": [], "semantic_review": "retired"}


def delivery_decision(row):
    row = row or {}
    status = row.get("status") or "pending"
    if status == "passed":
        return {"ok": True, "status": status, "reason": "", "owner": "", "options": []}
    if (
        status == "suspect"
        and row.get("accepted_by") == "human"
        and row.get("accepted_bundle_hash") == row.get("bundle_hash")
        and row.get("accepted_source_hash") == row.get("source_hash")
        and row.get("prompt_hashes")
    ):
        return {"ok": True, "status": status, "reason": "", "owner": "", "options": []}
    owner = row.get("owner") or "script"
    reason = row.get("reason") or "出片语义审查未通过"
    options = ["根据建议修改"]
    if status == "suspect":
        options = ["接受可疑项并出片", "根据建议修改"]
    elif status == "failed":
        options = ["再审一次", "根据建议修改"]
    return {"ok": False, "status": status or "pending", "reason": reason, "owner": owner, "options": options}


def accept_delivery_suspects(folder):
    state = load_reviews(folder)
    row = dict(state.get("delivery") or {})
    if row.get("status") != "suspect":
        return False, row.get("reason") or "当前不是待人工判定的出片审查"
    if any(item.get("severity") == "block" for item in row.get("issues") or []):
        return False, "有阻断项，不能接受后出片"
    row["accepted_suspects"] = True
    row["accepted_by"] = "human"
    row["accepted_bundle_hash"] = row.get("bundle_hash") or ""
    row["accepted_source_hash"] = row.get("source_hash") or ""
    state["delivery"] = row
    save_reviews(folder, state)
    return True, ""


def _match_live(folder, payload, brief):
    state = load_reviews(folder)
    row = state.get("generation_technical") or {}
    if not row.get("prompt_hashes"):
        raise GenerationBlocked(
            "还没有这次出片的技术快照",
            {"ok": False, "status": "failed", "reason": "还没有这次出片的技术快照", "owner": "feed", "technical": True},
        )
    source = disk_source_hash(folder, brief)
    if row.get("source_hash") != source:
        raise GenerationBlocked(
            "磁盘上的剧本或下游稿已经变了",
            {"ok": False, "status": "failed", "reason": "磁盘上的剧本或下游稿已经变了", "owner": "script", "technical": True},
        )
    if payload["hash"] not in set(row.get("prompt_hashes") or []):
        raise GenerationBlocked(
            "即将提交的提示词和这次出片开始时不一致",
            {"ok": False, "status": "failed", "reason": "即将提交的提示词和这次出片开始时不一致", "owner": "feed", "technical": True},
        )


def authorize_video_submit(folder, shot_id, has_tail, user_image_ids=(), template=None, soundtrack="off", brief="", project_id=""):
    """Read the shot from disk twice. Submit only the text that was reviewed for this tail state."""
    from end.render_loop import project_lock

    lock = project_lock(project_id or str(folder))

    def build():
        spec = _spec(folder, None)
        _assets, shots = _assets_shots(spec)
        index = next((i for i, shot in enumerate(shots) if shot.get("id") == shot_id), None)
        if index is None:
            raise GenerationBlocked("磁盘上没有镜头 %s" % shot_id, {"ok": False, "owner": "feed", "reason": "没有这个镜头"})
        shot = shots[index]
        text = planned_video_text(
            spec,
            shot,
            index,
            template=template,
            soundtrack=soundtrack,
            user_image_ids=user_image_ids,
            has_tail=bool(has_tail),
        )
        assert_generation_prompt(folder, text)
        from end.render_loop import choose_video_mode

        mode = choose_video_mode(shot, user_image_ids=user_image_ids, continues=bool(has_tail))
        tail_from = shots[index - 1]["id"] if has_tail and index else ""
        payload = _payload(text, _binding(shot_id, mode, has_tail, shot.get("images") or [], tail_from))
        payload["source_hash"] = disk_source_hash(folder, brief)
        return payload

    with lock:
        first = build()
        _match_live(folder, first, brief)
        second = build()
        if second["hash"] != first["hash"] or second["source_hash"] != first["source_hash"]:
            raise GenerationBlocked(
                "review→submit 窗口内磁盘上的稿件变了",
                {"ok": False, "status": "failed", "owner": "feed", "reason": "提交前持久内容变了"},
            )
        _store_lease(folder, {
            "kind": "video",
            "step_id": shot_id,
            "has_tail": bool(has_tail),
            "user_image_ids": [str(item) for item in (user_image_ids or [])],
            "template": template or "",
            "soundtrack": soundtrack or "off",
            "brief": brief or "",
            "source_hash": first["source_hash"],
            "payload_hash": first["hash"],
        })
        return first["text"]


def authorize_image_submit(folder, asset_id, reference, template=None, brief="", project_id=""):
    """Read the asset prompt from disk twice before an image submit."""
    from end.render_loop import image_prompt, project_lock

    lock = project_lock(project_id or str(folder))

    def build():
        spec = _spec(folder, None)
        assets, _shots = _assets_shots(spec)
        asset = next((item for item in assets if item.get("id") == asset_id), None)
        if asset is None:
            raise GenerationBlocked("磁盘上没有资产 %s" % asset_id, {"ok": False, "owner": "asset", "reason": "没有这个资产"})
        text = image_prompt(asset, reference=bool(reference), template=template)
        assert_generation_prompt(folder, text)
        payload = _payload(text, _binding(asset_id, "image", False, [], "", bool(reference)))
        payload["source_hash"] = disk_source_hash(folder, brief)
        return payload

    with lock:
        first = build()
        _match_live(folder, first, brief)
        second = build()
        if second["hash"] != first["hash"] or second["source_hash"] != first["source_hash"]:
            raise GenerationBlocked(
                "review→submit 窗口内磁盘上的稿件变了",
                {"ok": False, "status": "failed", "owner": "asset", "reason": "提交前持久内容变了"},
            )
        _store_lease(folder, {
            "kind": "image",
            "step_id": asset_id,
            "reference": bool(reference),
            "template": template or "",
            "brief": brief or "",
            "source_hash": first["source_hash"],
            "payload_hash": first["hash"],
        })
        return first["text"]


def _lease_key(kind, step_id):
    return "%s:%s" % (kind, step_id)


def _store_lease(folder, lease):
    state = load_reviews(folder)
    leases = dict(state.get("submit_leases") or {})
    leases[_lease_key(lease["kind"], lease["step_id"])] = lease
    state["submit_leases"] = leases
    save_reviews(folder, state)


def _video_lease_current(folder, lease):
    if disk_source_hash(folder, lease.get("brief") or "") != lease.get("source_hash"):
        return False
    spec = _spec(folder, None)
    _assets, shots = _assets_shots(spec)
    shot_id = lease.get("step_id")
    index = next((i for i, shot in enumerate(shots) if shot.get("id") == shot_id), None)
    if index is None:
        return False
    text = planned_video_text(
        spec,
        shots[index],
        index,
        template=lease.get("template") or None,
        soundtrack=lease.get("soundtrack") or "off",
        user_image_ids=lease.get("user_image_ids") or (),
        has_tail=bool(lease.get("has_tail")),
    )
    from end.render_loop import choose_video_mode

    mode = choose_video_mode(
        shots[index],
        user_image_ids=lease.get("user_image_ids") or (),
        continues=bool(lease.get("has_tail")),
    )
    tail_from = shots[index - 1]["id"] if lease.get("has_tail") and index else ""
    payload = _payload(text, _binding(shot_id, mode, lease.get("has_tail"), shots[index].get("images") or [], tail_from))
    return payload["hash"] == lease.get("payload_hash")


def _image_lease_current(folder, lease):
    if disk_source_hash(folder, lease.get("brief") or "") != lease.get("source_hash"):
        return False
    from end.render_loop import image_prompt

    spec = _spec(folder, None)
    assets, _shots = _assets_shots(spec)
    asset = next((item for item in assets if item.get("id") == lease.get("step_id")), None)
    if asset is None:
        return False
    text = image_prompt(asset, reference=bool(lease.get("reference")), template=lease.get("template") or None)
    payload = _payload(text, _binding(lease.get("step_id"), "image", False, [], "", bool(lease.get("reference"))))
    return payload["hash"] == lease.get("payload_hash")


def lease_still_current(folder, kind, step_id):
    """Re-read disk for this image or video step. A missing lease is not current.

    An image step never borrows a video lease, and the other way around.
    """
    if kind not in ("image", "video") or not step_id:
        return False
    leases = (load_reviews(folder) or {}).get("submit_leases") or {}
    lease = leases.get(_lease_key(kind, step_id))
    if not isinstance(lease, dict) or lease.get("kind") != kind or lease.get("step_id") != step_id:
        return False
    if kind == "image":
        return _image_lease_current(folder, lease)
    return _video_lease_current(folder, lease)


def quarantine_stale_download(folder, path):
    """Archive a rejected download. Return True only when the active file is gone.

    A failed copy leaves the active file in place. Callers must not describe
    that as archived, and must not delete it themselves.
    """
    from end.history_keep import ArchiveError, archive_file

    if path is None or not Path(path).is_file():
        return False
    try:
        archived = archive_file(folder, path, "stale-download")
    except ArchiveError:
        return False
    if archived is None:
        return False
    try:
        Path(path).unlink()
    except OSError:
        return False
    return not Path(path).is_file()


def assert_generation_prompt(folder, text):
    """Refuse a submit that is empty or was not in this render's technical snapshot."""
    if not str(text or "").strip():
        raise GenerationBlocked(
            "提示词是空的",
            {"ok": False, "status": "failed", "reason": "提示词是空的", "owner": "feed", "technical": True},
        )
    state = load_reviews(folder)
    row = state.get("generation_technical") or {}
    digest = content_hash(text)
    if digest not in set(row.get("prompt_hashes") or []):
        raise GenerationBlocked(
            "即将提交的提示词和这次出片开始时不一致",
            {"ok": False, "status": "failed", "reason": "即将提交的提示词和这次出片开始时不一致", "owner": "feed", "technical": True},
        )


def user_image_ids(work_dir, shot):
    from end.render_loop import load_run_state

    state = load_run_state(work_dir)
    steps = state.get("steps") or {}
    found = []
    for asset_id in shot.get("images") or []:
        step = steps.get(asset_id) or {}
        if step.get("local_override"):
            found.append(asset_id)
    return found


def public_semantic(folder, spec=None, brief=""):
    """Read-only status for the existing project payload. Does not call a model."""
    script, clash = current_script(folder, spec)
    state = load_reviews(folder)
    script_hash = content_hash(script) if script else ""
    context_hash = content_hash(story_context(folder, brief)) if script else ""
    row = state.get("script") or {}
    delivery = state.get("delivery") or {}
    confirmed = state.get("confirmed_script_hash") or ""
    allowed, allow_reason = downstream_allowed(folder, spec, brief) if script and not clash else (False, clash or "还没有剧本")
    script_fresh = bool(row) and row.get("script_hash") == script_hash and row.get("context_hash") == context_hash
    return {
        "script_status": (row.get("status") if script_fresh else "") or ("failed" if clash else "pending"),
        "script_reason": clash or (row.get("reason") if script_fresh else "还没有对当前剧本的语义审查"),
        "confirmed": bool(allowed),
        "confirm_reason": "" if allowed else allow_reason,
        "confirmed_script_hash": confirmed,
        "script_hash": script_hash,
        "delivery_status": delivery.get("status") if delivery else "",
        "delivery_reason": delivery.get("reason") or "",
        "delivery_matches_script": delivery.get("confirmed_script_hash") == script_hash and bool(script_hash),
        "delivery_history": bool(delivery),
        "delivery_history_status": delivery.get("status") or "",
        "delivery_semantic_review": "retired",
        "delivery_blocks_render": False,
        "issues": (row.get("issues") if script_fresh else []) or [],
        "delivery_issues": list(delivery.get("issues") or []),
    }


def apply_script_review_gate(folder, spec, locks, brief, outcome, force=False, complete=None):
    """Attach the script review to a format-passed script gate. Does not edit the script."""
    del locks
    row = review_script(folder, spec, brief=brief, force=force, complete=complete)
    outcome = dict(outcome or {})
    if row.get("skipped_repeat"):
        progress = dict((outcome.get("progress") or {}))
        pipeline = dict(progress.get("pipeline") or {})
        question = dict(progress.get("question") or {})
        message = dict(outcome.get("message") or {})
        pipeline["human_release"] = True
        pipeline["passed"] = True
        pipeline["current"] = "script"
        pipeline["action"] = "wait"
        pipeline["semantic_status"] = row.get("status") or "unreviewed"
        pipeline["semantic_scope"] = "script"
        reply = "这份稿已经人工接受，正文和原要求没变，不再审一遍。下游仍对照这份已定稿检查，不会出图。"
        options = ["继续"]
        question.update({"id": "script", "kind": "single", "reply": reply, "options": options, "cards": []})
        message.update({"role": "assistant", "content": reply, "options": options, "choice_kind": "single", "cards": []})
        progress["pipeline"] = pipeline
        progress["question"] = question
        progress["phase"] = "gate_script"
        outcome["progress"] = progress
        outcome["message"] = message
        outcome["terminal"] = "gate"
        outcome["status"] = "gate"
        return outcome
    progress = dict(outcome.get("progress") or {})
    pipeline = dict(progress.get("pipeline") or {})
    question = dict(progress.get("question") or {})
    message = dict(outcome.get("message") or {})
    status = row.get("status") or "failed"
    pipeline["semantic_status"] = status
    pipeline["semantic_owner"] = "script"
    pipeline["semantic_scope"] = "script"
    detail = row.get("reason") or ""
    if status == "passed":
        note = "语义审查已通过，还没有阻断项。确认后才进入资产。"
        if detail:
            note = note + "\n" + detail
        reply = (question.get("reply") or message.get("content") or "").strip()
        reply = (reply + "\n" + note).strip()
        options = ["继续", "精修"]
        pipeline["passed"] = True
    elif status == "suspect":
        reply = "语义审查有可疑项，需要你判定。接受后才确认剧本并进入下游。\n" + detail
        options = ["接受可疑项并继续", "根据建议修改"]
        pipeline["passed"] = True
    else:
        reply = "语义审查未通过，不能进入资产或分镜。审查没有改剧本。\n" + (detail or "审查没有完成")
        options = ["再审一次", "根据建议修改"] if status == "failed" else ["根据建议修改"]
        pipeline["passed"] = False
        pipeline["retry_write"] = False
        pipeline["report"] = detail
        pipeline["repair"] = detail
    question.update({"id": "script", "kind": "single", "reply": reply, "options": options, "cards": []})
    message.update({"role": "assistant", "content": reply, "options": options, "choice_kind": "single", "cards": []})
    progress["pipeline"] = pipeline
    progress["question"] = question
    progress["semantic"] = public_semantic(folder, spec, brief)
    if status == "passed":
        progress["phase"] = "gate_script"
    outcome["progress"] = progress
    outcome["message"] = message
    outcome["terminal"] = "gate"
    outcome["status"] = "gate"
    return outcome
