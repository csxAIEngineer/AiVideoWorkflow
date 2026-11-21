"""Program-owned storyboard skeleton: shot ids and durations locked before the model writes."""

import re

from Agents.workers.asset_skeleton import _place_name, _PROP_LINE, _SKIP_PROP, _split_cast_names, extract_asset_catalog
from Agents.workers.script_skeleton import parse_script_scenes
from end.locks import pack_limit

_BEAT = re.compile(r"^▲\s*(.+)$")
_CAST_LINE = re.compile(r"^出场\s*[:：]\s*(.+)$")
_SOUND_LINE = re.compile(r"^【音效】\s*(.+)$")
_NARRATION = re.compile(r"^(?:旁白(?:（VO）|\(VO\))?|VO)\s*[:：]\s*(.+)$")
_DIALOGUE = re.compile(r"^(.+?)\s*[:：]\s*(.+)$")
_MUSIC = re.compile(r"(背景乐|背景音乐|配乐|BGM|音乐)")
_SKIP_DIALOGUE = (
    "出场", "道具", "动作", "景别机位", "时长", "资产", "站位", "朝向", "光线",
    "对白", "旁白", "音效", "种类", "格位",
)


def storyboard_skeleton(state):
    """Build the shot skeleton from the official script, assets, and duration lock."""
    state = state or {}
    script = str(state.get("source") or state.get("script") or "").strip()
    locks = dict(state.get("locks") or {})
    assets = list(state.get("assets") or state.get("asset_list") or [])
    if not assets:
        assets = extract_asset_catalog(script)
    duration_lock = _duration_lock(locks)
    scenes = _scene_units(script)
    shots = allocate_shot_durations(
        scenes,
        duration_lock,
        max_shot_seconds=_max_shot_seconds(locks),
    )
    for shot in shots:
        shot["assets"] = _shot_assets(shot, assets)
    return {
        "duration_lock": duration_lock,
        "shots": shots,
        "locks": locks,
        "assets": assets,
    }


def format_storyboard_skeleton(skeleton):
    """Read-only contract text prepended to first-write and repair prompts."""
    skeleton = skeleton or {}
    shots = list(skeleton.get("shots") or [])
    duration_lock = skeleton.get("duration_lock")
    if not shots:
        return ""
    lines = [
        "当前分镜骨架如下。镜号、每镜时长、资产和对白原文是锁定槽。",
        "合计时长必须等于用户锁%s秒。不要增删镜头，不要改镜号，不要改时长，不要改对白原文。"
        % (duration_lock if duration_lock not in (None, "") else ""),
        "只填每镜的画面层（动作、景别机位、站位朝向光线写在同一槽）和旁白。",
        "声景对照已公布的动作要点来写。配乐已抽成要点槽，不要把它写进音效里另编一版。",
        "不要整份采用某一专员的分镜而把合计时长改短。",
    ]
    for shot in shots:
        lines.append("")
        lines.append("## %s" % shot["id"])
        if shot.get("scene_heading"):
            lines.append("场景：%s" % shot["scene_heading"])
        lines.append("时长：%s" % shot["duration"])
        if shot.get("assets"):
            lines.append("资产：%s" % "，".join(shot["assets"]))
        else:
            lines.append("资产：")
        dialogue = shot.get("dialogue") or "无"
        lines.append("对白：%s" % dialogue)
        beats = shot.get("action_beats") or []
        if beats:
            lines.append("动作要点：%s" % " / ".join(beats))
        music = shot.get("music_beat") or "无"
        lines.append("配乐要点：%s" % music)
        sound = shot.get("sound_beat") or "无"
        lines.append("声景要点：%s" % sound)
        lines.append("画面层：【填写】")
        narration = shot.get("narration")
        if narration:
            lines.append("旁白：%s" % narration)
        else:
            lines.append("旁白：【填写】")
        lines.append("声景：【填写，对照动作要点】")
    return "\n".join(lines).strip() + "\n"



def _max_shot_seconds(locks):
    return pack_limit(locks=locks)


def _split_overlong_shots(shots, limit):
    """Split one locked shot into several, keeping the original duration sum."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return list(shots or [])
    if limit <= 0:
        return list(shots or [])
    out = []
    for shot in shots or []:
        duration = int(shot["duration"])
        if duration <= limit:
            out.append(dict(shot))
            continue
        shares = _fill_to_limit(duration, limit)
        last = len(shares) - 1
        for index, seconds in enumerate(shares):
            child = dict(shot)
            child["duration"] = seconds
            child["cast"] = list(shot.get("cast") or [])
            child["props"] = list(shot.get("props") or [])
            child["action_beats"] = list(shot.get("action_beats") or [])
            if index != last:
                child["dialogue"] = ""
                child["narration"] = ""
            if index > 0:
                child["action_continuous"] = True
            out.append(child)
    for index, shot in enumerate(out, 1):
        shot["id"] = "S%02d" % index
    return out


def allocate_shot_durations(scenes, duration_lock, max_shot_seconds=None):
    """Integer seconds per shot, summing exactly to the lock. Beats are not dropped."""
    scenes = [dict(scene) for scene in (scenes or [])]
    if not scenes:
        return []
    scene_seconds = _scene_seconds(scenes, duration_lock)
    shots = []
    index = 1
    for scene, seconds in zip(scenes, scene_seconds):
        units = _shot_units_for_scene(scene, seconds)
        for unit in units:
            shot = {
                "id": "S%02d" % index,
                "duration": unit["duration"],
                "scene_id": scene.get("id") or "",
                "scene_heading": scene.get("heading") or "",
                "cast": list(scene.get("cast") or []),
                "props": list(scene.get("props") or []),
                "place": scene.get("place") or "",
                "action_beats": list(unit.get("action_beats") or []),
                "dialogue": unit.get("dialogue") or "",
                "narration": unit.get("narration") or "",
                "sound_beat": unit.get("sound_beat") or "",
                "music_beat": unit.get("music_beat") or "",
            }
            shots.append(shot)
            index += 1
    if duration_lock and shots:
        _fit_total(shots, int(duration_lock))
    if max_shot_seconds:
        shots = _split_overlong_shots(shots, max_shot_seconds)
    return shots


def _duration_lock(locks):
    value = (locks or {}).get("duration_seconds")
    if value in (None, ""):
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _scene_units(script):
    scenes = parse_script_scenes(script, include_legacy=True)
    if not scenes:
        # Do not invent a shot list from unstructured prose.
        return []
    found = []
    lines = str(script or "").splitlines()
    heads = []
    for index, line in enumerate(lines):
        for scene in scenes:
            heading = scene.get("heading") or ""
            if heading and heading in line and line.strip().startswith("#"):
                heads.append((index, scene))
                break
    for cursor, (start, scene) in enumerate(heads):
        end = heads[cursor + 1][0] if cursor + 1 < len(heads) else len(lines)
        body = "\n".join(lines[start + 1:end])
        parsed = _parse_scene_body(scene.get("id") or "", scene.get("heading") or "", body)
        parsed["duration"] = scene.get("duration")
        parsed["place"] = _place_name(scene.get("heading") or "")
        found.append(parsed)
    return found


def _parse_scene_body(scene_id, heading, body):
    cast = []
    props = []
    beats = []
    pending = None
    sounds = []
    music = []
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _CAST_LINE.match(line)
        if match:
            for name in _split_cast_names(match.group(1)):
                if name and not _SKIP_PROP.match(name) and name not in cast:
                    cast.append(name)
            continue
        prop_match = _PROP_LINE.match(line)
        if prop_match:
            for name in _split_cast_names(prop_match.group(1)):
                if not name or _SKIP_PROP.match(name) or name in props:
                    continue
                props.append(name)
            continue
        beat = _BEAT.match(line)
        if beat:
            pending = {
                "action_beats": [beat.group(1).strip()],
                "dialogue": "",
                "narration": "",
                "sound_beat": "",
                "music_beat": "",
            }
            beats.append(pending)
            continue
        sound = _SOUND_LINE.match(line)
        if sound:
            diegetic, bgm = _split_sound(sound.group(1).strip())
            target = pending or _ensure_pending(beats)
            pending = target
            target["sound_beat"] = _join_cue(target.get("sound_beat"), diegetic)
            target["music_beat"] = _join_cue(target.get("music_beat"), bgm)
            if diegetic:
                sounds.append(diegetic)
            if bgm:
                music.append(bgm)
            continue
        narration = _NARRATION.match(line)
        if narration:
            target = pending or _ensure_pending(beats)
            pending = target
            target["narration"] = narration.group(1).strip()
            continue
        spoken = _DIALOGUE.match(line)
        if spoken and spoken.group(1).strip() not in _SKIP_DIALOGUE and not line.startswith("【"):
            speaker, line_text = spoken.group(1).strip(), spoken.group(2).strip()
            target = pending or _ensure_pending(beats)
            pending = target
            phrase = "%s：%s" % (speaker, line_text)
            target["dialogue"] = _join_cue(target.get("dialogue"), phrase)
            continue
        if line.startswith("#"):
            continue
        target = pending or _ensure_pending(beats)
        pending = target
        if line not in target["action_beats"]:
            target["action_beats"].append(line)
    if not beats:
        beats.append({
            "action_beats": [],
            "dialogue": "",
            "narration": "",
            "sound_beat": " / ".join(sounds),
            "music_beat": " / ".join(music),
        })
    return {
        "id": scene_id,
        "heading": heading,
        "cast": cast,
        "props": props,
        "beats": beats,
        "duration": None,
        "place": "",
    }


def _ensure_pending(beats):
    if beats:
        return beats[-1]
    item = {
        "action_beats": [],
        "dialogue": "",
        "narration": "",
        "sound_beat": "",
        "music_beat": "",
    }
    beats.append(item)
    return item


def _split_sound(text):
    parts = re.split(r"[，,;；。]+", text)
    diegetic = []
    music = []
    for part in parts:
        item = part.strip()
        if not item:
            continue
        if _MUSIC.search(item):
            music.append(item)
        else:
            diegetic.append(item)
    return "，".join(diegetic), "，".join(music)


def _join_cue(previous, incoming):
    incoming = str(incoming or "").strip()
    previous = str(previous or "").strip()
    if not incoming:
        return previous
    if not previous:
        return incoming
    if incoming in previous:
        return previous
    return "%s / %s" % (previous, incoming)


def _scene_seconds(scenes, duration_lock):
    declared = [scene.get("duration") for scene in scenes]
    if all(isinstance(item, int) or (isinstance(item, float) and float(item).is_integer()) for item in declared if item is not None) and all(item for item in declared):
        values = [int(item) for item in declared]
        if duration_lock is None:
            return values
        total = sum(values)
        if total == int(duration_lock):
            return values
        return _distribute(int(duration_lock), len(values), values)
    count = len(scenes)
    if duration_lock is None:
        return [1] * count
    return _distribute(int(duration_lock), count)


def _shot_units_for_scene(scene, seconds):
    beats = list(scene.get("beats") or []) or [{
        "action_beats": [],
        "dialogue": "",
        "narration": "",
        "sound_beat": "",
        "music_beat": "",
    }]
    seconds = max(int(seconds), 1)
    if len(beats) > seconds:
        beats = _merge_beats(beats, seconds)
    shares = _distribute(seconds, len(beats))
    units = []
    for beat, duration in zip(beats, shares):
        unit = dict(beat)
        unit["duration"] = duration
        units.append(unit)
    return units


def _merge_beats(beats, target):
    packed = [dict(item, action_beats=list(item.get("action_beats") or [])) for item in beats]
    while len(packed) > target and len(packed) >= 2:
        # Merge the shortest adjacent pair, preferring later pairs last.
        best = 0
        best_len = len(packed[0].get("action_beats") or []) + len(packed[1].get("action_beats") or [])
        for index in range(len(packed) - 1):
            size = len(packed[index].get("action_beats") or []) + len(packed[index + 1].get("action_beats") or [])
            if size < best_len:
                best = index
                best_len = size
        left = packed[best]
        right = packed.pop(best + 1)
        left["action_beats"] = list(left.get("action_beats") or []) + list(right.get("action_beats") or [])
        left["dialogue"] = _join_cue(left.get("dialogue"), right.get("dialogue"))
        left["narration"] = _join_cue(left.get("narration"), right.get("narration"))
        left["sound_beat"] = _join_cue(left.get("sound_beat"), right.get("sound_beat"))
        left["music_beat"] = _join_cue(left.get("music_beat"), right.get("music_beat"))
    return packed


def _fill_to_limit(duration, limit, minimum=4):
    """Split an overlong span by filling earlier shots up to ``limit``.

    A 40s scene becomes 30+10, and a 25s scene stays 25. The last leftover
    stays at least ``minimum`` seconds when the total allows it; the overall
    sum is never shortened.
    """
    duration = int(duration)
    limit = int(limit)
    minimum = int(minimum)
    if duration <= limit:
        return [duration]
    parts = (duration + limit - 1) // limit
    shares = [limit] * (parts - 1)
    last = duration - limit * (parts - 1)
    if last < minimum:
        need = minimum - last
        last = minimum
        index = len(shares) - 1
        while need > 0 and index >= 0:
            can = shares[index] - minimum
            take = min(max(can, 0), need)
            shares[index] -= take
            need -= take
            index -= 1
        if need > 0:
            last = duration - sum(shares)
    shares.append(last)
    return shares


def _distribute(total, count, weights=None):
    if count <= 0:
        return []
    total = int(total)
    if total < count:
        # Still one second each would exceed the lock. Merge is handled by callers
        # for beats; for scenes, give 1s to the first `total` and 0 is not allowed.
        # Caller should have merged. Fall back to 1s min and let _fit_total correct.
        values = [1] * count
        return values
    if weights and len(weights) == count and sum(weights) > 0:
        raw = [total * (float(weight) / sum(weights)) for weight in weights]
        values = [max(1, int(item)) for item in raw]
        # largest remainder on the fractional parts after the floor, but we already
        # forced min 1. Adjust below.
    else:
        base = total // count
        values = [base] * count
        for index in range(total % count):
            values[index] += 1
        return values
    _fit_list(values, total)
    return values


def _fit_list(values, total):
    if not values:
        return values
    for index, value in enumerate(values):
        if value < 1:
            values[index] = 1
    current = sum(values)
    cursor = 0
    while current < total:
        values[cursor % len(values)] += 1
        current += 1
        cursor += 1
    cursor = 0
    while current > total:
        index = cursor % len(values)
        if values[index] > 1:
            values[index] -= 1
            current -= 1
        cursor += 1
        if cursor > len(values) * (current + 1):
            break
    return values


def _fit_total(shots, total):
    values = [int(shot["duration"]) for shot in shots]
    _fit_list(values, total)
    for shot, value in zip(shots, values):
        shot["duration"] = value


def _shot_assets(shot, assets):
    names = list(shot.get("cast") or []) + list(shot.get("props") or [])
    place = str(shot.get("place") or "").strip()
    if place:
        names.append(place)
    found = []
    for name in names:
        asset_id = _match_asset(name, assets)
        if asset_id and asset_id not in found:
            found.append(asset_id)
    return found


def _match_asset(name, assets):
    name = str(name or "").strip()
    if not name:
        return ""
    exact = []
    fuzzy = []
    for item in assets or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "九宫格":
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        asset_id = str(item.get("id") or "").strip()
        if not asset_id:
            continue
        if title == name:
            exact.append(asset_id)
        elif title and (name in title or title in name):
            fuzzy.append(asset_id)
    if exact:
        return exact[0]
    if fuzzy:
        return fuzzy[0]
    return ""


def _empty_shot(shot_id, duration):
    return {
        "id": shot_id,
        "duration": duration,
        "scene_id": "",
        "scene_heading": "",
        "cast": [],
        "props": [],
        "place": "",
        "action_beats": [],
        "dialogue": "",
        "narration": "",
        "sound_beat": "",
        "music_beat": "",
        "assets": [],
    }


from end.storyboard_contract import _blocks, _duration, format_asset_line, parse_asset_line


def restore_storyboard_locks(text, skeleton):
    """Write shot ids, durations, assets, and spoken lines back onto the board."""
    skeleton = skeleton or {}
    shots = list(skeleton.get("shots") or [])
    if not shots:
        return str(text or "")
    existing = {block["id"]: block for block in _blocks(text)}
    blocks = []
    for shot in shots:
        fields = dict((existing.get(shot["id"]) or {}).get("fields") or {})
        fields["时长"] = str(shot["duration"])
        if shot.get("assets"):
            fields["资产"] = "，".join(shot["assets"])
        elif "资产" not in fields:
            fields["资产"] = ""
        if shot.get("dialogue"):
            fields["对白"] = shot["dialogue"]
        elif "对白" not in fields:
            fields["对白"] = "无"
        if shot.get("narration"):
            fields["旁白"] = shot["narration"]
        blocks.append(_render_shot(shot["id"], fields, shot))
    return "\n\n".join(blocks).strip() + "\n"


def storyboard_skeleton_failures(text, skeleton):
    """Fail when shot ids or durations diverge from the skeleton or the duration lock."""
    skeleton = skeleton or {}
    shots = list(skeleton.get("shots") or [])
    duration_lock = skeleton.get("duration_lock")
    if not shots:
        if duration_lock not in (None, ""):
            return ["无法从剧本抽出场次骨架，不能校验时长锁"]
        return []
    blocks = _blocks(text)
    errors = []
    written_ids = [block["id"] for block in blocks]
    expected_ids = [shot["id"] for shot in shots]
    if written_ids != expected_ids:
        missing = [shot_id for shot_id in expected_ids if shot_id not in written_ids]
        extra = [shot_id for shot_id in written_ids if shot_id not in expected_ids]
        if missing:
            errors.append("镜号锁缺失：%s" % "、".join(missing))
        if extra:
            errors.append("自增镜号：%s" % "、".join(extra))
        if not missing and not extra:
            errors.append("镜号锁顺序被改写")
    by_id = {block["id"]: block for block in blocks}
    total = 0
    have_all_durations = True
    for shot in shots:
        block = by_id.get(shot["id"])
        if not block:
            have_all_durations = False
            continue
        duration = _duration((block.get("fields") or {}).get("时长") or "")
        if duration is None:
            have_all_durations = False
            errors.append("%s 时长锁缺失，锁定为 %s 秒" % (shot["id"], shot["duration"]))
            continue
        total += duration
        if duration != int(shot["duration"]):
            errors.append("%s 时长锁被改写：正文是 %s，锁定为 %s 秒" % (
                shot["id"], duration, shot["duration"],
            ))
        expected_assets = list(shot.get("assets") or [])
        written_assets = parse_asset_line("资产：" + ((block.get("fields") or {}).get("资产") or "")) or []
        if expected_assets and written_assets != expected_assets:
            errors.append("%s 资产锁被改写" % shot["id"])
        expected_line = shot.get("dialogue") or ""
        written_line = ((block.get("fields") or {}).get("对白") or "").strip()
        if expected_line and written_line and written_line != expected_line and written_line != "无":
            if expected_line not in written_line:
                errors.append("%s 对白原文被改写" % shot["id"])
    if duration_lock not in (None, ""):
        if have_all_durations and len(blocks) == len(shots) and total != int(duration_lock):
            errors.append("时长合计是 %s 秒，锁定为 %s 秒" % (total, duration_lock))
        elif not errors:
            # A shorter complete board with different shot counts still fails the lock.
            written_total = 0
            complete = True
            for block in blocks:
                duration = _duration((block.get("fields") or {}).get("时长") or "")
                if duration is None:
                    complete = False
                    break
                written_total += duration
            if complete and written_total != int(duration_lock):
                errors.append("时长合计是 %s 秒，锁定为 %s 秒" % (written_total, duration_lock))
    return errors


def _render_shot(shot_id, fields, shot):
    lines = ["## %s" % shot_id]
    if fields.get("动作"):
        lines.append("动作：%s" % fields["动作"])
    elif shot.get("action_beats"):
        # Keep the 动作 line empty for the fill slot; 要点 already live in the prompt.
        pass
    if fields.get("景别机位"):
        lines.append("景别机位：%s" % fields["景别机位"])
    lines.append("时长：%s" % (fields.get("时长") or shot["duration"]))
    assets = fields.get("资产")
    asset_line = format_asset_line(
        [part.strip() for part in re.split(r"[、,，\s]+", assets) if part.strip()]
        if assets else shot.get("assets") or []
    )
    if asset_line:
        lines.append(asset_line)
    for key in ("站位", "朝向", "光线"):
        if fields.get(key):
            lines.append("%s：%s" % (key, fields[key]))
    if fields.get("站位、朝向、光线"):
        lines.append("站位、朝向、光线：%s" % fields["站位、朝向、光线"])
    lines.append("对白：%s" % (fields.get("对白") or "无"))
    if fields.get("旁白"):
        lines.append("旁白：%s" % fields["旁白"])
    elif shot.get("narration"):
        lines.append("旁白：%s" % shot["narration"])
    if fields.get("音效"):
        lines.append("音效：%s" % fields["音效"])
    return "\n".join(lines)
