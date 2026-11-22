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


    def test_storyboard_suite_sends_unmatched_labels_to_merge(self):
        """Suite merge keeps the same leftover contract as the worker generator."""
        from AI.qwen_api.suite_graph import run_suite

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
        rewritten = "远景，把已经盖上的画面层改掉了。"
        mapped = "中景，顾深站在民政局大厅门口。"
        note = "\n".join((
            "## S01",
            "画面层：%s" % stamped,
            "旁白：无",
            "声景：心跳与婚礼进行曲叠在一起。",
            "",
            "## S02",
            leftover,
        ))

        class StoryboardModel(object):
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
                    return Reply(json.dumps({
                        "storyboard": "\n".join((
                            "## S01",
                            "画面层：%s" % rewritten,
                            "旁白：无",
                            "声景：心跳与婚礼进行曲叠在一起。",
                            "",
                            "## S02",
                            "画面层：%s" % mapped,
                            "旁白：无",
                            "声景：大厅回声。",
                        )) + "\n",
                    }, ensure_ascii=False))
                return Reply(json.dumps({"text": note}, ensure_ascii=False))

        model = StoryboardModel()
        result = run_suite("storyboard", brief, model)
        draft = result["storyboard"]
        self.assertTrue(model.merge_called)
        self.assertIn(leftover, model.merge_user)
        self.assertIn("标签没对上", model.merge_user)
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        s02 = draft.split("## S02", 1)[1]
        self.assertIn(mapped, s02)


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

    def test_storyboard_partial_fold_sends_unmatched_labels_to_merge(self):
        """A nonempty partial fold plus unmatched specialist body still merges.

        Specialists may write 画面内容 while the skeleton wants 画面层. The
        leftover body goes to the director, marked as a possible 标签没对上,
        and already-stamped slots survive a merge rewrite.
        """
        from Agents.workers.generator import run_generator

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
        stamped = "近景，顾深低头整理领结，手指发抖。"
        leftover = "画面内容：中景，顾深站在民政局大厅门口。"
        rewritten = "远景，把已经盖上的画面层改掉了。"
        mapped = "中景，顾深站在民政局大厅门口。"
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role == "director":
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "storyboard": "\n".join((
                        "## S01",
                        "画面层：%s" % rewritten,
                        "旁白：无",
                        "声景：心跳与婚礼进行曲叠在一起。",
                        "",
                        "## S02",
                        "画面层：%s" % mapped,
                        "旁白：无",
                        "声景：大厅回声。",
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
        self.assertIn("director", calls)
        self.assertIn(leftover, complete.user)
        self.assertIn("标签没对上", complete.user)
        draft = json.loads(produced["raw"])["storyboard"]
        self.assertIn("画面层：%s" % stamped, draft)
        self.assertNotIn(rewritten, draft)
        s02 = draft.split("## S02", 1)[1]
        self.assertIn(mapped, s02)



    def test_packing_partial_fold_sends_unmatched_labels_to_merge(self):
        """Wan packing keeps the leftover-to-merge contract.

        Official fields that match the skeleton are stamped first. Unmatched
        specialist labels still go to the director, marked as a possible
        标签没对上, and already-stamped lock fields survive a merge rewrite.
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
            if role == "director":
                complete.user = messages[-1]["content"]
                return json.dumps({
                    "feed": "\n".join((
                        "## 【封装单元 1/1】",
                        "duration: 10",
                        "总体描述:",
                        mapped,
                        "参考素材引用:",
                        "分镜:",
                        "分镜1（00:00.000-00:05.000）：",
                        "分镜2（00:05.000-00:10.000）：",
                        "台词:",
                        rewritten,
                        "音效 / BGM:",
                        "风格 / 情绪:",
                        "负向清单:",
                        "不要字幕、不要水印、不要复杂文字",
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

        complete.user = ""
        produced = run_generator(
            "feed",
            brief,
            role="生成员",
            complete=complete,
            files=["short-drama-director/references/wan-adapter.md"],
        )
        self.assertIn("director", calls)
        self.assertIn(leftover, complete.user)
        self.assertIn("标签没对上", complete.user)
        draft = json.loads(produced["raw"])["feed"]
        self.assertIn(stamped, draft)
        self.assertNotIn(rewritten, draft)
        self.assertIn(mapped, draft)
        # Official Wan fields stay in the published packing draft.
        self.assertIn("总体描述:", draft)
        self.assertIn("台词:", draft)
        self.assertIn("负向清单:", draft)


if __name__ == "__main__":
    unittest.main()
