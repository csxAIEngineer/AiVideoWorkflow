"""A product upload references the prop sheet. It does not grow a person still."""

import tempfile
import unittest
from pathlib import Path

from AI.skills.registry import json_shape
from end.gates import invented_upload_ids, invented_upload_claim
from end.image_node import prepare_image_node
from end.intake import KEEP_PERSON, KEEP_PRODUCT, enrich_brief, merge_intake, save_intake
from end.stage_runner import _script_suite_brief, check_stage

PRODUCT = (
    "**主体种类：**\n"
    "药品包装盒（非处方药）。\n\n"
    "**外观：**\n"
    "浅紫色盒子，白色花朵，OTC，萘普生钠胶囊。\n\n"
    "**场景：**\n"
    "纯白背景，没有任何人物。建议面向女性消费者。\n"
)
PERSON = "**主体种类：**\n一位年轻女性的半身肖像。\n"
PILL = {
    "id": "U1",
    "title": "痛经胶囊",
    "narration": "",
    "reading": PRODUCT,
}


def _assets():
    return [
        {
            "id": "图3",
            "title": "女主角色设定板",
            "prompt": "女主四格设定",
            "references": [],
            "subject": "女主",
            "subject_kind": "character",
            "view": "single",
        },
        {
            "id": "图4",
            "title": "男主角色设定板",
            "prompt": "男主四格设定",
            "references": [],
            "subject": "男主",
            "subject_kind": "character",
            "view": "single",
        },
        {
            "id": "图6",
            "title": "毓婷萘普生钠胶囊道具设定图",
            "prompt": "浅紫色药品包装，萘普生钠胶囊，OTC。禁止人物。",
            "references": [],
            "subject": "毓婷萘普生钠胶囊",
            "subject_kind": "prop",
            "view": "single",
        },
    ]


class UploadBindingTest(unittest.TestCase):
    def test_product_reading_binds_the_prop_not_a_person_still(self):
        spec = merge_intake(
            {
                "assets": _assets(),
                "shots": [
                    {"id": "S03", "prompt": "她服药", "images": ["图3", "图6", "图4"]},
                ],
            },
            [PILL],
        )
        ids = [asset["id"] for asset in spec["assets"]]
        self.assertNotIn("A1", ids)
        by_id = {asset["id"]: asset for asset in spec["assets"]}
        self.assertEqual(by_id["图6"]["references"], ["U1"])
        self.assertEqual(by_id["图3"]["references"], [])
        self.assertEqual(by_id["图4"]["references"], [])
        self.assertEqual(by_id["图6"]["prompt"], "浅紫色药品包装，萘普生钠胶囊，OTC。禁止人物。")
        self.assertNotIn(KEEP_PERSON, [asset["prompt"] for asset in spec["assets"]])
        self.assertEqual(spec["shots"][0]["images"], ["图3", "图6", "图4"])

    def test_orphan_person_prompt_is_removed_when_the_prop_exists(self):
        spec = merge_intake(
            {
                "assets": _assets()
                + [
                    {
                        "id": "A1",
                        "title": "痛经胶囊",
                        "prompt": KEEP_PERSON,
                        "references": ["U1"],
                        "subject": "痛经胶囊",
                        "subject_kind": "other",
                        "view": "single",
                    }
                ],
                "shots": [{"id": "S03", "prompt": "她服药", "images": ["图3", "图6", "A1"]}],
            },
            [PILL],
        )
        self.assertNotIn("A1", [asset["id"] for asset in spec["assets"]])
        prop = next(asset for asset in spec["assets"] if asset["id"] == "图6")
        self.assertEqual(prop["references"], ["U1"])
        self.assertEqual(spec["shots"][0]["images"], ["图3", "图6"])
        prepared = prepare_image_node(spec, items=[PILL], policy="drama_still")
        self.assertNotIn("A1", [asset["id"] for asset in prepared["assets"]])
        again = next(asset for asset in prepared["assets"] if asset["id"] == "图6")
        self.assertEqual(again["references"], ["U1"])
        self.assertEqual(again["view"], "single")

    def test_woman_reading_binds_the_female_lead(self):
        spec = merge_intake(
            {"assets": _assets(), "shots": []},
            [{"id": "U1", "title": "女主", "narration": "", "reading": PERSON}],
        )
        by_id = {asset["id"]: asset for asset in spec["assets"]}
        self.assertEqual(by_id["图3"]["references"], ["U1"])
        self.assertEqual(by_id["图4"]["references"], [])
        self.assertEqual(by_id["图6"]["references"], [])
        self.assertNotIn("A1", by_id)

    def test_a_product_reference_on_the_lead_moves_to_the_prop(self):
        assets = _assets()
        assets[0]["references"] = ["U1"]
        spec = merge_intake({"assets": assets, "shots": []}, [PILL])
        by_id = {asset["id"]: asset for asset in spec["assets"]}
        self.assertEqual(by_id["图6"]["references"], ["U1"])
        self.assertEqual(by_id["图3"]["references"], [])
        self.assertNotIn(KEEP_PERSON, [asset["prompt"] for asset in spec["assets"]])

    def test_product_without_a_matching_prop_does_not_use_the_person_prompt(self):
        spec = merge_intake(
            {
                "assets": [
                    {
                        "id": "图9",
                        "title": "宝剑",
                        "prompt": "一把金属长剑",
                        "subject": "宝剑",
                        "subject_kind": "prop",
                        "view": "single",
                    }
                ],
                "shots": [],
            },
            [PILL],
        )
        sword = next(asset for asset in spec["assets"] if asset["id"] == "图9")
        self.assertEqual(sword["references"], [])
        spawned = next(asset for asset in spec["assets"] if asset["id"] != "图9")
        self.assertEqual(spawned["references"], ["U1"])
        self.assertEqual(spawned["prompt"], KEEP_PRODUCT)
        self.assertEqual(spawned["subject_kind"], "prop")
        self.assertNotEqual(spawned["prompt"], KEEP_PERSON)

    def test_unread_upload_still_uses_the_person_default(self):
        spec = merge_intake(
            {"assets": [], "shots": []},
            [{"id": "U1", "title": "车站", "narration": ""}],
        )
        self.assertEqual(spec["assets"][0]["prompt"], KEEP_PERSON)
        self.assertEqual(spec["assets"][0]["references"], ["U1"])

    def test_narration_still_wins_over_the_product_default(self):
        spec = merge_intake(
            {"assets": [], "shots": []},
            [{"id": "U1", "title": "痛经胶囊", "narration": "原句旁白", "reading": PRODUCT}],
        )
        self.assertEqual(spec["assets"][0]["prompt"], "原句旁白")
        self.assertNotEqual(spec["assets"][0]["prompt"], KEEP_PERSON)

    def test_writer_is_told_not_to_invent_an_upload(self):
        brief = enrich_brief("做一条广告", [PILL])
        self.assertIn("不要写成 U0", brief)
        self.assertIn("不要写进角色图", brief)
        self.assertIn("药品包装盒", brief)
        shape = json_shape()
        self.assertIn("不要另造和上传图同名的资产", shape)
        self.assertIn("保持这张图里的人和场景", shape)
        folder = Path(tempfile.mkdtemp())
        save_intake(folder, [PILL])
        script_brief = _script_suite_brief({"brief": "做一条广告"}, folder, {}, "", {})
        self.assertIn("不要写成 U0", script_brief)
        self.assertIn("药品包装盒", script_brief)
        self.assertIn("U1", script_brief)

    def test_script_gate_rejects_an_invented_upload_id(self):
        self.assertEqual(invented_upload_ids("男女主照片 U0、痛经胶囊产品图 U1", [PILL]), ["U0"])
        self.assertEqual(invented_upload_ids("镜头 U01 开场", [PILL]), [])
        self.assertIn("U0", invented_upload_claim("基于上传照片 U0", [PILL]))
        folder = Path(tempfile.mkdtemp())
        save_intake(folder, [PILL])
        internal = folder / "_内部_"
        internal.mkdir()
        script = internal / "剧本.md"
        script.write_text(
            "素材形态：M2（已入库：男女主照片 U0、痛经胶囊产品图 U1）\n",
            encoding="utf-8",
        )
        ok, report = check_stage(folder, "script", {"material_mode": "M2"})
        self.assertFalse(ok)
        self.assertIn("写了没有上传的图 U0", report)
        self.assertNotIn("写了没有上传的图 U0、U1", report)
        script.write_text("女主服下胶囊。包装见 U1。\n", encoding="utf-8")
        ok, report = check_stage(folder, "script", {"material_mode": "M2"})
        self.assertTrue(ok, report)


if __name__ == "__main__":
    unittest.main()
