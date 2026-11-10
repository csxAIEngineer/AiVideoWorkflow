"""One storyboard manuscript. shots[] is a projection of the same shot ids.

Previs files stay on disk. This module does not read them and does not invent
a duration or an asset id.
"""

import json
import re
from pathlib import Path

CONTRACT = "shot-prose-v1"
FILL = "补写分镜"
COMPLETE = "补全"
MARKER = "分镜.contract"
HOLD = "分镜.hold"
PLACEHOLDER_PROMPT = "按这张资产图演这一镜"

ASSET_FIELD_EXAMPLE = "资产：图1，图1_3，图2_3"
ASSET_FIELD_RULES = "\n".join((
    "资产字段只写对照表里的完整 id，原样照抄，多个用中文逗号「，」分开。",
    "格式示例（只示范格式，对照表里没有的 id 不要用）：%s" % ASSET_FIELD_EXAMPLE,
    "资产字段不要写括号、名称或说明。名称可以写在动作或站位。",
    "不要把图1_3写成图1，不要按类别重编号，不要靠名称猜 id，不要发明对照表以外的 id。",
    "这一镜的人物或场景对不上对照表时，资产字段留空，不要改用别的资产。",
))
STORYBOARD_INSTRUCTION = "\n".join((
    "只写一份完整分镜，放在 storyboard 字符串。不要写 previs，不要写预演故事板。",
    "不要写段、格、六问、九宫格或预演完成标记。",
    "每一镜一个标题，标题单独成行。镜号写成 S01、S02：",
    "## S01",
    "动作：这一镜里发生的动作",
    "景别机位：景别和机位",
    "时长：6",
    ASSET_FIELD_EXAMPLE,
    "站位、朝向、光线、对白、旁白只有这一镜确实要写才各占一行。",
    "时长必须是这一镜写出的整数秒，不要省略，也不要写区间。",
    ASSET_FIELD_RULES,
    "不要另写一份镜头稿。若仍然提交 shots，每一项的 id、duration、images 必须和同一镜号正文一致。",
))

_HEADING = re.compile(r"^##\s*(?:镜号\s*)?(S\d+)\b", re.I)
_FIELD = re.compile(
    r"^(动作|景别机位|时长|资产|站位|朝向|光线|对白|旁白)\s*[:：]\s*(.*)$"
)
_DURATION = re.compile(r"^(\d+)\s*秒?$")


def needs_human(report):
    """Structural storyboard failures wait for an explicit rewrite."""
    text = str(report or "").strip()
    if "分镜是空的" in text or "预演故事板是空的" in text:
        return True
    return text.startswith("分镜未完成") or text.startswith("镜头不一致")


def has_shot_headings(text):
    return any(_HEADING.match(line.strip()) for line in str(text or "").splitlines())


def _canonical_id(raw):
    match = re.search(r"(\d+)", str(raw or ""))
    if not match:
        return str(raw or "").strip().upper()
    return "S%02d" % int(match.group(1))


def _blocks(text):
    found = []
    current = None
    for line in str(text or "").splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            if current is not None:
                found.append(current)
            current = {"id": _canonical_id(heading.group(1)), "fields": {}}
            continue
        if current is None:
            continue
        field = _FIELD.match(line.strip())
        if field and field.group(1) not in current["fields"]:
            current["fields"][field.group(1)] = field.group(2).strip()
    if current is not None:
        found.append(current)
    return found


def _duration(text):
    match = _DURATION.match(str(text or "").strip())
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    return value


def _assets(text, known):
    parts = [part for part in re.split(r"[、,，\s]+", str(text or "").strip()) if part]
    if not parts:
        return None
    if any(part not in known for part in parts):
        return None
    return parts


def _prompt(fields):
    chunks = [fields.get("动作") or "", fields.get("景别机位") or ""]
    for label in ("站位", "朝向", "光线"):
        value = fields.get(label) or ""
        if value:
            chunks.append("%s：%s" % (label, value))
    return "\n".join(chunk for chunk in chunks if chunk)


def project_shots(text, known_ids):
    """Return ``(shots, report)``. ``shots`` is empty when the manuscript is not usable.

    A missing duration stays missing. Asset names are not matched to ids.
    """
    known = {str(item).strip() for item in (known_ids or []) if str(item).strip()}
    blocks = _blocks(text)
    if not blocks:
        return [], "分镜未完成：缺镜号"
    shots = []
    gaps = []
    seen = set()
    for block in blocks:
        shot_id = block["id"]
        fields = block["fields"]
        missing = []
        if shot_id in seen:
            missing.append("镜号重复")
        seen.add(shot_id)
        if not fields.get("动作"):
            missing.append("动作")
        if not fields.get("景别机位"):
            missing.append("景别机位")
        duration = _duration(fields.get("时长") or "")
        if duration is None:
            missing.append("时长")
        images = _assets(fields.get("资产") or "", known)
        if images is None:
            missing.append("资产")
        if missing:
            gaps.append("%s 缺%s" % (shot_id, "、".join(missing)))
            continue
        shots.append(
            {
                "id": shot_id,
                "duration": duration,
                "prompt": _prompt(fields),
                "images": images,
                "dialogue": fields.get("对白") or "",
                "narration": fields.get("旁白") or "",
            }
        )
    if gaps:
        return [], "分镜未完成：" + "；".join(gaps)
    return shots, ""


def _is_placeholder(shots):
    if not isinstance(shots, list) or not shots:
        return True
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        prompt = str(shot.get("prompt") or "").strip()
        if prompt and prompt != PLACEHOLDER_PROMPT:
            return False
    return True


def _same_images(left, right):
    return [str(item) for item in (left or [])] == [str(item) for item in (right or [])]


def shots_conflict(projected, supplied):
    """Reject a second shot list that disagrees. A placeholder list is not a manuscript."""
    if not isinstance(supplied, list) or not supplied or _is_placeholder(supplied):
        return ""
    rows = [item for item in supplied if isinstance(item, dict)]
    if not rows:
        return ""
    got = [_canonical_id(item.get("id") or item.get("shot_id") or "") for item in rows]
    want = [item["id"] for item in projected]
    if got != want:
        return "镜头不一致：正文镜号 %s，shots 镜号 %s" % ("、".join(want), "、".join(got))
    for shot, row in zip(projected, rows):
        raw = row.get("duration")
        if raw in (None, ""):
            raw = row.get("duration_sec")
        try:
            duration = int(raw)
        except (TypeError, ValueError):
            return "镜头不一致：%s 的 shots 没有时长" % shot["id"]
        if duration != int(shot["duration"]):
            return "镜头不一致：%s 时长正文 %s，shots 为 %s" % (shot["id"], shot["duration"], duration)
        images = row.get("images")
        if images is None:
            images = []
        if not isinstance(images, list):
            images = [images]
        if not _same_images(shot["images"], images):
            return "镜头不一致：%s 资产正文 %s，shots 为 %s" % (
                shot["id"],
                "、".join(shot["images"]),
                "、".join(str(item) for item in images),
            )
    return ""


def locked_asset_catalog(assets):
    """Final asset ids already stored on the spec. Names stay labels, not a second catalog."""
    lines = ["已锁定资产 id 对照（资产字段只写等号左边的 id）："]
    seen = set()
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        name = str(asset.get("title") or asset.get("subject") or "").strip() or "名称未写"
        lines.append("%s = %s" % (asset_id, name))
    if len(lines) == 1:
        return "已锁定资产 id 对照：空。资产字段留空，不要编造 id。"
    return "\n".join(lines)


def known_ids(assets):
    found = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "").strip()
        if asset_id and asset_id not in found:
            found.append(asset_id)
    return found


def project_into_spec(spec, result):
    """Project a new storyboard. Do not copy previs and do not keep a placeholder shot."""
    spec = dict(spec or {})
    result = result or {}
    text = str(result.get("storyboard") or "").strip()
    if text:
        spec["storyboard"] = text
    shots, report = project_shots(text, known_ids(spec.get("assets")))
    if not report:
        report = shots_conflict(shots, result.get("shots"))
    spec["storyboard_contract"] = CONTRACT
    if report:
        spec["storyboard_hold"] = report
        if _is_placeholder(spec.get("shots")):
            spec["shots"] = []
        return spec
    spec["shots"] = shots
    spec.pop("storyboard_hold", None)
    return spec


def lock_projected_shots(before, after):
    """Feed may rewrite prompt text. It may not renumber shots or invent duration or assets."""
    if (before or {}).get("storyboard_contract") != CONTRACT:
        return after
    after = dict(after or {})
    locked = [dict(item) for item in (before.get("shots") or []) if isinstance(item, dict)]
    prompts = {}
    for item in after.get("shots") or []:
        if isinstance(item, dict) and item.get("id"):
            prompts[item.get("id")] = item.get("prompt")
    restored = []
    for src in locked:
        row = dict(src)
        prompt = prompts.get(src.get("id"))
        if str(prompt or "").strip():
            row["prompt"] = prompt
        restored.append(row)
    after["shots"] = restored
    after["storyboard_contract"] = CONTRACT
    return after


def _internal(folder):
    return Path(folder) / "_内部_"


def write_marker(folder):
    path = _internal(folder)
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text(CONTRACT + "\n", encoding="utf-8")


def marker_present(folder):
    path = _internal(folder) / MARKER
    if not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8").strip() == CONTRACT
    except OSError:
        return False


def note_hold(folder, message):
    path = _internal(folder) / HOLD
    text = str(message or "").strip()
    if not text:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def read_hold(folder):
    path = _internal(folder) / HOLD
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def rendering_conflict(folder):
    """Stop a paid resume when a contracted board no longer matches video steps.

    Projects without the new contract keep the existing resume behavior.
    """
    path = Path(folder) / "spec.json"
    if not path.is_file():
        return ""
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(spec, dict) or spec.get("storyboard_contract") != CONTRACT:
        return ""
    shot_ids = [
        str(item.get("id"))
        for item in (spec.get("shots") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not shot_ids:
        return ""
    from end.render_loop import load_run_state

    steps = (load_run_state(folder).get("steps") or {})
    video_ids = [
        str(step_id)
        for step_id, step in steps.items()
        if isinstance(step, dict) and step.get("kind") == "video"
    ]
    extra = [step_id for step_id in video_ids if step_id not in shot_ids]
    if not extra:
        return ""
    return "分镜镜号与已开始的出片不一致（%s）。已暂停，不会从预演稿重建，也不会继续出片。" % "、".join(extra)
