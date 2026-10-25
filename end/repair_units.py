"""Split a machine-check report into one unit per failed item, then splice replies back.

Passing blocks stay in the saved draft. They are not placed in the repair brief.
"""

import json
import os
import re
import tempfile

_ASSET_HEAD = re.compile(r"^(#{2,6})\s*\*{0,2}\s*图\s*(\d+)\b", re.M)
_FIGURE_SUMMARY = re.compile(
    r"^\[([+x])\]\s*图\s*(\d+)\s+\S+\s+(PASS|FAIL)\s+(.*?)\s*$"
)
_BOARD_SEG = re.compile(r"^#{1,6}\s*\**\s*段\s*(\d+)\b[^\n]*", re.M)
_FEED_HEAD = re.compile(
    r"^#{1,6}[^\n]{0,60}?段\s*([0-9]+)\s*(?:[/／]\s*([0-9]+))?[^\n]*$",
    re.M,
)
_FEED_BRACKET = re.compile(
    r"^[ \t]*【?[ \t]*段[ \t]*([0-9]+)[ \t]*(?:[/／][ \t]*([0-9]+))?[ \t]*】?[ \t]*$",
    re.M,
)
_SEGMENT_NO = re.compile(r"段\s*(\d+)")
_FIGURE_NO = re.compile(r"图\s*(\d+)(?!\d)")
_HEADING = re.compile(r"^#{1,6}\s+.+$", re.M)

REPAIR_TASK = "任务：建议修补"


def asset_pieces(text):
    text = str(text or "")
    matches = list(_ASSET_HEAD.finditer(text))
    pieces = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pieces.append((int(match.group(2)), text[match.start():end].strip()))
    return pieces


def failed_asset_targets(report, pieces):
    """Indexes of 图 blocks the checker marked FAIL, in file order."""
    rows = []
    for line in str(report or "").splitlines():
        match = _FIGURE_SUMMARY.match(line.strip())
        if match:
            rows.append((match.group(1) == "x", int(match.group(2)), match.group(4).strip()))
    if not rows or len(rows) != len(pieces):
        return []
    if any(pieces[index][0] != number for index, (_failed, number, _title) in enumerate(rows)):
        return []
    targets = []
    for index, (failed, number, title) in enumerate(rows):
        if failed:
            targets.append((index, number, title, pieces[index][1]))
    return targets


def repair_brief(stage, units):
    payload = json.dumps(units, ensure_ascii=False, indent=2)
    return "%s\n阶段：%s\n失败项：\n%s\n" % (REPAIR_TASK, stage, payload)


def units_for(stage, report, asset="", previs="", feed=""):
    """One dict per failed item. Empty when this report names nothing we can edit."""
    stage = str(stage or "")
    if stage == "asset":
        return _asset_units(report, asset)
    if stage == "storyboard":
        return _segment_units("段", report, previs, whole_when_empty=True)
    if stage == "feed":
        return _feed_units(report, feed)
    return []


def apply_repairs(stage, units, repairs, asset="", previs="", storyboard="", feed=""):
    """Return the stage fields with only the named units replaced."""
    found = {}
    for item in repairs or []:
        if isinstance(item, dict) and item.get("id"):
            found[str(item.get("id"))] = str(item.get("text") or "")
    if stage == "asset":
        return _apply_asset(units, found, asset)
    if stage == "storyboard":
        previs_text = _apply_segments(previs, units, found, _BOARD_SEG)
        previs_text = _apply_loose(previs_text, previs, units, found, "previs")
        previs_text = _append_snippet(
            previs_text,
            _pieces(previs, _BOARD_SEG),
            found.get("previs-rest") or "",
        )
        return {"storyboard": str(storyboard or ""), "previs": previs_text}
    if stage == "feed":
        return {"feed": _apply_feed(feed, units, found)}
    return {}


def _asset_units(report, text):
    pieces = asset_pieces(text)
    targets = failed_asset_targets(report, pieces)
    if not targets:
        targets = _asset_fallback(report, pieces)
    units = []
    for index, number, title, body in targets:
        label = "图%d" % number
        if title:
            label = "%s · %s" % (label, title[:24])
        units.append(_unit(
            "图-%s" % index,
            label,
            "\n".join(_block_fail_lines(body)),
            body,
            index=index,
            number=number,
        ))
    return units


def _asset_fallback(report, pieces):
    """Use a 图 number when the report has no aligned [x] rows and that number is unique."""
    numbers = []
    for line in _fail_lines(report):
        match = _FIGURE_NO.search(line)
        if match:
            number = int(match.group(1))
            if number not in numbers:
                numbers.append(number)
    targets = []
    for number in numbers:
        matches = [(index, body) for index, (piece_number, body) in enumerate(pieces) if piece_number == number]
        if len(matches) != 1:
            continue
        index, body = matches[0]
        heading = body.splitlines()[0] if body else ""
        title = heading.split("·", 1)[-1].strip() if "·" in heading else ""
        targets.append((index, number, title, body))
    return targets


def prepare_asset_repair(text, units):
    """Insert missing checker phrases, then keep only blocks that still fail."""
    patched, indexes = fill_failed_blocks(text, units)
    refreshed = units_for("asset", _check_asset_text(patched), asset=patched)
    return patched, indexes, refreshed


def fill_failed_blocks(text, units):
    """Return the asset text with mechanical gaps filled on the named blocks only."""
    pieces = asset_pieces(text)
    bodies = [body for _number, body in pieces]
    indexes = []
    for unit in units or []:
        index = unit.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(bodies):
            continue
        filled = fill_block_gaps(bodies[index])
        if filled != bodies[index]:
            bodies[index] = filled.strip()
            indexes.append(index)
    if not indexes:
        return str(text or ""), []
    joined = "\n\n".join(body.strip() for body in bodies if body.strip())
    if joined:
        joined += "\n"
    return joined, indexes


def fill_block_gaps(body):
    """Append the phrases this block's own FAIL lines name. Other blocks stay untouched."""
    text = str(body or "")
    if not text.strip():
        return text
    extra = _gap_lines(text, "\n".join(_block_fail_lines(text)))
    if not extra:
        return text
    return text.rstrip() + "\n" + "\n".join(extra) + "\n"


_MISSING_MARK = ("缺", "未写明", "未声明", "未显式声明", "未禁")
_QUOTE_TOKEN = {"防第二张脸": "different face"}


MODE_MARK = "缺【参考场景锁定模式】正文"
MODE_OPTIONS = ("仅允许摄影机移动", "允许按子区域切换机位")
MODEL_OPTION = "交给模型改这一块"


def _gap_lines(text, blob):
    """Phrases named by this block's own missing-field lines.

    A1/A5/A7 lines are not inserts. The two lock-mode sentences are mutually
    exclusive, so they are left for the user to choose.
    """
    extra = []
    for line in str(blob or "").splitlines():
        if not any(mark in line for mark in _MISSING_MARK):
            continue
        if "块类型无法判定" in line:
            continue
        for phrase in re.findall(r"「([^」]+)」", line):
            _add_phrase(text, extra, _QUOTE_TOKEN.get(phrase, phrase))
        if "缺模板字段" in line:
            for field in re.findall(r"(【[^】]+】)", line):
                if not re.search(re.escape(field[:-1]) + r"[^】]*】", text):
                    _add_phrase(text, extra, field)
        named = re.search(r"缺：([^（\n]+)", line)
        if named:
            compact = text.replace(" ", "")
            for name in named.group(1).split("、"):
                name = name.strip()
                if name and name not in text and name.replace(" ", "") not in compact:
                    _add_phrase(text, extra, name)
        if "缺图底声纹锚" in line and "声纹" not in text:
            _add_phrase(text, extra, "声纹")
        if "fifth panel" in line.lower() and "fifth panel" not in text.lower():
            _add_phrase(text, extra, "fifth panel")
        if "缺标注降级条款" in line and (not re.search(r"1\s*[~～-]\s*9", text) or "对照表" not in text):
            _add_phrase(text, extra, "格内编号 1~9，图外对照表")
        if "未禁手持" in line and not re.search(r"禁止手持|不得手持|不出现手", text):
            _add_phrase(text, extra, "禁止手持")
    return extra


def _add_phrase(text, extra, phrase):
    if not phrase or phrase in extra or _phrase_present(text, phrase):
        return
    extra.append(phrase)


def _phrase_present(text, phrase):
    if phrase == "16:9":
        return bool(re.search(r"16\s*[:：]\s*9", text))
    if phrase == "全图共 9 格":
        return bool(re.search(r"全图共\s*9\s*格", text))
    if phrase == "全图共 4 格":
        return bool(re.search(r"全图共\s*4\s*格", text))
    if phrase == "五官 10 项锁定":
        return "五官 10 项锁定" in text or "五官10项锁定" in text
    if phrase == "≤8 词音色速览标签":
        return bool(re.search(r"≤\s*8\s*词|8\s*词", text))
    if phrase == "different face":
        return any(
            token in text.lower()
            for token in ("different face", "second face", "second recognizable face", "another face")
        )
    return phrase in text


def _block_fail_lines(body):
    report = _check_asset_text(body)
    return [line.strip() for line in report.splitlines() if "FAIL:" in line]


def _block_reason_lines(body):
    """Every FAIL row from this block alone, including its [x] summary."""
    return [line.strip() for line in _check_asset_text(body).splitlines() if "FAIL" in line.strip()]


def direction_labels(body, lines=None):
    """Buttons for one failed block. Lock-mode sentences stay as themselves."""
    text = str(body or "")
    rows = list(lines if lines is not None else _block_reason_lines(text))
    blob = "\n".join(rows)
    labels = []
    if MODE_MARK in blob and not any(_phrase_present(text, phrase) for phrase in MODE_OPTIONS):
        labels.extend(MODE_OPTIONS)
    for phrase in _gap_lines(text, blob):
        label = "补上「%s」" % phrase
        if label not in labels:
            labels.append(label)
    labels.append(MODEL_OPTION)
    return labels


def block_question(unit):
    """One single-choice question. The reply names only this block's FAIL rows."""
    body = str((unit or {}).get("body") or "")
    lines = _block_reason_lines(body)
    label = str((unit or {}).get("label") or "这一块")
    reply = "%s\n不满足：\n%s\n选一个改法。" % (label, "\n".join(lines))
    return {
        "id": "asset_repair_choice",
        "kind": "single",
        "reply": reply,
        "options": direction_labels(body, lines),
        "cards": [],
        "unit_id": str((unit or {}).get("id") or ""),
    }


def next_asset_question(text, choices):
    """The first failed block that does not yet have a recorded choice."""
    chosen = {str(key) for key in dict(choices or {})}
    for unit in units_for("asset", _check_asset_text(text), asset=text):
        if str(unit.get("id") or "") not in chosen:
            return block_question(unit)
    return None


def reopen_asset_question(text, choices):
    """Drop choices on blocks that still fail, then ask the first of those blocks."""
    kept = {str(key): value for key, value in dict(choices or {}).items()}
    pending = units_for("asset", _check_asset_text(text), asset=text)
    for unit in pending:
        kept.pop(str(unit.get("id") or ""), None)
    if not pending:
        return kept, None
    return kept, block_question(pending[0])


def choice_action(label):
    """Map a button label to a phrase insert or a one-block model repair."""
    text = " ".join(str(label or "").split())
    if text == MODEL_OPTION:
        return {"model": True, "phrase": ""}
    if text in MODE_OPTIONS:
        return {"model": False, "phrase": text}
    prefix = "补上「"
    if text.startswith(prefix) and text.endswith("」"):
        phrase = text[len(prefix):-1]
        if phrase:
            return {"model": False, "phrase": phrase}
    return None


def apply_repair_choices(text, units, choices):
    """Apply chosen phrases locally. Return only the blocks the user handed to the model."""
    pieces = asset_pieces(text)
    bodies = [body for _number, body in pieces]
    indexes = []
    model_units = []
    chosen = {str(key): value for key, value in dict(choices or {}).items()}
    for unit in units or []:
        choice = chosen.get(str(unit.get("id") or ""))
        if not isinstance(choice, dict):
            continue
        index = unit.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(bodies):
            continue
        if choice.get("model"):
            fresh = dict(unit)
            fresh["body"] = bodies[index]
            fresh["suggestion"] = "\n".join(_block_fail_lines(bodies[index]))
            model_units.append(fresh)
            continue
        phrase = str(choice.get("phrase") or "")
        if not phrase or _phrase_present(bodies[index], phrase):
            continue
        bodies[index] = bodies[index].rstrip() + "\n" + phrase
        indexes.append(index)
    if not indexes:
        return str(text or ""), [], model_units
    joined = "\n\n".join(body.strip() for body in bodies if body.strip())
    if joined:
        joined += "\n"
    return joined, indexes, model_units


def _check_asset_text(text):
    from end.gates import check_asset_blocks

    handle = tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False)
    name = handle.name
    try:
        body = str(text or "")
        handle.write(body if body.endswith("\n") else body + "\n")
        handle.close()
        _code, report = check_asset_blocks(name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    return report or ""


def _segment_units(prefix, report, text, whole_when_empty):
    pieces = _pieces(text, _BOARD_SEG)
    grouped, loose = _group_fail_lines(report)
    units = []
    for number in sorted(grouped):
        body = _piece_body(pieces, number)
        if not body and not whole_when_empty:
            continue
        if not body:
            body = "## 段 %d" % number
        units.append(_unit(
            "%s-%s" % (prefix, number),
            "段%d" % number,
            "\n".join(grouped[number]),
            body,
            number=number,
        ))
    if loose and not pieces and whole_when_empty:
        units.append(_unit("previs", "预演", "\n".join(loose), str(text or ""), number=0))
    elif loose and pieces:
        units.append(_unit("previs-rest", "未点名的段", "\n".join(loose), "", number=0))
    return units


def _feed_units(report, text):
    pieces = _feed_pieces(text)
    grouped, loose = _group_fail_lines(report)
    units = []
    for number in sorted(grouped):
        body = _piece_body(pieces, number)
        if not body:
            continue
        units.append(_unit(
            "段-%s" % number,
            "段%d" % number,
            "\n".join(grouped[number]),
            body,
            number=number,
        ))
    if loose:
        section = _named_sections(text, loose) if pieces else str(text or "")
        units.append(_unit("feed-rest", "未点名的失败项", "\n".join(loose), section, number=0))
    if not units and _fail_lines(report) and str(text or "").strip() and not pieces:
        units.append(_unit("feed-rest", "投喂稿", "\n".join(_fail_lines(report)), text, number=0))
    return units


def _apply_asset(units, found, text):
    from end.stage_runner import _patch_asset_block

    pieces = asset_pieces(text)
    bodies = [body for _number, body in pieces]
    indexes = []
    for unit in units:
        index = unit.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(bodies):
            continue
        indexes.append(index)
        fresh = found.get(unit["id"], "")
        replacement = _replacement_block(fresh, int(unit.get("number") or 0), unit.get("body") or "")
        if replacement and replacement.strip() != str(unit.get("body") or "").strip():
            bodies[index] = _patch_asset_block(replacement).strip()
    joined = "\n\n".join(body.strip() for body in bodies if body.strip())
    if joined:
        joined += "\n"
    return {
        "image_prompts": joined or str(text or ""),
        "_figure_repair": True,
        "_figure_indexes": indexes,
    }


def _apply_segments(text, units, found, pattern):
    replacements = {}
    for unit in units:
        number = int(unit.get("number") or 0)
        if number <= 0:
            continue
        fresh = str(found.get(unit["id"]) or "").strip()
        if not fresh:
            continue
        replacements[number] = _force_segment(fresh, number, unit.get("body") or "")
    if not replacements:
        return str(text or "")
    return _splice(text, pattern, replacements)


def _apply_feed(text, units, found):
    pieces = _feed_pieces(text)
    if not pieces:
        return _replace_whole(text, found.get("feed-rest") or "")
    pattern = _FEED_HEAD if _marks(text, _FEED_HEAD) >= 2 else _FEED_BRACKET
    replaced = _apply_segments(text, units, found, pattern)
    return _append_snippet(replaced, pieces, found.get("feed-rest") or "")


def _apply_loose(replaced, original, units, found, loose_id):
    if not any(unit.get("id") == loose_id for unit in units):
        return replaced
    if _pieces(original, _BOARD_SEG):
        return _append_snippet(replaced, _pieces(original, _BOARD_SEG), found.get(loose_id) or "")
    return _replace_whole(original, found.get(loose_id) or "")


def _replace_whole(original, fresh):
    fresh = str(fresh or "").strip()
    if not fresh:
        return str(original or "")
    return fresh if fresh.endswith("\n") else fresh + "\n"


def _append_snippet(text, pieces, fresh):
    snippet = str(fresh or "").strip()
    if not snippet:
        return text
    for _number, body in pieces:
        head = body.splitlines()[0] if body else ""
        if head and head in snippet:
            return text
    base = str(text or "").rstrip()
    if not base:
        return snippet + "\n"
    return base + "\n\n" + snippet + "\n"


def _unit(unit_id, label, suggestion, body, index=None, number=0):
    item = {
        "id": unit_id,
        "label": label,
        "suggestion": _quote(suggestion),
        "body": _quote(body),
        "number": number,
    }
    if index is not None:
        item["index"] = index
    return item


def _quote(body):
    return str(body or "").replace("任务：", "任务 ")


def _fail_lines(report):
    lines = []
    for raw in str(report or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[+]") or line.startswith("[i]"):
            continue
        if "FAIL" in line or line.startswith("[x]"):
            lines.append(line)
    return lines


def _group_fail_lines(report):
    grouped = {}
    loose = []
    for line in _fail_lines(report):
        match = _SEGMENT_NO.search(line)
        if not match:
            loose.append(line)
            continue
        number = int(match.group(1))
        grouped.setdefault(number, []).append(line)
    return grouped, loose


def _pieces(text, pattern):
    text = str(text or "")
    matches = list(pattern.finditer(text))
    pieces = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pieces.append((int(match.group(1)), text[match.start():end].strip()))
    return pieces


def _feed_pieces(text):
    if _marks(text, _FEED_HEAD) >= 2:
        return _pieces(text, _FEED_HEAD)
    if _marks(text, _FEED_BRACKET) >= 2:
        return _pieces(text, _FEED_BRACKET)
    return []


def _marks(text, pattern):
    return len(list(pattern.finditer(str(text or ""))))


def _piece_body(pieces, number):
    for piece_number, body in pieces:
        if piece_number == number:
            return body
    return ""


def _named_sections(text, lines):
    text = str(text or "")
    heads = list(_HEADING.finditer(text))
    if not heads:
        return ""
    wanted = []
    blob = "\n".join(lines)
    for match in heads:
        title = match.group(0).strip()
        if title and title in blob:
            wanted.append(match.start())
    if not wanted:
        return ""
    chunks = []
    for start in wanted:
        end = len(text)
        for match in heads:
            if match.start() > start:
                end = match.start()
                break
        chunks.append(text[start:end].strip())
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _replacement_block(fresh, number, original):
    parts = asset_pieces(fresh)
    same = [(item_number, body) for item_number, body in parts if item_number == number]
    if not same and len(parts) == 1:
        same = parts
    if not same:
        text = str(fresh or "").strip()
        if not text:
            return ""
        return _force_figure_heading(text, number, original)
    title = ""
    heading = str(original or "").splitlines()
    if heading and "·" in heading[0]:
        title = heading[0].split("·", 1)[1].strip()
    chosen = same[-1][1]
    if title:
        titled = [body for _item_number, body in same if title in body.splitlines()[0]]
        if titled:
            chosen = titled[-1]
    return _force_figure_heading(chosen, number, original)


def _force_figure_heading(body, number, original):
    lines = str(body or "").strip().splitlines()
    if not lines:
        return ""
    if re.match(r"^#{2,6}\s*\*{0,2}\s*图\s*%d\b" % number, lines[0]):
        return "\n".join(lines).strip()
    head = str(original or "").splitlines()
    head = head[0] if head else "#### 图%d" % number
    rest = lines[1:] if lines[0].lstrip().startswith("#") else lines
    return (head + "\n" + "\n".join(rest)).strip()


def _force_segment(body, number, original):
    lines = str(body or "").strip().splitlines()
    if not lines:
        return ""
    if re.match(r"^#{1,6}\s*\**\s*段\s*%d\b" % number, lines[0]) or re.match(
        r"^段\s*%d\b" % number, lines[0]
    ):
        return "\n".join(lines).strip()
    head = str(original or "").splitlines()
    head = head[0] if head else "## 段 %d" % number
    rest = lines[1:] if lines[0].lstrip().startswith("#") else lines
    return (head + "\n" + "\n".join(rest)).strip()


def _splice(text, pattern, replacements):
    text = str(text or "")
    matches = list(pattern.finditer(text))
    if not matches:
        return text
    parts = []
    cursor = 0
    seen = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = int(match.group(1))
        parts.append(text[cursor:match.start()])
        if number in replacements and number not in seen:
            parts.append(replacements[number].strip() + "\n\n")
            seen.add(number)
        else:
            parts.append(text[match.start():end])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)
