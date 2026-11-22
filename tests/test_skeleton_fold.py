"""Programmatic slot fold: specialist fills overwrite empty skeleton slots."""

import json
import unittest

from Agents.workers.script_skeleton import format_script_skeleton
from Agents.workers.skeleton_fold import fold_stage_draft
from Agents.workers.storyboard_skeleton import format_storyboard_skeleton


ASSET_SKELETON = """当前资产骨架如下。图号和格位是锁定槽，只读。
只填各格的外形、服装或场景描述。不要自增图号，不要改格位，不要另开一份完整资产板再合并。
标题、种类、格位、场景名称和参考场景锁定模式是只读锁定项。

#### 图1 · 沈棠（角色）
种类：角色
格位：四格
格1：【填写】
格2：【填写】
格3：【填写】
格4：【填写】

#### 图2 · 顾深（角色）
种类：角色
格位：四格
格1：【填写】
格2：【填写】
格3：【填写】
格4：【填写】

#### 图13 · 红毯（道具）
种类：道具
格位：四格
格1：【填写】
格2：【填写】
格3：【填写】
格4：【填写】

#### 图25 · 婚礼现场（母版）
种类：场景母版
格位：整板
场景名称：婚礼现场（资产图编号：图25）
整板固定 16:9。
整板：【填写】
"""

CHARACTER_NOTE = """#### 图1 · 沈棠（角色）
种类：角色
格位：四格
格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。
格2：【正面全身·无脸】白色A字拖尾婚纱。
格3：【侧面全身·无脸】侧身站立，展现婚纱背部。
格4：【背面全身·无脸】拖尾铺展。
"""

PROP_NOTE = """#### 图13 · 红毯（道具）
种类：道具
格位：四格
格1：【正面视图】鲜红色天鹅绒材质。
格2：【45°侧视图】展示红毯厚度与柔软度。
格3：【顶部俯视图】纹理均匀。
格4：【关键细节特写】角落有一朵白花脱落痕迹。
"""

SCENE_NOTE = json.dumps({
    "text": "",
    "image_prompts": [
        {
            "id": "图25",
            "name": "婚礼现场（母版）",
            "prompt": (
                "#### 图25 · 婚礼现场（母版）\n"
                "种类：场景母版\n"
                "格位：整板\n"
                "场景名称：婚礼现场（资产图编号：图25）\n"
                "整板固定 16:9。\n"
                "整板：【场景定位】高档酒店宴会厅入口及仪式区。"
            ),
        }
    ],
}, ensure_ascii=False)

SKILL_DUMP = json.dumps({
    "text": "## sd-asset-engine/SKILL.md\n\n你是**资产锁定阶段的专精引擎**。",
}, ensure_ascii=False)


def _asset_brief():
    return "任务：资产锁定\n\n" + ASSET_SKELETON


class SkeletonFoldTest(unittest.TestCase):
    def test_asset_fold_uses_specialist_fills_and_keeps_empty_slots(self):
        draft = fold_stage_draft("asset", _asset_brief(), [
            {"skill": "sd-asset-engine/SKILL.md", "text": SKILL_DUMP},
            {"skill": "sd-asset-engine/references/character-portrait-templates.md", "text": CHARACTER_NOTE},
            {"skill": "sd-asset-engine/references/prop-template.md", "text": PROP_NOTE},
            {"skill": "sd-asset-engine/references/scene-grid-template.md", "text": SCENE_NOTE},
        ])
        self.assertIn("格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。", draft)
        self.assertIn("格1：【正面视图】鲜红色天鹅绒材质。", draft)
        self.assertIn("整板：【场景定位】高档酒店宴会厅入口及仪式区。", draft)
        self.assertIn("#### 图2 · 顾深（角色）", draft)
        self.assertIn("#### 图1 · 沈棠（角色）", draft)
        # 图2 was not in any specialist note, so its slots stay empty.
        fig2 = draft.split("#### 图2 · 顾深（角色）", 1)[1].split("#### 图13", 1)[0]
        self.assertEqual(fig2.count("【填写】"), 4)
        self.assertNotIn("你是**资产锁定阶段的专精引擎**", draft)


    def test_asset_fold_uses_truncated_scene_json_and_keeps_unwritten_slots(self):
        skeleton = ASSET_SKELETON + """
#### 图26 · 婚礼现场（九宫格）
种类：九宫格
格位：九宫格
场景名称：婚礼现场（资产图编号：图26）
整板固定 16:9。
全图共 9 格，不多不少。
【参考场景锁定模式】仅允许摄影机移动。
格1：【填写】
格2：【填写】
格3：【填写】
格4：【填写】
格5：【填写】
格6：【填写】
格7：【填写】
格8：【填写】
格9：【填写】
"""
        complete_master = (
            "#### 图25 · 婚礼现场（母版）\n"
            "种类：场景母版\n"
            "格位：整板\n"
            "场景名称：婚礼现场（资产图编号：图25）\n"
            "整板固定 16:9。\n"
            "整板：【场景定位】高档酒店宴会厅入口及仪式区。"
        )
        partial_grid = (
            "#### 图26 · 婚礼现场（九宫格）\n"
            "种类：九宫格\n"
            "格位：九宫格\n"
            "场景名称：婚礼现场（资产图编号：图26）\n"
            "整板固定 16:9。\n"
            "全图共 9 格，不多不少。\n"
            "【参考场景锁定模式】仅允许摄影机移动。\n"
            "格1：【景别】宴会厅入口全景，红毯铺入仪式区。\n"
            "格2：【景别】主桌侧向中景，灯光低饱和。\n"
            "格3：【景别】舞台方向的"
        )
        opened = json.dumps(partial_grid, ensure_ascii=False)[:-1]
        truncated = (
            '{"text":"","image_prompts":['
            '{"id":"图25","prompt":%s},'
            '{"id":"图26","prompt":%s'
        ) % (json.dumps(complete_master, ensure_ascii=False), opened)
        self.assertRaises(json.JSONDecodeError, json.loads, truncated)
        draft = fold_stage_draft("asset", "任务：资产锁定\n\n" + skeleton, [
            {"skill": "sd-asset-engine/references/scene-grid-template.md", "text": truncated},
        ])
        self.assertIn("整板：【场景定位】高档酒店宴会厅入口及仪式区。", draft)
        fig26 = draft.split("#### 图26 · 婚礼现场（九宫格）", 1)[1]
        self.assertIn("格1：【景别】宴会厅入口全景，红毯铺入仪式区。", fig26)
        self.assertIn("格2：【景别】主桌侧向中景，灯光低饱和。", fig26)
        self.assertIn("格3：【景别】舞台方向的", fig26)
        self.assertEqual(fig26.count("【填写】"), 6)
        self.assertIn("格1：【填写】", draft.split("#### 图1 · 沈棠（角色）", 1)[1].split("#### 图2", 1)[0])

    def test_script_fold_uses_specialist_fills_and_keeps_empty_slots(self):
        brief = "\n".join((
            "任务：剧本",
            "",
            format_script_skeleton({
                "locks": {"duration_seconds": 30, "aspect": "16:9"},
                "scenes": [
                    {
                        "id": "EP001-SC01",
                        "heading": "EP001-SC01｜内·婚礼现场·日（0s - 15s）",
                        "cast": "",
                    },
                    {
                        "id": "EP001-SC02",
                        "heading": "EP001-SC02｜内·民政局大厅·上午（15s - 35s）",
                        "cast": "",
                    },
                ],
            }),
        ))
        notes = [{
            "skill": "sd-script-engine/SKILL.md",
            "text": "\n".join((
                "## EP001-SC01｜内·婚礼现场·日（0s - 15s）",
                "出场：沈棠、顾深",
                "情节：红毯边角一朵白花悄然脱落。",
                "对白：",
                "【无】",
                "情绪：压抑、疏离",
            )),
        }]
        draft = fold_stage_draft("script", brief, notes)
        self.assertIn("## EP001-SC01｜内·婚礼现场·日（0s - 15s）", draft)
        self.assertIn("出场：沈棠、顾深", draft)
        self.assertIn("情节：红毯边角一朵白花悄然脱落。", draft)
        self.assertIn("## EP001-SC02｜内·民政局大厅·上午（15s - 35s）", draft)
        sc02 = draft.split("## EP001-SC02｜内·民政局大厅·上午（15s - 35s）", 1)[1]
        self.assertIn("出场：【填写】", sc02)
        self.assertNotIn("沈棠、顾深", sc02)

    def test_storyboard_fold_uses_specialist_fills_and_keeps_empty_slots(self):
        skeleton = format_storyboard_skeleton({
            "duration_lock": 10,
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "assets": ["图1"],
                    "dialogue": "顾深：好。",
                    "action_beats": ["整理领结"],
                    "music_beat": "无",
                    "sound_beat": "心跳",
                    "scene_heading": "EP001-SC01｜内·婚礼现场·日（0s - 15s）",
                },
                {
                    "id": "S02",
                    "duration": 5,
                    "assets": ["图25"],
                    "dialogue": "无",
                    "scene_heading": "EP001-SC02｜内·民政局大厅·上午（15s - 35s）",
                },
            ],
        })
        brief = "任务：分镜\n\n" + skeleton
        notes = [{
            "skill": "sd-storyboard-engine/SKILL.md",
            "text": "\n".join((
                "## S01",
                "画面层：近景，顾深低头整理领结，手指发抖。",
                "旁白：无",
                "声景：心跳与婚礼进行曲叠在一起。",
            )),
        }]
        draft = fold_stage_draft("storyboard", brief, notes)
        self.assertIn("## S01", draft)
        self.assertIn("画面层：近景，顾深低头整理领结，手指发抖。", draft)
        self.assertIn("旁白：无", draft)
        self.assertIn("声景：心跳与婚礼进行曲叠在一起。", draft)
        self.assertIn("时长：5", draft)
        self.assertIn("对白：顾深：好。", draft)
        self.assertIn("## S02", draft)
        s02 = draft.split("## S02", 1)[1]
        self.assertIn("画面层：【填写】", s02)
        self.assertIn("旁白：【填写】", s02)
        self.assertNotIn("顾深低头整理领结", s02)


class SuiteFoldWiringTest(unittest.TestCase):
    def test_asset_suite_publishes_folded_draft_even_if_merge_copies_skeleton(self):
        from AI.qwen_api.suite_graph import run_suite

        brief = "\n".join((
            "阶段：P2",
            "赛道：1",
            "来源：1",
            "动作档：R2",
            "模型：h3",
            "素材形态：M1",
            "任务：资产锁定",
            "只写资产提示词，放在 image_prompts。每条以「#### 图N · 名称」开头。",
            "",
            ASSET_SKELETON.strip(),
        ))

        class AssetModel(object):
            def __init__(self):
                self.merge_called = False

            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    self.merge_called = True
                    return Reply(json.dumps({"image_prompts": ASSET_SKELETON}, ensure_ascii=False))
                if "character-portrait-templates.md" in system and "prop-template.md" in system:
                    return Reply(json.dumps({"text": CHARACTER_NOTE + "\n\n" + PROP_NOTE}, ensure_ascii=False))
                if "character-portrait-templates.md" in system:
                    return Reply(json.dumps({"text": CHARACTER_NOTE}, ensure_ascii=False))
                if "prop-template.md" in system:
                    return Reply(json.dumps({"text": PROP_NOTE}, ensure_ascii=False))
                if "scene-grid-template.md" in system:
                    return Reply(SCENE_NOTE)
                return Reply(SKILL_DUMP)

        model = AssetModel()
        result = run_suite("asset", brief, model)
        draft = result["image_prompts"]
        self.assertIn("格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。", draft)
        self.assertIn("格1：【正面视图】鲜红色天鹅绒材质。", draft)
        self.assertIn("整板：【场景定位】高档酒店宴会厅入口及仪式区。", draft)
        fig2 = draft.split("#### 图2 · 顾深（角色）", 1)[1].split("#### 图13", 1)[0]
        self.assertEqual(fig2.count("【填写】"), 4)
        self.assertFalse(model.merge_called)


class GeneratorFoldWiringTest(unittest.TestCase):
    def test_asset_generator_publishes_folded_draft_even_if_director_copies_skeleton(self):
        from Agents.workers.generator import run_generator

        brief = _asset_brief()
        calls = []

        def complete(role, messages):
            calls.append(role)
            user = messages[-1]["content"]
            if role == "director":
                return json.dumps({"image_prompts": ASSET_SKELETON}, ensure_ascii=False)
            if "character-portrait-templates.md" in user or "character-portrait-templates.md" in messages[0]["content"]:
                return json.dumps({"text": CHARACTER_NOTE}, ensure_ascii=False)
            if "prop-template.md" in messages[0]["content"]:
                return json.dumps({"text": PROP_NOTE}, ensure_ascii=False)
            if "scene-grid-template.md" in messages[0]["content"]:
                return SCENE_NOTE
            return SKILL_DUMP

        produced = run_generator(
            "asset",
            brief,
            role="生成员",
            complete=complete,
            files=[
                "sd-asset-engine/SKILL.md",
                "sd-asset-engine/references/character-portrait-templates.md",
                "sd-asset-engine/references/prop-template.md",
                "sd-asset-engine/references/scene-grid-template.md",
            ],
        )
        raw = produced["raw"]
        data = json.loads(raw)
        draft = data["image_prompts"]
        self.assertIn("格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。", draft)
        self.assertIn("格1：【正面视图】鲜红色天鹅绒材质。", draft)
        self.assertNotIn("director", calls)


if __name__ == "__main__":
    unittest.main()
