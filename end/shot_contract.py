"""Read-only video-submit contract for the project view.

The render loop still chooses the mode, binds images, and may extract a
continuation frame at submit time. This module only reports that result.
It does not extract frames, upload stills, or write run state.
"""

import os
from pathlib import Path

from end.board_shots import _h3_fields
from end.product_names import UNIT_CLAIM, UNIT_HEAD_ANY
from end.render_loop import (
    H3_MAX_REFERENCE_IMAGES,
    VIDEO_MODES,
    _http_media_url,
    _output_file,
    choose_video_mode,
    continuation_tail_name,
    load_run_state,
    output_paths,
)


def annotate_shots(shots, sources, work_dir, project_id):
    """Copy each shot and add the fields the 分镜 panel reads.

    ``sources`` is the parallel list from ``assemble_steps``. Shot dicts
    passed in are not modified.
    """
    work_dir = Path(work_dir)
    steps = (load_run_state(work_dir).get("steps") or {})
    leases = _video_leases(work_dir)
    rows = []
    shots = list(shots or [])
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        row["images"] = list(shot.get("images") or [])
        row["end_image"] = str(shot.get("end_image") or "")
        row["images_source"] = _image_source(sources, index)
        row["feed"] = feed_fields(shot.get("prompt") or "")
        written = str(shot.get("video_mode") or "").strip()
        step = _video_step(steps, row.get("id"))
        value = _resolved_mode(row, step, work_dir, index, shots)
        row["video_mode_resolved"] = {
            "value": value,
            "inferred": written == "",
            "written": written,
        }
        row["first_frame_source"] = str(shot.get("first_frame_source") or "none")
        row["reference"] = _reference_items(
            row,
            value,
            index,
            shots,
            steps,
            leases,
            work_dir,
            project_id,
        )
        row["first_frame"] = _first_frame_item(
            row,
            value,
            index,
            shots,
            steps,
            work_dir,
            project_id,
        )
        rows.append(row)
    return rows


def feed_fields(prompt):
    """Structured fields parsed from one packed unit. Missing pieces stay empty."""
    text = str(prompt or "")
    heading = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if UNIT_HEAD_ANY.match(stripped):
            heading = stripped
        break
    title = ""
    unit = ""
    index = None
    total = None
    if heading:
        title = heading.lstrip("#").strip()
        claim = UNIT_CLAIM.match(heading)
        if claim:
            index = int(claim.group(2))
            total = int(claim.group(3))
            unit = "%s/%s" % (index, total)
    fields = _h3_fields(text) or {}
    return {
        "heading": heading,
        "title": title,
        "unit": unit,
        "index": index,
        "total": total,
        "subject_definitions": str(fields.get("subject_definitions") or ""),
        "detailed_description": str(fields.get("detailed_description") or ""),
        "overall_soundscape": str(fields.get("overall_soundscape") or ""),
        "non_diegetic_music": str(fields.get("non_diegetic_music") or ""),
    }


def _image_source(sources, index):
    source = sources[index] if index < len(sources or []) else None
    if not isinstance(source, dict):
        source = {}
    return {
        "value": str(source.get("value") or "storyboard"),
        "index": source.get("index", None),
    }


def _video_step(steps, shot_id):
    step = (steps or {}).get(shot_id) or {}
    if not isinstance(step, dict):
        return {}
    if str(step.get("kind") or "video") != "video":
        return {}
    return step


def _stored_video_mode(step):
    """Mode a completed video step already stored. Empty when it did not."""
    if not step or step.get("unaccepted"):
        return ""
    if step.get("status") != "done":
        return ""
    mode = str(step.get("video_mode") or "").strip()
    if mode not in VIDEO_MODES:
        return ""
    return mode


def _resolved_mode(shot, step, work_dir, index, shots):
    stored = _stored_video_mode(step)
    if stored:
        return stored
    continues = _previous_ready(work_dir, shots, index)
    from end.script_review import user_image_ids

    try:
        return choose_video_mode(
            shot,
            user_image_ids=user_image_ids(work_dir, shot),
            continues=continues,
        )
    except RuntimeError:
        written = str((shot or {}).get("video_mode") or "").strip()
        return written or "text"


def _previous_ready(work_dir, shots, index):
    if index <= 0:
        return False
    previous = shots[index - 1] or {}
    return _clip_ready(work_dir, previous.get("id"))


def _clip_ready(work_dir, shot_id):
    """True when submit would already have this clip as the previous file.

    Status has to be done and the clip file has to be on disk. A missing
    file cannot yield a tail, and this does not create one.
    """
    shot_id = str(shot_id or "")
    if not shot_id:
        return False
    steps = (load_run_state(work_dir).get("steps") or {})
    step = _video_step(steps, shot_id)
    if not step or step.get("unaccepted") or step.get("status") != "done":
        return False
    path = _output_file(work_dir, step.get("output_file") or "")
    if path is None or not path.is_file():
        path, _relative = output_paths(work_dir, shot_id, "video")
    try:
        return bool(path.is_file() and path.stat().st_size > 0)
    except OSError:
        return False


def _video_leases(work_dir):
    from end.script_review import load_reviews

    state = load_reviews(work_dir)
    leases = state.get("submit_leases") if isinstance(state, dict) else {}
    return leases if isinstance(leases, dict) else {}


def _recorded_has_tail(step, lease):
    """Whether this completed submit already had a continuation URL.

    The lease records ``has_tail`` at authorize time. It does not record the
    URL list. None means there is no such record.
    """
    if not step or step.get("status") != "done":
        return None
    if not isinstance(lease, dict) or "has_tail" not in lease:
        return None
    return bool(lease.get("has_tail"))


def _recorded_reference(step):
    """Submitted reference list, when a completed step actually stored one.

    The current submit path stores ``video_mode`` and the lease's ``has_tail``.
    It does not store the media URLs. This honors a list if one is present.
    """
    if not step or step.get("status") != "done":
        return None
    raw = None
    for key in ("submitted_media", "reference_media"):
        if key in step and isinstance(step.get(key), list):
            raw = step.get(key)
            break
    if raw is None:
        return None
    items = []
    for item in raw:
        coerced = _coerce_recorded(item)
        if coerced is not None:
            items.append(coerced)
    return items


def _coerce_recorded(item):
    if isinstance(item, str):
        url = item.strip() or None
        return _ref_item("", url, "bound_image", pending=False)
    if not isinstance(item, dict):
        return None
    role = str(item.get("role") or "")
    if role in ("first_frame", "last_frame"):
        return None
    source = str(item.get("source") or "").strip()
    if not source:
        if role == "previous_tail" or str(item.get("id") or "").endswith("_tail"):
            source = "previous_tail"
        else:
            source = "bound_image"
    url = item.get("url")
    if url is None:
        url = None
    else:
        url = str(url).strip() or None
    if "pending" in item:
        pending = bool(item.get("pending"))
    else:
        pending = source == "previous_tail" and not url
    return _ref_item(item.get("id") or "", url, source, pending=pending)


def _ref_item(item_id, url, source, pending):
    return {
        "id": str(item_id or ""),
        "url": url or None,
        "source": source,
        "pending": bool(pending),
        "available": bool(url),
    }


def _reference_items(shot, mode, index, shots, steps, leases, work_dir, project_id):
    del index, shots, leases, work_dir, project_id
    if mode != "reference":
        return []
    step = _video_step(steps, shot.get("id"))
    recorded = _recorded_reference(step)
    if recorded is not None:
        return recorded
    images = list(shot.get("images") or [])[:H3_MAX_REFERENCE_IMAGES]
    items = []
    for asset_id in images:
        url = _stored_image_url(steps, asset_id)
        items.append(_ref_item(asset_id, url, "bound_image", pending=False))
    return items


def _first_frame_item(shot, mode, index, shots, steps, work_dir, project_id):
    """Where the first frame comes from. Reference mode does not borrow a tail."""
    if mode not in ("first_frame", "first_last_frame"):
        return None
    source = str(shot.get("first_frame_source") or "").strip() or "none"
    if source == "previous_tail":
        url = None
        if index > 0 and _previous_ready(work_dir, shots, index):
            url = _existing_tail_url(work_dir, project_id, shot.get("id"))
        pending = url is None
        stem, _filename = continuation_tail_name(shot.get("id"))
        return _ref_item(stem + "_tail", url, "previous_tail", pending=pending)
    if source != "none":
        return _ref_item(source, _stored_image_url(steps, source), "designed_still", pending=False)
    return _ref_item("", None, "none", pending=False)


def _stored_image_url(steps, asset_id):
    step = (steps or {}).get(asset_id) or {}
    if not isinstance(step, dict) or step.get("kind") == "video":
        return None
    return _http_media_url(step.get("public_url")) or None


def _existing_tail_url(work_dir, project_id, shot_id):
    """Public URL for a tail file that is already on disk. Never creates one."""
    _stem, filename = continuation_tail_name(shot_id)
    path = Path(work_dir) / "outputs" / "images" / filename
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    kind = (os.environ.get("AIVIDEO_IMAGE_STORE") or "local").strip().lower() or "local"
    if kind == "local":
        from end.image_store import ImageStoreError, LocalImageStore

        try:
            return LocalImageStore().url(project_id, filename)
        except (ImageStoreError, OSError):
            pass
    try:
        stamp = int(path.stat().st_mtime)
    except OSError:
        stamp = 0
    return "/media/projects/%s/images/%s?v=%s" % (project_id, filename, stamp)
