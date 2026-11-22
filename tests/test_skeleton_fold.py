"""Programmatic slot fold: specialist fills overwrite empty skeleton slots."""

import json
import unittest

from Agents.workers.asset_skeleton import asset_skeleton_failures, format_asset_skeleton
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



def _packing_shot_state(rows):
    return {
        "storyboard": "\n".join("## %s\n时长：%s" % pair for pair in rows),
        "shots": [{"id": shot_id, "duration": seconds} for shot_id, seconds in rows],
        "locks": {"model": "h3", "material_mode": "M1", "track": "1"},
    }



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

    def test_asset_fold_fills_scene_master_and_grid_from_template_labels(self):
        skeleton = {
            "script": "## EP001-SC01｜内·酒店宴会厅·日\n\n出场：沈棠\n",
            "assets": [
                {
                    "id": "图1",
                    "title": "酒店宴会厅",
                    "kind": "场景母版",
                    "grid": "整板",
                    "slots": ["整板"],
                },
                {
                    "id": "图2",
                    "title": "酒店宴会厅",
                    "kind": "九宫格",
                    "grid": "九宫格",
                    "slots": ["格%s" % number for number in range(1, 10)],
                },
            ],
        }
        note = (
            "【场景定位】场景名称：酒店宴会厅（资产图编号：图2）。"
            "时代/地域：当代都市。建筑类型：高档酒店宴会厅。"
            "空间类型：室内半封闭空间。时间段/天气：婚礼日间，室内暖光。\n"
            "【场景画面描写】整体画面：高档酒店宴会厅入口及仪式区，暖色吊灯低饱和，红毯铺入主舞台。\n"
            "第一行左：宴会厅入口全景，红毯铺入仪式区。\n"
            "第一行中：主桌侧向中景，灯光低饱和。\n"
            "第一行右：舞台方向全景，背景是金色幕布。\n"
            "第二行左：签到台特写，花艺与名册。\n"
            "第二行中：宾客席过道，椅背系白色缎带。\n"
            "第二行右：香槟塔近景，玻璃反光。\n"
            "第三行左：高位俯瞰全场布局。\n"
            "第三行中：舞台侧面，司仪位置与台阶。\n"
            "第三行右：窗口侧光，纱帘与午后光斑。\n"
        )
        draft = fold_stage_draft("asset", "任务：资产锁定\n\n" + format_asset_skeleton(skeleton), [
            {"skill": "sd-asset-engine/references/scene-grid-template.md", "text": note},
        ])
        fig1 = draft.split("#### 图1 · 酒店宴会厅（母版）", 1)[1].split("#### 图2", 1)[0]
        fig2 = draft.split("#### 图2 · 酒店宴会厅（九宫格）", 1)[1]
        self.assertIn("整板：", fig1)
        self.assertIn("高档酒店宴会厅入口及仪式区", fig1)
        self.assertIn("室内半封闭空间", fig1)
        self.assertNotIn("【填写】", fig1)
        self.assertIn("格1：宴会厅入口全景，红毯铺入仪式区。", fig2)
        self.assertIn("格2：主桌侧向中景，灯光低饱和。", fig2)
        self.assertIn("格3：舞台方向全景，背景是金色幕布。", fig2)
        self.assertIn("格4：签到台特写，花艺与名册。", fig2)
        self.assertIn("格5：宾客席过道，椅背系白色缎带。", fig2)
        self.assertIn("格6：香槟塔近景，玻璃反光。", fig2)
        self.assertIn("格7：高位俯瞰全场布局。", fig2)
        self.assertIn("格8：舞台侧面，司仪位置与台阶。", fig2)
        self.assertIn("格9：窗口侧光，纱帘与午后光斑。", fig2)
        self.assertNotIn("【填写】", fig2)
        self.assertNotIn(
            "填写槽仍是【填写】，资产稿未填完",
            asset_skeleton_failures(draft, skeleton),
        )

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
    def test_asset_suite_publishes_merge_model_output_even_if_fold_would_fill(self):
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
        merged = """#### 图1 · 沈棠（角色）
种类：角色
格位：四格
格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。
格2：【正面全身·无脸】白色A字拖尾婚纱。
格3：【侧面全身·无脸】侧身站立，展现婚纱背部。
格4：【背面全身·无脸】拖尾铺展。

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
格1：【正面视图】鲜红色天鹅绒材质。
格2：【45°侧视图】展示红毯厚度与柔软度。
格3：【顶部俯视图】纹理均匀。
格4：【关键细节特写】角落有一朵白花脱落痕迹。

#### 图25 · 婚礼现场（母版）
种类：场景母版
格位：整板
场景名称：婚礼现场（资产图编号：图25）
整板固定 16:9。
整板：【场景定位】高档酒店宴会厅入口及仪式区。
"""

        class AssetModel(object):
            def __init__(self):
                self.merge_called = False
                self.merge_user = ""

            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    self.merge_called = True
                    self.merge_user = messages[-1]["content"]
                    return Reply(json.dumps({"image_prompts": merged}, ensure_ascii=False))
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
        self.assertTrue(model.merge_called)
        self.assertIn("格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。", model.merge_user)
        self.assertEqual(draft.strip(), merged.strip())
        self.assertIn("格1：【面部特写】约25岁女性，脸型鹅蛋脸偏长。", draft)
        self.assertIn("格1：【正面视图】鲜红色天鹅绒材质。", draft)
        self.assertIn("整板：【场景定位】高档酒店宴会厅入口及仪式区。", draft)
        fig2 = draft.split("#### 图2 · 顾深（角色）", 1)[1].split("#### 图13", 1)[0]
        self.assertEqual(fig2.count("【填写】"), 4)


    def test_asset_suite_sends_specialist_bodies_to_merge_even_if_fold_is_nonempty(self):
        """Asset merge matches origin/dev-server: never skip the merge model for assets.

        A partial fold (scene slots filled, character written as ①②③④ / 正面视图)
        must still send specialist bodies through the merge model. The published
        draft is the merge model's skeleton fill, not the partial fold.
        """
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
        character_note = (
            "图1 沈棠\n"
            "① 正面特写：约25岁女性，脸型鹅蛋脸偏长。\n"
            "② 正面视图：白色A字拖尾婚纱。\n"
            "③ 侧面视图：侧身站立，展现婚纱背部。\n"
            "④ 背面视图：拖尾铺展。\n"
        )
        merged = """#### 图1 · 沈棠（角色）
种类：角色
格位：四格
格1：约25岁女性，脸型鹅蛋脸偏长。
格2：白色A字拖尾婚纱。
格3：侧身站立，展现婚纱背部。
格4：拖尾铺展。

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
整板：高档酒店宴会厅入口及仪式区。
"""

        class AssetModel(object):
            def __init__(self):
                self.merge_called = False
                self.merge_user = ""

            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    self.merge_called = True
                    self.merge_user = messages[-1]["content"]
                    return Reply(json.dumps({"image_prompts": merged}, ensure_ascii=False))
                if "character-portrait-templates.md" in system:
                    return Reply(json.dumps({"text": character_note}, ensure_ascii=False))
                if "prop-template.md" in system:
                    return Reply(json.dumps({"text": ""}, ensure_ascii=False))
                if "scene-grid-template.md" in system:
                    return Reply(SCENE_NOTE)
                return Reply(SKILL_DUMP)

        model = AssetModel()
        result = run_suite("asset", brief, model)
        draft = result["image_prompts"]
        self.assertTrue(model.merge_called)
        self.assertIn("① 正面特写：约25岁女性，脸型鹅蛋脸偏长。", model.merge_user)
        self.assertIn("【场景定位】高档酒店宴会厅入口及仪式区。", model.merge_user)
        self.assertEqual(draft.strip(), merged.strip())
        fig1 = draft.split("#### 图1 · 沈棠（角色）", 1)[1].split("#### 图2", 1)[0]
        self.assertIn("格1：约25岁女性，脸型鹅蛋脸偏长。", fig1)
        self.assertNotIn("【填写】", fig1)
        fig2 = draft.split("#### 图2 · 顾深（角色）", 1)[1].split("#### 图13", 1)[0]
        self.assertEqual(fig2.count("【填写】"), 4)


    def test_storyboard_suite_refills_only_missing_action_and_camera(self):
        """Suite keeps the same missing-slot refill contract as the worker generator."""
        from AI.qwen_api.suite_graph import run_suite
        from Agents.workers.storyboard_skeleton import restore_storyboard_locks
        from end.storyboard_contract import project_shots

        skeleton_obj = {
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
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "\n".join((
            "阶段：P3",
            "赛道：1",
            "来源：1",
            "动作档：R2",
            "模型：h3",
            "素材形态：M1",
            "只加载：sd-storyboard-engine/SKILL.md",
            "任务：分镜",
            "",
            skeleton,
        ))
        stamped = "近景，顾深低头整理领结，手指发抖。"
        leftover = "画面内容：中景，顾深站在民政局大厅门口。"
        rewritten = "把已经盖上的动作改掉了。"
        action_fill = "顾深站在民政局大厅门口。"
        camera_fill = "中景"
        note = "\n".join((
            "## S01",
            "画面层：%s" % stamped,
            "旁白：无",
            "声景：心跳与婚礼进行曲叠在一起。",
            "",
            "## S02",
            leftover,
            "旁白：无",
        ))

        class StoryboardModel(object):
            def __init__(self):
                self.merge_called = False
                self.refill_called = False
                self.refill_system = ""
                self.refill_user = ""

            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    self.merge_called = True
                    return Reply(json.dumps({
                        "storyboard": "\n".join((
                            "## S01",
                            "动作：%s" % rewritten,
                            "时长：99",
                        )) + "\n",
                    }, ensure_ascii=False))
                if "只返回漏掉的内容" in system or "只补还空着的槽" in system:
                    self.refill_called = True
                    self.refill_system = system
                    self.refill_user = messages[-1]["content"]
                    return Reply(json.dumps({
                        "storyboard": "\n".join((
                            "## S01",
                            "动作：%s" % rewritten,
                            "时长：99",
                            "",
                            "## S02",
                            "动作：%s" % action_fill,
                            "景别机位：%s" % camera_fill,
                        )) + "\n",
                    }, ensure_ascii=False))
                return Reply(json.dumps({"text": note}, ensure_ascii=False))

        model = StoryboardModel()
        result = run_suite("storyboard", brief, model)
        draft = result["storyboard"]
        self.assertFalse(model.merge_called)
        self.assertTrue(model.refill_called)
        self.assertNotIn("你是汇总节点", model.refill_system)
        self.assertIn("S02 动作", model.refill_user)
        self.assertIn("S02 景别机位", model.refill_user)
        self.assertNotIn("S01 动作", model.refill_user)
        self.assertNotIn("S01 景别机位", model.refill_user)
        self.assertIn("只返回漏掉的内容", model.refill_user)
        self.assertIn("故事一致性", model.refill_user)
        self.assertIn("人物一致性", model.refill_user)
        self.assertIn("逻辑合理", model.refill_user)
        self.assertIn(leftover, model.refill_user)
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertNotIn("时长：99", draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)
        s02 = draft.split("## S02", 1)[1]
        self.assertIn("动作：%s" % action_fill, s02)
        self.assertIn("景别机位：%s" % camera_fill, s02)
        restored = restore_storyboard_locks(draft, skeleton_obj)
        _shots, report = project_shots(restored, ["图1", "图25"])
        self.assertNotIn("缺动作", report or "")
        self.assertNotIn("缺景别机位", report or "")


    def test_storyboard_refill_stamps_missing_action_and_camera_without_shot_headings(self):
        """Refill accepts ## S01 fields, S01 动作： one-liners, and headerless blocks.

        Already-filled 动作/景别机位/旁白 and 时长/资产/对白 are not rewritten.
        """
        from AI.qwen_api.suite_graph import run_suite
        from Agents.workers.storyboard_skeleton import restore_storyboard_locks
        from end.storyboard_contract import project_shots

        skeleton_obj = {
            "duration_lock": 15,
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
                {
                    "id": "S03",
                    "duration": 5,
                    "assets": ["图2"],
                    "dialogue": "沈棠：走吧。",
                    "scene_heading": "EP001-SC02｜内·民政局大厅·上午（15s - 35s）",
                },
            ],
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "\n".join((
            "阶段：P3",
            "赛道：1",
            "来源：1",
            "动作档：R2",
            "模型：h3",
            "素材形态：M1",
            "只加载：sd-storyboard-engine/SKILL.md",
            "任务：分镜",
            "",
            skeleton,
        ))
        stamped = "近景，顾深低头整理领结，手指发抖。"
        rewritten = "把已经盖上的动作改掉了。"
        s02_action = "顾深站在民政局大厅门口。"
        s02_camera = "中景"
        s03_action = "沈棠转身离开。"
        s03_camera = "全景"
        note = "\n".join((
            "## S01",
            "画面层：%s" % stamped,
            "旁白：无",
            "",
            "## S02",
            "旁白：无",
            "",
            "## S03",
            "旁白：无",
        ))

        class StoryboardModel(object):
            def __init__(self):
                self.merge_called = False
                self.refill_called = False
                self.refill_user = ""

            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    self.merge_called = True
                    return Reply(json.dumps({"storyboard": "时长：99"}, ensure_ascii=False))
                if "只返回漏掉的内容" in system or "只补还空着的槽" in system:
                    self.refill_called = True
                    self.refill_user = messages[-1]["content"]
                    return Reply(json.dumps({
                        "storyboard": "\n".join((
                            "## S01",
                            "动作：%s" % rewritten,
                            "时长：99",
                            "对白：改掉的对白",
                            "",
                            "S02 动作：%s" % s02_action,
                            "S02 景别机位：%s" % s02_camera,
                            "",
                            "动作：%s" % s03_action,
                            "景别机位：%s" % s03_camera,
                        )) + "\n",
                    }, ensure_ascii=False))
                return Reply(json.dumps({"text": note}, ensure_ascii=False))

        model = StoryboardModel()
        result = run_suite("storyboard", brief, model)
        draft = result["storyboard"]
        self.assertFalse(model.merge_called)
        self.assertTrue(model.refill_called)
        self.assertIn("S02 动作", model.refill_user)
        self.assertIn("S02 景别机位", model.refill_user)
        self.assertIn("S03 动作", model.refill_user)
        self.assertIn("S03 景别机位", model.refill_user)
        self.assertNotIn("S01 动作", model.refill_user)
        self.assertNotIn("S01 景别机位", model.refill_user)
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertNotIn("时长：99", draft)
        self.assertNotIn("改掉的对白", draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)
        self.assertIn("对白：沈棠：走吧。", draft)
        s02 = draft.split("## S02", 1)[1].split("## S03", 1)[0]
        s03 = draft.split("## S03", 1)[1]
        self.assertIn("动作：%s" % s02_action, s02)
        self.assertIn("景别机位：%s" % s02_camera, s02)
        self.assertIn("动作：%s" % s03_action, s03)
        self.assertIn("景别机位：%s" % s03_camera, s03)
        restored = restore_storyboard_locks(draft, skeleton_obj)
        _shots, report = project_shots(restored, ["图1", "图25", "图2"])
        self.assertNotIn("缺动作", report or "")
        self.assertNotIn("缺景别机位", report or "")


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

    def test_storyboard_partial_fold_refills_only_missing_action_and_camera(self):
        """A nonempty partial fold asks the model only for empty 动作/景别机位.

        Unmatched specialist labels are reference, not a whole-draft merge.
        Already-filled 时长/资产/对白 stay verbatim. The refill must keep
        故事一致性, 人物一致性, and 逻辑合理 once stamped.
        """
        from Agents.workers.generator import run_generator
        from Agents.workers.storyboard_skeleton import restore_storyboard_locks
        from end.storyboard_contract import project_shots

        skeleton_obj = {
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
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "任务：分镜\n\n" + skeleton
        stamped = "近景，顾深低头整理领结，手指发抖。"
        leftover = "画面内容：中景，顾深站在民政局大厅门口。"
        rewritten = "把已经盖上的动作改掉了。"
        action_fill = "顾深站在民政局大厅门口。"
        camera_fill = "中景"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                complete.system = messages[0]["content"]
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "storyboard": "\n".join((
                        "## S01",
                        "动作：%s" % rewritten,
                        "时长：99",
                        "",
                        "## S02",
                        "动作：%s" % action_fill,
                        "景别机位：%s" % camera_fill,
                    )) + "\n",
                }, ensure_ascii=False)
            return json.dumps({
                "text": "\n".join((
                    "## S01",
                    "画面层：%s" % stamped,
                    "旁白：无",
                    "声景：心跳与婚礼进行曲叠在一起。",
                    "",
                    "## S02",
                    leftover,
                    "旁白：无",
                )),
            }, ensure_ascii=False)

        complete.system = ""
        complete.user = ""
        produced = run_generator(
            "storyboard",
            brief,
            role="生成员",
            complete=complete,
            files=["sd-storyboard-engine/SKILL.md"],
        )
        self.assertEqual(calls.count("specialist"), 1)
        self.assertEqual(len([role for role in calls if role != "specialist"]), 1)
        self.assertNotIn("你是汇总节点", complete.system)
        self.assertNotIn("请按槽汇编成这一阶段的稿", complete.user)
        self.assertIn("S02 动作", complete.user)
        self.assertIn("S02 景别机位", complete.user)
        self.assertNotIn("S01 动作", complete.user)
        self.assertNotIn("S01 景别机位", complete.user)
        self.assertIn("只返回漏掉的内容", complete.user)
        self.assertIn("故事一致性", complete.user)
        self.assertIn("人物一致性", complete.user)
        self.assertIn("逻辑合理", complete.user)
        self.assertIn(leftover, complete.user)
        draft = json.loads(produced["raw"])["storyboard"]
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertNotIn("时长：99", draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)
        s02 = draft.split("## S02", 1)[1]
        self.assertIn("动作：%s" % action_fill, s02)
        self.assertIn("景别机位：%s" % camera_fill, s02)
        restored = restore_storyboard_locks(draft, skeleton_obj)
        _shots, report = project_shots(restored, ["图1", "图25"])
        self.assertNotIn("缺动作", report or "")
        self.assertNotIn("缺景别机位", report or "")


    def test_storyboard_full_fold_skips_refill_even_with_unmatched_labels(self):
        """When every 动作 and 景别机位 is already covered, do not call refill."""
        from Agents.workers.generator import run_generator

        skeleton = format_storyboard_skeleton({
            "duration_lock": 10,
            "shots": [
                {
                    "id": "S01",
                    "duration": 5,
                    "assets": ["图1"],
                    "dialogue": "顾深：好。",
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
        s01 = "近景，顾深低头整理领结，手指发抖。"
        s02 = "中景，顾深站在民政局大厅门口。"
        leftover = "动作要点：顾深站定。"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                raise AssertionError("refill was called even though no contract slot was empty")
            return json.dumps({
                "text": "\n".join((
                    "## S01",
                    "画面层：%s" % s01,
                    "旁白：无",
                    "",
                    "## S02",
                    "画面层：%s" % s02,
                    "旁白：无",
                    leftover,
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "storyboard",
            brief,
            role="生成员",
            complete=complete,
            files=["sd-storyboard-engine/SKILL.md"],
        )
        self.assertEqual(calls, ["specialist"])
        draft = json.loads(produced["raw"])["storyboard"]
        self.assertIn("画面层：%s" % s01, draft)
        self.assertIn("画面层：%s" % s02, draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)


    def test_storyboard_fold_stamps_action_and_camera_without_shot_headings(self):
        """Filled 动作/景别机位 stamp even without ## Sxx or 画面层.

        Skill-path heads start a new S01, S02, … sequence. First filled value wins.
        """
        from Agents.workers.generator import run_generator
        from Agents.workers.storyboard_skeleton import restore_storyboard_locks
        from end.storyboard_contract import project_shots

        skeleton_obj = {
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
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "任务：分镜\n\n" + skeleton
        s01_action = "顾深低头整理领结，手指发抖。"
        s01_camera = "近景"
        s02_action = "顾深站在民政局大厅门口。"
        s02_camera = "中景"
        later_action = "把已经盖上的动作改掉了。"
        later_camera = "特写"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                raise AssertionError("refill was called even though 动作 and 景别机位 were already written")
            return json.dumps({
                "text": "\n".join((
                    "## short-drama-director/references/acting-direction.md",
                    "动作：%s" % s01_action,
                    "景别机位：%s" % s01_camera,
                    "旁白：无",
                    "",
                    "动作：%s" % s02_action,
                    "景别机位：%s" % s02_camera,
                    "旁白：无",
                    "",
                    "## short-drama-director/references/camera-language.md",
                    "动作：%s" % later_action,
                    "景别机位：%s" % later_camera,
                    "旁白：把已经盖上的旁白改掉了。",
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "storyboard",
            brief,
            role="生成员",
            complete=complete,
            files=["sd-storyboard-engine/SKILL.md"],
        )
        self.assertEqual(calls, ["specialist"])
        draft = json.loads(produced["raw"])["storyboard"]
        s01 = draft.split("## S01", 1)[1].split("## S02", 1)[0]
        s02 = draft.split("## S02", 1)[1]
        self.assertIn("动作：%s" % s01_action, s01)
        self.assertIn("景别机位：%s" % s01_camera, s01)
        self.assertIn("旁白：无", s01)
        self.assertNotIn(later_action, s01)
        self.assertNotIn(later_camera, s01)
        self.assertIn("动作：%s" % s02_action, s02)
        self.assertIn("景别机位：%s" % s02_camera, s02)
        self.assertNotIn(later_action, draft)
        self.assertIn("时长：5", s01)
        self.assertIn("资产：图1", s01)
        self.assertIn("对白：顾深：好。", s01)
        self.assertIn("动作要点：整理领结", s01)
        restored = restore_storyboard_locks(draft, skeleton_obj)
        _shots, report = project_shots(restored, ["图1", "图25"])
        self.assertNotIn("缺动作", report or "")
        self.assertNotIn("缺景别机位", report or "")


    def test_storyboard_fold_does_not_treat_action_notes_as_action(self):
        """动作要点 is not 动作. 站位、朝向、光线 is not 景别机位."""
        from Agents.workers.generator import run_generator

        skeleton_obj = {
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
            ],
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "任务：分镜\n\n" + skeleton
        action_note = "整理领结时不要抬肘"
        staging_note = "门口左侧，面向镜头，顶光"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "storyboard": "\n".join((
                        "## S01",
                        "动作：补问写出的动作。",
                        "景别机位：近景",
                    )),
                }, ensure_ascii=False)
            return json.dumps({
                "text": "\n".join((
                    "## short-drama-director/references/acting-direction.md",
                    "动作要点：%s" % action_note,
                    "站位、朝向、光线：%s" % staging_note,
                )),
            }, ensure_ascii=False)

        complete.user = ""
        produced = run_generator(
            "storyboard",
            brief,
            role="生成员",
            complete=complete,
            files=["sd-storyboard-engine/SKILL.md"],
        )
        self.assertIn("specialist", calls)
        self.assertEqual(len([role for role in calls if role != "specialist"]), 1)
        self.assertIn("S01 动作", complete.user)
        self.assertIn("S01 景别机位", complete.user)
        draft = json.loads(produced["raw"])["storyboard"]
        self.assertIn("动作要点：整理领结", draft)
        self.assertNotIn("动作要点：%s" % action_note, draft)
        self.assertNotIn("动作：%s" % action_note, draft)
        self.assertNotIn("景别机位：%s" % staging_note, draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)


    def test_storyboard_refill_stamps_missing_action_and_camera_without_shot_headings(self):
        """Refill accepts ## S01 fields, S01 动作： one-liners, and headerless blocks.

        Already-filled 动作/景别机位/旁白 and 时长/资产/对白 are not rewritten.
        """
        from Agents.workers.generator import run_generator
        from Agents.workers.storyboard_skeleton import restore_storyboard_locks
        from end.storyboard_contract import project_shots

        skeleton_obj = {
            "duration_lock": 15,
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
                {
                    "id": "S03",
                    "duration": 5,
                    "assets": ["图2"],
                    "dialogue": "沈棠：走吧。",
                    "scene_heading": "EP001-SC02｜内·民政局大厅·上午（15s - 35s）",
                },
            ],
        }
        skeleton = format_storyboard_skeleton(skeleton_obj)
        brief = "任务：分镜\n\n" + skeleton
        stamped = "近景，顾深低头整理领结，手指发抖。"
        rewritten = "把已经盖上的动作改掉了。"
        s02_action = "顾深站在民政局大厅门口。"
        s02_camera = "中景"
        s03_action = "沈棠转身离开。"
        s03_camera = "全景"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "storyboard": "\n".join((
                        "## S01",
                        "动作：%s" % rewritten,
                        "时长：99",
                        "对白：改掉的对白",
                        "",
                        "S02 动作：%s" % s02_action,
                        "S02 景别机位：%s" % s02_camera,
                        "",
                        "动作：%s" % s03_action,
                        "景别机位：%s" % s03_camera,
                    )) + "\n",
                }, ensure_ascii=False)
            return json.dumps({
                "text": "\n".join((
                    "## S01",
                    "画面层：%s" % stamped,
                    "旁白：无",
                    "",
                    "## S02",
                    "旁白：无",
                    "",
                    "## S03",
                    "旁白：无",
                )),
            }, ensure_ascii=False)

        complete.user = ""
        produced = run_generator(
            "storyboard",
            brief,
            role="生成员",
            complete=complete,
            files=["sd-storyboard-engine/SKILL.md"],
        )
        self.assertEqual(calls.count("specialist"), 1)
        self.assertEqual(len([role for role in calls if role != "specialist"]), 1)
        self.assertIn("S02 动作", complete.user)
        self.assertIn("S02 景别机位", complete.user)
        self.assertIn("S03 动作", complete.user)
        self.assertIn("S03 景别机位", complete.user)
        self.assertNotIn("S01 动作", complete.user)
        self.assertNotIn("S01 景别机位", complete.user)
        draft = json.loads(produced["raw"])["storyboard"]
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertNotIn("时长：99", draft)
        self.assertNotIn("改掉的对白", draft)
        self.assertIn("时长：5", draft)
        self.assertIn("资产：图1", draft)
        self.assertIn("对白：顾深：好。", draft)
        self.assertIn("对白：沈棠：走吧。", draft)
        s02 = draft.split("## S02", 1)[1].split("## S03", 1)[0]
        s03 = draft.split("## S03", 1)[1]
        self.assertIn("动作：%s" % s02_action, s02)
        self.assertIn("景别机位：%s" % s02_camera, s02)
        self.assertIn("动作：%s" % s03_action, s03)
        self.assertIn("景别机位：%s" % s03_camera, s03)
        restored = restore_storyboard_locks(draft, skeleton_obj)
        _shots, report = project_shots(restored, ["图1", "图25", "图2"])
        self.assertNotIn("缺动作", report or "")
        self.assertNotIn("缺景别机位", report or "")


    def test_packing_partial_fold_refills_only_missing_wan_fields(self):
        """Wan packing asks the model only for official fields still empty after fold.

        Already-stamped 台词, duration, and 负向清单 stay verbatim. Unmatched
        specialist labels are reference, not a whole-draft merge rewrite.
        """
        from Agents.workers.generator import run_generator

        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 10",
            "总体描述:",
            "参考素材引用:",
            "分镜:",
            "分镜1（00:00.000-00:05.000）：",
            "分镜2（00:05.000-00:10.000）：",
            "台词:",
            "音效 / BGM:",
            "风格 / 情绪:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "\n".join((
            "阶段：P4",
            "赛道：1",
            "来源：1",
            "动作档：none",
            "模型：h3",
            "素材形态：M1",
            "任务：提示词封装",
            "",
            skeleton,
        ))
        stamped = '顾深说："好。"'
        leftover = "画面内容：近景，顾深低头整理领结，手指发抖。"
        rewritten = '顾深说："把已经盖上的台词改掉了。"'
        mapped = "近景，顾深低头整理领结，手指发抖。"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                complete.system = messages[0]["content"]
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "feed": "\n".join((
                        "## 【封装单元 1/1】",
                        "duration: 99",
                        "总体描述:",
                        mapped,
                        "台词:",
                        rewritten,
                        "负向清单:",
                        "不要改这条负向清单",
                    )) + "\n",
                }, ensure_ascii=False)
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "台词:",
                    stamped,
                    leftover,
                )),
            }, ensure_ascii=False)

        complete.system = ""
        complete.user = ""
        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        self.assertEqual(calls.count("specialist"), 1)
        self.assertEqual(len([role for role in calls if role != "specialist"]), 1)
        self.assertNotIn("你是汇总节点", complete.system)
        self.assertNotIn("请汇总成这一阶段的一份稿", complete.user)
        self.assertIn("单元1 总体描述", complete.user)
        self.assertIn("单元1 分镜", complete.user)
        self.assertIn("单元1 音效 / BGM", complete.user)
        self.assertIn("单元1 风格 / 情绪", complete.user)
        self.assertNotIn("单元1 台词", complete.user)
        self.assertNotIn("单元1 负向清单", complete.user)
        self.assertIn("只返回漏掉的内容", complete.user)
        self.assertIn("故事一致性", complete.user)
        self.assertIn("人物一致性", complete.user)
        self.assertIn("逻辑合理", complete.user)
        self.assertIn(leftover, complete.user)
        draft = json.loads(produced["raw"])["feed"]
        self.assertIn(stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertIn(mapped, draft)
        self.assertIn("duration: 10", draft)
        self.assertNotIn("duration: 99", draft)
        self.assertIn("不要字幕、不要水印、不要复杂文字", draft)
        self.assertNotIn("不要改这条负向清单", draft)
        self.assertIn("总体描述:", draft)
        self.assertIn("台词:", draft)
        self.assertIn("负向清单:", draft)



    def test_packing_full_fold_skips_refill_even_with_unmatched_labels(self):
        """When every official Wan field is already filled, do not call refill."""
        from Agents.workers.generator import run_generator

        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 10",
            "总体描述:",
            "近景，顾深低头整理领结。",
            "参考素材引用:",
            "图1",
            "分镜:",
            "分镜1（00:00.000-00:10.000）：",
                    "顾深整理领结。",
            "台词:",
            '顾深说："好。"',
            "音效 / BGM:",
            "心跳",
            "风格 / 情绪:",
            "紧张",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "任务：提示词封装\n\n" + skeleton
        leftover = "画面内容：近景，顾深低头整理领结。"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                raise AssertionError("refill was called even though no Wan field was empty")
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "总体描述:",
                    "近景，顾深低头整理领结。",
                    "参考素材引用:",
                    "图1",
                    "分镜:",
                    "分镜1（00:00.000-00:10.000）：",
                    "顾深整理领结。",
                    "台词:",
                    '顾深说："好。"',
                    "音效 / BGM:",
                    "心跳",
                    "风格 / 情绪:",
                    "紧张",
                    leftover,
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        self.assertEqual(calls, ["specialist"])
        draft = json.loads(produced["raw"])["feed"]
        self.assertIn("近景，顾深低头整理领结。", draft)
        self.assertIn('顾深说："好。"', draft)
        self.assertIn("duration: 10", draft)


    def test_packing_refill_stamps_missing_fields_without_unit_header(self):
        """The refill may return only the missing field bodies, with no unit title."""
        from Agents.workers.generator import run_generator

        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 10",
            "总体描述:",
            "参考素材引用:",
            "分镜:",
            "分镜1（00:00.000-00:05.000）：",
            "台词:",
            "音效 / BGM:",
            "风格 / 情绪:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "任务：提示词封装\n\n" + skeleton
        stamped = '顾深说："好。"'
        mapped = "近景，顾深低头整理领结，手指发抖。"

        def complete(role, messages):
            if role != "specialist":
                return json.dumps({
                    "feed": "\n".join((
                        "总体描述:",
                        mapped,
                    )) + "\n",
                }, ensure_ascii=False)
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "台词:",
                    stamped,
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        draft = json.loads(produced["raw"])["feed"]
        self.assertIn(stamped, draft)
        self.assertIn(mapped, draft)
        self.assertIn("duration: 10", draft)
        self.assertIn("## 【封装单元 1/1】", draft)



    def test_packing_fold_stamps_shot_bodies_into_locked_headings(self):
        """Specialist 分镜 headings land on the locked lines; empty copies are not kept."""
        from Agents.workers.feed import shot_skeleton_failures
        from Agents.workers.generator import run_generator

        heading1 = "分镜1（00:00.000-00:05.000）："
        heading2 = "分镜2（00:05.000-00:12.000）："
        body1 = "安那停下脚步，回头望向李敬。"
        body2 = "硬切转场至现代博物馆展厅。"
        style_lock = "画面风格：电影感，冷色调灯光。"
        asset_line = "资产：图1、图2"
        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 12",
            asset_line,
            "总体描述:",
            "参考素材引用:",
            "分镜:",
            heading1,
            heading2,
            "台词:",
            "音效 / BGM:",
            "风格 / 情绪:",
            style_lock,
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "任务：提示词封装\n\n" + skeleton
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role != "specialist":
                raise AssertionError("refill was called even though 分镜 and other official fields were filled")
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "duration: 99",
                    "资产：图99",
                    "总体描述:",
                    "安那与李敬在城墙外分别。",
                    "参考素材引用:",
                    "图1、图2",
                    "分镜:",
                    heading1,
                    body1,
                    heading2,
                    body2,
                    "台词:",
                    '安那说："我走了。"',
                    "音效 / BGM:",
                    "风雪声",
                    "风格 / 情绪:",
                    "不要这句风格",
                    "负向清单:",
                    "不要改这条负向清单",
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        self.assertEqual(calls, ["specialist"])
        draft = json.loads(produced["raw"])["feed"]
        self.assertEqual(draft.count(heading1), 1)
        self.assertEqual(draft.count(heading2), 1)
        fenjing = draft.split("分镜:", 1)[1].split("台词:", 1)[0]
        self.assertIn(body1, fenjing)
        self.assertIn(body2, fenjing)
        self.assertLess(fenjing.find(heading1), fenjing.find(body1))
        self.assertLess(fenjing.find(body1), fenjing.find(heading2))
        self.assertLess(fenjing.find(heading2), fenjing.find(body2))
        self.assertIn("duration: 12", draft)
        self.assertNotIn("duration: 99", draft)
        self.assertIn(asset_line, draft)
        self.assertNotIn("资产：图99", draft)
        self.assertIn(style_lock, draft)
        self.assertNotIn("不要这句风格", draft)
        self.assertIn("不要字幕、不要水印、不要复杂文字", draft)
        self.assertNotIn("不要改这条负向清单", draft)
        self.assertEqual(
            shot_skeleton_failures(draft, _packing_shot_state((("S01", 5), ("S02", 7)))),
            [],
        )


    def test_packing_fold_does_not_rewrite_filled_shots(self):
        """Already-filled 分镜 bodies stay verbatim when the specialist rewrites them."""
        from Agents.workers.feed import shot_skeleton_failures
        from Agents.workers.generator import run_generator

        heading1 = "分镜1（00:00.000-00:05.000）："
        heading2 = "分镜2（00:05.000-00:12.000）："
        body1 = "安那停下脚步，回头望向李敬。"
        body2 = "硬切转场至现代博物馆展厅。"
        rewritten1 = "把已经盖上的分镜1改掉了。"
        rewritten2 = "把已经盖上的分镜2改掉了。"
        style_lock = "画面风格：电影感，冷色调灯光。"
        asset_line = "资产：图1、图2"
        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 12",
            asset_line,
            "总体描述:",
            "安那与李敬在城墙外分别。",
            "参考素材引用:",
            "图1、图2",
            "分镜:",
            heading1,
            body1,
            heading2,
            body2,
            "台词:",
            '安那说："我走了。"',
            "音效 / BGM:",
            "风雪声",
            "风格 / 情绪:",
            style_lock,
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "任务：提示词封装\n\n" + skeleton

        def complete(role, messages):
            if role != "specialist":
                raise AssertionError("refill was called even though no Wan field was empty")
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "duration: 99",
                    "资产：图99",
                    "分镜:",
                    heading1,
                    rewritten1,
                    heading2,
                    rewritten2,
                    "台词:",
                    '安那说："把已经盖上的台词改掉了。"',
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        draft = json.loads(produced["raw"])["feed"]
        fenjing = draft.split("分镜:", 1)[1].split("台词:", 1)[0]
        self.assertEqual(fenjing.count(heading1), 1)
        self.assertEqual(fenjing.count(heading2), 1)
        self.assertIn(body1, fenjing)
        self.assertIn(body2, fenjing)
        self.assertNotIn(rewritten1, draft)
        self.assertNotIn(rewritten2, draft)
        self.assertIn('安那说："我走了。"', draft)
        self.assertNotIn("把已经盖上的台词改掉了", draft)
        self.assertIn("duration: 12", draft)
        self.assertIn(asset_line, draft)
        self.assertIn(style_lock, draft)
        self.assertEqual(
            shot_skeleton_failures(draft, _packing_shot_state((("S01", 5), ("S02", 7)))),
            [],
        )


    def test_packing_fold_drops_trailing_empty_duplicate_shot_headings(self):
        """A draft that already has filled shots plus trailing empty copies is cleaned."""
        from Agents.workers.feed import shot_skeleton_failures
        from Agents.workers.generator import run_generator

        heading1 = "分镜1（00:00.000-00:05.000）："
        heading2 = "分镜2（00:05.000-00:12.000）："
        body1 = "安那停下脚步，回头望向李敬。"
        body2 = "硬切转场至现代博物馆展厅。"
        style_lock = "画面风格：电影感，冷色调灯光。"
        asset_line = "资产：图1、图2"
        skeleton = "\n".join((
            "万相 3.0 的封装骨架已经按封装单元写好。只填每个字段名下面的正文。",
            "不要改 ## 标题，不要改 duration，不要调换字段顺序。",
            "",
            "## 【封装单元 1/1】",
            "duration: 12",
            asset_line,
            "总体描述:",
            "安那与李敬在城墙外分别。",
            "参考素材引用:",
            "图1、图2",
            "分镜:",
            heading1,
            body1,
            heading2,
            body2,
            heading1,
            heading2,
            "台词:",
            '安那说："我走了。"',
            "音效 / BGM:",
            "风雪声",
            "风格 / 情绪:",
            style_lock,
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        brief = "任务：提示词封装\n\n" + skeleton

        def complete(role, messages):
            if role != "specialist":
                raise AssertionError("refill was called even though no Wan field was empty")
            return json.dumps({
                "text": "\n".join((
                    "## 【封装单元 1/1】",
                    "分镜:",
                    heading1,
                    "把已经盖上的分镜1改掉了。",
                    heading2,
                    "把已经盖上的分镜2改掉了。",
                )),
            }, ensure_ascii=False)

        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        draft = json.loads(produced["raw"])["feed"]
        fenjing = draft.split("分镜:", 1)[1].split("台词:", 1)[0]
        self.assertEqual(fenjing.count(heading1), 1)
        self.assertEqual(fenjing.count(heading2), 1)
        self.assertIn(body1, fenjing)
        self.assertIn(body2, fenjing)
        self.assertNotIn("把已经盖上的分镜1改掉了", draft)
        self.assertNotIn("把已经盖上的分镜2改掉了", draft)
        self.assertIn("duration: 12", draft)
        self.assertIn(asset_line, draft)
        self.assertIn(style_lock, draft)
        self.assertEqual(
            shot_skeleton_failures(draft, _packing_shot_state((("S01", 5), ("S02", 7)))),
            [],
        )




class PackingRepairFoldTest(unittest.TestCase):
    def test_a_repair_brief_fold_keeps_only_the_locked_packing_units(self):
        """A repair brief may repeat the packing units after the locked skeleton.

        Fold keeps the first locked set, stamps specialist bodies into those
        shot headings, and leaves 上一阶段 / packing-plan copies out of the extract.
        """
        locked = "\n".join((
            "## 【封装单元 1/2】",
            "duration: 30",
            "总体描述:",
            "参考素材引用:",
            "分镜:",
            "分镜1（00:00.000-00:12.000）：",
            "分镜2（00:12.000-00:18.000）：",
            "分镜3（00:18.000-00:24.000）：",
            "分镜4（00:24.000-00:30.000）：",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
            "",
            "## 【封装单元 2/2】",
            "duration: 30",
            "总体描述:",
            "参考素材引用:",
            "分镜:",
            "分镜1（00:00.000-00:05.000）：",
            "分镜2（00:05.000-00:12.000）：",
            "分镜3（00:12.000-00:18.000）：",
            "分镜4（00:18.000-00:24.000）：",
            "分镜5（00:24.000-00:30.000）：",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        ))
        leaked = "\n".join((
            "## 【封装单元 1/2】",
            "duration: 30",
            "总体描述:",
            "旧单元一总体",
            "参考素材引用:",
            "旧单元一素材",
            "分镜:",
            "分镜1（00:00.000-00:12.000）：",
            "旧第一镜",
            "分镜2（00:12.000-00:18.000）：",
            "旧第二镜",
            "分镜3（00:18.000-00:24.000）：",
            "旧第三镜",
            "分镜4（00:24.000-00:30.000）：",
            "旧第四镜",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
            "",
            "## 【封装单元 2/2】",
            "duration: 30",
            "总体描述:",
            "旧单元二总体",
            "参考素材引用:",
            "旧单元二素材",
            "分镜:",
            "分镜1（00:00.000-00:05.000）：",
            "旧第五镜",
            "分镜2（00:05.000-00:12.000）：",
            "旧第六镜",
            "分镜3（00:12.000-00:18.000）：",
            "旧第七镜",
            "分镜4（00:18.000-00:24.000）：",
            "旧第八镜",
            "分镜5（00:24.000-00:30.000）：",
            "旧第九镜",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        ))
        brief = "\n".join((
            "任务：提示词封装",
            "",
            "要改的底稿：",
            "当前骨架如下。只填或修改字段名下面的正文。",
            "",
            locked,
            "",
            "改完的封装稿放在 feed。",
            "上一阶段正文（锁定参考，整段保留）：",
            "# 第001集｜过所",
            "上一阶段剧本分镜留在这里。",
            "分镜时长合计 60s。可以合并镜头。",
            "【封装单元 1/2】镜头 1-4：12+6+6+6=30",
            "【封装单元 2/2】镜头 5-9：5+7+6+6+6=30",
            "当前骨架如下。只填或修改字段名下面的正文。",
            "",
            leaked,
            "",
            "对白和旁白必须逐字出现，包括角色前缀和表演提示。",
        ))
        specialist = "\n".join((
            "## 【封装单元 1/2】",
            "duration: 30",
            "总体描述:",
            "单元一总体",
            "参考素材引用:",
            "单元一素材",
            "分镜:",
            "分镜1（00:00.000-00:12.000）：",
            "第一镜画面",
            "分镜2（00:12.000-00:18.000）：",
            "第二镜画面",
            "分镜3（00:18.000-00:24.000）：",
            "第三镜画面",
            "分镜4（00:24.000-00:30.000）：",
            "第四镜画面",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
            "",
            "## 【封装单元 2/2】",
            "duration: 30",
            "总体描述:",
            "单元二总体",
            "参考素材引用:",
            "单元二素材",
            "分镜:",
            "分镜1（00:00.000-00:05.000）：",
            "第五镜画面",
            "分镜2（00:05.000-00:12.000）：",
            "第六镜画面",
            "分镜3（00:12.000-00:18.000）：",
            "第七镜画面",
            "分镜4（00:18.000-00:24.000）：",
            "第八镜画面",
            "分镜5（00:24.000-00:30.000）：",
            "第九镜画面",
            "台词:",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        ))
        draft = fold_stage_draft("feed", brief, [specialist])
        self.assertEqual(draft.count("## 【封装单元"), 2, draft)
        self.assertIn("## 【封装单元 1/2】", draft)
        self.assertIn("## 【封装单元 2/2】", draft)
        first, second = draft.split("## 【封装单元 2/2】", 1)
        self.assertIn("第一镜画面", first)
        self.assertIn("第四镜画面", first)
        self.assertNotIn("第五镜画面", first)
        self.assertEqual(first.count("分镜1（00:00.000-00:12.000）："), 1)
        self.assertIn("第五镜画面", second)
        self.assertIn("第九镜画面", second)
        self.assertNotIn("第一镜画面", second)
        self.assertEqual(second.count("分镜1（00:00.000-00:05.000）："), 1)
        self.assertEqual(second.count("分镜5（00:24.000-00:30.000）："), 1)
        self.assertIn("单元一总体", first)
        self.assertIn("单元二总体", second)
        self.assertNotIn("旧单元", draft)
        self.assertNotIn("旧第一镜", draft)
        self.assertNotIn("上一阶段正文", draft)
        self.assertNotIn("上一阶段剧本分镜留在这里", draft)
        self.assertNotIn("改完的封装稿放在 feed", draft)
        self.assertNotIn("【封装单元 1/2】镜头 1-4", draft)



if __name__ == "__main__":
    unittest.main()
