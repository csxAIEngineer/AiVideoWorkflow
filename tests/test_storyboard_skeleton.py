"""Program-owned storyboard skeleton. Shot ids and durations are locked to the user duration.

    python3 -m unittest tests.test_storyboard_skeleton
"""

import unittest
from pathlib import Path

from Agents.workers.storyboard import generator_messages, run_storyboard_loop
from Agents.workers.storyboard_skeleton import (
    format_storyboard_skeleton,
    restore_storyboard_locks,
    storyboard_skeleton,
    storyboard_skeleton_failures,
)


SCRIPT = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主、湿疹小怪兽群

▲ 女主坐在床边，手臂上冒出红色小怪兽。
▲ 小怪兽抓挠女主的皮肤，女主伸手狂挠。

女主：好痒……根本停不下来！

【音效】急促的摩擦声，小怪兽尖锐的笑声。

## EP001-SC02｜内·卧室·日（15s - 35s）

出场：女主、湿疹小怪兽群、芙美松卫士

▲ 女主撞倒精芙美松凝胶支装，药膏管滚落。
▲ 药膏管变身为芙美松卫士，举起凝胶枪。
▲ 卫士扣动扳机，凝胶光束交织成网。

芙美松卫士：别慌，我来平复局面。

【音效】药膏落地声，科技感充能声，随后是柔软的“噗”声。舒缓背景音乐起。

## EP001-SC03｜内·卧室·日（35s - 50s）

出场：女主、湿疹小怪兽群、芙美松卫士

▲ 凝胶网罩住小怪兽，它们化作光斑消散。
▲ 女主手臂红肿消失，她露出轻松的笑容。

女主：真的不痒了……感觉好平静。

## EP001-SC04｜内·产品展示区·无背景（50s - 60s）

出场：精芙美松凝胶

▲ 纯白背景下，精芙美松凝胶支装缓缓旋转。
▲ 屏幕上方浮现口号：平复瘙痒，守护肌肤。

旁白（VO）：精芙美松凝胶，温和平复，安心之选。

【音效】清脆的品牌结束音。
"""

ASSETS = [
    {"id": "图1", "title": "女主", "kind": "角色"},
    {"id": "图2", "title": "湿疹小怪兽群", "kind": "角色"},
    {"id": "图3", "title": "芙美松卫士", "kind": "角色"},
    {"id": "图4", "title": "精芙美松凝胶", "kind": "道具"},
    {"id": "图5", "title": "卧室", "kind": "场景母版"},
    {"id": "图6", "title": "卧室九宫格", "kind": "九宫格"},
    {"id": "图7", "title": "产品展示区", "kind": "场景母版"},
    {"id": "图8", "title": "产品展示区九宫格", "kind": "九宫格"},
]


class StoryboardAllocatorTest(unittest.TestCase):
    def test_shot_durations_are_integers_that_sum_to_the_user_lock(self):
        skeleton = storyboard_skeleton({
            "source": SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": ASSETS,
        })
        shots = skeleton["shots"]
        self.assertGreaterEqual(len(shots), 4)
        self.assertEqual([shot["id"] for shot in shots], ["S%02d" % (i + 1) for i in range(len(shots))])
        self.assertTrue(all(isinstance(shot["duration"], int) and shot["duration"] >= 1 for shot in shots))
        self.assertEqual(sum(shot["duration"] for shot in shots), 60)
        self.assertEqual(skeleton["duration_lock"], 60)
        # Scene timings 15+20+15+10 are kept as per-scene sums.
        by_scene = {}
        for shot in shots:
            by_scene.setdefault(shot["scene_id"], 0)
            by_scene[shot["scene_id"]] += shot["duration"]
        self.assertEqual(by_scene["EP001-SC01"], 15)
        self.assertEqual(by_scene["EP001-SC02"], 20)
        self.assertEqual(by_scene["EP001-SC03"], 15)
        self.assertEqual(by_scene["EP001-SC04"], 10)
        # No story beat is dropped from the 要点.
        joined = " ".join(" ".join(shot.get("action_beats") or []) for shot in shots)
        self.assertIn("女主坐在床边", joined)
        self.assertIn("撞倒精芙美松凝胶支装", joined)
        self.assertIn("凝胶网罩住小怪兽", joined)
        self.assertIn("精芙美松凝胶支装缓缓旋转", joined)
        text = format_storyboard_skeleton(skeleton)
        self.assertIn("时长：", text)
        self.assertIn(str(shots[0]["duration"]), text)
        self.assertIn("对白：女主：好痒……根本停不下来！", text)
        self.assertIn("配乐要点", text)
        self.assertIn("背景音乐", text)
        self.assertIn("画面层", text)



FORTY_BOARD = """## S01
动作：女主坐在床边抓挠。
景别机位：中近景
时长：5
资产：图1，图2，图5
对白：女主：好痒……根本停不下来！

## S02
动作：药膏管变身卫士。
景别机位：全景
时长：8
资产：图1，图3，图4，图5
对白：芙美松卫士：别慌，我来平复局面。

## S03
动作：凝胶网罩住小怪兽。
景别机位：全景
时长：12
资产：图1，图2，图3，图5
对白：女主：真的不痒了……感觉好平静。

## S04
动作：卫士变回药膏管。
景别机位：中景
时长：5
资产：图3，图4，图5
对白：无

## S05
动作：产品旋转，口号出现。
景别机位：特写
时长：10
资产：图4，图7
旁白：VO：精芙美松凝胶，温和平复，安心之选。
"""


class StoryboardSkeletonGuardTest(unittest.TestCase):
    def _skeleton(self):
        return storyboard_skeleton({
            "source": SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": ASSETS,
        })

    def test_a_forty_second_board_fails_a_sixty_second_lock(self):
        skeleton = self._skeleton()
        self.assertEqual(sum(shot["duration"] for shot in skeleton["shots"]), 60)
        errors = storyboard_skeleton_failures(FORTY_BOARD, skeleton)
        self.assertTrue(any("60" in item or "时长" in item or "40" in item for item in errors), errors)
        self.assertNotEqual(sum(_shot_durations(FORTY_BOARD)), 60)
        self.assertEqual(sum(_shot_durations(FORTY_BOARD)), 40)

    def test_first_write_and_repair_prompts_contain_the_skeleton(self):
        state = {
            "attempt": 0,
            "source": SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": ASSETS,
            "draft": "",
            "opening": "",
        }
        first = generator_messages(state)[-1]["content"]
        skel = format_storyboard_skeleton(storyboard_skeleton(state))
        self.assertIn(skel.strip(), first)
        self.assertIn("合计时长必须等于用户锁60秒", first)
        repair = dict(state)
        repair["attempt"] = 1
        repair["draft"] = FORTY_BOARD
        repair["critic_report"] = {"verdict": "revise", "issues": []}
        repair["machine_report"] = {"errors": ["时长锁被改写"], "warnings": []}
        repair_user = generator_messages(repair)[-1]["content"]
        self.assertIn(skel.strip(), repair_user)

    def test_restore_writes_back_shot_ids_durations_assets_and_dialogue(self):
        skeleton = self._skeleton()
        restored = restore_storyboard_locks(FORTY_BOARD, skeleton)
        errors = storyboard_skeleton_failures(restored, skeleton)
        # Extra or missing shots still fail; locked fields on overlapping shots are restored.
        for shot in skeleton["shots"]:
            self.assertIn("## %s" % shot["id"], restored)
            self.assertIn("时长：%s" % shot["duration"], restored)
            if shot.get("dialogue") and shot["dialogue"] != "无":
                self.assertIn("对白：%s" % shot["dialogue"], restored)
        self.assertEqual(sum(_shot_durations(restored)), 60)

    def test_official_loop_rejects_a_shorter_director_board(self):
        skeleton = self._skeleton()

        def complete(role, messages):
            if role == "specialist":
                return "专员只填槽"
            if role == "director":
                return __import__("json").dumps({"storyboard": FORTY_BOARD}, ensure_ascii=False)
            return '{"verdict":"accept","issues":[]}'

        result = run_storyboard_loop(
            SCRIPT,
            locks={"duration_seconds": 60, "aspect": "16:9", "material_mode": "M1"},
            assets=ASSETS,
            known_ids=[item["id"] for item in ASSETS],
            complete=complete,
            max_attempts=1,
        )
        self.assertNotEqual(result["status"], "pass")
        board = result.get("storyboard") or result.get("draft") or ""
        # The 5-shot 40s board is not adopted as-is: missing skeleton shots are
        # restored and the incomplete fill fails the machine check.
        self.assertIn("## S06", board)
        self.assertEqual(sum(_shot_durations(board)), 60)
        errors = " ".join(
            str(item)
            for item in (result.get("machine_report") or {}).get("errors") or [result.get("machine_report")]
        )
        self.assertTrue(
            "时长" in errors or "60" in errors or "骨架" in errors or "缺动作" in errors or "镜号" in errors,
            result,
        )


def _shot_durations(text):
    from end.storyboard_contract import project_shots
    shots, report = project_shots(text, [item["id"] for item in ASSETS])
    if report:
        # durations from headings even when other fields fail
        found = []
        for line in str(text or "").splitlines():
            if line.startswith("时长"):
                found.append(int("".join(ch for ch in line if ch.isdigit()) or 0))
        return found
    return [int(shot["duration"]) for shot in shots]



FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
OFFICIAL_SEGMENT_SCRIPT = (FIXTURE_DIR / "official_012e6420539c_script.md").read_text(encoding="utf-8")
OFFICIAL_80S_H3_SCRIPT = (FIXTURE_DIR / "official_1f2b8f1f1c9d_script.md").read_text(encoding="utf-8")
OFFICIAL_53S_BOARD = (FIXTURE_DIR / "official_012e6420539c_storyboard.md").read_text(encoding="utf-8")


class StoryboardOfficialScriptTest(unittest.TestCase):
    def test_official_segment_script_allocates_sixty_seconds_and_rejects_the_53s_board(self):
        self.assertIn("### Segment 1：痛点爆发 (00:00 - 00:15)", OFFICIAL_SEGMENT_SCRIPT)
        self.assertIn("### Segment 4：品牌定格 (00:50 - 01:00)", OFFICIAL_SEGMENT_SCRIPT)
        skeleton = storyboard_skeleton({
            "source": OFFICIAL_SEGMENT_SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": ASSETS,
        })
        shots = skeleton["shots"]
        self.assertTrue(shots)
        self.assertTrue(all(isinstance(shot["duration"], int) and shot["duration"] >= 1 for shot in shots))
        self.assertEqual(sum(shot["duration"] for shot in shots), 60)
        by_scene = {}
        for shot in shots:
            by_scene.setdefault(shot["scene_id"], 0)
            by_scene[shot["scene_id"]] += shot["duration"]
        self.assertEqual(sorted(by_scene.values()), [10, 15, 15, 20])
        self.assertEqual(sum(_shot_durations(OFFICIAL_53S_BOARD)), 53)
        errors = storyboard_skeleton_failures(OFFICIAL_53S_BOARD, skeleton)
        self.assertTrue(errors, errors)
        self.assertTrue(
            any("60" in item or "时长" in item or "53" in item or "镜号" in item for item in errors),
            errors,
        )

    def test_empty_skeleton_fails_when_a_duration_lock_is_set(self):
        skeleton = storyboard_skeleton({
            "source": "女主手臂很痒。没有场次标题。",
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": ASSETS,
        })
        self.assertEqual(skeleton["shots"], [])
        self.assertEqual(skeleton["duration_lock"], 60)
        errors = storyboard_skeleton_failures(FORTY_BOARD, skeleton)
        self.assertTrue(errors, errors)
        self.assertTrue(
            any("骨架" in item or "场次" in item for item in errors),
            errors,
        )


PROP_SCRIPT = """# 第001集｜红怪退散

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室镜前·日（0s - 15s）

出场：女主、红色小怪兽群
道具：镜子、手臂皮肤

▲ 女主对着镜子，手臂皮肤上浮现红色小怪兽。
▲ 女主伸手抓挠手臂。

女主（O.S.）：好痒……根本停不下来！

## EP001-SC02｜内·卧室镜前·日（15s - 40s）

出场：女主、芙美松卫士、红色小怪兽群
道具：发射枪、凝胶网

▲ 芙美松卫士滑入，举起发射枪。
▲ 卫士扣动扳机，凝胶交织成网。
▲ 凝胶网罩住小怪兽。

芙美松卫士：冷静下来。

## EP001-SC03｜内·卧室镜前·日（40s - 60s）

出场：女主、芙美松卫士
道具：U1产品静物图

▲ 女主看着手臂，露出微笑。
▲ 镜头拉远，画面中央定格出现产品图。

旁白（VO）：精芙美松凝胶，温和平复，还你自在肌肤。
"""

PROP_ASSETS = [
    {"id": "图1", "title": "女主", "kind": "角色"},
    {"id": "图2", "title": "红色小怪兽群", "kind": "角色"},
    {"id": "图3", "title": "芙美松卫士", "kind": "角色"},
    {"id": "图5", "title": "镜子", "kind": "道具"},
    {"id": "图6", "title": "手臂皮肤", "kind": "道具"},
    {"id": "图8", "title": "发射枪", "kind": "道具"},
    {"id": "图9", "title": "凝胶网", "kind": "道具"},
    {"id": "图10", "title": "U1产品静物图", "kind": "道具"},
    {"id": "图11", "title": "卧室镜前", "kind": "场景母版"},
    {"id": "图12", "title": "卧室镜前", "kind": "九宫格"},
]

NO_PROP_ASSETS = [
    item for item in PROP_ASSETS if item["kind"] != "道具"
]


def _shots_for_scene(shots, scene_id):
    return [shot for shot in shots if shot.get("scene_id") == scene_id]


class StoryboardPropAssetBindTest(unittest.TestCase):
    def test_prop_assets_are_locked_onto_shots_when_they_exist(self):
        skeleton = storyboard_skeleton({
            "source": PROP_SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": PROP_ASSETS,
        })
        shots = skeleton["shots"]
        self.assertEqual(len(shots), 7)
        self.assertEqual(sum(shot["duration"] for shot in shots), 60)
        sc01 = _shots_for_scene(shots, "EP001-SC01")
        sc02 = _shots_for_scene(shots, "EP001-SC02")
        sc03 = _shots_for_scene(shots, "EP001-SC03")
        self.assertEqual(len(sc01), 2)
        self.assertEqual(len(sc02), 3)
        self.assertEqual(len(sc03), 2)
        for shot in sc01:
            self.assertEqual(shot["assets"], ["图1", "图2", "图5", "图6", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        for shot in sc02:
            self.assertEqual(shot["assets"], ["图1", "图3", "图2", "图8", "图9", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        for shot in sc03:
            self.assertEqual(shot["assets"], ["图1", "图3", "图10", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        self.assertEqual(sc01[-1]["dialogue"], "女主（O.S.）：好痒……根本停不下来！")
        self.assertEqual(sc02[-1]["dialogue"], "芙美松卫士：冷静下来。")
        text = format_storyboard_skeleton(skeleton)
        self.assertIn("资产：图1，图2，图5，图6，图11", text)
        self.assertIn("资产：图1，图3，图2，图8，图9，图11", text)
        self.assertIn("资产：图1，图3，图10，图11", text)
        self.assertNotIn("对白：道具：", text)
        self.assertNotIn("图12", [asset_id for shot in shots for asset_id in shot["assets"]])

    def test_missing_prop_assets_are_skipped_and_prop_line_is_not_dialogue(self):
        skeleton = storyboard_skeleton({
            "source": PROP_SCRIPT,
            "locks": {"duration_seconds": 60, "aspect": "16:9"},
            "assets": NO_PROP_ASSETS,
        })
        shots = skeleton["shots"]
        self.assertEqual(len(shots), 7)
        self.assertEqual(sum(shot["duration"] for shot in shots), 60)
        sc01 = _shots_for_scene(shots, "EP001-SC01")
        sc02 = _shots_for_scene(shots, "EP001-SC02")
        sc03 = _shots_for_scene(shots, "EP001-SC03")
        for shot in sc01:
            self.assertEqual(shot["assets"], ["图1", "图2", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        for shot in sc02:
            self.assertEqual(shot["assets"], ["图1", "图3", "图2", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        for shot in sc03:
            self.assertEqual(shot["assets"], ["图1", "图3", "图11"])
            self.assertNotIn("道具：", shot.get("dialogue") or "")
        invented = {
            asset_id
            for shot in shots
            for asset_id in shot["assets"]
            if asset_id not in {"图1", "图2", "图3", "图11"}
        }
        self.assertEqual(invented, set())
        text = format_storyboard_skeleton(skeleton)
        self.assertNotIn("对白：道具：", text)
        self.assertNotIn("图5", text)
        self.assertNotIn("图9", text)
        self.assertNotIn("图10", text)


class StoryboardH3DurationSplitTest(unittest.TestCase):
    def test_official_80s_three_scene_script_splits_scenes_over_20s(self):
        self.assertIn("## EP001-SC01｜外·明代江南雨巷·日（0s - 25s）", OFFICIAL_80S_H3_SCRIPT)
        self.assertIn("## EP001-SC03｜外·明代山间石阶·晨（50s - 80s）", OFFICIAL_80S_H3_SCRIPT)
        skeleton = storyboard_skeleton({
            "source": OFFICIAL_80S_H3_SCRIPT,
            "locks": {"duration_seconds": 80, "model": "h3", "aspect": "16:9"},
        })
        shots = skeleton["shots"]
        self.assertEqual([shot["id"] for shot in shots], ["S01", "S02", "S03", "S04", "S05", "S06"])
        self.assertEqual([shot["duration"] for shot in shots], [13, 12, 13, 12, 15, 15])
        self.assertEqual(sum(shot["duration"] for shot in shots), 80)
        self.assertEqual(skeleton["duration_lock"], 80)
        self.assertEqual(
            [shot["scene_id"] for shot in shots],
            ["EP001-SC01", "EP001-SC01", "EP001-SC02", "EP001-SC02", "EP001-SC03", "EP001-SC03"],
        )
        self.assertFalse((shots[0].get("dialogue") or "").strip())
        self.assertIn("风从背入，先暖后散", shots[1].get("dialogue") or "")
        self.assertFalse((shots[2].get("dialogue") or "").strip())
        self.assertIn("静中藏寒，莫让凉意趁虚而入", shots[3].get("dialogue") or "")
        self.assertFalse((shots[4].get("dialogue") or "").strip())
        self.assertIn("晨露侵骨，宜早驱之", shots[5].get("dialogue") or "")

    def test_scene_over_30s_is_split_for_wan(self):
        script = (
            "## EP001-SC01｜外·场地·日（0s - 40s）\n\n"
            "出场：大侠\n"
            "情节：一场四十秒的戏。\n"
            "对白：大侠：到此为止。\n"
        )
        skeleton = storyboard_skeleton({
            "source": script,
            "locks": {"duration_seconds": 40, "model": "h3", "aspect": "16:9"},
        })
        shots = skeleton["shots"]
        self.assertEqual([shot["duration"] for shot in shots], [20, 20])
        self.assertEqual(sum(shot["duration"] for shot in shots), 40)

    def test_official_80s_script_also_splits_when_model_is_unset(self):
        skeleton = storyboard_skeleton({
            "source": OFFICIAL_80S_H3_SCRIPT,
            "locks": {"duration_seconds": 80, "aspect": "16:9"},
        })
        shots = skeleton["shots"]
        self.assertEqual([shot["id"] for shot in shots], ["S01", "S02", "S03", "S04", "S05", "S06"])
        self.assertEqual([shot["duration"] for shot in shots], [13, 12, 13, 12, 15, 15])
        self.assertEqual(sum(shot["duration"] for shot in shots), 80)


if __name__ == "__main__":
    unittest.main()
