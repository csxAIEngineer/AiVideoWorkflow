"""Packing durations and spoken lines are fixed around the feed model call.

No live model. The fake complete returns a short draft, and the worker
corrects it before the machine check.

    python3 -m unittest tests.test_feed_packing_plan
"""

import json
import re
import unittest

from Agents.workers.feed import (
    apply_packing_plan,
    correct_unit_durations,
    generator_messages,
    packing_groups,
    restore_spoken_lines,
    run_feed_loop,
)
from end.stage_runner import feed_duration_failures, group_shot_durations

SHOTS = [6, 5, 5, 6, 6, 4, 6, 7, 5, 6]
PLANNED = [11, 11, 10, 13, 11]
LINE = "小君：（轻叹）三天没洗，贴得跟浆糊似的。"
EXACT = "男主：别改了。"
MISSING = "小君：怎么，一直看着我？"
ACCEPT = '{"verdict":"accept","issues":[]}'


def _board(lines):
    rows = []
    for index, seconds in enumerate(SHOTS, 1):
        rows.append("## S%02d" % index)
        rows.append("时长：%s" % seconds)
    for line in lines:
        rows.append("对白：%s" % line)
    return "\n".join(rows)


def _state(lines):
    return {
        "storyboard": _board(lines),
        "shots": [{"duration": seconds} for seconds in SHOTS],
    }


def _units(durations, bodies):
    total = len(durations)
    blocks = []
    for index, (seconds, body) in enumerate(zip(durations, bodies), 1):
        blocks.append("\n".join((
            "## 【封装单元 %s/%s】" % (index, total),
            "duration: %s" % seconds,
            "detailed_description:",
            body,
        )))
    return "\n\n".join(blocks)


def _declared(text):
    found = []
    for match in re.finditer(r"(?m)^duration:\s*(\d+(?:\.\d+)?)", text):
        found.append(float(match.group(1)))
    return found


class PackingPlanTest(unittest.TestCase):
    def test_fifteen_second_groups_sum_the_storyboard(self):
        groups = group_shot_durations(SHOTS)
        self.assertEqual([sum(group) for group in groups], PLANNED)
        self.assertEqual([sum(group) for group in packing_groups(_state([]))], PLANNED)
        self.assertEqual(sum(PLANNED), 56)

    def test_a_short_draft_is_overwritten_with_the_planned_sums(self):
        draft = _units([5, 5, 6, 6, 5], ["a", "b", "c", "d", "e"])
        fixed = correct_unit_durations(draft, group_shot_durations(SHOTS))
        self.assertEqual(_declared(fixed), PLANNED)
        self.assertNotIn("duration: 5\n", fixed)
        errors = feed_duration_failures(fixed, _board([]), _state([])["shots"])
        self.assertFalse(errors, errors)

    def test_a_compound_duration_line_is_replaced_by_the_unit_sum(self):
        draft = _units([5, 5, 6, 6, 5], ["a", "b", "c", "d", "e"]).replace(
            "duration: 5",
            "duration: 5+5+6+6+5 = 27s",
            1,
        )
        fixed = correct_unit_durations(draft, group_shot_durations(SHOTS))
        self.assertNotIn("27s", fixed)
        self.assertNotIn("5+5+6+6+5", fixed)
        self.assertEqual(_declared(fixed), PLANNED)

    def test_correct_durations_are_left_unchanged(self):
        draft = _units(PLANNED, ["a", "b", "c", "d", "e"])
        self.assertEqual(correct_unit_durations(draft, group_shot_durations(SHOTS)), draft)

    def test_a_different_unit_count_is_not_rewritten(self):
        draft = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 27",
            "<d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>",
        ))
        result = apply_packing_plan(draft, _state([LINE, MISSING]))
        self.assertIn("duration: 27", result)
        self.assertNotIn("duration: 11", result)
        self.assertNotIn("## 【封装单元 2/", result)
        self.assertIn(LINE, result)
        self.assertNotIn(MISSING, result)
        errors = feed_duration_failures(result, _board([LINE, MISSING]), _state([])["shots"])
        self.assertTrue(any("时长被压缩" in item for item in errors), errors)
        self.assertTrue(any(MISSING in item for item in errors), errors)
        self.assertFalse(any(LINE in item for item in errors), errors)

    def test_a_stripped_d_tag_is_restored_and_an_exact_line_stays(self):
        stripped = "<d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>"
        restored = restore_spoken_lines(stripped, [LINE])
        self.assertIn("<d>[Chinese] %s</d>" % LINE, restored)
        exact = "<d>[Chinese] %s</d>" % LINE
        self.assertEqual(restore_spoken_lines(exact, [LINE]), exact)
        bare = "三天没洗，贴得跟浆糊似的。"
        self.assertEqual(restore_spoken_lines(bare, [LINE]), bare)
        absent = "## 【封装单元 1/1】\n没有这句。\n"
        self.assertEqual(restore_spoken_lines(absent, [MISSING]), absent)

    def test_the_brief_and_the_written_draft_use_the_plan_without_a_model(self):
        bad = _units(
            [5, 5, 6, 6, 5],
            [
                "<d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>",
                "<d>[Chinese] %s</d>" % EXACT,
                "She waits.",
                "She waits.",
                "She waits.",
            ],
        )
        prompts = []

        def complete(role, messages):
            prompts.append(messages[-1]["content"])
            if role == "critic":
                return ACCEPT
            if role == "director":
                return json.dumps({"feed": bad}, ensure_ascii=False)
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        def checker(text, state):
            errors = feed_duration_failures(
                text,
                state.get("storyboard") or "",
                state.get("shots"),
            )
            return {
                "ok": not errors,
                "errors": errors,
                "warnings": [],
                "fatal": "",
                "needs_human": False,
            }

        board = _board([LINE, EXACT])
        result = run_feed_loop(
            board,
            shots=[{"duration": seconds} for seconds in SHOTS],
            max_attempts=1,
            complete=complete,
            checker=checker,
        )
        self.assertEqual(result["status"], "pass", result.get("machine_report"))
        self.assertEqual(result["attempts"], 1)
        self.assertIn("6+5=11", prompts[0])
        self.assertIn("【封装单元 1/5】镜头 1-2：6+5=11", prompts[0])
        self.assertIn(LINE, prompts[0])
        self.assertIn(EXACT, prompts[0])
        self.assertEqual(_declared(result["feed"]), PLANNED)
        self.assertIn(LINE, result["feed"])
        self.assertIn("<d>[Chinese] %s</d>" % EXACT, result["feed"])
        repair = generator_messages({
            "attempt": 1,
            "draft": bad,
            "storyboard": board,
            "shots": [{"duration": seconds} for seconds in SHOTS],
            "critic_report": {},
            "machine_report": {"errors": ["时长被压缩"], "warnings": []},
        })[-1]["content"]
        self.assertIn("任务：建议修补", repair)
        self.assertIn("6+7=13", repair)
        self.assertIn(LINE, repair)
