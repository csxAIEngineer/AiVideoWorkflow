"""Turn a DeepSeek spec into a ComfyUI graph: Nano Banana stills into MiniMax clips, then one joined video."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.build_workflows import gemini_node, save_image_node  # noqa: E402


def _clamp_duration(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 5
    return max(4, min(15, seconds))


def _safe_id(value, fallback):
    text = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or ""))
    return text[:40] or fallback


def build_workflow(spec):
    assets = []
    for index, item in enumerate(spec.get("assets") or []):
        assets.append(
            {
                "id": _safe_id(item.get("id"), "A%s" % (index + 1)),
                "title": str(item.get("title") or "资产"),
                "prompt": str(item.get("prompt") or "").strip(),
                "depends_on": _safe_id(item.get("depends_on"), "") if item.get("depends_on") else "",
            }
        )
    assets = [item for item in assets if item["prompt"]][:8]
    known = {item["id"] for item in assets}
    for item in assets:
        if item["depends_on"] not in known or item["depends_on"] == item["id"]:
            item["depends_on"] = ""

    shots = []
    for index, item in enumerate(spec.get("shots") or []):
        images = [_safe_id(name, "") for name in (item.get("images") or [])]
        images = [name for name in images if name in known][:4]
        if not images and assets:
            images = [assets[0]["id"]]
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        shots.append(
            {
                "id": _safe_id(item.get("id"), "S%02d" % (index + 1)),
                "duration": _clamp_duration(item.get("duration")),
                "prompt": prompt,
                "images": images,
            }
        )
    shots = shots[:6]
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
        nid = 200 + index * 2
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
                "label": "video_%s" % (index + 1),
                "name": "videos.video_%s" % (index + 1),
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
    return {
        "id": "aivideo-project",
        "revision": 0,
        "last_node_id": max(node["id"] for node in nodes),
        "last_link_id": link_id - 1,
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"frontendVersion": "1.47.11", "ds": {"scale": 0.55, "offset": [0, 0]}},
        "version": 0.4,
    }
