"""Fold specialist slot fills into a stage skeleton. The merger does not choose a draft."""

import json
import re

_MARKERS = {
    "asset": "当前资产骨架如下。",
    "script": "当前剧本骨架如下。",
    "storyboard": "当前分镜骨架如下。",
}
_FIELDS = {
    "asset": "image_prompts",
    "script": "script",
    "storyboard": "storyboard",
    "feed": "feed",
}
_SLOT_LINE = re.compile(r"^(格\d+|整板)\s*[:：]\s*(.*)$")
_ROW_CELL = {
    "第一行左": "格1",
    "第一行中": "格2",
    "第一行右": "格3",
    "第二行左": "格4",
    "第二行中": "格5",
    "第二行右": "格6",
    "第三行左": "格7",
    "第三行中": "格8",
    "第三行右": "格9",
}
_ROW_CELL_LINE = re.compile(
    r"^(%s)\s*[:：]\s*(.*)$" % "|".join(_ROW_CELL)
)
_SECTION_HEAD = re.compile(r"^【(场景定位|场景画面描写)】(.*)$")
_SCENE_TITLE_LINE = re.compile(r"场景名称\s*[:：]\s*(.+)$")
_SCENE_TITLE_VALUE = re.compile(r"^(.+?)（资产图编号：(图\s*\d+)）\s*$")
_KIND_LINE = re.compile(r"^种类：(.+)$")
_SCRIPT_LIST_HEAD = re.compile(r"^-\s+(?:#{2,6}\s+)?(.+?)$")
_SCRIPT_CAST = re.compile(r"^出场\s*[:：]\s*(.*)$")
_STORY_HEAD = re.compile(r"^#{2,6}\s+(\S+)\s*$")
_STORY_FILL = re.compile(r"^(画面层|旁白|声景)\s*[:：]\s*(.*)$")
_SCENE_ID = re.compile(r"(EP\d+[-_]?SC\d+|SC\d+)", re.I)


def fold_stage_draft(task, brief, partials):
    """Return the official draft assembled from skeleton slots plus specialist fills.

    Empty when this task has no skeleton in the brief. Unfilled slots stay 【填写】.
    Skill-document notes that do not carry slots are ignored.
    """
    task = str(task or "").strip()
    notes = [_note_as_markdown(item) for item in (partials or [])]
    if task == "feed":
        skeleton = _feed_skeleton_from_brief(brief)
        if not skeleton.strip():
            return ""
        return _fold_feed(skeleton, notes)
    mark = _MARKERS.get(task)
    if not mark:
        return ""
    skeleton = _skeleton_from_brief(brief, mark)
    if not skeleton.strip():
        return ""
    if task == "asset":
        return _fold_asset(skeleton, notes)
    if task == "script":
        return _fold_script(skeleton, notes)
    return _fold_storyboard(skeleton, notes)


def folded_merge_output(task, draft):
    """JSON object the suite merge step already returns."""
    field = _FIELDS.get(str(task or "").strip())
    if not field:
        return ""
    return json.dumps({field: draft}, ensure_ascii=False)


def _skeleton_from_brief(brief, mark):
    text = str(brief or "")
    start = text.find(mark)
    if start < 0:
        return ""
    rest = text[start:]
    cut = rest.find("\n各节点正文：")
    if cut >= 0:
        rest = rest[:cut]
    return rest.strip() + "\n"


def _note_as_markdown(item):
    if isinstance(item, dict):
        raw = item.get("text")
        if raw is None:
            raw = item.get("body") or ""
    else:
        raw = item
    text = str(raw or "").strip()
    if not text:
        return ""
    data = _json_object(text)
    if isinstance(data, dict):
        parts = _cards_from_payload(data)
        if parts:
            return "\n\n".join(parts)
        for key in ("image_prompts", "script", "storyboard", "text", "feed"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return text
    recovered = _cards_from_truncated_json(text)
    if recovered:
        return "\n\n".join(recovered)
    return text


def _cards_from_payload(data):
    parts = []
    prompts = data.get("image_prompts")
    if isinstance(prompts, list):
        for card in prompts:
            if isinstance(card, str) and card.strip():
                parts.append(card.strip())
            elif isinstance(card, dict):
                block = str(card.get("prompt") or card.get("text") or "").strip()
                if block:
                    parts.append(block)
    return parts


def _cards_from_truncated_json(text):
    """Keep complete and trailing partial card strings from a cut-off JSON note."""
    raw = str(text or "")
    if "{" not in raw or "image_prompts" not in raw:
        return []
    parts = []
    for value in _json_string_values(raw):
        if _looks_like_asset_card(value):
            parts.append(value.strip())
    return parts


def _looks_like_asset_card(value):
    text = str(value or "")
    if "#### 图" in text or "整板：" in text or "整板:" in text:
        return True
    return bool(_SLOT_LINE.search(text))


def _json_string_values(text):
    """Decode JSON strings, including a final unterminated value after a token cap."""
    found = []
    raw = str(text or "")
    i = 0
    n = len(raw)
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    while i < n:
        if raw[i] != '"':
            i += 1
            continue
        i += 1
        chunks = []
        closed = False
        while i < n:
            ch = raw[i]
            if ch == '"':
                closed = True
                i += 1
                break
            if ch != "\\":
                chunks.append(ch)
                i += 1
                continue
            if i + 1 >= n:
                break
            esc = raw[i + 1]
            if esc in escapes:
                chunks.append(escapes[esc])
                i += 2
                continue
            if esc == "u" and i + 5 < n:
                try:
                    chunks.append(chr(int(raw[i + 2:i + 6], 16)))
                except ValueError:
                    break
                i += 6
                continue
            break
        found.append("".join(chunks))
        if not closed:
            break
    return found


def _json_object(raw):
    start = str(raw or "").find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(str(raw)[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _filled(value):
    text = str(value or "").strip()
    if not text:
        return False
    if text == "【填写】" or text.startswith("【填写"):
        return False
    return True



def _scene_slot_index(pieces):
    """Map skeleton 图号 and 场景名称 to master/grid partners."""
    by_scene = {}
    kinds = {}
    titles = {}
    for asset_id, body in pieces:
        kind = ""
        title = ""
        for line in str(body or "").splitlines():
            match = _KIND_LINE.match(line.strip())
            if match:
                kind = match.group(1).strip()
            found = _scene_title_and_id(line)
            if found:
                title, _figure = found
        kinds[asset_id] = kind
        if title:
            titles[asset_id] = title
            by_scene.setdefault(title, {})[kind] = asset_id
    return {"by_scene": by_scene, "kinds": kinds, "titles": titles}


def _scene_title_and_id(line):
    match = _SCENE_TITLE_LINE.search(str(line or "").strip())
    if not match:
        return None
    raw = match.group(1).strip()
    parsed = _SCENE_TITLE_VALUE.match(raw)
    if parsed:
        from Agents.workers.asset_skeleton import _canonical_id

        return parsed.group(1).strip(), _canonical_id(parsed.group(2))
    return raw, ""


def _asset_fills_from_note(note, index):
    from Agents.workers.asset_skeleton import _asset_pieces

    fills = {}
    cards = _asset_pieces(note)
    blocks = cards or _template_scene_blocks(note, index)
    for asset_id, body in blocks:
        for slot, value in _slot_fills_from_body(body).items():
            for key in _resolve_slot_targets(asset_id, slot, index):
                fills[key] = value
    return fills


def _template_scene_blocks(note, index):
    text = str(note or "").strip()
    if not text:
        return []
    parts = [part for part in re.split(r"(?=【场景定位】)", text) if part.strip()]
    if not parts:
        parts = [text]
    blocks = []
    for part in parts:
        asset_id = _figure_id_in_text(part)
        if not asset_id:
            asset_id = _scene_asset_id(_scene_title_in_text(part), index)
        if not asset_id:
            asset_id = _lone_scene_asset_id(index)
        if asset_id:
            blocks.append((asset_id, part))
    return blocks


def _figure_id_in_text(text):
    from Agents.workers.asset_skeleton import _canonical_id

    match = re.search(r"资产图编号：\s*(图\s*\d+)", str(text or ""))
    if match:
        return _canonical_id(match.group(1))
    return ""


def _scene_title_in_text(text):
    for line in str(text or "").splitlines():
        found = _scene_title_and_id(line)
        if found and found[0]:
            return found[0]
    return ""


def _scene_asset_id(title, index):
    scene = (index or {}).get("by_scene", {}).get(title) or {}
    return scene.get("九宫格") or scene.get("场景母版") or ""


def _lone_scene_asset_id(index):
    scenes = list(((index or {}).get("by_scene") or {}).values())
    if len(scenes) != 1:
        return ""
    return scenes[0].get("九宫格") or scenes[0].get("场景母版") or ""


def _resolve_slot_targets(asset_id, slot, index):
    index = index or {}
    kind = index.get("kinds", {}).get(asset_id)
    title = index.get("titles", {}).get(asset_id)
    scene = index.get("by_scene", {}).get(title) or {}
    if slot == "整板" and kind == "九宫格" and scene.get("场景母版"):
        return [(scene["场景母版"], "整板")]
    if slot.startswith("格") and kind == "场景母版" and scene.get("九宫格"):
        return [(scene["九宫格"], slot)]
    return [(asset_id, slot)]


def _slot_fills_from_body(body):
    official = {}
    cells = {}
    sections = {"场景定位": [], "场景画面描写": []}
    current = None
    for line in str(body or "").splitlines():
        stripped = line.strip()
        match = _SLOT_LINE.match(stripped)
        if match and _filled(match.group(2)):
            official[match.group(1)] = match.group(2).strip()
            current = None
            continue
        cell = _ROW_CELL_LINE.match(stripped)
        if cell and _filled(cell.group(2)):
            cells[_ROW_CELL[cell.group(1)]] = cell.group(2).strip()
            current = None
            continue
        section = _SECTION_HEAD.match(stripped)
        if section:
            current = section.group(1)
            rest = section.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current and stripped.startswith("【"):
            current = None
            continue
        if current and stripped:
            sections[current].append(stripped)
    fills = dict(cells)
    if "整板" not in official:
        board = _master_board_text(sections)
        if board:
            fills["整板"] = board
    fills.update(official)
    return fills


def _master_board_text(sections):
    parts = []
    for name in ("场景定位", "场景画面描写"):
        text = re.sub(r"\s+", " ", " ".join(sections.get(name) or [])).strip()
        if text:
            parts.append("【%s】%s" % (name, text))
    return " ".join(parts)


def _fold_asset(skeleton, notes):
    from Agents.workers.asset_skeleton import _asset_pieces

    pieces = _asset_pieces(skeleton)
    if not pieces:
        return ""
    index = _scene_slot_index(pieces)
    fills = {}
    for note in notes:
        fills.update(_asset_fills_from_note(note, index))
    blocks = []
    for asset_id, body in pieces:
        lines = []
        for line in str(body or "").splitlines():
            match = _SLOT_LINE.match(line.strip())
            key = (asset_id, match.group(1)) if match else None
            if key in fills:
                lines.append("%s：%s" % (match.group(1), fills[key]))
            else:
                lines.append(line)
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(block for block in blocks if block) + "\n"


def _fold_script(skeleton, notes):
    from Agents.workers.script_skeleton import parse_script_scenes

    locked = _locked_script_scenes(skeleton)
    if not locked:
        return ""
    written = {}
    for note in notes:
        for scene in parse_script_scenes(note):
            scene_id = scene.get("id")
            if scene_id:
                written[scene_id] = scene
    blocks = []
    header = _script_lock_header(skeleton)
    if header:
        blocks.append(header)
    for scene in locked:
        heading = str(scene.get("heading") or "").strip()
        if not heading.startswith("#"):
            heading = "## %s" % heading
        body = _script_body(scene, written.get(scene.get("id")))
        blocks.append("%s\n%s" % (heading, body) if body else heading)
    return "\n\n".join(blocks).strip() + "\n"


def _locked_script_scenes(skeleton):
    from Agents.workers.script_skeleton import parse_script_scenes, _scene_id

    found = []
    pending = None
    for line in str(skeleton or "").splitlines():
        raw = line.strip()
        match = _SCRIPT_LIST_HEAD.match(raw)
        if match and ("｜" in match.group(1) or _SCENE_ID.search(match.group(1))):
            if pending:
                found.append(pending)
            heading = match.group(1).strip()
            heading = re.sub(r"^#{2,6}\s+", "", heading)
            pending = {
                "id": _scene_id(heading) or heading,
                "heading": heading,
                "cast": "",
            }
            continue
        if pending:
            cast = _SCRIPT_CAST.match(raw.lstrip())
            if cast:
                pending["cast"] = cast.group(1).strip()
    if pending:
        found.append(pending)
    if found:
        return found
    return [
        {"id": scene.get("id"), "heading": scene.get("heading"), "cast": scene.get("cast") or ""}
        for scene in parse_script_scenes(skeleton)
        if scene.get("heading")
    ]


def _script_lock_header(skeleton):
    lines = []
    started = False
    for line in str(skeleton or "").splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]?\s*(DURATION_TARGET|成片时长秒|成片时长|ASPECT_RATIO|成片画幅|画幅)\s*[:：=]", stripped):
            started = True
            lines.append(line.rstrip())
            continue
        if started and not stripped:
            break
        if started:
            if stripped.startswith("已确认结构") or stripped.startswith("- ") or stripped.startswith("填写槽"):
                break
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _script_body(locked, written):
    locked_cast = str((locked or {}).get("cast") or "").strip()
    if locked_cast == "【填写】":
        locked_cast = ""
    if not written:
        return "出场：%s" % (locked_cast or "【填写】")
    body = str(written.get("body") or "").strip()
    names = list(written.get("cast_names") or [])
    raw_cast = str(written.get("cast") or "").strip()
    if names or _filled(raw_cast):
        return body if body else "出场：%s" % (raw_cast or "、".join(names))
    if not body:
        return "出场：%s" % (locked_cast or "【填写】")
    if _SCRIPT_CAST.search(body):
        return body
    return "出场：%s\n%s" % (locked_cast or "【填写】", body)


_STORY_NOTE_FIELDS = ("动作", "景别机位", "画面层", "旁白", "声景")
_STORY_NOTE_LINE = re.compile(
    r"^(动作|景别机位|画面层|旁白|声景)\s*[:：]\s*(.*)$"
)
_STORY_PREFIX_LINE = re.compile(
    r"^(\S+)\s+(动作|景别机位|画面层|旁白|声景)\s*[:：]\s*(.*)$"
)


def _canonical_story_shot_id(name, shot_ids):
    raw = str(name or "").strip()
    if raw in shot_ids:
        return raw
    return ""


def _store_story_fill(fills, shot_id, name, value):
    if not shot_id or name not in _STORY_NOTE_FIELDS or not _filled(value):
        return
    fills.setdefault((shot_id, name), str(value).strip())


def _next_open_story_shot(shot_ids, start, already, fills, field):
    for index in range(start, len(shot_ids)):
        shot_id = shot_ids[index]
        if _filled(already.get((shot_id, field))) or _filled(fills.get((shot_id, field))):
            continue
        return index, shot_id
    return None, None


def _storyboard_already_filled(sections):
    already = {}
    for shot_id, body in sections:
        for name, value in _storyboard_field_map(body).items():
            if _filled(value):
                already[(shot_id, name)] = value
    return already


def _storyboard_note_fills(notes, shot_ids, already=None):
    """Map specialist or refill lines onto shot slots. First filled value wins."""
    fills = {}
    already = already or {}
    shot_ids = list(shot_ids or [])
    for note in notes or []:
        current = None
        explicit = False
        seq_index = 0
        block_fields = set()
        for line in str(note or "").splitlines():
            stripped = line.strip()
            heading = _STORY_HEAD.match(stripped)
            if heading:
                shot_id = _canonical_story_shot_id(heading.group(1).strip(), shot_ids)
                if shot_id:
                    current = shot_id
                    explicit = True
                    block_fields = set()
                    seq_index = shot_ids.index(shot_id) + 1
                else:
                    current = None
                    explicit = False
                    seq_index = 0
                    block_fields = set()
                continue
            prefixed = _STORY_PREFIX_LINE.match(stripped)
            if prefixed:
                shot_id = _canonical_story_shot_id(prefixed.group(1), shot_ids)
                if shot_id and _filled(prefixed.group(3)):
                    _store_story_fill(fills, shot_id, prefixed.group(2), prefixed.group(3))
                    seq_index = max(seq_index, shot_ids.index(shot_id) + 1)
                    current = None
                    explicit = False
                    block_fields = set()
                continue
            field = _STORY_NOTE_LINE.match(stripped)
            if not field or not _filled(field.group(2)):
                continue
            name = field.group(1)
            if not explicit:
                if current is None or name in block_fields:
                    index, shot_id = _next_open_story_shot(
                        shot_ids, seq_index, already, fills, name,
                    )
                    current = shot_id
                    block_fields = set()
                    if index is not None:
                        seq_index = index + 1
            if current:
                _store_story_fill(fills, current, name, field.group(2))
                block_fields.add(name)
    return fills


def _apply_storyboard_fills(skeleton, fills):
    sections = _storyboard_sections(skeleton)
    if not sections:
        return str(skeleton or "")
    blocks = []
    for shot_id, body in sections:
        current_fields = _storyboard_field_map(body)
        lines = list(str(body or "").splitlines()) or ["## %s" % shot_id]
        insert_at = 1 if lines and _STORY_HEAD.match(lines[0].strip()) else 0
        for name in _STORY_NOTE_FIELDS:
            key = (shot_id, name)
            if key not in fills or not _filled(fills[key]):
                continue
            if _filled(current_fields.get(name)):
                continue
            replaced = False
            for index, line in enumerate(lines):
                match = _STORY_SLOT_LINE.match(line.strip())
                if match and match.group(1) == name and not _filled(match.group(2)):
                    indent = line[:len(line) - len(line.lstrip())]
                    lines[index] = "%s%s：%s" % (indent, name, fills[key])
                    replaced = True
                    break
            if not replaced:
                lines.insert(insert_at, "%s：%s" % (name, fills[key]))
            insert_at += 1
            current_fields[name] = fills[key]
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(block for block in blocks if block) + "\n"


def _fold_storyboard(skeleton, notes):
    sections = _storyboard_sections(skeleton)
    if not sections:
        return ""
    shot_ids = [shot_id for shot_id, _body in sections]
    already = _storyboard_already_filled(sections)
    fills = _storyboard_note_fills(notes, shot_ids, already)
    return _apply_storyboard_fills(skeleton, fills)


def _storyboard_sections(skeleton):
    found = []
    current = None
    buf = []
    started = False
    for line in str(skeleton or "").splitlines():
        heading = _STORY_HEAD.match(line.strip())
        if heading:
            started = True
            if current is not None:
                found.append((current, "\n".join(buf)))
            current = heading.group(1).strip()
            buf = [line]
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        found.append((current, "\n".join(buf)))
    return found if started else []


_LABEL_LINE = re.compile(r"^(.+?)\s*[:：]\s*(.*)$")
LEFTOVER_LABEL_NOTE = "可能只是标签没对上"


def unmatched_specialist_partials(task, folded, partials):
    """Specialist bodies (or leftover lines) that were not stamped into the fold.

    Label mismatch is the expected case: the body is still useful, but the
    slot name did not match the skeleton.
    """
    del task  # the folded draft already reflects this task's slot names
    folded_lines = _folded_line_index(folded)
    leftovers = []
    for item in partials or []:
        skill = ""
        if isinstance(item, dict):
            skill = str(item.get("skill") or "")
        unused = _unused_note_text(_note_as_markdown(item), folded_lines)
        if unused:
            leftovers.append({"skill": skill, "text": unused})
    return leftovers


def fold_has_empty_slots(folded, task=""):
    """True when the folded skeleton still has an unfilled slot."""
    text = str(folded or "")
    if "【填写" in text:
        return True
    if str(task or "") == "feed" or _feed_units(text):
        return _feed_has_empty_slots(text)
    return False


_STORY_CONTRACT_FIELDS = ("动作", "景别机位")
_STORY_OPTIONAL_FILL = ("旁白",)
_STORY_SLOT_LINE = re.compile(
    r"^(动作|景别机位|时长|资产|对白|旁白|画面层|声景)\s*[:：]\s*(.*)$"
)
_REFILL_SYSTEM = (
    "只补还空着的槽。只返回漏掉的内容，不要重写已经盖上的栏，不要另写一份完整稿。"
    "填上去之后必须满足故事一致性、人物一致性、逻辑合理。"
)


def missing_contract_slots(task, folded):
    """Official slots the gate still needs after fold, as ``(unit, field)`` pairs.

    Storyboard expands a filled 画面层 into 动作/景别机位 before checking.
    Packing lists official Wan fields that are still empty.
    """
    task = str(task or "").strip()
    if task == "storyboard":
        return _storyboard_missing_slots(folded)
    if task == "feed":
        return _feed_missing_slots(folded)
    return []


def missing_slot_refill_system(task=""):
    """System prompt for a missing-slot refill, not a whole-draft merge."""
    del task
    return _REFILL_SYSTEM


def missing_slot_refill_prompt(task, folded, missing, leftovers=None):
    """Ask the model to return only the missing slot contents."""
    del task
    blocks = ["漏掉的槽："]
    for unit, field in missing or []:
        blocks.append("%s %s" % (unit, field))
    blocks.extend([
        "",
        "只返回漏掉的内容，不要重写已经盖上的栏。",
        "填上去之后必须满足故事一致性、人物一致性、逻辑合理。",
    ])
    leftover_blocks = []
    for item in leftovers or []:
        if isinstance(item, dict):
            body = str(item.get("text") or "").strip()
            skill = str(item.get("skill") or "")
        else:
            body = str(item or "").strip()
            skill = ""
        if not body:
            continue
        if skill:
            leftover_blocks.extend(["", "## %s" % skill, body])
        else:
            leftover_blocks.extend(["", body])
    if leftover_blocks:
        blocks.extend(["", "可参考的专员正文（可能只是标签没对上）："] + leftover_blocks)
    if str(folded or "").strip():
        blocks.extend(["", "已盖上的骨架：", str(folded).rstrip()])
    return "\n".join(blocks)


def apply_missing_slot_refill(task, folded, refill_raw):
    """Stamp refill values into empty contract slots; keep already-filled locks."""
    task = str(task or "").strip()
    if not str(folded or "").strip():
        return refill_raw
    refill_text = _draft_from_merge_raw(task, refill_raw)
    if task == "storyboard":
        draft = _stamp_storyboard_missing_slots(folded, refill_text)
        return folded_merge_output(task, draft)
    if task == "feed":
        draft = _fold_feed(folded, [refill_text])
        return folded_merge_output(task, draft or folded)
    return folded


def _storyboard_field_map(body):
    fields = {}
    for line in str(body or "").splitlines():
        match = _STORY_SLOT_LINE.match(line.strip())
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2).strip()
    layer = fields.get("画面层")
    if _filled(layer):
        from Agents.workers.storyboard_skeleton import _split_picture_layer
        action, camera = _split_picture_layer(layer)
        if action and not _filled(fields.get("动作")):
            fields["动作"] = action
        if camera and not _filled(fields.get("景别机位")):
            fields["景别机位"] = camera
    return fields


def _storyboard_missing_slots(folded):
    missing = []
    for shot_id, body in _storyboard_sections(folded):
        fields = _storyboard_field_map(body)
        for name in _STORY_CONTRACT_FIELDS:
            if not _filled(fields.get(name)):
                missing.append((shot_id, name))
        for name in _STORY_OPTIONAL_FILL:
            if name in fields and not _filled(fields.get(name)):
                missing.append((shot_id, name))
    return missing


def _feed_missing_slots(folded):
    missing = []
    for unit in _feed_units(folded):
        label = "单元%s" % unit["index"]
        for name, _header, body in unit["fields"]:
            if _wan_field_empty(name, body):
                missing.append((label, name))
    return missing


def _expand_picture_layer_fills(fills):
    from Agents.workers.storyboard_skeleton import _split_picture_layer

    for (shot_id, name), value in list(fills.items()):
        if name != "画面层" or not _filled(value):
            continue
        action, camera = _split_picture_layer(value)
        if action:
            fills.setdefault((shot_id, "动作"), action)
        if camera:
            fills.setdefault((shot_id, "景别机位"), camera)


def _stamp_storyboard_missing_slots(folded, refill_text):
    sections = _storyboard_sections(folded)
    if not sections:
        return str(folded or "")
    shot_ids = [shot_id for shot_id, _body in sections]
    already = _storyboard_already_filled(sections)
    fills = _storyboard_note_fills([refill_text], shot_ids, already)
    _expand_picture_layer_fills(fills)
    return _apply_storyboard_fills(folded, fills)


def leftover_merge_blocks(folded, leftovers):
    """User-facing note so merge maps unmatched bodies into empty slots."""
    blocks = [
        "已经按对上的标签盖进骨架。下面没盖上的专员正文可能只是标签没对上，"
        "请映射到还空着的槽，不要改已经盖上的锁栏。",
    ]
    if str(folded or "").strip():
        blocks.extend(["", "已盖上的骨架：", str(folded).rstrip()])
    if leftovers:
        blocks.extend(["", "没盖上的专员正文（可能只是标签没对上）："])
        for item in leftovers:
            skill = str(item.get("skill") or "")
            body = str(item.get("text") or "").strip()
            if not body:
                continue
            if skill:
                blocks.extend(["", "## %s" % skill, body])
            else:
                blocks.extend(["", body])
    return "\n".join(blocks)


def apply_merge_to_empty_slots(task, folded, merge_raw):
    """Keep already-stamped fold slots; copy merge fills only into empty ones."""
    task = str(task or "").strip()
    if not str(folded or "").strip():
        return merge_raw
    merge_text = _draft_from_merge_raw(task, merge_raw)
    if task == "storyboard":
        draft = _fill_empty_storyboard_slots(folded, merge_text)
        return folded_merge_output(task, draft)
    if task == "feed":
        if not _feed_units(merge_text):
            # Informal or JSON merge bodies stay intact for apply_packing_plan.
            return merge_raw
        draft = _fold_feed(folded, [merge_text])
        return folded_merge_output(task, draft)
    return merge_raw


def _folded_line_index(folded):
    found = set()
    for line in str(folded or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        found.add(raw)
        match = _LABEL_LINE.match(raw)
        if match:
            found.add("%s：%s" % (match.group(1).strip(), match.group(2).strip()))
    return found


def _unused_note_text(note, folded_lines):
    unused = []
    for line in str(note or "").splitlines():
        raw = line.strip()
        if not raw:
            if unused and unused[-1] != "":
                unused.append("")
            continue
        match = _LABEL_LINE.match(raw)
        normalized = "%s：%s" % (match.group(1).strip(), match.group(2).strip()) if match else raw
        if raw in folded_lines or normalized in folded_lines:
            continue
        unused.append(line.rstrip())
    return "\n".join(unused).strip()


def _draft_from_merge_raw(task, raw):
    text = str(raw or "").strip()
    data = _json_object(text)
    field = _FIELDS.get(task)
    if isinstance(data, dict):
        if field:
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                return value
        for key in ("storyboard", "feed", "script", "image_prompts", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return text


def _fill_empty_storyboard_slots(folded, merge_text):
    fills = {}
    current = None
    for line in str(merge_text or "").splitlines():
        heading = _STORY_HEAD.match(line.strip())
        if heading:
            current = heading.group(1).strip()
            continue
        if not current:
            continue
        field = _STORY_FILL.match(line.strip())
        if field and _filled(field.group(2)):
            fills[(current, field.group(1))] = field.group(2).strip()
    sections = _storyboard_sections(folded)
    if not sections:
        return str(folded or "")
    blocks = []
    for shot_id, body in sections:
        lines = []
        for line in str(body or "").splitlines():
            field = _STORY_FILL.match(line.strip())
            key = (shot_id, field.group(1)) if field else None
            if key and not _filled(field.group(2)) and key in fills:
                lines.append("%s：%s" % (field.group(1), fills[key]))
            else:
                lines.append(line)
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(block for block in blocks if block) + "\n"


_FEED_UNIT_HEAD = re.compile(
    r"^#{0,6}\s*【封装单元\s*(\d+)\s*/\s*(\d+)】(?:\s*·\s*段\S+)?\s*$"
)
_FEED_SKELETON_MARKS = (
    "万相 3.0 的封装骨架已经按",
    "当前骨架如下。",
)
_FEED_SKELETON_CUTS = (
    "\n各节点正文：",
    "\n对白和旁白必须逐字出现",
    "\n改完的封装稿放在",
    "\n上一阶段正文",
)


def _feed_skeleton_from_brief(brief):
    text = str(brief or "")
    start = -1
    for mark in _FEED_SKELETON_MARKS:
        start = text.find(mark)
        if start >= 0:
            break
    if start < 0:
        start = text.find("## 【封装单元")
        if start < 0:
            start = text.find("【封装单元")
    if start < 0:
        return ""
    rest = text[start:]
    for mark in _FEED_SKELETON_CUTS:
        cut = rest.find(mark)
        if cut >= 0:
            rest = rest[:cut]
    return _locked_feed_skeleton(rest)


def _locked_feed_skeleton(text):
    """Keep the first locked unit set; drop later packing-plan or draft copies."""
    raw = str(text or "")
    if not raw.strip():
        return ""
    units = _feed_units(raw)
    if not units:
        return raw.strip() + "\n"
    heading = _FEED_UNIT_HEAD.match((units[0].get("lines") or [""])[0].strip())
    total = int(heading.group(2)) if heading else 0
    kept = [units[0]]
    for unit in units[1:]:
        if total and len(kept) >= total:
            break
        if unit["index"] != kept[-1]["index"] + 1:
            break
        kept.append(unit)
    parts = []
    prefix = kept[0].get("prefix") or ""
    if prefix.strip():
        parts.append(prefix.rstrip())
    for unit in kept:
        parts.append("\n".join(unit["lines"]).rstrip())
    return "\n\n".join(part for part in parts if part) + "\n"


def _fold_feed(skeleton, notes):
    units = _feed_units(skeleton)
    if not units:
        return ""
    fills = _feed_fills(notes, default_unit=units[0]["index"] if len(units) == 1 else None)
    parts = []
    prefix = units[0].get("prefix") or ""
    if prefix.strip():
        parts.append(prefix.rstrip())
    for unit in units:
        parts.append(_render_feed_unit(unit, fills).rstrip())
    return "\n\n".join(part for part in parts if part) + "\n"


def _feed_has_empty_slots(text):
    for unit in _feed_units(text):
        for name, _header, body in unit["fields"]:
            if _wan_field_empty(name, body):
                return True
    return False


def _feed_units(text):
    raw = str(text or "")
    if not raw.strip():
        return []
    lines = raw.splitlines()
    starts = [index for index, line in enumerate(lines) if _FEED_UNIT_HEAD.match(line.strip())]
    if not starts:
        return []
    found = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
        heading = lines[start].strip()
        match = _FEED_UNIT_HEAD.match(heading)
        prefix = "\n".join(lines[:start]) if offset == 0 else ""
        body_lines = lines[start:end]
        found.append({
            "index": int(match.group(1)),
            "prefix": prefix,
            "lines": body_lines,
            "fields": _wan_field_list(body_lines),
        })
    return found


def _wan_field_list(lines):
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE, canonical_wan_name

    fields = []
    current = None
    header = ""
    body = []
    for line in lines:
        match = WAN_FIELD_LINE.match(line.strip())
        if match:
            if current is not None:
                fields.append((current, header, "\n".join(body).strip("\n")))
            current = canonical_wan_name(match.group("name"))
            header = line
            rest = match.group("rest") or ""
            body = [rest] if rest.strip() else []
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        fields.append((current, header, "\n".join(body).strip("\n")))
    return fields


def _wan_field_empty(name, body):
    from Agents.workers.feed.wan_format import WAN_NEGATIVE_LINE, WAN_SHOT_HEADING

    text = str(body or "").strip()
    if not text or text.startswith("【填写"):
        return True
    if name == "负向清单":
        return False
    if name == "分镜":
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            match = WAN_SHOT_HEADING.match(raw)
            if not match:
                return False
            if (match.group("tail") or "").strip():
                return False
        return True
    return False



def _foreign_field_header(line):
    """A labeled line that is not an official Wan field, so it is not part of the current fill."""
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE, WAN_SHOT_HEADING

    raw = str(line or "").strip()
    if not raw or WAN_FIELD_LINE.match(raw) or WAN_SHOT_HEADING.match(raw):
        return False
    match = _LABEL_LINE.match(raw)
    if not match:
        return False
    name = match.group(1).strip()
    if name.endswith("说") or name in ("画外音", "旁白", "VO"):
        return False
    if "，" in name or "。" in name or '"' in name or "“" in name or "”" in name:
        return False
    return 1 <= len(name) <= 24


def _is_dialogue_line(line):
    raw = str(line or "").strip()
    if not raw:
        return False
    match = _LABEL_LINE.match(raw)
    if not match:
        return False
    name = match.group(1).strip()
    return name.endswith("说") or name in ("画外音", "旁白", "VO")


def _split_leaked_shots_from_dialogue(text):
    """Shot headings mixed into 台词, plus the dialogue lines around them."""
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE, WAN_SHOT_HEADING, canonical_wan_name

    dialogue = []
    shots = []
    current = None

    def flush():
        nonlocal current
        if current is None:
            return
        picture = []
        for line in current.pop("body_lines"):
            if _is_dialogue_line(line):
                dialogue.append(line.strip())
                current["ended"] = True
                continue
            if current.get("ended"):
                if str(line or "").strip():
                    dialogue.append(line.strip())
                continue
            picture.append(line)
        current["body"] = "\n".join(picture).strip("\n").strip()
        shots.append(current)
        current = None

    for line in str(text or "").splitlines():
        raw = line.strip()
        field = WAN_FIELD_LINE.match(raw)
        if field:
            name = canonical_wan_name(field.group("name"))
            if name == "台词":
                flush()
                rest = (field.group("rest") or "").strip()
                if rest:
                    dialogue.append(rest)
                continue
            if name and name != "分镜":
                flush()
                continue
        match = WAN_SHOT_HEADING.match(raw)
        if match:
            flush()
            tail = (match.group("tail") or "").strip()
            current = {
                "num": int(match.group("num")),
                "start": match.group("start"),
                "end": match.group("end"),
                "body_lines": [tail] if tail else [],
            }
            continue
        if current is not None:
            current["body_lines"].append(line)
        elif raw:
            dialogue.append(raw)
    flush()
    bodies = {}
    for shot in shots:
        body = str(shot.get("body") or "").strip()
        if shot["num"] not in bodies:
            bodies[shot["num"]] = shot
        elif body and not str(bodies[shot["num"]].get("body") or "").strip():
            bodies[shot["num"]] = shot
    return bodies, "\n".join(dialogue).strip()


def _render_shot_fill(shots):
    lines = []
    for number in sorted(shots):
        shot = shots[number]
        lines.append("分镜%s（%s-%s）：" % (number, shot["start"], shot["end"]))
        body = str(shot.get("body") or "").strip()
        if body:
            lines.append(body)
    return "\n".join(lines)


def _feed_fills(notes, default_unit=None):
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE, canonical_wan_name

    fills = {}
    shot_fills = {}
    dialogue_parts = {}

    def keep_shot(unit, shot):
        if unit is None or not shot:
            return
        body = str(shot.get("body") or "").strip()
        current = shot_fills.get((unit, shot["num"]))
        if current is None:
            shot_fills[(unit, shot["num"])] = shot
            return
        if not str(current.get("body") or "").strip() and body:
            shot_fills[(unit, shot["num"])] = shot

    def store(unit, field, chunks):
        if unit is None or not field:
            return
        text = "\n".join(chunks).strip("\n").strip()
        if field == "分镜":
            leading, shots = _parse_wan_shot_blocks(text)
            if shots:
                for shot in shots:
                    keep_shot(unit, shot)
                return
            if text and not _wan_field_empty(field, text):
                fills.setdefault((unit, field), text)
            return
        if field == "台词":
            leaked, dialogue = _split_leaked_shots_from_dialogue(text)
            for shot in leaked.values():
                keep_shot(unit, shot)
            if dialogue:
                parts = dialogue_parts.setdefault(unit, [])
                if dialogue not in parts:
                    parts.append(dialogue)
            return
        if text and not _wan_field_empty(field, text):
            fills.setdefault((unit, field), text)

    for note in notes or []:
        current_unit = default_unit
        current_field = None
        body = []
        for line in str(note or "").splitlines():
            heading = _FEED_UNIT_HEAD.match(line.strip())
            if heading:
                store(current_unit, current_field, body)
                current_unit = int(heading.group(1))
                current_field = None
                body = []
                continue
            match = WAN_FIELD_LINE.match(line.strip())
            if match:
                store(current_unit, current_field, body)
                current_field = canonical_wan_name(match.group("name"))
                rest = match.group("rest") or ""
                body = [rest] if rest.strip() else []
                if current_unit is None:
                    current_unit = default_unit
                continue
            if current_field is not None and _foreign_field_header(line):
                store(current_unit, current_field, body)
                current_field = None
                body = []
                continue
            if current_field is not None:
                body.append(line)
        store(current_unit, current_field, body)
    units = sorted({unit for unit, _num in shot_fills} | set(dialogue_parts))
    for unit in units:
        shots = {
            num: shot
            for (shot_unit, num), shot in shot_fills.items()
            if shot_unit == unit
        }
        if shots:
            rendered = _render_shot_fill(shots)
            existing = fills.get((unit, "分镜"))
            if existing and not _parse_wan_shot_blocks(existing)[1]:
                fills[(unit, "分镜")] = existing
            else:
                fills[(unit, "分镜")] = rendered
        dialogue = "\n".join(dialogue_parts.get(unit) or []).strip()
        if dialogue:
            fills[(unit, "台词")] = dialogue
    return fills


def _parse_wan_shot_blocks(text):
    """Leading prose plus official 分镜 headings, each with the body under it."""
    from Agents.workers.feed.wan_format import WAN_SHOT_HEADING

    leading = []
    shots = []
    current = None

    def flush():
        nonlocal current
        if current is None:
            return
        current["body"] = "\n".join(current.pop("body_lines")).strip("\n").strip()
        shots.append(current)
        current = None

    for line in str(text or "").splitlines():
        match = WAN_SHOT_HEADING.match(line.strip())
        if match:
            flush()
            tail = (match.group("tail") or "").strip()
            current = {
                "num": int(match.group("num")),
                "start": match.group("start"),
                "end": match.group("end"),
                "body_lines": [tail] if tail else [],
            }
            continue
        if current is None:
            leading.append(line)
        else:
            current["body_lines"].append(line)
    flush()
    return "\n".join(leading).strip(), shots


def _locked_wan_shots(body):
    """First occurrence of each locked 分镜 number, with its start-end times."""
    locked = []
    seen = set()
    for shot in _parse_wan_shot_blocks(body)[1]:
        if shot["num"] in seen:
            continue
        seen.add(shot["num"])
        locked.append((shot["num"], shot["start"], shot["end"]))
    return locked


def _first_nonempty_shot_bodies(text):
    leading, shots = _parse_wan_shot_blocks(text)
    bodies = {}
    for shot in shots:
        body = str(shot.get("body") or "").strip()
        if shot["num"] not in bodies:
            bodies[shot["num"]] = body
        elif not str(bodies[shot["num"]] or "").strip() and body:
            bodies[shot["num"]] = body
    return leading, bodies


def _render_locked_shot_field(locked, bodies):
    lines = []
    for number, start, end in locked:
        lines.append("分镜%s（%s-%s）：" % (number, start, end))
        body = str(bodies.get(number) or "").strip()
        if body:
            lines.append(body)
    return "\n".join(lines)


def _normalize_wan_shot_field(skeleton_body, current_body=None, fill=None, apply_fill=False):
    """Stamp or keep bodies on locked headings; drop trailing empty copies."""
    locked = _locked_wan_shots(skeleton_body)
    source = current_body if current_body is not None else skeleton_body
    if not locked:
        if apply_fill:
            return str(skeleton_body or "").strip("\n")
        return str(source or "").strip("\n")
    _leading, bodies = _first_nonempty_shot_bodies(source)
    kept = {
        number: body
        for number, body in bodies.items()
        if str(body or "").strip() and any(number == item[0] for item in locked)
    }
    if apply_fill and fill:
        fill_leading, fill_bodies = _first_nonempty_shot_bodies(fill)
        fill_has_headings = bool(_parse_wan_shot_blocks(fill)[1])
        for number, _start, _end in locked:
            if str(kept.get(number) or "").strip():
                continue
            if fill_has_headings:
                body = str(fill_bodies.get(number) or "").strip()
                if body:
                    kept[number] = body
            elif len(locked) == 1:
                prose = str(fill_leading or fill or "").strip()
                if prose:
                    kept[number] = prose
    return _render_locked_shot_field(locked, kept)


def _merge_shot_sources(*texts):
    """First nonempty body wins for each shot number; heading-less prose is kept if no shots."""
    shots = {}
    prose = ""
    for text in texts:
        if not str(text or "").strip():
            continue
        leaked, _dialogue = _split_leaked_shots_from_dialogue(text)
        if leaked:
            for number, shot in leaked.items():
                body = str(shot.get("body") or "").strip()
                current = shots.get(number)
                if current is None:
                    shots[number] = shot
                elif body and not str(current.get("body") or "").strip():
                    shots[number] = shot
            continue
        leading, parsed = _parse_wan_shot_blocks(text)
        if parsed:
            for shot in parsed:
                body = str(shot.get("body") or "").strip()
                current = shots.get(shot["num"])
                if current is None:
                    shots[shot["num"]] = shot
                elif body and not str(current.get("body") or "").strip():
                    shots[shot["num"]] = shot
        elif not prose:
            prose = str(leading or text or "").strip()
    if shots:
        return _render_shot_fill(shots)
    return prose


def _render_feed_unit(unit, fills):
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE, canonical_wan_name, WAN_NEGATIVE_LINE

    lines = list(unit["lines"])
    skip_body_of = set()
    out = []
    current = None
    for line in lines:
        match = WAN_FIELD_LINE.match(line.strip())
        if match:
            current = canonical_wan_name(match.group("name"))
            body = _field_body_from_unit(unit, current)
            fill = fills.get((unit["index"], current))
            if current == "分镜":
                leaked, _dialogue = _split_leaked_shots_from_dialogue(_field_body_from_unit(unit, "台词"))
                recovered = _render_shot_fill(leaked) if leaked else ""
                combined = _merge_shot_sources(fill, recovered)
                apply_fill = bool(combined)
                normalized = _normalize_wan_shot_field(body, body, combined, apply_fill)
                out.append(_with_field_body(_wan_header_only(line), normalized) if normalized else _wan_header_only(line))
                skip_body_of.add(current)
                continue
            if current == "台词":
                leaked, cleaned = _split_leaked_shots_from_dialogue(body)
                if leaked:
                    out.append(_with_field_body(_wan_header_only(line), cleaned) if cleaned else _wan_header_only(line))
                    skip_body_of.add(current)
                    continue
            if fill and _wan_field_empty(current, body):
                out.append(_with_field_body(line, fill))
                skip_body_of.add(current)
                continue
            out.append(line)
            continue
        if current in skip_body_of:
            # Locked 分镜 headings are already rendered onto the field header.
            raw = line.strip()
            if current == "负向清单" and raw == WAN_NEGATIVE_LINE:
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _wan_header_only(header_line):
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE

    raw = str(header_line or "")
    match = WAN_FIELD_LINE.match(raw.strip())
    if not match or not (match.group("rest") or "").strip():
        return raw
    indent = raw[:len(raw) - len(raw.lstrip())]
    punct = "：" if "：" in raw.strip().split(match.group("name"), 1)[-1][:2] or "：" in raw else ":"
    return "%s%s%s" % (indent, match.group("name"), punct)


def _field_body_from_unit(unit, name):
    for field, _header, body in unit["fields"]:
        if field == name:
            return body
    return ""


def _with_field_body(header_line, fill):
    from Agents.workers.feed.wan_format import WAN_FIELD_LINE

    match = WAN_FIELD_LINE.match(header_line.strip())
    rest = (match.group("rest") or "").strip() if match else ""
    if rest:
        indent = header_line[:len(header_line) - len(header_line.lstrip())]
        raw = header_line.strip()
        punct = "：" if "：" in raw.split(match.group("name"), 1)[-1][:2] or "：" in raw else ":"
        written = raw.split("：", 1)[0] if "：" in raw else raw.split(":", 1)[0]
        return "%s%s%s%s" % (indent, written, punct, fill)
    return header_line.rstrip() + "\n" + fill
