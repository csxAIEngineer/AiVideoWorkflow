"""Program-owned asset skeleton. 图 numbers and grid slots are locked.

    python3 -m unittest tests.test_asset_skeleton
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.workers.asset import generator_messages, run_p2_asset_loop
from end.stage_runner import _produce, _uses_worker
from Agents.workers.asset_skeleton import (
    asset_skeleton_failures,
    extract_asset_catalog,
    format_asset_skeleton,
    asset_skeleton,
    restore_asset_locks,
)


SCRIPT = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主、湿疹小怪兽群

▲ 女主坐在床边，手臂上冒出红色小怪兽。

女主：好痒……根本停不下来！

## EP001-SC02｜内·卧室·日（15s - 35s）

出场：女主、湿疹小怪兽群、芙美松卫士

▲ 女主撞倒精芙美松凝胶支装，药膏管滚落，变身芙美松卫士。

芙美松卫士：别慌，我来平复局面。

## EP001-SC03｜内·卧室·日（35s - 50s）

出场：女主、湿疹小怪兽群、芙美松卫士

女主：真的不痒了……感觉好平静。

## EP001-SC04｜内·产品展示区·无背景（50s - 60s）

出场：精芙美松凝胶

▲ 纯白背景下，精芙美松凝胶支装缓缓旋转。

旁白（VO）：精芙美松凝胶，温和平复，安心之选。
"""


class AssetSkeletonBuildTest(unittest.TestCase):
    def test_extracts_global_figure_ids_and_grid_slots_from_the_script(self):
        skeleton = asset_skeleton({"script": SCRIPT})
        items = skeleton["assets"]
        self.assertEqual(
            [(item["id"], item["title"], item["kind"], item["grid"]) for item in items],
            [
                ("图1", "女主", "角色", "四格"),
                ("图2", "湿疹小怪兽群", "角色", "四格"),
                ("图3", "芙美松卫士", "角色", "四格"),
                ("图4", "精芙美松凝胶", "道具", "四格"),
                ("图5", "卧室", "场景母版", "整板"),
                ("图6", "卧室", "九宫格", "九宫格"),
                ("图7", "产品展示区", "场景母版", "整板"),
                ("图8", "产品展示区", "九宫格", "九宫格"),
            ],
        )
        self.assertEqual(items[0]["slots"], ["格1", "格2", "格3", "格4"])
        self.assertEqual(items[5]["slots"], ["格1", "格2", "格3", "格4", "格5", "格6", "格7", "格8", "格9"])
        self.assertEqual(items[4]["slots"], ["整板"])
        text = format_asset_skeleton(skeleton)
        self.assertIn("#### 图1 · 女主（角色）", text)
        self.assertIn("格位：四格", text)
        self.assertIn("#### 图6 · 卧室（九宫格）", text)
        self.assertIn("格位：九宫格", text)
        self.assertIn("场景名称：卧室（资产图编号：图5）", text)
        self.assertIn("场景名称：卧室（资产图编号：图6）", text)
        self.assertIn("【参考场景锁定模式】仅允许摄影机移动。", text)
        self.assertIn("全图共 9 格，不多不少。", text)
        self.assertIn("整板固定 16:9。", text)
        self.assertIn("填写", text)
        self.assertNotIn("图9", text)



class AssetSkeletonPromptTest(unittest.TestCase):
    def test_first_write_and_repair_prompts_contain_the_skeleton(self):
        first = {
            "attempt": 0,
            "script": SCRIPT,
            "locks": {},
            "asset_list": [],
            "draft": "",
        }
        first_user = generator_messages(first)[-1]["content"]
        first_skel = format_asset_skeleton(asset_skeleton(first))
        self.assertIn(first_skel.strip(), first_user)
        self.assertIn("#### 图1 · 女主（角色）", first_user)
        self.assertIn("#### 图8 · 产品展示区（九宫格）", first_user)

        repair = dict(first)
        repair["attempt"] = 1
        repair["draft"] = "#### 图1 · 女主\n旧稿"
        repair["critic_report"] = {"verdict": "revise", "issues": []}
        repair["machine_report"] = {"errors": ["图2 缺格位"], "warnings": []}
        repair_user = generator_messages(repair)[-1]["content"]
        self.assertIn(first_skel.strip(), repair_user)
        self.assertIn("#### 图2 · 湿疹小怪兽群（角色）", repair_user)



TAMPERED_ASSETS = """#### 图1 · 林浅
手绘描述不要丢。
格1：脸

#### 图9 · 自造角色
不是清单里的图。
"""


class AssetSkeletonRestoreTest(unittest.TestCase):
    def test_restore_puts_back_locked_ids_and_grid_slots(self):
        skeleton = asset_skeleton({"script": SCRIPT})
        restored = restore_asset_locks(TAMPERED_ASSETS, skeleton)
        self.assertIn("#### 图1 · 女主（角色）", restored)
        self.assertNotIn("#### 图1 · 林浅", restored)
        self.assertIn("手绘描述不要丢。", restored)
        for number in range(2, 9):
            self.assertIn("#### 图%s" % number, restored)
        self.assertIn("#### 图2 · 湿疹小怪兽群（角色）", restored)
        self.assertIn("格位：四格", restored)
        self.assertIn("#### 图6 · 卧室（九宫格）", restored)
        self.assertIn("格位：九宫格", restored)
        self.assertIn("#### 图9 · 自造角色", restored)

    def test_machine_check_fails_missing_or_invented_figure_ids(self):
        skeleton = asset_skeleton({"script": SCRIPT})
        errors = asset_skeleton_failures(TAMPERED_ASSETS, skeleton)
        self.assertTrue(any("图2" in item for item in errors), errors)
        self.assertTrue(any("图9" in item for item in errors), errors)
        restored = restore_asset_locks(TAMPERED_ASSETS, skeleton)
        leftover = asset_skeleton_failures(restored, skeleton)
        self.assertTrue(any("图9" in item for item in leftover), leftover)
        self.assertFalse(any("图2" in item and "缺失" in item for item in leftover), leftover)



class AssetOfficialPathTest(unittest.TestCase):
    def test_official_end_path_passes_and_restores_the_skeleton(self):
        # Figure repairs stay on the existing graph; first write stays on _write_model.
        self.assertFalse(_uses_worker("asset", {}))
        self.assertFalse(_uses_worker("asset", {"repair": "[x] 图1 FAIL"}))

        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text(SCRIPT + "\n", encoding="utf-8")
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": TAMPERED_ASSETS}

        project = {
            "template": "short-drama-director",
            "model": "qwen3.8-flash",
            "brief": "女主手臂很痒。",
            "progress": {"pipeline": {}},
        }
        result = _produce(
            project,
            folder,
            "asset",
            {"aspect": "16:9", "duration_seconds": 60, "material_mode": "M1", "model": "h3", "track": "1"},
            "",
            writer,
            lambda *a, **k: "pack",
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("当前资产骨架如下", calls[0])
        self.assertIn("#### 图1 · 女主（角色）", calls[0])
        self.assertIn("#### 图8 · 产品展示区（九宫格）", calls[0])
        text = result.get("image_prompts") or ""
        self.assertIn("#### 图1 · 女主（角色）", text)
        self.assertNotIn("#### 图1 · 林浅", text)
        self.assertIn("#### 图2 · 湿疹小怪兽群（角色）", text)
        self.assertIn("手绘描述不要丢。", text)


CAST_GUN_SCRIPT = """# 第001集｜皮肤卫士

- DURATION_TARGET：15 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主角、小怪兽、芙美松凝胶支装、喷射枪、卫士
女主角：别过来！
卫士（内心独白/字幕特效）：我来挡住。
"""


class AssetSkeletonContractTest(unittest.TestCase):
    def test_catalog_headings_lock_names_and_a3_sentence(self):
        skeleton = asset_skeleton({"script": CAST_GUN_SCRIPT})
        items = skeleton["assets"]
        self.assertEqual(
            [(item["id"], item["title"], item["kind"]) for item in items],
            [
                ("图1", "女主角", "角色"),
                ("图2", "小怪兽", "角色"),
                ("图3", "卫士", "角色"),
                ("图4", "芙美松凝胶支装", "道具"),
                ("图5", "喷射枪", "道具"),
                ("图6", "卧室", "场景母版"),
                ("图7", "卧室", "九宫格"),
            ],
        )
        text = format_asset_skeleton(skeleton)
        self.assertIn("#### 图1 · 女主角（角色）", text)
        self.assertIn("#### 图4 · 芙美松凝胶支装（道具）", text)
        self.assertIn("#### 图5 · 喷射枪（道具）", text)
        self.assertIn("#### 图6 · 卧室（母版）", text)
        self.assertIn("#### 图7 · 卧室（九宫格）", text)
        self.assertIn("场景名称：卧室（资产图编号：图6）", text)
        self.assertIn("场景名称：卧室（资产图编号：图7）", text)
        self.assertIn("【参考场景锁定模式】仅允许摄影机移动。", text)

    def test_prop_field_line_is_added_to_the_catalog(self):
        script = """## EP001-SC01｜内·卧室·夜（0s - 60s）

出场：女主、红色小怪兽们
道具：精芙美松凝胶管（U1）
"""
        assets = extract_asset_catalog(script)
        titles = [(item["title"], item["kind"]) for item in assets]
        self.assertIn(("女主", "角色"), titles)
        self.assertIn(("红色小怪兽们", "角色"), titles)
        self.assertIn(("精芙美松凝胶管（U1）", "道具"), titles)
        none_script = script.replace("精芙美松凝胶管（U1）", "无")
        none_titles = [(item["title"], item["kind"]) for item in extract_asset_catalog(none_script)]
        self.assertNotIn(("无", "道具"), none_titles)
        self.assertNotIn(("无", "角色"), none_titles)

    def test_absent_appearance_notes_are_skipped_but_names_with_wu_are_kept(self):
        script = """## EP001-SC01｜外·红毯边·日（0s - 8s）

出场：无（特写）

## EP001-SC02｜外·红毯·日（8s - 16s）

出场：无

## EP001-SC03｜外·红毯·日（16s - 30s）

出场：无头怪物、顾深
"""
        titles = [(item["title"], item["kind"]) for item in extract_asset_catalog(script)]
        self.assertNotIn(("无（特写）", "角色"), titles)
        self.assertNotIn(("无", "角色"), titles)
        self.assertIn(("无头怪物", "角色"), titles)
        self.assertIn(("顾深", "角色"), titles)
        self.assertEqual(titles[0], ("无头怪物", "角色"))

    def test_absent_notes_with_slash_in_parentheses_are_skipped(self):
        script = """## EP001-SC00｜视觉隐喻/闪前·红毯局部（0s - 3s）

出场：无（仅道具/环境）
道具：红毯、白花

## EP001-SC01｜内·民政局门口·日（3s - 18s）

出场：沈棠、顾深
道具：结婚证
"""
        titles = [(item["title"], item["kind"]) for item in extract_asset_catalog(script)]
        self.assertNotIn(("无（仅道具", "角色"), titles)
        self.assertNotIn(("环境）", "角色"), titles)
        self.assertNotIn(("无（仅道具/环境）", "角色"), titles)
        self.assertIn(("沈棠", "角色"), titles)
        self.assertIn(("顾深", "角色"), titles)
        self.assertIn(("红毯", "道具"), titles)
        self.assertIn(("白花", "道具"), titles)
        self.assertIn(("结婚证", "道具"), titles)
        self.assertEqual(titles[0], ("沈棠", "角色"))

    def test_script_without_cast_fails_closed_and_does_not_lock_scenes_only(self):
        script = """# 第001集｜皮肤卫士

## EP001-SC01｜内·卧室·日（0s - 15s）

女主角：好痒……根本停不下来！
"""
        skeleton = asset_skeleton({"script": script})
        errors = asset_skeleton_failures("", skeleton)
        self.assertTrue(any("剧本缺出场，不能生成资产骨架" in item for item in errors), errors)
        text = format_asset_skeleton(skeleton)
        self.assertNotIn("#### 图1 · 卧室（母版）", text)
        self.assertNotIn("#### 图2 · 卧室（九宫格）", text)
        self.assertNotIn("场景名称：卧室", text)

    def test_scene_name_mismatch_fails(self):
        script = """## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主角
"""
        skeleton = asset_skeleton({"script": script})
        board = """#### 图1 · 女主角（角色）
种类：角色
格位：四格
格1：脸

#### 图2 · 卧室（母版）
种类：场景母版
格位：整板
场景名称：浴室（资产图编号：图2）
整板：浴室镜前的灯光。

#### 图3 · 卧室（九宫格）
种类：九宫格
格位：九宫格
场景名称：卧室（资产图编号：图3）
【参考场景锁定模式】仅允许摄影机移动。
格1：床边。
"""
        errors = asset_skeleton_failures(board, skeleton)
        self.assertTrue(
            any("场景名称" in item or "浴室" in item or "图2" in item for item in errors),
            errors,
        )

    def test_leftover_fill_marker_fails(self):
        script = """## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主角
"""
        skeleton = asset_skeleton({"script": script})
        board = """#### 图1 · 女主角（角色）
种类：角色
格位：四格
格1：【填写】

#### 图2 · 卧室（母版）
种类：场景母版
格位：整板
场景名称：卧室（资产图编号：图2）
整板：卧室全景。

#### 图3 · 卧室（九宫格）
种类：九宫格
格位：九宫格
场景名称：卧室（资产图编号：图3）
【参考场景锁定模式】仅允许摄影机移动。
格1：床边。
"""
        errors = asset_skeleton_failures(board, skeleton)
        self.assertTrue(any("填写" in item for item in errors), errors)

    def test_restore_keeps_fill_and_puts_back_heading_name_and_a3(self):
        script = """## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主角
"""
        skeleton = asset_skeleton({"script": script})
        tampered = """#### 图1 · 林浅
格1：手绘描述不要丢。

#### 图2 · 改过的母版
整板：真实卧室描写不要丢。
场景名称：客厅（资产图编号：图9）

#### 图3 · 改过的九宫格
格1：真实格1不要丢。
【参考场景锁定模式】模式A档
"""
        # Mismatched 场景名称 must not be silently rewritten while keeping
        # a wrong-place body. That block's fill is dropped back to 【填写】.
        restored = restore_asset_locks(tampered, skeleton)
        self.assertIn("#### 图1 · 女主角（角色）", restored)
        self.assertNotIn("#### 图1 · 林浅", restored)
        self.assertIn("手绘描述不要丢。", restored)
        self.assertIn("#### 图2 · 卧室（母版）", restored)
        self.assertIn("场景名称：卧室（资产图编号：图2）", restored)
        self.assertNotIn("场景名称：客厅（资产图编号：图9）", restored)
        self.assertIn("#### 图3 · 卧室（九宫格）", restored)
        self.assertIn("【参考场景锁定模式】仅允许摄影机移动。", restored)
        self.assertIn("真实格1不要丢。", restored)
        errors = asset_skeleton_failures(tampered, skeleton)
        self.assertTrue(any("场景名称" in item or "客厅" in item or "图2" in item for item in errors), errors)
        leftover = asset_skeleton_failures(restored, skeleton)
        self.assertTrue(any("填写" in item for item in leftover), leftover)
        self.assertNotIn("真实卧室描写不要丢。", restored)


    def test_official_check_stage_fails_when_script_has_no_cast(self):
        from end.stage_runner import check_stage

        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text(
            "## EP001-SC01｜内·卧室·日（0s - 15s）\n\n女主角：好痒。\n",
            encoding="utf-8",
        )
        (internal / "assets.md").write_text(
            "#### 图1 · 卧室（母版）\n种类：场景母版\n整板：卧室全景。\n",
            encoding="utf-8",
        )
        ok, report = check_stage(
            folder,
            "asset",
            {"aspect": "16:9", "duration_seconds": 15, "material_mode": "M1"},
        )
        self.assertFalse(ok, report)
        self.assertIn("剧本缺出场，不能生成资产骨架", report)


NO_PROP_SCRIPT = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主、湿疹小怪兽群

▲ 女主坐在床边。

女主：好痒……

## EP001-SC02｜内·卧室·日（15s - 35s）

出场：女主、芙美松卫士

芙美松卫士：别慌。
"""

_PORTRAIT_COHORT = (
    "sd-asset-engine/references/character-portrait-templates.md",
    "sd-asset-engine/references/prop-template.md",
)


def _asset_brief(script):
    return (
        "只写资产提示词，放在 image_prompts。每条以「#### 图N · 名称」开头。\n\n"
        + format_asset_skeleton(asset_skeleton({"script": script}))
    )


class AssetSkeletonSliceTest(unittest.TestCase):
    def test_portrait_cohort_keeps_character_and_prop_slots_only(self):
        from Agents.workers.asset_skeleton import slice_asset_brief

        sliced = slice_asset_brief(_asset_brief(SCRIPT), _PORTRAIT_COHORT)
        self.assertIn("#### 图1 · 女主（角色）", sliced)
        self.assertIn("#### 图2 · 湿疹小怪兽群（角色）", sliced)
        self.assertIn("#### 图3 · 芙美松卫士（角色）", sliced)
        self.assertIn("#### 图4 · 精芙美松凝胶（道具）", sliced)
        self.assertNotIn("#### 图5 · 卧室（母版）", sliced)
        self.assertNotIn("#### 图6 · 卧室（九宫格）", sliced)
        self.assertNotIn("#### 图7 · 产品展示区（母版）", sliced)
        self.assertNotIn("#### 图8 · 产品展示区（九宫格）", sliced)
        self.assertIn("只写资产提示词", sliced)


    def test_portrait_cohort_without_props_keeps_only_character_slots(self):
        from Agents.workers.asset_skeleton import slice_asset_brief

        sliced = slice_asset_brief(_asset_brief(NO_PROP_SCRIPT), _PORTRAIT_COHORT)
        self.assertIn("#### 图1 · 女主（角色）", sliced)
        self.assertIn("#### 图2 · 湿疹小怪兽群（角色）", sliced)
        self.assertIn("#### 图3 · 芙美松卫士（角色）", sliced)
        self.assertNotIn("（道具）", sliced)
        self.assertNotIn("#### 图4 · 卧室（母版）", sliced)
        self.assertNotIn("#### 图5 · 卧室（九宫格）", sliced)
        self.assertNotIn("场景名称：", sliced)


    def test_scene_grid_cohort_keeps_only_scene_slots(self):
        from Agents.workers.asset_skeleton import slice_asset_brief

        sliced = slice_asset_brief(
            _asset_brief(SCRIPT),
            ("sd-asset-engine/references/scene-grid-template.md",),
        )
        self.assertNotIn("#### 图1 · 女主（角色）", sliced)
        self.assertNotIn("#### 图4 · 精芙美松凝胶（道具）", sliced)
        self.assertIn("#### 图5 · 卧室（母版）", sliced)
        self.assertIn("#### 图6 · 卧室（九宫格）", sliced)
        self.assertIn("#### 图7 · 产品展示区（母版）", sliced)
        self.assertIn("#### 图8 · 产品展示区（九宫格）", sliced)
        self.assertIn("只写资产提示词", sliced)



    def test_three_list_cohort_does_not_get_fill_slots(self):
        from Agents.workers.asset_skeleton import slice_asset_brief

        sliced = slice_asset_brief(
            _asset_brief(SCRIPT),
            (
                "sd-asset-engine/SKILL.md",
                "sd-asset-engine/references/three-list-extraction.md",
            ),
        )
        self.assertIn("#### 图1 · 女主（角色）", sliced)
        self.assertIn("#### 图4 · 精芙美松凝胶（道具）", sliced)
        self.assertIn("#### 图5 · 卧室（母版）", sliced)
        self.assertNotIn("【填写】", sliced)
        self.assertIn("只写资产提示词", sliced)

    def test_spatial_cohort_sees_scene_slots_without_fill(self):
        from Agents.workers.asset_skeleton import slice_asset_brief

        sliced = slice_asset_brief(
            _asset_brief(SCRIPT),
            (
                "short-drama-director/references/asset-spatial-ledger.md",
                "short-drama-director/references/asset-delivery-modes.md",
            ),
        )
        self.assertNotIn("#### 图1 · 女主（角色）", sliced)
        self.assertNotIn("#### 图4 · 精芙美松凝胶（道具）", sliced)
        self.assertIn("#### 图5 · 卧室（母版）", sliced)
        self.assertIn("#### 图8 · 产品展示区（九宫格）", sliced)
        self.assertNotIn("【填写】", sliced)
        self.assertIn("只写资产提示词", sliced)




if __name__ == "__main__":
    unittest.main()
