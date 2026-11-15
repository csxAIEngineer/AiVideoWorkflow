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
        self.assertEqual([sum(group) for group in groups], [15, 12, 15, 15, 7])
        self.assertEqual(sum(sum(group) for group in groups), sum(seconds for _shot, seconds in EPISODE))
        self.assertEqual([shot["id"] for shot in skeleton[1]], ["S03", "S04"])
        self.assertEqual(
            [shot["heading"] for shot in skeleton[1]],
            ["[Shot 1]", "[Shot 2] At 00:07.000"],
        )
        self.assertIsNone(skeleton[1][0]["cut"])
        self.assertEqual(skeleton[1][1]["cut"], "00:07.000")
        self.assertEqual(sum(shot["duration"] for shot in skeleton[1]), 12)
        self.assertEqual([shot["id"] for shot in skeleton[4]], ["S09"])
        self.assertEqual([shot["heading"] for shot in skeleton[4]], ["[Shot 1]"])
        self.assertNotIn("At", skeleton[4][0]["heading"])
        self.assertEqual(format_cut_timecode(7), "00:07.000")
        self.assertEqual(format_cut_timecode(1.25), "00:01.250")

    def test_the_brief_carries_the_skeleton_for_the_writer_to_fill(self):
        state = _state(PAIR)
        note = packing_plan_note(state)
        self.assertIn("骨架（S03、S04）：[Shot 1] / [Shot 2] At 00:07.000", note)
        self.assertIn("[Shot 1]\n[Shot 2] At 00:07.000", note)
        self.assertIn("不要自己发明切点", note)
        self.assertIn("不要增删、合并或调换镜头", note)
        scaffold = h3_packing_scaffold(packing_groups(state))
        self.assertIn(scaffold, note)
        self.assertIn("detailed_description:\n[Shot 1]\n[Shot 2] At 00:07.000\n", scaffold)
        single = packing_plan_note(_state((("S09", 7),)))
        self.assertIn("骨架（S09）：[Shot 1]", single)
        body = _description(single.split("## 【封装单元 1/1】", 1)[1])
        self.assertEqual(body.strip(), "[Shot 1]")
        self.assertNotIn("At ", body)
        system = generator_messages(dict(state, attempt=0, source=state["storyboard"]))[0]["content"]
        self.assertIn("不要自己发明切点", system)
        self.assertIn("第一镜不写时间", system)

    def test_a_fractional_preceding_shot_sets_the_cut(self):
        skeleton = packing_shot_skeleton(_state((("S01", 1.25), ("S02", 2))))
        self.assertEqual(skeleton[0][1]["heading"], "[Shot 2] At 00:01.250")
        match = VALIDATOR_CUT.search(skeleton[0][1]["heading"])
        self.assertEqual(match.groups(), ("2", "00", "01", "250"))
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
        self.assertIn("[Shot 3]", joined)
        self.assertIn("[Shot 5]", joined)
        self.assertIn("[Shot 1]", joined)
        self.assertIn("At 00:07.000", joined)
        self.assertIn("不要沿用全集镜号", joined)
        self.assertIn("不要自己发明切点", joined)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, state)
        self.assertFalse(report["ok"])
        self.assertIn("镜号锁", report["report"])
        self.assertIn("[Shot 3]", report["report"])
        self.assertIn("[Shot 5]", report["report"])

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
        self.assertTrue(any("没有 [Shot N]" in item or "[Shot 2] At 00:07.000" in item for item in shot_skeleton_failures(absent, state)))

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
        self.assertIn("[Shot 3]", report)
        self.assertIn("[Shot 5]", report)


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
        self.assertTrue(first.startswith("[Shot 1]"))
        self.assertNotIn("At ", first)
        self.assertNotRegex(first, r"(?:At|在)\s*\d")
        self.assertTrue(second.startswith("[Shot 2] At 00:07.000"))
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
        self.assertEqual(first, "[Shot 1] 她进门。")
        self.assertTrue(second.startswith("[Shot 2] At 00:07.000"))
        self.assertIn("她坐下", second)
        self.assertNotIn("00:05.000", fixed)
        self.assertNotIn("00:00.000", fixed)
        self.assertEqual(shot_skeleton_failures(fixed, state), [])

    def test_a_missing_heading_is_put_back_without_invented_prose(self):
        state = _state(PAIR)
        fixed = apply_packing_plan(draft_of("[Shot 3] She enters the room."), state)
        lines = [line for line in _description(fixed).splitlines() if line.strip()]
        self.assertEqual(lines[0], "[Shot 1] She enters the room.")
        self.assertEqual(lines[1], "[Shot 2] At 00:07.000")
        self.assertEqual(shot_skeleton_failures(fixed, state), [])

    def test_an_extra_shot_stays_and_still_fails(self):
        state = _state(PAIR)
        draft = _pair_feed("\n".join((
            "[Shot 1] She enters.",
            "[Shot 2] At 00:01.000, she sits.",
            "[Shot 9] She leaves the room.",
        )))
        fixed = apply_packing_plan(draft, state)
        self.assertIn("[Shot 2] At 00:07.000, she sits.", fixed)
        self.assertIn("[Shot 9] She leaves the room.", fixed)
        errors = shot_skeleton_failures(fixed, state)
        self.assertTrue(any("[Shot 9]" in item for item in errors), errors)
        self.assertIn("不要自己发明切点", "\n".join(errors))

    def test_a_span_over_15_seconds_is_split_not_clamped(self):
        rows = (("S01", 6), ("S02", 5), ("S03", 16))
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
        self.assertEqual(_declared(fixed), [11, 16])
        self.assertNotIn("duration: 15", fixed)
        chunks = _chunks(fixed)
        self.assertEqual(len(chunks), 2)
        self.assertIn("[Shot 2] At 00:06.000", chunks[0])
        self.assertNotIn("[Shot 3]", chunks[0])
        second = [line for line in _description(chunks[1]).splitlines() if line.strip()]
        self.assertTrue(second[0].startswith("[Shot 1]"))
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
        self.assertIn("【封装单元 1/1】S03、S04：[Shot 1] / [Shot 2] At 00:07.000", brief)
        self.assertIn("sd-feed-engine/references/feed-mode-and-gates.md", brief)
        self.assertIn("short-drama-director/references/h3-adapter.md", brief)
        self.assertIn("short-drama-director/references/prompt-feeding-checklist.md", brief)
        self.assertEqual(
            packing_repair_targets(state["machine_report"], h3=True),
            [
                "sd-feed-engine/references/feed-mode-and-gates.md",
                "short-drama-director/references/h3-adapter.md",
                "short-drama-director/references/prompt-feeding-checklist.md",
            ],
        )
        files = suite_files("feed", "阶段：P4\n模型：h3\n" + brief)
        previous = [{"skill": relative, "text": "旧节点"} for relative in files]
        nodes = select_nodes(files, brief, previous)
        self.assertIn("short-drama-director/references/h3-adapter.md", nodes)
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
        self.assertIn("[Shot 1]", result["feed"])
        self.assertIn("[Shot 2] At 00:07.000", result["feed"])
        self.assertNotIn("[Shot 9]", result["feed"])
        self.assertIn("duration: 12", result["feed"])
        repair = next(item for item in prompts if "镜号锁没过" in item)
        self.assertIn("不要自己发明切点", repair)
        self.assertIn("[Shot 2] At 00:07.000", repair)
        self.assertIn("short-drama-director/references/h3-adapter.md", repair)


if __name__ == "__main__":
    unittest.main()
