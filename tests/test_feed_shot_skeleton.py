"""Packed H3 shots use a program-owned local skeleton.

Cut times come from the accepted storyboard. The packing model does not
invent them. ``[Shot 1]`` has no timestamp. Later shots are
``[Shot N] At MM:SS.mmm`` at the sum of earlier shots in that unit only.

    python3 -m unittest tests.test_feed_shot_skeleton
"""

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.checks import check_feed_draft
from Agents.workers.critic import machine_check_skill
from Agents.workers.feed import (
    apply_packing_plan,
    format_cut_timecode,
    generator_messages,
    h3_packing_scaffold,
    packing_groups,
    packing_plan_note,
    packing_repair_targets,
    packing_shot_skeleton,
    run_feed_loop,
    shot_skeleton_failures,
)
from Agents.workers.generator import select_nodes
from AI.skills.registry import suite_files
from end.stage_runner import check_stage, feed_duration_failures

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "AI" / "skills" / "manju-laoli-skill" / "short-drama-director" / "scripts" / "validate_prompt.py"
_SPEC = importlib.util.spec_from_file_location("shot_skeleton_validate_prompt", VALIDATOR)
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)
validate_h3 = _VALIDATOR.validate_h3

# Unit 2 is S03=7 + S04=5. Unit 5 is S09 alone.
EPISODE = (
    ("S01", 8),
    ("S02", 7),
    ("S03", 7),
    ("S04", 5),
    ("S05", 8),
    ("S06", 7),
    ("S07", 8),
    ("S08", 7),
    ("S09", 7),
)
PAIR = (("S03", 7), ("S04", 5))
VALIDATOR_CUT = re.compile(
    r"\[Shot\s+(\d+)\][^\n]{0,14}?(?:At|在)\s*(\d{1,2}):(\d{2})\.(\d{3})"
)
ACCEPT = '{"verdict":"accept","issues":[]}'


def _board(rows):
    lines = []
    for shot_id, seconds in rows:
        lines.append("## %s" % shot_id)
        lines.append("时长：%s" % seconds)
    return "\n".join(lines)


def _state(rows, model="h3"):
    return {
        "storyboard": _board(rows),
        "shots": [{"id": shot_id, "duration": seconds} for shot_id, seconds in rows],
        "locks": {"model": model, "material_mode": "M1", "track": "1"},
    }


def _unit(index, total, duration, description):
    return "\n".join((
        "## 【封装单元 %s/%s】" % (index, total),
        "duration: %s" % duration,
        "subject_definitions:",
        "<Subject 1>: a woman.",
        "detailed_description:",
        description,
        "overall_soundscape:",
        "room",
        "non_diegetic_music:",
        "N/A",
    ))


def _pair_feed(description, duration=12):
    return _unit(1, 1, duration, description) + "\n"


def _description(text):
    if "\n分镜:\n" in text:
        body = text.split("\n分镜:\n", 1)[1]
        for stop in ("\n台词:", "\n音效 / BGM:", "\noverall_soundscape:"):
            if stop in body:
                return body.split(stop, 1)[0]
        return body
    return text.split("detailed_description:\n", 1)[1].split("\noverall_soundscape:", 1)[0]


def _declared(text):
    return [float(item) for item in re.findall(r"(?m)^duration:\s*(\d+(?:\.\d+)?)", text)]


def _chunks(text):
    from end.product_names import UNIT_HEAD_ANY

    heads = list(UNIT_HEAD_ANY.finditer(text))
    found = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        found.append(text[match.start():end])
    return found


def _h3(description, duration=12):
    return "\n".join((
        "duration: %s" % duration,
        "subject_definitions:",
        "<Subject 1>: a woman in the room.",
        "detailed_description:",
        description,
        "overall_soundscape:",
        "Room tone.",
        "non_diegetic_music:",
        "N/A",
    ))


class ShotSkeletonPlanTest(unittest.TestCase):
    def test_unit_two_restarts_locally_and_unit_five_has_no_cut(self):
        state = _state(EPISODE)
        skeleton = packing_shot_skeleton(state)
        groups = packing_groups(state)
        self.assertEqual([sum(group) for group in groups], [22, 20, 22])
        self.assertEqual(sum(sum(group) for group in groups), sum(seconds for _shot, seconds in EPISODE))
        self.assertEqual([shot["id"] for shot in skeleton[1]], ["S04", "S05", "S06"])
        self.assertEqual(
            [shot["heading"] for shot in skeleton[1]],
            [
                "分镜1（00:00.000-00:05.000）：",
                "分镜2（00:05.000-00:13.000）：",
                "分镜3（00:13.000-00:20.000）：",
            ],
        )
        self.assertEqual(skeleton[1][0]["cut"], "00:00.000")
        self.assertEqual(skeleton[1][1]["cut"], "00:05.000")
        self.assertEqual(sum(shot["duration"] for shot in skeleton[1]), 20)
        self.assertEqual([shot["id"] for shot in skeleton[2]], ["S07", "S08", "S09"])
        self.assertEqual(skeleton[2][0]["heading"], "分镜1（00:00.000-00:08.000）：")
        self.assertEqual(skeleton[2][0]["cut"], "00:00.000")
        self.assertEqual(format_cut_timecode(7), "00:07.000")
        self.assertEqual(format_cut_timecode(1.25), "00:01.250")

    def test_the_brief_carries_the_skeleton_for_the_writer_to_fill(self):
        state = _state(PAIR)
        note = packing_plan_note(state)
        self.assertIn("骨架（S03、S04）：分镜1（00:00.000-00:07.000）： / 分镜2（00:07.000-00:12.000）：", note)
        self.assertIn("分镜1（00:00.000-00:07.000）：", note)
        self.assertIn("不要自己发明切点", note)
        self.assertIn("不要增删、合并或调换镜头", note)
        scaffold = h3_packing_scaffold(packing_groups(state))
        self.assertIn(scaffold, note)
        self.assertIn("分镜:\n分镜1（00:00.000-00:07.000）：\n分镜2（00:07.000-00:12.000）：\n", scaffold)
        single = packing_plan_note(_state((("S09", 7),)))
        self.assertIn("骨架（S09）：分镜1（00:00.000-00:07.000）：", single)
        body = _description(single.split("## 【封装单元 1/1】", 1)[1])
        self.assertTrue(body.strip().startswith("分镜1（00:00.000-00:07.000）："), body)
        self.assertNotIn("[Shot", body)
        system = generator_messages(dict(state, attempt=0, source=state["storyboard"]))[0]["content"]
        self.assertIn("不要自己发明切点", system)
        self.assertIn("分镜1（00:00.000-", system)

    def test_a_fractional_preceding_shot_sets_the_cut(self):
        skeleton = packing_shot_skeleton(_state((("S01", 1.25), ("S02", 2))))
        self.assertEqual(skeleton[0][1]["heading"], "分镜2（00:01.250-00:03.250）：")
        self.assertIsNone(VALIDATOR_CUT.search("[Shot 1]"))

    def test_official_heading_matches_the_validator_and_h2_still_rejects_shot_1(self):
        match = VALIDATOR_CUT.search("[Shot 2] At 00:07.000")
        self.assertEqual(match.groups(), ("2", "00", "07", "000"))
        bad, _warns, _stats = validate_h3(_h3("[Shot 1] At 00:00.000 she stands.", 7))
        self.assertTrue(any("H2" in item and "不得带时间戳" in item for item in bad), bad)
        ok, _warns, _stats = validate_h3(_h3(
            "[Shot 1] <Subject 1> enters.\n"
            "[Shot 2] At 00:07.000, the camera cuts to her face."
        ))
        h2 = [
            item for item in ok
            if item.startswith("H2") and any(mark in item for mark in ("时间戳", "缺切点", "未递增", "未严格递增"))
        ]
        self.assertEqual(h2, [], ok)


class ShotSkeletonGateTest(unittest.TestCase):
    def test_two_local_shots_pass_and_a_single_shot_has_no_cut(self):
        pair = _pair_feed("[Shot 1] <Subject 1> enters.\n[Shot 2] At 00:07.000, the camera cuts to her.")
        single = _unit(1, 1, 7, "[Shot 1] <Subject 1> stays.") + "\n"
        pair_state = _state(PAIR)
        single_state = _state((("S09", 7),))
        self.assertEqual(shot_skeleton_failures(pair, pair_state), [])
        self.assertEqual(shot_skeleton_failures(single, single_state), [])
        with patch("end.gates.check_feed", return_value=(0, "")):
            pair_report = check_feed_draft(pair, pair_state)
            single_report = check_feed_draft(single, single_state)
        self.assertTrue(pair_report["ok"], pair_report)
        self.assertTrue(single_report["ok"], single_report)

    def test_episode_wide_numbers_fail(self):
        draft = _pair_feed("[Shot 3] She enters.\n[Shot 5] At 00:12.000, she sits.")
        state = _state(PAIR)
        errors = shot_skeleton_failures(draft, state)
        joined = "\n".join(errors)
        self.assertIn("分镜3", joined)
        self.assertIn("分镜5", joined)
        self.assertIn("分镜1", joined)
        self.assertIn("00:07.000", joined)
        self.assertIn("不要沿用全集镜号", joined)
        self.assertIn("不要自己发明切点", joined)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, state)
        self.assertFalse(report["ok"])
        self.assertIn("镜号锁", report["report"])
        self.assertIn("分镜3", report["report"])
        self.assertIn("分镜5", report["report"])

    def test_missing_or_wrong_cut_fails(self):
        state = _state(PAIR)
        wrong = _pair_feed("[Shot 1] She enters.\n[Shot 2] At 00:05.000, she sits.")
        missing = _pair_feed("[Shot 1] She enters.\n[Shot 2] she sits.")
        absent = _pair_feed("[Shot 1] She enters.")
        for draft in (wrong, missing, absent):
            errors = shot_skeleton_failures(draft, state)
            self.assertTrue(errors, draft)
            self.assertIn("00:07.000", "\n".join(errors))
        self.assertTrue(any("切点必须是" in item for item in shot_skeleton_failures(wrong, state)))
        self.assertTrue(any("切点必须是" in item for item in shot_skeleton_failures(missing, state)))
        self.assertTrue(any("没有分镜" in item or "分镜2（00:07.000-" in item or "分镜2" in item for item in shot_skeleton_failures(absent, state)))

    def test_a_timestamp_on_the_first_shot_fails(self):
        draft = _unit(1, 1, 7, "[Shot 1] At 00:00.000 she stands.") + "\n"
        state = _state((("S09", 7),))
        errors = shot_skeleton_failures(draft, state)
        self.assertTrue(any("不能带时间戳" in item and "00:00.000" in item for item in errors), errors)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, state)
        self.assertFalse(report["ok"])
        self.assertIn("不能带时间戳", report["report"])

    def test_check_stage_fails_global_numbers(self):
        state = _state(PAIR)
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "分镜.md").write_text(state["storyboard"] + "\n", encoding="utf-8")
        (internal / "feed.md").write_text(
            _pair_feed("[Shot 3] She enters.\n[Shot 5] At 00:12.000, she sits."),
            encoding="utf-8",
        )
        (folder / "spec.json").write_text(
            json.dumps({"shots": state["shots"]}),
            encoding="utf-8",
        )
        with patch("end.stage_runner.check_feed", return_value=(0, "")):
            passed, report = check_stage(folder, "feed", state["locks"])
        self.assertFalse(passed)
        self.assertIn("镜号锁", report)
        self.assertIn("分镜3", report)
        self.assertIn("分镜5", report)


class ShotSkeletonPatchTest(unittest.TestCase):
    def test_apply_repairs_global_numbers_and_the_duration_lock_holds(self):
        state = _state(PAIR)
        draft = _pair_feed(
            "[Shot 3] At 00:15.000, she enters the room.\n[Shot 5] At 00:22.000, she sits.",
            duration=9,
        )
        fixed = apply_packing_plan(draft, state)
        self.assertNotIn("[Shot 3]", fixed)
        self.assertNotIn("[Shot 5]", fixed)
        self.assertNotIn("00:15.000", fixed)
        self.assertNotIn("00:22.000", fixed)
        self.assertIn("she enters the room", fixed)
        self.assertIn("she sits", fixed)
        self.assertIn("\nduration: 12\n", fixed)
        self.assertNotIn("duration: 9", fixed)
        self.assertNotIn("duration: 15", fixed)
        first, second = [line for line in _description(fixed).splitlines() if line.strip()][:2]
        self.assertTrue(first.startswith("分镜1（00:00.000-00:07.000）："), first)
        self.assertNotIn("At ", first)
        self.assertNotRegex(first, r"(?:At|在)\s*\d")
        self.assertTrue(second.startswith("分镜2（00:07.000-00:12.000）："), second)
        self.assertIn("she sits", second)
        self.assertEqual(shot_skeleton_failures(fixed, state), [])
        self.assertFalse(feed_duration_failures(fixed, state["storyboard"], state["shots"]))
        self.assertEqual(apply_packing_plan(fixed, state), fixed)
        self.assertEqual(_declared(fixed), [12])

    def test_apply_converts_a_chinese_cut_and_strips_the_first_timestamp(self):
        state = _state(PAIR)
        draft = _pair_feed("[Shot 1] At 00:00.000, 她进门。\n[Shot 2] 在 00:05.000 处，她坐下。")
        self.assertTrue(shot_skeleton_failures(draft, state))
        fixed = apply_packing_plan(draft, state)
        first, second = [line for line in _description(fixed).splitlines() if line.strip()][:2]
        self.assertTrue(first.startswith("分镜1（00:00.000-00:07.000）："), first)
        self.assertIn("她进门。", first)
        self.assertTrue(second.startswith("分镜2（00:07.000-00:12.000）："), second)
        self.assertIn("她坐下", second)
        self.assertNotIn("00:05.000", fixed)
        self.assertIn("00:00.000", fixed)
        self.assertEqual(shot_skeleton_failures(fixed, state), [])

    def test_a_missing_heading_is_put_back_without_invented_prose(self):
        state = _state(PAIR)
        fixed = apply_packing_plan(draft_of("[Shot 3] She enters the room."), state)
        lines = [line for line in _description(fixed).splitlines() if line.strip()]
        self.assertTrue(lines[0].startswith("分镜1（00:00.000-00:07.000）："), lines[0])
        self.assertIn("She enters the room.", lines[0])
        self.assertTrue(lines[1].startswith("分镜2（00:07.000-00:12.000）："), lines[1])
        errors = shot_skeleton_failures(fixed, state)
        self.assertTrue(any("没有正文" in item or "分镜2" in item for item in errors), errors)

    def test_an_extra_shot_stays_and_still_fails(self):
        state = _state(PAIR)
        draft = _pair_feed("\n".join((
            "[Shot 1] She enters.",
            "[Shot 2] At 00:01.000, she sits.",
            "[Shot 9] She leaves the room.",
        )))
        fixed = apply_packing_plan(draft, state)
        self.assertIn("分镜2（00:07.000-00:12.000）：", fixed)
        self.assertIn("she sits.", fixed)
        self.assertIn("分镜", fixed)
        self.assertIn("She leaves the room.", fixed)
        errors = shot_skeleton_failures(fixed, state)
        self.assertTrue(any("分镜9" in item or "分镜3" in item for item in errors), errors)
        self.assertIn("不要自己发明切点", "\n".join(errors))

    def test_a_span_over_30_seconds_is_split_not_clamped(self):
        rows = (("S01", 6), ("S02", 5), ("S03", 31))
        state = _state(rows)
        draft = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 15",
            "subject_definitions:",
            "<Subject 1>: a woman.",
            "detailed_description:",
            "[Shot 1] She waits.",
            "[Shot 2] At 00:06.000, he enters.",
            "[Shot 3] At 00:11.000, the long beat.",
            "overall_soundscape:",
            "room",
            "non_diegetic_music:",
            "N/A",
        ))
        fixed = apply_packing_plan(draft, state)
        self.assertEqual(_declared(fixed), [11, 31])
        self.assertNotIn("duration: 15", fixed)
        self.assertNotIn("duration: 30", fixed)
        chunks = _chunks(fixed)
        self.assertEqual(len(chunks), 2)
        self.assertIn("分镜2（00:06.000-00:11.000）：", chunks[0])
        self.assertNotIn("分镜3", chunks[0])
        second = [line for line in _description(chunks[1]).splitlines() if line.strip()]
        self.assertTrue(second[0].startswith("分镜1（"), second)
        self.assertNotIn("At ", second[0])
        self.assertIn("the long beat", second[0])
        self.assertEqual(len(second), 1)
        errors = feed_duration_failures(fixed, state["storyboard"], state["shots"])
        self.assertTrue(any("必须把这一镜拆开" in item for item in errors), errors)
        self.assertFalse(any("时长被压缩" in item for item in errors), errors)
        self.assertEqual(shot_skeleton_failures(fixed, state), [])

    def test_seedance_keeps_its_own_shot_lines(self):
        draft = _pair_feed("[Shot 3] She enters.\n[Shot 5] At 00:12.000, she sits.")
        state = _state(PAIR, model="2.5")
        self.assertEqual(apply_packing_plan(draft, state), draft)
        note = packing_plan_note(state)
        self.assertNotIn("骨架（", note)
        self.assertNotIn("subject_definitions:", note)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, state)
        self.assertTrue(report["ok"], report)
        self.assertNotIn("镜号锁", report["report"])


def draft_of(description):
    return _pair_feed(description)


class ShotSkeletonRepairTest(unittest.TestCase):
    def test_the_repair_brief_tells_the_writer_to_restore_the_skeleton(self):
        state = _state(PAIR)
        draft = _pair_feed("[Shot 3] She enters.\n[Shot 5] At 00:12.000, she sits.")
        state.update({
            "attempt": 1,
            "draft": draft,
            "source": state["storyboard"],
            "critic_report": {},
            "machine_report": {
                "errors": shot_skeleton_failures(draft, state),
                "warnings": [],
            },
        })
        brief = generator_messages(state)[-1]["content"]
        self.assertIn("镜号锁没过", brief)
        self.assertIn("不要自己发明切点", brief)
        self.assertIn("不要沿用全集镜号", brief)
        self.assertIn("分镜1（00:00.000-00:07.000）：", brief)
        self.assertIn("分镜2（00:07.000-00:12.000）：", brief)
        # 万相封装只挂 SKILL.md 和 wan-adapter（288187b）；镜号锁修补点名这两个写手。
        self.assertIn("sd-feed-engine/SKILL.md", brief)
        self.assertIn("short-drama-director/references/wan-adapter.md", brief)
        self.assertEqual(
            packing_repair_targets(state["machine_report"], h3=True),
            [
                "sd-feed-engine/SKILL.md",
                "short-drama-director/references/wan-adapter.md",
            ],
        )
        files = suite_files("feed", "阶段：P4\n模型：h3\n" + brief)
        previous = [{"skill": relative, "text": "旧节点"} for relative in files]
        nodes = select_nodes(files, brief, previous)
        self.assertIn("short-drama-director/references/wan-adapter.md", nodes)
        skill = machine_check_skill("feed")
        self.assertIn("镜号锁", skill)
        self.assertIn("shot_skeleton_failures", skill)
        self.assertIn("不要要求生成员另写切点", skill)

    def test_a_repair_pass_keeps_the_skeleton_after_an_extra_shot(self):
        state_rows = PAIR
        dirty = _pair_feed("\n".join((
            "[Shot 1] <Subject 1> enters.",
            "[Shot 2] At 00:01.000, she sits.",
            "[Shot 9] She leaves the room.",
        )))
        clean = _pair_feed("[Shot 1] <Subject 1> enters.\n[Shot 2] At 00:07.000, she sits.")
        prompts = []
        directors = {"n": 0}

        def complete(role, messages):
            prompts.append(messages[-1]["content"])
            if role == "critic":
                return ACCEPT
            if role == "director":
                directors["n"] += 1
                body = dirty if directors["n"] == 1 else clean
                return json.dumps({"feed": body}, ensure_ascii=False)
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        def checker(text, state):
            errors = list(shot_skeleton_failures(text, state))
            errors.extend(feed_duration_failures(
                text,
                state.get("storyboard") or "",
                state.get("shots"),
            ))
            return {
                "ok": not errors,
                "errors": errors,
                "warnings": [],
                "fatal": "",
                "needs_human": False,
            }

        result = run_feed_loop(
            _board(state_rows),
            shots=[{"id": shot_id, "duration": seconds} for shot_id, seconds in state_rows],
            locks={"model": "h3"},
            max_attempts=2,
            complete=complete,
            checker=checker,
        )
        self.assertEqual(result["status"], "pass", result.get("machine_report"))
        self.assertEqual(result["attempts"], 2)
        self.assertIn("分镜1（00:00.000-00:07.000）：", result["feed"])
        self.assertIn("分镜2（00:07.000-00:12.000）：", result["feed"])
        self.assertNotIn("[Shot 9]", result["feed"])
        self.assertIn("duration: 12", result["feed"])
        # 循环修补走 _repair_user（4de97e1），不再发 generator_messages 的「镜号锁没过」
        # 简报；该简报内容由 test_the_repair_brief_tells_the_writer_to_restore_the_skeleton
        # 覆盖。这里只校验修补结果保留了骨架（上面的断言）。
        self.assertTrue(any("分镜2（00:07.000-00:12.000）：" in item for item in prompts))


class UnitAssetLineTest(unittest.TestCase):
    def _state(self):
        rows = (
            ("S03", 7, ["图1", "图4"]),
            ("S04", 5, ["图2", "图5", "图6"]),
        )
        lines = []
        shots = []
        for shot_id, seconds, images in rows:
            lines.append("## %s" % shot_id)
            lines.append("时长：%s" % seconds)
            lines.append("资产：" + "，".join(images))
            shots.append({"id": shot_id, "duration": seconds, "images": list(images)})
        return {
            "storyboard": "\n".join(lines),
            "shots": shots,
            "locks": {"model": "h3", "material_mode": "M1", "track": "1"},
        }

    def test_the_program_line_replaces_a_model_list(self):
        state = self._state()
        draft = _pair_feed("\n".join((
            "[Shot 1] The man waits.",
            "资产：图5",
            "[Shot 2] At 00:01.000, she sits.",
        )))
        fixed = apply_packing_plan(draft, state)
        self.assertIn("duration: 12\n资产：图1，图4，图2，图5，图6\n", fixed)
        self.assertEqual(fixed.count("资产："), 1)
        self.assertIn("The man waits.", fixed)
        self.assertNotIn("资产：图5", fixed)
        self.assertNotIn("资产：", _description(fixed))
        self.assertEqual(shot_skeleton_failures(fixed, state), [])
        self.assertEqual(apply_packing_plan(fixed, state), fixed)

    def test_the_scaffold_already_carries_the_line(self):
        state = self._state()
        note = packing_plan_note(state)
        self.assertIn("资产：图1，图4，图2，图5，图6", note)
        self.assertIn("不要改这行", note)
        self.assertIn("不要另写一份资产列表", note)

    def test_an_invented_line_is_removed_when_the_storyboard_names_nothing(self):
        state = _state(PAIR)
        draft = _pair_feed("\n".join((
            "[Shot 1] She enters.",
            "资产：图7",
            "[Shot 2] At 00:07.000, she sits.",
        )))
        fixed = apply_packing_plan(draft, state)
        self.assertNotIn("资产：", fixed)
        self.assertNotIn("图7", fixed)
        self.assertIn("She enters.", fixed)
        self.assertIn("分镜2（00:07.000-00:12.000）：", fixed)
        self.assertEqual(apply_packing_plan(fixed, state), fixed)

    def test_the_line_can_come_from_the_storyboard_text(self):
        state = _state(PAIR)
        state["storyboard"] = "\n".join((
            "## S03",
            "时长：7",
            "资产：图1，图4",
            "## S04",
            "时长：5",
            "资产：图2",
        ))
        fixed = apply_packing_plan(
            _pair_feed("[Shot 1] She waits.\n[Shot 2] At 00:01.000, she sits."),
            state,
        )
        self.assertIn("资产：图1，图4，图2", fixed)
        self.assertEqual(fixed.count("资产："), 1)
        self.assertEqual(apply_packing_plan(fixed, state), fixed)


class PackingSkeletonLocksTest(unittest.TestCase):
    def test_ws10_seedance_skeleton_uses_the_same_locks_as_packing_groups(self):
        state = {
            "storyboard": "",
            "shots": [{"duration": 10} for _ in range(4)],
            "locks": {"model": "2.0"},
        }
        skeleton = packing_shot_skeleton(state)
        groups = packing_groups(state)
        self.assertEqual(len(skeleton), len(groups))
        self.assertEqual(len(skeleton), 4)
        self.assertTrue(all(len(unit) == 1 for unit in skeleton))


if __name__ == "__main__":
    unittest.main()
