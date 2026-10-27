"""ComfyUI graphs for the web site: downloadable full graph, plus split cloud jobs."""

import re
from pathlib import Path

from end.build_workflows import gemini_node, save_image_node

from end.soundtrack import normalize_soundtrack

MIN_SECONDS = 4
MAX_SECONDS = 15
# One H3 call accepts 9 reference images. The previous shot's tail frame uses one slot.
MAX_SHOT_REFERENCES = 8
CHARS_PER_SECOND = 4.1


def _spoken_chars(text):
    return len(re.sub(r"[\s，。！？、…—\-：:（）()「」\"“”]", "", text or ""))


def _clamp_duration(value, dialogue=""):
    """Keep a beat's own length. The generator only accepts 4–15 for one call."""
    if value not in (None, ""):
        try:
            return max(MIN_SECONDS, min(MAX_SECONDS, int(value)))
        except (TypeError, ValueError):
            pass
    chars = _spoken_chars(dialogue)
    if chars:
        needed = int(round(chars / CHARS_PER_SECOND)) + 1
        return max(MIN_SECONDS, min(MAX_SECONDS, needed))
    return MIN_SECONDS


def _safe_id(value, fallback):
    text = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or ""))
    return text[:40] or fallback


def _is_upload_id(value):
    """Opening uploads are U1, U2, … They are references, not generated stills."""
    return bool(re.fullmatch(r"U\d+", str(value or "")))


def _reference_ids(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    refs = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("id")
        text = _safe_id(item, "")
        if text and text not in refs:
            refs.append(text)
    return refs


def collect_steps(spec):
    """Assets and shots that actually become nodes, in run order.

    When a delivery feed or 图 blocks exist, those texts replace the short
    asset line and the storyboard-column shot line for this read.
    """
    from end.prompt_handoff import prepare_delivery_spec

    spec = prepare_delivery_spec(spec)
    assets = []
    pending_refs = []
    for index, item in enumerate(spec.get("assets") or []):
        asset = {
            "id": _safe_id(item.get("id"), "A%s" % (index + 1)),
            "title": str(item.get("title") or "资产"),
            "prompt": str(item.get("prompt") or "").strip(),
            "depends_on": _safe_id(item.get("depends_on"), "") if item.get("depends_on") else "",
        }
        for key in ("subject", "subject_kind", "view"):
            text = str(item.get(key) or "").strip()
            if text:
                asset[key] = text
        assets.append(asset)
        pending_refs.append(_reference_ids(item.get("references")))
    kept = [
        (item, refs)
        for item, refs in zip(assets, pending_refs)
        if item["prompt"] and not _is_upload_id(item["id"])
    ]
    assets = [item for item, _refs in kept]
    pending_refs = [refs for _item, refs in kept]
    known = {item["id"] for item in assets}
    for item, refs in zip(assets, pending_refs):
        if item["depends_on"] not in known or item["depends_on"] == item["id"]:
            item["depends_on"] = ""
        cleaned = []
        for ref in refs:
            if ref and ref not in known and ref != item["id"] and ref not in cleaned:
                cleaned.append(ref)
        if cleaned:
            item["references"] = cleaned

    shots = []
    for index, item in enumerate(spec.get("shots") or []):
        if item.get("images") is None:
            images = [assets[0]["id"]] if assets else []
        else:
            images = [_safe_id(name, "") for name in (item.get("images") or [])]
            images = [name for name in images if name in known or _is_upload_id(name)][:MAX_SHOT_REFERENCES]
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        dialogue = str(item.get("dialogue") or "").strip()
        narration = str(item.get("narration") or "").strip()
        duration = _clamp_duration(item.get("duration"), dialogue)
        if narration:
            duration = max(duration, _clamp_duration(None, narration))
        shot = {
            "id": _safe_id(item.get("id"), "S%02d" % (index + 1)),
            "duration": duration,
            "prompt": prompt,
            "dialogue": dialogue,
            "narration": narration,
            "images": images,
        }
        end_image = _safe_id(item.get("end_image"), "") if item.get("end_image") else ""
        if end_image:
            shot["end_image"] = end_image
        video_mode = str(item.get("video_mode") or "").strip()
        if video_mode:
            shot["video_mode"] = video_mode
        if "tts_text" in item:
            shot["tts_text"] = str(item.get("tts_text") or "")
        shots.append(shot)
    return assets, shots


def ordered_assets(assets):
    """Parents before children so depends_on can load an already finished still."""
    remaining = list(assets)
    done = set()
    ordered = []
    while remaining:
        progress = False
        for item in list(remaining):
            parent = item["depends_on"]
            if parent and parent not in done:
                continue
            ordered.append(item)
            done.add(item["id"])
            remaining.remove(item)
            progress = True
        if not progress:
            ordered.extend(remaining)
            break
    return ordered


def task_plan(spec, soundtrack="off"):
    """Local render rows: Qwen stills, Wan clips, ffmpeg join, optional narration, then burn 字幕.ass."""
    assets, shots = collect_steps(spec)
    if not assets or not shots:
        return []
    tasks = []
    total = 0
    for asset in ordered_assets(assets):
        tasks.append(
            {
                "id": asset["id"],
                "kind": "image",
                "title": asset["title"],
                "duration": None,
                "state": "pending",
                "detail": "千问生图",
            }
        )
    for shot in shots:
        total += shot["duration"]
        tasks.append(
            {
                "id": shot["id"],
                "kind": "video",
                "title": shot["id"],
                "duration": shot["duration"],
                "state": "pending",
                "detail": "万相出镜",
            }
        )
    tasks.append(
        {
            "id": "join",
            "kind": "join",
            "title": "按镜头顺序拼接",
            "duration": total,
            "state": "pending",
            "detail": "本机拼接",
        }
    )
    mode = normalize_soundtrack(soundtrack)
    if mode != "off":
        tasks.append(
            {
                "id": "audio",
                "kind": "audio",
                "title": "声轨",
                "duration": total,
                "state": "pending",
                "detail": "千问旁白",
            }
        )
    tasks.append(
        {
            "id": "subtitles",
            "kind": "subtitles",
            "title": "烧录字幕",
            "duration": total,
            "state": "pending",
            "detail": "ffmpeg 烧录 字幕.ass",
        }
    )
    return tasks


def _shell(nodes, links, graph_id="aivideo"):
    return {
        "id": graph_id,
        "revision": 0,
        "last_node_id": max((node["id"] for node in nodes), default=1),
        "last_link_id": max((link[0] for link in links), default=0),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"frontendVersion": "1.47.11", "ds": {"scale": 0.7, "offset": [0, 0]}},
        "version": 0.4,
    }


def load_image_node(nid, title, filename, x, y):
    return {
        "id": nid,
        "type": "LoadImage",
        "pos": [x, y],
        "size": [320, 314],
        "flags": {},
        "order": nid,
        "mode": 0,
        "inputs": [],
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": []},
            {"name": "MASK", "type": "MASK", "links": None},
        ],
        "title": title,
        "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "LoadImage"},
        "widgets_values": [filename, "image"],
    }


def build_image_workflow(asset, reference_filename=None):
    """workflow0: exactly one Gemini still (+ optional LoadImage for depends_on)."""
    nodes = []
    links = []
    link_id = 1
    gen_id = 10
    save_id = 11
    y = 40
    image_link = None
    prompt = asset["prompt"]
    if reference_filename:
        load_id = 9
        nodes.append(load_image_node(load_id, "参考 " + (asset["depends_on"] or "图"), reference_filename, 40, y))
        image_link = link_id
        links.append([link_id, load_id, 0, gen_id, 0, "IMAGE"])
        nodes[0]["outputs"][0]["links"] = [link_id]
        link_id += 1
        prompt = "参考图是同一身份的定妆，必须锁定同一张脸和外形，不要换脸。\n" + prompt
        gen_x = 420
    else:
        gen_x = 40
    nodes.append(
        gemini_node(
            gen_id,
            "%s %s · workflow0" % (asset["id"], asset["title"]),
            prompt,
            gen_x,
            y,
            image_link,
        )
    )
    save_link = link_id
    links.append([save_link, gen_id, 0, save_id, 0, "IMAGE"])
    link_id += 1
    gen = next(node for node in nodes if node["id"] == gen_id)
    gen["outputs"][0]["links"] = list(gen["outputs"][0].get("links") or []) + [save_link]
    nodes.append(save_image_node(save_id, "保存 " + asset["id"], asset["id"], gen_x + 480, y, save_link))
    return _shell(nodes, links, "aivideo-workflow0-" + asset["id"])


def build_shot_workflow(shot, image_files):
    """workflow1: LoadImage refs + one MiniMax clip + SaveVideo."""
    nodes = []
    links = []
    link_id = 1
    inputs = []
    for slot, asset_id in enumerate(shot["images"]):
        filename = image_files.get(asset_id)
        if not filename:
            continue
        load_id = 20 + slot
        nodes.append(load_image_node(load_id, "加载 " + asset_id, filename, 40, 40 + slot * 360))
        lid = link_id
        links.append([lid, load_id, 0, 200, slot, "IMAGE"])
        load = next(node for node in nodes if node["id"] == load_id)
        load["outputs"][0]["links"] = [lid]
        link_id += 1
        inputs.append(
            {
                "label": "image_%s" % (slot + 1),
                "name": "model.reference_images.image_%s" % (slot + 1),
                "shape": 7,
                "type": "IMAGE",
                "link": lid,
            }
        )
    if not inputs:
        raise ValueError("镜头 %s 没有可用的参考图" % shot["id"])
    inputs.append({"label": "video_1", "name": "model.reference_videos.video_1", "shape": 7, "type": "VIDEO", "link": None})
    inputs.append({"label": "audio_1", "name": "model.reference_audios.audio_1", "shape": 7, "type": "AUDIO", "link": None})
    video_link = link_id
    links.append([video_link, 200, 0, 201, 0, "VIDEO"])
    link_id += 1
    nodes.append(
        {
            "id": 200,
            "type": "MinimaxHailuo03ReferenceNode",
            "pos": [520, 40],
            "size": [460, 520],
            "flags": {},
            "order": 200,
            "mode": 0,
            "inputs": inputs,
            "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [video_link]}],
            "title": "%s · MiniMax H3 · %s秒 · workflow1" % (shot["id"], shot["duration"]),
            "properties": {"Node name for S&R": "MinimaxHailuo03ReferenceNode"},
            "widgets_values": ["MiniMax H3", shot["prompt"], "768P", "9:16", shot["duration"], 42, "fixed", False],
            "color": "#432",
            "bgcolor": "#653",
        }
    )
    nodes.append(
        {
            "id": 201,
            "type": "SaveVideo",
            "pos": [1040, 40],
            "size": [400, 140],
            "flags": {},
            "order": 201,
            "mode": 0,
            "inputs": [{"name": "video", "type": "VIDEO", "link": video_link}],
            "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
            "title": "保存 " + shot["id"],
            "properties": {},
            "widgets_values": ["video/" + shot["id"], "auto", "auto"],
        }
    )
    return _shell(nodes, links, "aivideo-workflow1-" + shot["id"])


def build_join_workflow(clip_names):
    """Cloud fallback: LoadVideo clips into ConcatenateVideo + SaveVideo."""
    nodes = []
    links = []
    link_id = 1
    concat_inputs = []
    for index, name in enumerate(clip_names):
        load_id = 40 + index
        nodes.append(
            {
                "id": load_id,
                "type": "LoadVideo",
                "pos": [40, 40 + index * 220],
                "size": [320, 180],
                "flags": {},
                "order": load_id,
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": []}],
                "title": "加载 " + Path(name).stem,
                "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "LoadVideo"},
                "widgets_values": [name],
            }
        )
        lid = link_id
        links.append([lid, load_id, 0, 300, index, "VIDEO"])
        nodes[-1]["outputs"][0]["links"] = [lid]
        link_id += 1
        concat_inputs.append(
            {
                "label": "video%s" % index,
                "name": "videos.video%s" % index,
                "shape": 7,
                "type": "VIDEO",
                "link": lid,
            }
        )
    joined = link_id
    links.append([joined, 300, 0, 301, 0, "VIDEO"])
    link_id += 1
    nodes.append(
        {
            "id": 300,
            "type": "ConcatenateVideo",
            "pos": [520, 40],
            "size": [420, 480],
            "flags": {},
            "order": 300,
            "mode": 0,
            "inputs": concat_inputs,
            "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [joined]}],
            "title": "按镜头顺序拼接",
            "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "ConcatenateVideo"},
            "widgets_values": ["auto"],
            "color": "#432",
            "bgcolor": "#653",
        }
    )
    nodes.append(
        {
            "id": 301,
            "type": "SaveVideo",
            "pos": [1040, 40],
            "size": [420, 150],
            "flags": {},
            "order": 301,
            "mode": 0,
            "inputs": [{"name": "video", "type": "VIDEO", "link": joined}],
            "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
            "title": "保存整段",
            "properties": {},
            "widgets_values": ["video/joined", "auto", "auto"],
        }
    )
    return _shell(nodes, links, "aivideo-join")


def build_workflow(spec):
    """Downloadable review graph. Cloud runner uses split workflow0 / workflow1 instead."""
    assets, shots = collect_steps(spec)
    if not assets or not shots:
        raise ValueError("生成结果里没有可用的资产图或镜头")

    nodes = []
    links = []
    link_id = 1

    def add_link(origin, origin_slot, target, target_slot, link_type):
        nonlocal link_id
        current = link_id
        links.append([current, origin, origin_slot, target, target_slot, link_type])
        link_id += 1
        return current

    layout = {}
    for index, asset in enumerate(assets):
        layout[asset["id"]] = {"gen": 10 + index * 2, "save": 11 + index * 2, "y": 40 + index * 560}

    for asset in assets:
        place = layout[asset["id"]]
        kind = "参考 " + asset["depends_on"] if asset["depends_on"] else "文生图"
        nodes.append(
            gemini_node(
                place["gen"],
                "%s %s · %s" % (asset["id"], asset["title"], kind),
                asset["prompt"],
                40,
                place["y"],
                None,
            )
        )

    for asset in assets:
        if not asset["depends_on"]:
            continue
        place = layout[asset["id"]]
        parent = layout[asset["depends_on"]]
        ref = add_link(parent["gen"], 0, place["gen"], 0, "IMAGE")
        parent_node = next(node for node in nodes if node["id"] == parent["gen"])
        parent_node["outputs"][0]["links"].append(ref)
        gen = next(node for node in nodes if node["id"] == place["gen"])
        gen["inputs"] = [
            {
                "label": "image_1",
                "name": "model.images.image_1",
                "shape": 7,
                "type": "IMAGE",
                "link": ref,
            }
        ]

    for asset in assets:
        place = layout[asset["id"]]
        save_link = add_link(place["gen"], 0, place["save"], 0, "IMAGE")
        gen = next(node for node in nodes if node["id"] == place["gen"])
        gen["outputs"][0]["links"].append(save_link)
        nodes.append(save_image_node(place["save"], "保存 " + asset["id"], asset["id"], 520, place["y"], save_link))

    concat_inputs = []
    for index, shot in enumerate(shots):
        nid = 1000 + index * 2
        save_id = nid + 1
        inputs = []
        for slot, asset_id in enumerate(shot["images"]):
            origin = layout[asset_id]["gen"]
            lid = add_link(origin, 0, nid, slot, "IMAGE")
            origin_node = next(node for node in nodes if node["id"] == origin)
            origin_node["outputs"][0]["links"].append(lid)
            inputs.append(
                {
                    "label": "image_%s" % (slot + 1),
                    "name": "model.reference_images.image_%s" % (slot + 1),
                    "shape": 7,
                    "type": "IMAGE",
                    "link": lid,
                }
            )
        inputs.append({"label": "video_1", "name": "model.reference_videos.video_1", "shape": 7, "type": "VIDEO", "link": None})
        inputs.append({"label": "audio_1", "name": "model.reference_audios.audio_1", "shape": 7, "type": "AUDIO", "link": None})
        video_link = add_link(nid, 0, save_id, 0, "VIDEO")
        concat_link = add_link(nid, 0, 300, index, "VIDEO")
        y = 40 + index * 620
        nodes.append(
            {
                "id": nid,
                "type": "MinimaxHailuo03ReferenceNode",
                "pos": [1100, y],
                "size": [460, 520],
                "flags": {},
                "order": nid,
                "mode": 0,
                "inputs": inputs,
                "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [video_link, concat_link]}],
                "title": "%s · MiniMax H3 · %s秒" % (shot["id"], shot["duration"]),
                "properties": {"Node name for S&R": "MinimaxHailuo03ReferenceNode"},
                "widgets_values": ["MiniMax H3", shot["prompt"], "768P", "9:16", shot["duration"], 42, "fixed", False],
                "color": "#432",
                "bgcolor": "#653",
            }
        )
        nodes.append(
            {
                "id": save_id,
                "type": "SaveVideo",
                "pos": [1620, y],
                "size": [400, 140],
                "flags": {},
                "order": save_id,
                "mode": 0,
                "inputs": [{"name": "video", "type": "VIDEO", "link": video_link}],
                "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
                "title": "保存 " + shot["id"],
                "properties": {},
                "widgets_values": ["video/" + shot["id"], "auto", "auto"],
            }
        )
        concat_inputs.append(
            {
                "label": "video%s" % index,
                "name": "videos.video%s" % index,
                "shape": 7,
                "type": "VIDEO",
                "link": concat_link,
            }
        )

    joined = add_link(300, 0, 301, 0, "VIDEO")
    nodes.append(
        {
            "id": 300,
            "type": "ConcatenateVideo",
            "pos": [2100, 40],
            "size": [420, 480],
            "flags": {},
            "order": 300,
            "mode": 0,
            "inputs": concat_inputs,
            "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [joined]}],
            "title": "按镜头顺序拼接",
            "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "ConcatenateVideo"},
            "widgets_values": ["auto"],
            "color": "#432",
            "bgcolor": "#653",
        }
    )
    nodes.append(
        {
            "id": 301,
            "type": "SaveVideo",
            "pos": [2580, 40],
            "size": [420, 150],
            "flags": {},
            "order": 301,
            "mode": 0,
            "inputs": [{"name": "video", "type": "VIDEO", "link": joined}],
            "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
            "title": "保存整段",
            "properties": {},
            "widgets_values": ["video/joined", "auto", "auto"],
        }
    )
    for node in nodes:
        node["mode"] = 2
        node["title"] = str(node.get("title") or "") + " · 云端分批"
    nodes.insert(
        0,
        {
            "id": 1,
            "type": "Note",
            "pos": [40, -220],
            "size": [520, 180],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "云端运行方式",
            "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "Note"},
            "widgets_values": [
                "网站不会把整图一次提交。\n"
                "workflow0：每张资产图单独上传，跑完后稍等再跑下一张。\n"
                "workflow1：每镜视频单独上传，用已下载的图作参考。\n"
                "最后在本机拼接，再用 ffmpeg 把 字幕.ass 烧进成片。"
            ],
        },
    )
    return _shell(nodes, links, "aivideo-project")
