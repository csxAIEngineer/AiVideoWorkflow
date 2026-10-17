"""Image-Node owns every still. Shots only reuse those stills."""

import unittest
from pathlib import Path

from end.image_node import prepare_image_node, video_nodes
from end.intake import enrich_brief
from end.pipeline import JSON_SHAPE, load_skill_pack

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = "所有视频用到的图片都要先经过Image-Node处理/生成"


def views_of(spec, subject):
    found = {}
    for asset in spec["assets"]:
        if asset.get("subject") == subject and asset.get("view") in ("front", "side", "back"):
            found[asset["view"]] = asset
    return found


class ImageNodeTests(unittest.TestCase):
    def test_skill_requires_the_image_node(self):
        text = (ROOT / "skills" / "image-node" / "luxury-suv" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(REQUIRED, text)
        self.assertIn("上传图只是参考", text)
        self.assertIn("references", text)
        pack = load_skill_pack()
        self.assertIn(REQUIRED, pack)
        self.assertIn("上传图只是参考", pack)
        self.assertIn("Image-Node", JSON_SHAPE)
        self.assertIn("front", JSON_SHAPE)
        self.assertIn("side", JSON_SHAPE)
        self.assertIn("back", JSON_SHAPE)
        self.assertIn("references", JSON_SHAPE)
        self.assertIn("上传图只是参考", JSON_SHAPE)
        brief = enrich_brief("雨夜", [{"id": "U1", "title": "车站", "narration": "她在等"}])
        self.assertIn("上传图只是参考", brief)
        self.assertIn("U1", brief)

    def test_character_and_prop_gain_three_views(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {"id": "P1", "title": "程屿", "prompt": "三十二岁男人，黑框眼镜", "subject": "程屿", "subject_kind": "character"},
                    {"id": "Umb", "title": "黑伞", "prompt": "哑光黑伞", "subject": "黑伞", "subject_kind": "prop"},
                    {"id": "Bridge", "title": "南桥空镜", "prompt": "廊桥雨夜，无人物"},
                ],
                "shots": [
                    {"id": "S01", "prompt": "她接过伞", "images": ["P1", "Umb", "没有这张"]},
                ],
            }
        )
        people = views_of(spec, "程屿")
        props = views_of(spec, "黑伞")
        self.assertEqual(set(people), {"front", "side", "back"})
        self.assertEqual(set(props), {"front", "side", "back"})
        self.assertEqual(people["front"]["id"], "P1")
        self.assertEqual(people["side"]["depends_on"], "P1")
        self.assertEqual(people["back"]["depends_on"], "P1")
        self.assertNotIn("拼侧面", people["front"]["prompt"])
        self.assertIn("左侧面", people["side"]["prompt"])
        bridge = next(asset for asset in spec["assets"] if asset["id"] == "Bridge")
        self.assertEqual(bridge["view"], "single")
        self.assertEqual(bridge["subject_kind"], "location")
        self.assertEqual(spec["shots"][0]["images"], ["P1", "Umb"])
        again = prepare_image_node(spec)
        self.assertEqual(
            [asset["id"] for asset in again["assets"]],
            [asset["id"] for asset in spec["assets"]],
        )

    def test_variant_stays_single_and_upload_is_a_front_reference(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {"id": "P1", "title": "主角", "prompt": "女人站在车站", "subject": "苏晚", "subject_kind": "character", "view": "front"},
                    {"id": "P1s", "title": "主角侧面", "prompt": "女人左侧面", "subject": "苏晚", "subject_kind": "character", "view": "side"},
                    {"id": "P1b", "title": "主角背面", "prompt": "女人背面", "subject": "苏晚", "subject_kind": "character", "view": "back"},
                    {"id": "P2", "title": "雨夜", "prompt": "同一个人淋雨", "depends_on": "P1", "subject": "苏晚", "subject_kind": "character"},
                    {"id": "U1", "title": "车站", "prompt": "用户上传的图", "subject_kind": "character"},
                ],
                "shots": [{"id": "S01", "prompt": "离开", "images": ["P2", "U1"]}],
            },
            locked_ids=["U1"],
        )
        ids = [asset["id"] for asset in spec["assets"]]
        self.assertNotIn("U1", ids)
        self.assertIn("P1", ids)
        self.assertIn("P2", ids)
        rain = next(asset for asset in spec["assets"] if asset["id"] == "P2")
        self.assertEqual(rain["view"], "single")
        self.assertEqual(rain["depends_on"], "P1")
        station = views_of(spec, "车站")
        self.assertEqual(set(station), {"front", "side", "back"})
        self.assertIn("U1", station["front"]["references"])
        self.assertEqual(station["side"]["depends_on"], station["front"]["id"])
        self.assertEqual(station["back"]["depends_on"], station["front"]["id"])
        self.assertEqual(station["side"]["references"], [])
        self.assertEqual(station["back"]["references"], [])
        self.assertEqual(spec["shots"][0]["images"], ["P2", station["front"]["id"]])
        owned = {asset["id"] for asset in next(node["assets"] for node in video_nodes(spec) if node["kind"] == "image")}
        self.assertNotIn("U1", owned)
        self.assertIn("U1", next(asset["references"] for asset in spec["assets"] if asset["id"] == station["front"]["id"]))

    def test_upload_depends_on_becomes_references_on_the_front(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {
                        "id": "P1",
                        "title": "程屿",
                        "prompt": "男人正面",
                        "depends_on": "U1",
                        "subject": "程屿",
                        "subject_kind": "character",
                        "view": "front",
                    },
                    {
                        "id": "P1s",
                        "title": "程屿侧面",
                        "prompt": "男人侧面",
                        "depends_on": "P1",
                        "references": ["U1"],
                        "subject": "程屿",
                        "subject_kind": "character",
                        "view": "side",
                    },
                ],
                "shots": [{"id": "S01", "prompt": "他走", "images": ["U1", "P1"]}],
            },
            locked_ids=["U1"],
        )
        ids = [asset["id"] for asset in spec["assets"]]
        self.assertNotIn("U1", ids)
        front = next(asset for asset in spec["assets"] if asset["id"] == "P1")
        side = next(asset for asset in spec["assets"] if asset["view"] == "side")
        self.assertEqual(front["depends_on"], "")
        self.assertIn("U1", front["references"])
        self.assertEqual(side["depends_on"], "P1")
        self.assertEqual(side["references"], [])
        self.assertEqual(spec["shots"][0]["images"], ["P1"])
        self.assertNotIn("U1", spec["shots"][0]["images"])

    def test_missing_upload_grows_a_turnaround_that_owns_the_reference(self):
        spec = prepare_image_node(
            {"assets": [], "shots": []},
            items=[{"id": "U1", "title": "女主", "narration": "她推开门"}],
        )
        people = views_of(spec, "女主")
        self.assertEqual(set(people), {"front", "side", "back"})
        self.assertEqual(people["front"]["references"], ["U1"])
        self.assertEqual(people["side"]["depends_on"], people["front"]["id"])
        self.assertEqual(people["side"]["references"], [])
        self.assertEqual(people["back"]["references"], [])
        self.assertNotIn("U1", [asset["id"] for asset in spec["assets"]])
        self.assertEqual(spec["shots"][0]["images"], [people["front"]["id"]])
        self.assertEqual(spec["shots"][0]["narration"], "她推开门")
        nodes = video_nodes(spec)
        owned = next(node["assets"] for node in nodes if node["kind"] == "image")
        shown = next(asset for asset in owned if asset["id"] == people["front"]["id"])
        self.assertEqual(shown["references"], ["U1"])
        self.assertNotIn("U1", [asset["id"] for asset in owned])

    def test_video_is_nodes_and_shots_only_reuse_image_node(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {"id": "P1", "title": "程屿", "prompt": "男人", "subject": "程屿", "subject_kind": "character"},
                ],
                "shots": [
                    {"id": "S01", "prompt": "他撑伞", "images": ["P1"]},
                    {"id": "S02", "prompt": "他走远", "images": ["P1", "P1-back"]},
                ],
            }
        )
        nodes = video_nodes(spec)
        image_nodes = [node for node in nodes if node["kind"] == "image"]
        shot_nodes = [node for node in nodes if node["role"] == "shot"]
        self.assertEqual(len(image_nodes), 1)
        self.assertTrue(image_nodes[0]["required"])
        owned = {asset["id"] for asset in image_nodes[0]["assets"]}
        self.assertEqual(owned, {asset["id"] for asset in spec["assets"]})
        self.assertEqual([node["id"] for node in shot_nodes], ["S01", "S02"])
        for node in shot_nodes:
            self.assertTrue(set(node["images"]) <= owned)
            self.assertEqual(len(node["assets"]), len(node["images"]))
        self.assertEqual([node["id"] for node in nodes if node["role"] == "function"], ["script", "storyboard", "image", "join", "subtitles", "film"])
        back = next(asset for asset in spec["assets"] if asset["view"] == "back")
        self.assertIn(back["id"], shot_nodes[1]["images"])

    def test_vehicle_policy_still_fills_front_side_and_back(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {"id": "Car", "title": "SUV", "prompt": "银色 SUV", "subject": "SUV", "subject_kind": "prop"},
                    {"id": "Road", "title": "山路", "prompt": "湿沥青", "subject": "山路", "subject_kind": "location"},
                ],
                "shots": [],
            },
            policy="vehicle_turnaround",
        )
        self.assertEqual(set(views_of(spec, "SUV")), {"front", "side", "back"})
        road = next(asset for asset in spec["assets"] if asset["id"] == "Road")
        self.assertEqual(road["view"], "single")
        self.assertEqual(road["subject_kind"], "location")

    def test_product_hero_fills_one_product_and_leaves_other_props(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {
                        "id": "H",
                        "title": "耳机",
                        "prompt": "银色耳机",
                        "subject": "耳机",
                        "subject_kind": "prop",
                        "view": "hero",
                        "references": ["U1"],
                    },
                    {
                        "id": "M",
                        "title": "耳机微距",
                        "prompt": "网罩细节",
                        "subject": "耳机",
                        "subject_kind": "prop",
                        "view": "macro",
                        "references": ["U1"],
                    },
                    {
                        "id": "Card",
                        "title": "说明书",
                        "prompt": "一张说明书",
                        "subject": "说明书",
                        "subject_kind": "prop",
                    },
                    {
                        "id": "Desk",
                        "title": "桌面",
                        "prompt": "浅灰桌面",
                        "subject": "桌面",
                        "subject_kind": "location",
                    },
                    {
                        "id": "Hand",
                        "title": "手",
                        "prompt": "一只手",
                        "subject": "手",
                        "subject_kind": "character",
                    },
                ],
                "shots": [{"id": "S01", "prompt": "停在微距", "images": ["H", "U1"]}],
            },
            locked_ids=["U1"],
            policy="product_hero",
        )
        product = {}
        for asset in spec["assets"]:
            if asset.get("subject") == "耳机":
                product[asset["view"]] = asset
        self.assertEqual(set(product), {"hero", "three_quarter", "macro"})
        self.assertEqual(product["hero"]["id"], "H")
        self.assertEqual(product["hero"]["depends_on"], "")
        self.assertEqual(product["macro"]["depends_on"], "H")
        self.assertEqual(product["three_quarter"]["depends_on"], "")
        self.assertIn("U1", product["hero"]["references"])
        self.assertEqual(product["macro"]["references"], [])
        self.assertEqual(product["three_quarter"]["references"], [])
        self.assertNotIn("U1", [asset["id"] for asset in spec["assets"]])
        self.assertEqual(spec["shots"][0]["images"], ["H"])
        card = [asset for asset in spec["assets"] if asset["subject"] == "说明书"]
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]["view"], "single")
        self.assertEqual(views_of(spec, "说明书"), {})
        self.assertEqual(views_of(spec, "手"), {})
        desk = next(asset for asset in spec["assets"] if asset["id"] == "Desk")
        self.assertEqual(desk["view"], "single")
        self.assertEqual(desk["subject_kind"], "location")
        again = prepare_image_node(spec, policy="product_hero")
        self.assertEqual(
            [asset["id"] for asset in again["assets"]],
            [asset["id"] for asset in spec["assets"]],
        )

    def test_product_hero_accepts_a_macro_alias_and_builds_the_hero(self):
        spec = prepare_image_node(
            {
                "assets": [
                    {
                        "id": "D1",
                        "title": "耳机",
                        "prompt": "银色网罩",
                        "subject": "耳机",
                        "subject_kind": "prop",
                        "view": "微距",
                    }
                ],
                "shots": [],
            },
            policy="product_hero",
        )
        views = {asset["view"] for asset in spec["assets"]}
        self.assertEqual(views, {"hero", "three_quarter", "macro"})
        hero = next(asset for asset in spec["assets"] if asset["view"] == "hero")
        macro = next(asset for asset in spec["assets"] if asset["view"] == "macro")
        self.assertEqual(macro["id"], "D1")
        self.assertEqual(macro["depends_on"], hero["id"])
        self.assertEqual(hero["depends_on"], "")


if __name__ == "__main__":
    unittest.main()
