"""Program-owned script skeleton. The model fills slots; user locks stay locked.

    python3 -m unittest tests.test_script_skeleton
"""

import tempfile
import unittest
from pathlib import Path

from Agents.workers.script import generator_messages, run_script_loop
from Agents.workers.script_skeleton import (
    format_script_skeleton,
    restore_script_locks,
    script_skeleton,
    script_skeleton_failures,
)


def _locks():
    return {
        "track": "1",
        "source": "1",
        "model": "h3",
        "aspect": "16:9",
        "delivery": "1",
        "duration_seconds": 60,
        "style": "手绘赛璐璐 · 粗描边 · 平涂色块 · 经典动画质感",
        "style_look": "手绘赛璐璐 · 粗描边 · 平涂色块 · 经典动画质感",
        "combat": "R2",
        "material_mode": "M2",
    }


class ScriptSkeletonBuildTest(unittest.TestCase):
    def test_first_write_locks_user_locks_and_does_not_invent_scenes(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": "", "source": "女主手臂很痒。"})
        text = format_script_skeleton(skeleton)
        self.assertIn("成片时长秒：60", text)
        self.assertIn("成片画幅：16:9", text)
        self.assertIn("画风正文：手绘赛璐璐 · 粗描边 · 平涂色块 · 经典动画质感", text)
        self.assertIn("填写槽", text)
        self.assertIn("出场：角色、道具", text)
        self.assertIn("## EP001-SC01｜内·地点·日（0s - 15s）", text)
        self.assertNotIn("已确认结构", text)
        self.assertNotIn("第一场", text)
        self.assertNotIn("痛点爆发", text)
        self.assertNotIn("卧室", text)
        self.assertNotIn("出场：女主", text)
        self.assertNotIn("出场：【填写】", text)



CONFIRMED = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 15s）

出场：女主
女主：好痒……根本停不下来！

## EP001-SC02｜内·卧室·日（15s - 35s）

出场：女主、芙美松卫士
芙美松卫士：别慌，我来平复局面。

## EP001-SC03｜内·卧室·日（35s - 50s）

出场：女主
女主：真的不痒了……感觉好平静。

## EP001-SC04｜内·产品展示区·无背景（50s - 60s）

出场：精芙美松凝胶
旁白（VO）：精芙美松凝胶，温和平复，安心之选。
"""


class ScriptSkeletonStructureTest(unittest.TestCase):
    def test_accepted_scenes_are_locked_on_later_repairs(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": CONFIRMED})
        text = format_script_skeleton(skeleton)
        self.assertIn("EP001-SC01｜内·卧室·日（0s - 15s）", text)
        self.assertIn("EP001-SC02｜内·卧室·日（15s - 35s）", text)
        self.assertIn("EP001-SC03｜内·卧室·日（35s - 50s）", text)
        self.assertIn("EP001-SC04｜内·产品展示区·无背景（50s - 60s）", text)
        self.assertIn("成片时长秒：60", text)
        self.assertEqual([scene["id"] for scene in skeleton["scenes"]], [
            "EP001-SC01",
            "EP001-SC02",
            "EP001-SC03",
            "EP001-SC04",
        ])
        self.assertEqual(skeleton["scenes"][0]["duration"], 15)
        self.assertEqual(skeleton["scenes"][1]["duration"], 20)
        self.assertEqual(skeleton["scenes"][2]["duration"], 15)
        self.assertEqual(skeleton["scenes"][3]["duration"], 10)
        self.assertEqual(sum(scene["duration"] for scene in skeleton["scenes"]), 60)
        self.assertIn("出场：女主", text)
        self.assertIn("出场：精芙美松凝胶", text)



class ScriptSkeletonPromptTest(unittest.TestCase):
    def test_first_write_and_repair_prompts_contain_the_skeleton(self):
        first = {
            "attempt": 0,
            "locks": _locks(),
            "draft": "",
            "source": "女主手臂很痒。",
            "opening": "",
        }
        first_user = generator_messages(first)[-1]["content"]
        first_skel = format_script_skeleton(script_skeleton(first))
        self.assertIn(first_skel, first_user)
        self.assertIn("成片时长秒：60", first_user)
        self.assertIn("## EP001-SC01｜内·地点·日（0s - 15s）", first_user)
        self.assertNotIn("已确认结构", first_user)

        repair = {
            "attempt": 1,
            "locks": _locks(),
            "draft": CONFIRMED,
            "source": "女主手臂很痒。",
            "opening": "任务：剧本",
            "critic_report": {"verdict": "revise", "issues": []},
            "machine_report": {"errors": ["对白缺结尾"], "warnings": []},
        }
        repair_user = generator_messages(repair)[-1]["content"]
        repair_skel = format_script_skeleton(script_skeleton(repair))
        self.assertIn(repair_skel, repair_user)
        self.assertIn("EP001-SC01｜内·卧室·日（0s - 15s）", repair_user)
        self.assertIn("成片时长秒：60", repair_user)



TAMPERED = """# 第001集｜皮肤卫士

- DURATION_TARGET：40 秒
- ASPECT_RATIO：9:16

## EP001-SC01｜内·卧室·日（0s - 10s）

出场：女主
女主：好痒……根本停不下来！

## EP001-SC02｜内·卧室·日（15s - 35s）

出场：女主、芙美松卫士
芙美松卫士：别慌，我来平复局面。

## EP001-SC03｜内·卧室·日（35s - 50s）

出场：女主
女主：真的不痒了……感觉好平静。
"""


class ScriptSkeletonRestoreTest(unittest.TestCase):
    def test_restore_writes_back_user_locks_and_confirmed_headings(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": CONFIRMED})
        restored = restore_script_locks(TAMPERED, skeleton)
        self.assertIn("DURATION_TARGET：60 秒", restored)
        self.assertNotIn("DURATION_TARGET：40 秒", restored)
        self.assertIn("ASPECT_RATIO：16:9", restored)
        self.assertNotIn("ASPECT_RATIO：9:16", restored)
        self.assertIn("## EP001-SC01｜内·卧室·日（0s - 15s）", restored)
        self.assertNotIn("0s - 10s", restored)
        self.assertIn("## EP001-SC04｜内·产品展示区·无背景（50s - 60s）", restored)
        self.assertIn("女主：好痒……根本停不下来！", restored)


    def test_machine_check_fails_tampered_locks_before_restore(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": CONFIRMED})
        errors = script_skeleton_failures(TAMPERED, skeleton)
        self.assertTrue(any("时长" in item or "60" in item or "40" in item for item in errors), errors)
        self.assertTrue(any("画幅" in item or "16:9" in item or "9:16" in item for item in errors), errors)
        self.assertTrue(any("EP001-SC01" in item or "时段" in item or "场" in item for item in errors), errors)
        self.assertTrue(any("EP001-SC04" in item for item in errors), errors)
        restored = restore_script_locks(TAMPERED, skeleton)
        leftover = script_skeleton_failures(restored, skeleton)
        self.assertFalse(
            any("时长" in item or "画幅" in item or "被改写" in item or "缺失" in item for item in leftover),
            leftover,
        )
        self.assertTrue(any("EP001-SC04" in item and "出场" in item for item in leftover), leftover)



class ScriptSkeletonLoopTest(unittest.TestCase):
    def test_official_loop_restores_locks_after_the_model_writes(self):
        def complete(role, messages):
            if role == "specialist":
                return "专员只填槽"
            if role == "director":
                return '''{"script": %s, "title": "题", "internal": "圆桌意见"}''' % (
                    __import__("json").dumps(TAMPERED + "\n## EP001-SC04｜内·产品展示区·无背景（50s - 55s）\n\n出场：精芙美松凝胶\n旁白（VO）：精芙美松凝胶，温和平复，安心之选。\n", ensure_ascii=False)
                )
            return '{"verdict":"accept","issues":[]}'

        def reviewer(draft, state):
            return {"status": "passed", "issues": [], "reason": ""}

        result = run_script_loop(
            "女主手臂很痒。",
            locks=_locks(),
            prior_draft=CONFIRMED,
            opening="任务：剧本",
            complete=complete,
            reviewer=reviewer,
        )
        script = result.get("script") or result.get("draft") or ""
        self.assertIn("DURATION_TARGET：60 秒", script)
        self.assertNotIn("DURATION_TARGET：40 秒", script)
        self.assertIn("ASPECT_RATIO：16:9", script)
        self.assertIn("## EP001-SC01｜内·卧室·日（0s - 15s）", script)
        self.assertIn("## EP001-SC04｜内·产品展示区·无背景（50s - 60s）", script)
        self.assertEqual(result["status"], "pass", result.get("machine_report"))



FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
OFFICIAL_SEGMENT_SCRIPT = (FIXTURE_DIR / "official_012e6420539c_script.md").read_text(encoding="utf-8")


class ScriptSkeletonHeadingFormatTest(unittest.TestCase):
    def test_official_segment_clock_headings_fail_machine_check(self):
        self.assertIn("### Segment 1：痛点爆发 (00:00 - 00:15)", OFFICIAL_SEGMENT_SCRIPT)
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        errors = script_skeleton_failures(OFFICIAL_SEGMENT_SCRIPT, skeleton)
        self.assertTrue(errors, errors)
        self.assertTrue(
            any("标题" in item or "格式" in item or "Segment" in item for item in errors),
            errors,
        )

    def test_conforming_headings_pass_when_ranges_sum_to_the_lock(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        self.assertEqual(script_skeleton_failures(CONFIRMED, skeleton), [])

    def test_scene_without_cast_line_fails(self):
        missing = CONFIRMED.replace("出场：女主\n女主：真的不痒了", "女主：真的不痒了", 1)
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        errors = script_skeleton_failures(missing, skeleton)
        self.assertTrue(errors, errors)
        self.assertTrue(any("出场" in item for item in errors), errors)

    def test_speaker_must_appear_in_cast_but_narration_need_not(self):
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        missing_speaker = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·日（0s - 60s）

出场：卫士
女主角：好痒……根本停不下来！
卫士（内心独白/字幕特效）：别慌，我来平复局面。
旁白（VO）：精芙美松凝胶，温和平复，安心之选。
"""
        errors = script_skeleton_failures(missing_speaker, skeleton)
        self.assertTrue(any("女主角" in item and "出场" in item for item in errors), errors)
        self.assertFalse(any("旁白" in item for item in errors), errors)

        complete = missing_speaker.replace("出场：卫士", "出场：女主角、卫士")
        self.assertEqual(script_skeleton_failures(complete, skeleton), [])

    def test_prop_and_mood_field_labels_are_not_speakers(self):
        script = """# 第001集｜皮肤卫士

- DURATION_TARGET：60 秒
- ASPECT_RATIO：16:9

## EP001-SC01｜内·卧室·夜（0s - 60s）

出场：女主、红色小怪兽们
道具：精芙美松凝胶管（U1）

▲ 女主坐在床边。

女主（O.S.）：好痒……根本停不下来！

情绪：焦躁、痛苦
"""
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        self.assertEqual(script_skeleton_failures(script, skeleton), [])


    def test_conforming_headings_fail_when_ranges_do_not_sum_to_the_lock(self):
        short = CONFIRMED.replace("（50s - 60s）", "（50s - 55s）")
        skeleton = script_skeleton({"locks": _locks(), "draft": ""})
        errors = script_skeleton_failures(short, skeleton)
        self.assertTrue(errors, errors)
        self.assertTrue(
            any("时段" in item or "合计" in item for item in errors),
            errors,
        )


class ScriptOfficialGateTest(unittest.TestCase):
    def test_official_check_stage_fails_a_scene_without_cast(self):
        from end.stage_runner import check_stage

        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        missing = CONFIRMED.replace("出场：女主\n女主：真的不痒了", "女主：真的不痒了", 1)
        (internal / "剧本.md").write_text(missing + "\n", encoding="utf-8")
        ok, report = check_stage(folder, "script", _locks())
        self.assertFalse(ok, report)
        self.assertIn("出场", report)


if __name__ == "__main__":
    unittest.main()
