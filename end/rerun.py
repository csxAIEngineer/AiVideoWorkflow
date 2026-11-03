"""Canvas prompt edits, and a rerun that starts at the edited node.

The edited text stays. Writing stages after it use the existing gates.
Image and shot edits restart the render from the steps that consume them.
"""

import json
import re
from pathlib import Path

from end.prompt_gate import align_feed_unit_headers, delivery_units
from end.repair_units import asset_pieces
from end.render_loop import invalidate_steps, load_run_state
from end.stage_runner import markdown_from_asset_items, stages_for
from end.trace import clear_downstream, write_internal
from end.workflow_from_spec import _safe_id, collect_steps

_NODES = ("script", "storyboard", "asset", "shot")
_TAIL = (("join", "join"), ("audio", "audio"), ("subtitles", "subtitles"))
_RENDER_PHASES = ("images", "videos", "join", "audio", "subtitles", "done", "image_debug")
_RESUME_LABEL = {
    "asset": "重写资产",
    "storyboard": "重写分镜",
    "feed": "重写投喂稿",
    "script": "重写剧本",
    "recon": "重写对标",
}


def figure_number(asset_id, index):
    """Map a canvas asset id onto the #### 图N block it edits."""
    text = str(asset_id or "")
    match = re.search(r"图\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.match(r"^A(\d+)$", text, re.I)
    if match:
        return int(match.group(1))
    return index + 1


def replace_figure_block(text, number, prompt):
    """Put `prompt` under the existing 图N heading. Other blocks stay."""
    pieces = list(asset_pieces(text))
    chunks = []
    found = False
    for num, body in pieces:
        if num != number:
            chunks.append(body.strip())
            continue
        found = True
        chunks.append(_figure_block(num, body, prompt))
    if not found and str(prompt or "").strip():
        chunks.append(_figure_block(number, "", prompt))
    if not chunks:
        return ""
    return "\n\n".join(chunks).strip() + "\n"


def apply_saved_content(spec, body):
    """Return a new spec with canvas edits. Raises ValueError when nothing applies."""
    if not isinstance(body, dict):
        raise ValueError("内容格式不对")
    spec = dict(spec or {})
    changed = False
    if "script" in body:
        spec["script"] = "" if body.get("script") is None else str(body.get("script"))
        changed = True
    if "storyboard" in body:
        spec["storyboard"] = "" if body.get("storyboard") is None else str(body.get("storyboard"))
        changed = True
    if "assets" in body:
        spec = _apply_assets(spec, body.get("assets"))
        changed = True
    if "shots" in body:
        spec = _apply_shots(spec, body.get("shots"))
        changed = True
    if not changed:
        raise ValueError("没有要保存的内容")
    return spec


def persist_edits(folder, spec, body):
    """Copy saved canvas text into the files the writing pipeline reads."""
    folder = Path(folder)
    body = body or {}
    if "script" in body:
        write_internal(folder, "剧本.md", spec.get("script") or "")
    if "storyboard" in body:
        write_internal(folder, "分镜.md", spec.get("storyboard") or "")
    if "assets" in body:
        write_internal(folder, "assets.md", spec.get("image_prompts") or "")
    if "shots" in body:
        _write_feed(folder, spec.get("feed") or "")


def steps_to_invalidate(spec, node, target_id=""):
    """Render steps whose output the edited node no longer matches."""
    assets, shots = collect_steps(spec or {})
    node = str(node or "")
    if node == "script":
        return _image_steps(assets) + _video_steps(shots) + list(_TAIL)
    if node == "storyboard":
        return _video_steps(shots) + list(_TAIL)
    if node == "asset":
        return _asset_steps(assets, shots, target_id)
    if node == "shot":
        return _shot_steps(shots, target_id)
    raise ValueError("不能从这里重跑")


def writing_resume(pipeline, locks, stage):
    """Accept `stage` and point the existing machine at the next write."""
    only = str((pipeline or {}).get("only") or "")
    stages = stages_for(locks, only)
    if stage not in stages:
        raise ValueError("这一段不在当前流程里")
    index = stages.index(stage)
    if index + 1 >= len(stages):
        raise ValueError("这一段后面没有要重写的阶段")
    nxt = stages[index + 1]
    resumed = {
        "only": only,
        "accepted": list(stages[: index + 1]),
        "current": nxt,
        "action": "write",
        "passed": True,
        "report": "",
        "check": "",
    }
    return resumed, nxt


def rerun_ready(project, folder):
    """True once the final prompts exist, or a render has already produced files."""
    if not project:
        return False
    phase = ((project.get("progress") or {}).get("phase") or "")
    if project.get("status") == "done" and phase == "prompts_ready":
        return True
    if phase in _RENDER_PHASES:
        return True
    if project.get("video_file"):
        return True
    progress = project.get("progress") or {}
    if project.get("status") == "paused" and progress.get("paused_from") in ("rendering", "building"):
        return True
    state = load_run_state(folder)
    steps = state.get("steps") or {}
    if any((step or {}).get("status") == "done" for step in steps.values()):
        return True
    outputs = Path(folder) / "outputs"
    if not outputs.is_dir():
        return False
    for path in outputs.rglob("*"):
        if path.is_file() and path.suffix in (".png", ".mp4", ".ass") and path.stat().st_size > 0:
            return True
    return False


def rerun_from(project_id, node, target_id=""):
    """Rerun from a canvas node. Returns a public view when render must ask first."""
    from end import server as site

    node = str(node or "")
    target_id = str(target_id or "")
    if node not in _NODES:
        raise ValueError("不能从这里重跑")
    with site.project_lock(project_id):
        project = site.read_project(project_id)
        if project is None:
            raise KeyError("没有这个项目")
        if _worker_busy(site, project_id):
            raise RuntimeError("正在跑，等这一轮结束再改")
        folder = site.project_dir(project_id)
        if not rerun_ready(project, folder):
            raise RuntimeError("提示词还没写完，不能从这里重跑")
        spec = site.load_spec(project_id) or {}
        _require_text(spec, node, target_id)
        if node in ("asset", "shot"):
            question = _render_question(site, project, spec)
            if question is not None:
                return question
        steps = steps_to_invalidate(spec, node, target_id)
        resume = None
        if node in ("script", "storyboard"):
            resume = writing_resume(
                (project.get("progress") or {}).get("pipeline") or {},
                site.read_locks(folder),
                node,
            )
        site.gate_for(project_id).resume()
        if resume is not None:
            return _rerun_writing(site, project, spec, folder, node, steps, resume)
        return _rerun_render(site, project, folder, steps)


def _worker_busy(site, project_id):
    """The write queue thread stays alive after it yields. Only a live handoff is busy."""
    if not site.thread_alive(project_id):
        return False
    return site._PIPELINE_HANDOFF.get(project_id) in ("writer", "render")


def _figure_block(number, old_body, prompt):
    heading = "#### 图%d" % number
    if old_body:
        first = old_body.splitlines()[0].strip()
        if first.startswith("#"):
            heading = first
    body = str(prompt or "").strip()
    lines = body.splitlines()
    if lines and lines[0].strip() == heading:
        body = "\n".join(lines[1:]).strip()
    if not body:
        return heading
    return "%s\n%s" % (heading, body)


def _find_asset(assets, asset_id):
    wanted = str(asset_id or "")
    safe = _safe_id(wanted, "")
    for item in assets:
        raw = str(item.get("id") or "")
        if raw == wanted or (safe and _safe_id(raw, "") == safe):
            return item
    return None


def _apply_assets(spec, incoming):
    if not isinstance(incoming, list):
        raise ValueError("资产格式不对")
    if not isinstance(spec.get("assets"), list):
        raise ValueError("还没有资产")
    assets = [dict(item) if isinstance(item, dict) else item for item in spec.get("assets") or []]
    edited = []
    for item in incoming:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("资产格式不对")
        target = _find_asset(assets, item.get("id"))
        if target is None:
            raise ValueError("没有资产 " + str(item.get("id") or ""))
        if "prompt" not in item:
            continue
        target["prompt"] = "" if item.get("prompt") is None else str(item.get("prompt"))
        target["prompt_edited"] = True
        edited.append(target["id"])
    spec = dict(spec)
    spec["assets"] = assets
    if edited:
        spec["image_prompts"] = _sync_image_prompts(spec.get("image_prompts") or "", assets, edited)
    return spec


def _sync_image_prompts(image, assets, edited_ids):
    if not asset_pieces(image):
        return markdown_from_asset_items(assets)
    edited = set(edited_ids)
    for index, asset in enumerate(assets):
        if asset.get("id") not in edited:
            continue
        image = replace_figure_block(image, figure_number(asset.get("id"), index), asset.get("prompt") or "")
    return image


def _apply_shots(spec, incoming):
    if not isinstance(incoming, list) or not incoming:
        raise ValueError("镜头格式不对")
    _assets, shots = collect_steps(spec)
    if not shots:
        raise ValueError("还没有镜头")
    known = {shot["id"]: index for index, shot in enumerate(shots)}
    units = list(delivery_units(spec.get("feed")))
    prompts = {shot["id"]: shot["prompt"] for shot in shots}
    edited = []
    for item in incoming:
        if not isinstance(item, dict) or "id" not in item or "prompt" not in item:
            raise ValueError("镜头格式不对")
        shot_id = str(item.get("id") or "")
        if shot_id not in known:
            raise ValueError("没有镜头 " + shot_id)
        prompt = "" if item.get("prompt") is None else str(item.get("prompt")).strip()
        prompts[shot_id] = prompt
        if units:
            units[known[shot_id]] = _ensure_unit(prompt, known[shot_id], len(units))
        edited.append(shot_id)
    spec = dict(spec)
    if units:
        spec["feed"] = align_feed_unit_headers("\n\n".join(unit.strip() for unit in units if str(unit).strip()))
        aligned = delivery_units(spec.get("feed"))
        for index, shot in enumerate(shots):
            if index < len(aligned):
                prompts[shot["id"]] = aligned[index]
    spec["shots"] = _shots_with_prompts(spec.get("shots"), shots, prompts, edited)
    return spec


def _ensure_unit(text, index, total):
    """Keep a replaced block visible to delivery_units, which drops text before the first head."""
    body = str(text or "").strip()
    first = body.splitlines()[0].strip() if body else ""
    if first.startswith("#") and "投喂单元" in first:
        return body
    return "## 【投喂单元 %s/%s】\n%s" % (index + 1, total, body)


def _shots_with_prompts(stored, shots, prompts, edited):
    rows = {}
    for item in stored or []:
        if isinstance(item, dict) and item.get("id"):
            rows[str(item.get("id"))] = dict(item)
    edited_ids = set(edited)
    merged = []
    for shot in shots:
        row = dict(rows.get(shot["id"]) or shot)
        row["id"] = shot["id"]
        if shot["id"] in edited_ids or shot["id"] in prompts:
            row["prompt"] = prompts[shot["id"]]
        if not row.get("images"):
            row["images"] = list(shot.get("images") or [])
        merged.append(row)
    return merged


def _write_feed(folder, feed):
    write_internal(folder, "feed.md", feed)
    root = Path(folder) / "feed.md"
    text = str(feed or "").strip()
    if text:
        root.write_text(text + "\n", encoding="utf-8")
    elif root.is_file():
        root.unlink()


def _image_steps(assets):
    return [(asset["id"], "image") for asset in assets]


def _video_steps(shots):
    return [(shot["id"], "video") for shot in shots]


def _dependent_ids(assets, root_id):
    doomed = {root_id}
    changed = True
    while changed:
        changed = False
        for asset in assets:
            parent = asset.get("depends_on") or ""
            if parent in doomed and asset["id"] not in doomed:
                doomed.add(asset["id"])
                changed = True
    return [asset["id"] for asset in assets if asset["id"] in doomed]


def _asset_steps(assets, shots, target_id):
    known = {asset["id"] for asset in assets}
    if target_id not in known:
        raise ValueError("没有资产 " + str(target_id or ""))
    doomed = set(_dependent_ids(assets, target_id))
    first = None
    for index, shot in enumerate(shots):
        if any(image in doomed for image in shot.get("images") or []):
            first = index
            break
    steps = [(asset["id"], "image") for asset in assets if asset["id"] in doomed]
    if first is None:
        return steps
    return steps + _video_steps(shots[first:]) + list(_TAIL)


def _shot_steps(shots, target_id):
    index = next((i for i, shot in enumerate(shots) if shot["id"] == target_id), None)
    if index is None:
        raise ValueError("没有镜头 " + str(target_id or ""))
    return _video_steps(shots[index:]) + list(_TAIL)


def _require_text(spec, node, target_id):
    if node == "script" and not str(spec.get("script") or "").strip():
        raise ValueError("提示词是空的")
    if node == "storyboard" and not str(spec.get("storyboard") or "").strip():
        raise ValueError("分镜是空的")
    if node == "asset":
        asset = _find_asset(spec.get("assets") or [], target_id)
        if asset is None:
            raise ValueError("没有资产 " + target_id)
        if not str(asset.get("prompt") or "").strip():
            raise ValueError("提示词是空的")
    if node == "shot":
        _assets, shots = collect_steps(spec)
        shot = next((item for item in shots if item["id"] == target_id), None)
        if shot is None:
            raise ValueError("没有镜头 " + target_id)
        if not str(shot.get("prompt") or "").strip():
            raise ValueError("提示词是空的")


def _render_question(site, project, spec):
    folder = site.project_dir(project["id"])
    locks = site.read_locks(folder)
    if (project.get("progress") or {}).get("lock_machine") and locks.get("model") not in ("", "h3"):
        return site._seedance_only(project)
    aspect = (
        locks.get("aspect")
        or site.locked_aspect(project.get("brief") or "")
        or site.locked_aspect(str((spec or {}).get("aspect_ratio") or ""))
    )
    if not aspect:
        return site._ask_aspect(project)
    return None


def _lock_stage_text(folder, spec, project, node):
    spec = dict(spec or {})
    if node == "script":
        text = str(spec.get("script") or project.get("script") or "")
        spec["script"] = text
        write_internal(folder, "剧本.md", text)
    else:
        text = str(spec.get("storyboard") or project.get("storyboard") or "")
        spec["storyboard"] = text
        write_internal(folder, "分镜.md", text)
    path = Path(folder) / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec


def _drop_film(folder, project):
    name = str(project.get("video_file") or "")
    if name and "/" not in name and ".." not in name:
        path = Path(folder) / "outputs" / name
        if path.is_file():
            path.unlink()
    project["video_file"] = ""


def _kept_done(folder, steps):
    pending = {step_id for step_id, _kind in steps}
    state = load_run_state(folder)
    kept = set()
    for step_id, step in (state.get("steps") or {}).items():
        if step_id in pending:
            continue
        if (step or {}).get("status") == "done":
            kept.add(step_id)
    return kept


def _rerun_writing(site, project, spec, folder, node, steps, resume):
    stage = node
    pipeline, nxt = resume
    _lock_stage_text(folder, spec, project, node)
    clear_downstream(folder, stage)
    spec = site.load_spec(project["id"]) or {}
    invalidate_steps(folder, project["id"], steps)
    _drop_film(folder, project)
    project["script"] = str(spec.get("script") or "")
    if node == "script":
        project["storyboard"] = ""
        project["image_prompts"] = ""
    else:
        project["storyboard"] = str(spec.get("storyboard") or "")
        project["image_prompts"] = str(spec.get("image_prompts") or project.get("image_prompts") or "")
    project["tasks"] = []
    project["finished_at"] = None
    project["error"] = None
    site.write_project(project)
    site._enqueue_stage(project, pipeline, _RESUME_LABEL.get(nxt, "下一段"))
    return None


def _rerun_render(site, project, folder, steps):
    invalidate_steps(folder, project["id"], steps)
    if any(kind in ("join", "subtitles", "video") for _step_id, kind in steps):
        _drop_film(folder, project)
    kept = _kept_done(folder, steps)
    project["finished_at"] = None
    project["error"] = None
    site.write_project(project)
    fresh = site.read_project(project["id"]) or project
    return site._start_explicit_render(fresh, keep_done=kept)
