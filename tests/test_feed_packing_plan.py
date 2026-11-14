"""Packing durations and spoken lines are fixed around the feed model call.

No live model. The fake complete returns a short draft, and the worker
corrects it before the machine check.

    python3 -m unittest tests.test_feed_packing_plan
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.workers.feed import (
    H3_SECTIONS,
    apply_packing_plan,
    correct_unit_durations,
    generator_messages,
    h3_packing_scaffold,
    packing_groups,
    packing_plan_note,
    packing_repair_targets,
    restore_spoken_lines,
    run_feed_loop,
)
from Agents.workers.generator import select_nodes
from AI.skills.registry import suite_files
from end.gates import H3_STRUCTURE_MARK, _H3_SECTIONS, h3_structure_report
from end.seam_ledger import packed_units
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


def _unit_chunks(text):
    from end.product_names import UNIT_HEAD_ANY

    heads = list(UNIT_HEAD_ANY.finditer(text))
    chunks = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        chunks.append(text[match.start():end].strip())
    return chunks


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

    def test_a_different_unit_count_is_materialized_from_the_plan(self):
        draft = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 27",
            "<d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>",
        ))
        result = apply_packing_plan(draft, _state([LINE, MISSING]))
        chunks = _unit_chunks(result)
        self.assertEqual(len(chunks), len(PLANNED), result)
        self.assertEqual(_declared(result), PLANNED)
        self.assertNotIn("duration: 27", result)
        self.assertTrue(chunks[0].startswith("## 【封装单元 1/%s】" % len(PLANNED)))
        self.assertTrue(chunks[-1].startswith("## 【封装单元 %s/%s】" % (len(PLANNED), len(PLANNED))))
        self.assertIn(LINE, chunks[-1])
        self.assertIn(MISSING, chunks[-1])
        for word in ("末态继承", "拼接节点", "换机位", "可变数据"):
            self.assertNotIn(word, result)
        errors = feed_duration_failures(result, _board([LINE, MISSING]), _state([])["shots"])
        self.assertFalse(errors, errors)

    def test_a_partial_first_group_keeps_plan_durations_and_later_lines(self):
        durations = [6, 5, 5, 4, 5, 5, 4]
        board = "\n".join((
            "## S01",
            "动作：她擦杯子",
            "时长：6",
            "对白：自语：杯子还是脏的。",
            "旁白：但习惯，不代表没事。",
            "## S02",
            "动作：男人在门口",
            "时长：5",
            "## S03",
            "动作：他走进来",
            "时长：5",
            "对白：男主（温和）：「别改了。」",
            "## S04",
            "动作：他坐下",
            "时长：4",
            "对白：我替她做了个决定。今晚，眼睛先下班。",
            "## S05",
            "动作：灯暗了",
            "时长：5",
            "## S06",
            "动作：她抬头",
            "时长：5",
            "## S07",
            "动作：她笑",
            "时长：4",
            "对白：小君（带笑）：「怎么，一直看着我？」",
        ))
        draft = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 6",
            "detailed_description:",
            "[Shot 1] She wipes a glass. <d>[Chinese] 杯子还是脏的。</d>",
            "[Shot 2] At 00:06.000, a man stands in the doorway. Xiaojun off screen: habit is not the same as fine.",
        ))
        state = {
            "storyboard": board,
            "shots": [{"duration": seconds} for seconds in durations],
            "locks": {"model": "h3"},
        }
        result = apply_packing_plan(draft, state)
        chunks = _unit_chunks(result)
        self.assertEqual(len(chunks), 3, result)
        self.assertEqual(_declared(result), [11, 14, 9])
        self.assertNotIn("\nduration: 6\n", "\n" + result)
        self.assertIn("[Shot 1]", chunks[0])
        self.assertIn("[Shot 2]", chunks[0])
        self.assertIn("At 00:06.000", chunks[0])
        self.assertIn("自语：杯子还是脏的。", chunks[0])
        self.assertIn("但习惯，不代表没事。", chunks[0])
        self.assertIn("男主（温和）：「别改了。」", chunks[1])
        self.assertIn("我替她做了个决定。今晚，眼睛先下班。", chunks[1])
        self.assertNotIn("男主（温和）：「别改了。」", chunks[0])
        self.assertNotIn("小君（带笑）：「怎么，一直看着我？」", chunks[1])
        self.assertIn("小君（带笑）：「怎么，一直看着我？」", chunks[2])
        self.assertIn("\ndetailed_description:\n", chunks[1])
        self.assertNotIn("overall_soundscape:\n<d>", result)
        for word in ("末态继承", "拼接节点", "换机位", "可变数据"):
            self.assertNotIn(word, result)
        errors = feed_duration_failures(result, board, state["shots"])
        self.assertFalse(errors, errors)

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
        self.assertIn("sd-feed-engine/references/feed-mode-and-gates.md", repair)
        self.assertIn("short-drama-director/references/h3-adapter.md", repair)

    def test_h3_brief_locks_headings_durations_and_section_lines(self):
        state = _state([LINE])
        state["locks"] = {"model": "h3"}
        note = packing_plan_note(state)
        self.assertEqual(H3_SECTIONS, _H3_SECTIONS)
        self.assertIn("6+5=11", note)
        self.assertIn("【封装单元 1/5】镜头 1-2：6+5=11", note)
        self.assertIn(LINE, note)
        scaffold = h3_packing_scaffold(packing_groups(state))
        self.assertIn(scaffold, note)
        self.assertIn("不要输出 JSON", note)
        self.assertIn("不要写 summary 或 retention_analysis", note)
        from end.product_names import H3_SUBJECT_SOUND_RULE

        self.assertIn("<s1>", H3_SUBJECT_SOUND_RULE)
        self.assertIn("[Shot 1]", H3_SUBJECT_SOUND_RULE)
        self.assertIn("overall_soundscape", H3_SUBJECT_SOUND_RULE)
        self.assertIn(H3_SUBJECT_SOUND_RULE, note)
        self.assertNotIn("\nsummary:", scaffold)
        self.assertNotIn("\nretention_analysis:", scaffold)
        chunks = scaffold.split("## 【封装单元 ")
        self.assertEqual(len(chunks), 6)
        for index, seconds in enumerate(PLANNED, 1):
            block = "## 【封装单元 " + chunks[index]
            self.assertTrue(block.startswith("## 【封装单元 %s/5】" % index))
            self.assertIn("\nduration: %s\n" % seconds, block)
            positions = [block.index("\n%s:" % name) for name in H3_SECTIONS]
            self.assertEqual(positions, sorted(positions))
            for name in H3_SECTIONS:
                self.assertIn("\n%s:\n" % name, block + "\n")
        messages = generator_messages(dict(state, attempt=0, source=state["storyboard"]))
        self.assertIn("不要自己发明 Ref2VA 版式", messages[0]["content"])
        self.assertIn(H3_SUBJECT_SOUND_RULE, messages[0]["content"])
        self.assertIn("## 【封装单元 5/5】", messages[-1]["content"])
        self.assertIn("\nnon_diegetic_music:", messages[-1]["content"])
        seedance = dict(state, locks={"model": "2.5"})
        seedance_note = packing_plan_note(seedance)
        self.assertIn("6+5=11", seedance_note)
        self.assertNotIn("subject_definitions:", seedance_note)
        self.assertNotIn(H3_SUBJECT_SOUND_RULE, seedance_note)
        seedance_system = generator_messages(dict(seedance, attempt=0, source="分镜"))[0]["content"]
        self.assertNotIn("不要输出 JSON", seedance_system)
        self.assertNotIn(H3_SUBJECT_SOUND_RULE, seedance_system)

    def test_json_blob_unwraps_nested_units_for_the_h3_check(self):
        inner = []
        for index in range(1, 6):
            shot = "[Shot 1] beat %s." % index
            if index == 1:
                shot += " <d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>"
            inner.append("\n".join((
                "## 【封装单元 %s/5】" % index,
                "duration: 6",
                "summary:",
                "summary-body-%s" % index,
                "detailed_description:",
                shot,
            )))
        payload = {
            "subject_definitions:": "<Subject 1> is a woman standing indoors.",
            "summary:": "parent-summary",
            "retention_analysis:": "<Subject 1>: fully_preserved for the woman.",
            "detailed_description:": "\n\n".join(inner),
            "overall_soundscape:": "Room tone.",
            "non_diegetic_music:": "none.",
        }
        blob = "duration: 6\n" + json.dumps(payload, ensure_ascii=False)
        state = _state([LINE, MISSING])
        state["locks"] = {"model": "h3"}
        restored = apply_packing_plan(blob, state)
        self.assertFalse(restored.lstrip().startswith("{"))
        self.assertNotIn('"subject_definitions:"', restored)
        self.assertNotIn("parent-summary", restored)
        self.assertNotIn("\nduration: 6\n", "\n" + restored)
        self.assertEqual(_declared(restored), PLANNED)
        chunks = _unit_chunks(restored)
        self.assertEqual(len(chunks), 5)
        for index, chunk in enumerate(chunks, 1):
            self.assertTrue(chunk.startswith("## 【封装单元 %s/5】" % index))
            positions = [chunk.index("\n%s:\n" % name) for name in H3_SECTIONS]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn("summary-body-%s" % index, chunk)
            self.assertNotIn("summary:", chunk)
            self.assertNotIn("retention_analysis:", chunk)
            self.assertIn("beat %s." % index, chunk)
            self.assertIn("<Subject 1> is a woman standing indoors.", chunk)
            self.assertIn("Room tone.", chunk)
            self.assertNotIn("fully_preserved", chunk)
            for other in range(1, 6):
                if other == index:
                    continue
                self.assertNotIn("summary-body-%s" % other, chunk)
                self.assertNotIn("beat %s." % other, chunk)
        self.assertIn("<d>[Chinese] %s</d>" % LINE, restored)
        self.assertIn(MISSING, _unit_chunks(restored)[-1])
        self.assertEqual(h3_structure_report(restored), "")
        self.assertNotIn(H3_STRUCTURE_MARK, h3_structure_report(restored))
        wrapped = json.dumps({"feed": blob}, ensure_ascii=False)
        wrapped_restored = apply_packing_plan(wrapped, state)
        self.assertEqual(_unit_chunks(wrapped_restored), chunks)
        seedance = apply_packing_plan(blob, dict(state, locks={"model": "2.5"}))
        self.assertIn('"subject_definitions:"', seedance)
        self.assertEqual(_unit_chunks(seedance), [])
        self.assertIn(LINE, seedance)

    def test_merged_markdown_shell_becomes_five_units(self):
        inner = "\n\n".join(
            "\n".join((
                "## 【封装单元 %s/5】" % index,
                "duration: 6",
                "[Shot 1] beat %s." % index,
            ))
            for index in range(1, 6)
        )
        shell = "\n".join((
            "duration: 6",
            "subject_definitions:",
            "<Subject 1> is a woman standing indoors.",
            "summary:",
            "She waits by the door.",
            "retention_analysis:",
            "<Subject 1>: fully_preserved for the woman.",
            "detailed_description:",
            inner,
            "overall_soundscape:",
            "Room tone.",
            "non_diegetic_music:",
            "none.",
        ))
        state = _state([])
        state["locks"] = {"model": "h3"}
        restored = apply_packing_plan(shell, state)
        chunks = _unit_chunks(restored)
        self.assertEqual(len(chunks), 5, restored)
        self.assertEqual(_declared(restored), PLANNED)
        self.assertEqual(h3_structure_report(restored), "")
        for index, chunk in enumerate(chunks, 1):
            self.assertIn("beat %s." % index, chunk)
            self.assertIn("\nsubject_definitions:\n", chunk)
            self.assertNotIn("She waits by the door.", chunk)
            self.assertNotIn("retention_analysis:", chunk)
            self.assertNotIn("fully_preserved", chunk)
            self.assertIn("Room tone.", chunk)
            self.assertIn("\nnon_diegetic_music:\n", chunk)
            self.assertNotIn("## 【封装单元", chunk.split("\n", 1)[1])

    def test_omitted_sections_are_stripped_and_the_gate_still_runs(self):
        body = "\n\n".join(
            "\n".join((
                "## 【封装单元 %s/5】" % index,
                "duration: %s" % seconds,
                "subject_definitions:",
                "<Subject 1> is a woman standing indoors.",
                "summary:",
                "She waits in unit %s." % index,
                "retention_analysis:",
                "<Subject 1>: fully_preserved for the woman.",
                "detailed_description:",
                "[Shot 1] She stands in unit %s." % index,
                "overall_soundscape:",
                "Room tone.",
                "non_diegetic_music:",
                "none.",
            ))
            for index, seconds in enumerate(PLANNED, 1)
        ) + "\n"
        spoken = body.replace(
            "[Shot 1] She stands in unit 1.",
            "[Shot 1] She stands in unit 1. <d>[Chinese] %s</d>" % EXACT,
            1,
        )
        state = _state([EXACT])
        state["locks"] = {"model": "h3"}
        self.assertEqual(h3_structure_report(spoken), "")
        restored = apply_packing_plan(spoken, state)
        self.assertNotIn("\nsummary:", restored)
        self.assertNotIn("\nretention_analysis:", restored)
        self.assertNotIn("She waits in unit", restored)
        self.assertNotIn("fully_preserved", restored)
        self.assertEqual(_declared(restored), PLANNED)
        self.assertIn("<d>[Chinese] %s</d>" % EXACT, restored)
        for chunk in _unit_chunks(restored):
            positions = [chunk.index("\n%s:\n" % name) for name in H3_SECTIONS]
            self.assertEqual(positions, sorted(positions))
        mention = spoken.replace(
            "[Shot 1] She stands in unit 2.",
            "[Shot 1] She stands in unit 2. The summary of this beat stays in the shot.",
            1,
        )
        kept_word = apply_packing_plan(mention, state)
        self.assertIn("The summary of this beat stays in the shot.", kept_word)
        self.assertNotIn("\nsummary:", kept_word)
        path = Path(tempfile.mkdtemp()) / "feed.md"
        path.write_text(spoken, encoding="utf-8")
        seen = []
        real_run = __import__("end.gates", fromlist=["run_script"]).run_script

        def spy(script, args, timeout=90):
            seen.append(Path(script).name)
            return real_run(script, args, timeout)

        from end.gates import check_feed

        with patch("end.gates.run_script", side_effect=spy):
            code, report = check_feed(path, "h3", "M1", "1")
        self.assertEqual(seen, ["validate_prompt.py"])
        self.assertNotIn(H3_STRUCTURE_MARK, report)
        self.assertEqual(code, 0, report)
        stored = path.read_text(encoding="utf-8")
        self.assertNotIn("\nsummary:", stored)
        self.assertNotIn("\nretention_analysis:", stored)
        self.assertIn("\nsubject_definitions:\n", stored)
        self.assertIn("\ndetailed_description:\n", stored)
        prose = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 11",
            "She waits in the doorway.",
        ))
        kept = apply_packing_plan(prose, {
            "locks": {"model": "h3"},
            "storyboard": "时长：11\n",
            "shots": [{"duration": 11}],
        })
        self.assertEqual(kept, prose)
        self.assertIn(H3_STRUCTURE_MARK, h3_structure_report(kept))

    def test_duration_and_spoken_lines_still_restore_on_a_markdown_draft(self):
        draft = _units(
            [6, 6, 6, 6, 6],
            [
                "<d>[Chinese] 三天没洗，贴得跟浆糊似的。</d>",
                "She waits.",
                "She waits.",
                "She waits.",
                "She waits.",
            ],
        )
        state = _state([LINE, MISSING])
        state["locks"] = {"model": "h3"}
        fixed = apply_packing_plan(draft, state)
        self.assertEqual(_declared(fixed), PLANNED)
        self.assertIn("<d>[Chinese] %s</d>" % LINE, fixed)
        self.assertIn(MISSING, _unit_chunks(fixed)[-1])
        self.assertIn("\ndetailed_description:\n", fixed)
        self.assertNotIn("subject_definitions:", fixed)

    def test_duration_and_dialogue_failures_rerun_the_packing_writers(self):
        h3_state = {
            "attempt": 1,
            "draft": "## 【封装单元 1/1】\nduration: 6\n",
            "storyboard": _board([LINE]),
            "shots": [{"duration": seconds} for seconds in SHOTS],
            "locks": {"model": "h3"},
            "critic_report": {},
            "machine_report": {"errors": ["时长被压缩：分镜合计 56s，封装声明 6s。"], "warnings": []},
        }
        duration_brief = generator_messages(h3_state)[-1]["content"]
        files = suite_files("feed", "阶段：P4\n模型：h3\n任务：提示词封装\n" + duration_brief)
        previous = [{"skill": relative, "text": "旧节点"} for relative in files]
        duration_nodes = select_nodes(files, duration_brief, previous)
        self.assertIn("sd-feed-engine/references/feed-mode-and-gates.md", duration_nodes)
        self.assertIn("short-drama-director/references/prompt-feeding-checklist.md", duration_nodes)
        self.assertIn("short-drama-director/references/h3-adapter.md", duration_nodes)
        self.assertNotIn("sd-feed-engine/SKILL.md", duration_nodes)
        self.assertNotIn("sd-feed-engine/references/asset-mapping-and-rules.md", duration_nodes)
        self.assertNotIn("short-drama-director/references/segment-splicing.md", duration_nodes)

        dialogue_state = dict(h3_state, machine_report={
            "errors": ["对白被压缩：封装稿缺少「别改了」。不要为了压时长删对白或旁白。"],
            "warnings": [],
        })
        dialogue_brief = generator_messages(dialogue_state)[-1]["content"]
        dialogue_nodes = select_nodes(files, dialogue_brief, previous)
        self.assertIn("sd-feed-engine/SKILL.md", dialogue_nodes)
        self.assertIn("short-drama-director/references/prompt-feeding-checklist.md", dialogue_nodes)
        self.assertNotIn("sd-feed-engine/references/feed-mode-and-gates.md", dialogue_nodes)
        self.assertNotIn("short-drama-director/references/h3-adapter.md", dialogue_nodes)
        self.assertEqual(
            packing_repair_targets(dialogue_state["machine_report"], h3=False),
            [
                "sd-feed-engine/SKILL.md",
                "short-drama-director/references/prompt-feeding-checklist.md",
            ],
        )

        unnamed = generator_messages(dict(
            h3_state,
            machine_report={"errors": ["FAIL: H1 缺段"], "warnings": []},
        ))[-1]["content"]
        self.assertEqual(select_nodes(files, unnamed, previous), [])
        self.assertEqual(packing_repair_targets({"errors": ["FAIL: H1 缺段"]}, h3=True), [])

        calls = []

        def complete(role, messages):
            path = ""
            if role == "specialist" and "本节点原文" in messages[0]["content"]:
                path = messages[0]["content"].split("本节点原文", 1)[1].split()[0]
            calls.append(path or role)
            if role == "specialist":
                return json.dumps({"text": "重写这一节点"}, ensure_ascii=False)
            if role == "director":
                return json.dumps({"feed": "\n".join((
                    "## 【封装单元 1/1】",
                    "duration: 6",
                    "[Shot 1] She wipes a glass.",
                ))}, ensure_ascii=False)
            return ACCEPT

        def checker(text, state):
            if int(state.get("attempt") or 0) < 2:
                return {
                    "ok": False,
                    "errors": [
                        "时长被压缩：分镜合计 34s，封装声明 6s。",
                        "对白被压缩：封装稿缺少「但习惯，不代表没事。」。不要为了压时长删对白或旁白。",
                    ],
                    "warnings": [],
                    "fatal": "",
                    "needs_human": False,
                }
            return {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False}

        board = "\n".join((
            "## S01",
            "时长：6",
            "旁白：但习惯，不代表没事。",
            "## S02",
            "时长：5",
            "## S03",
            "时长：16",
        ))
        result = run_feed_loop(
            board,
            shots=[{"duration": 6}, {"duration": 5}, {"duration": 16}],
            locks={"model": "h3"},
            max_attempts=2,
            complete=complete,
            checker=checker,
        )
        self.assertEqual(result["status"], "pass", result.get("machine_report"))
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(_declared(result["feed"]), [11, 16])
        self.assertNotIn("duration: 15", result["feed"])
        self.assertIn("但习惯，不代表没事。", _unit_chunks(result["feed"])[0])
        self.assertIn("## 【封装单元 2/2】", result["feed"])
        self.assertEqual(
            [unit["id"] for unit in packed_units({"feed": result["feed"]})],
            ["封装单元 1/2", "封装单元 2/2"],
        )
        split_errors = feed_duration_failures(
            result["feed"],
            board,
            [{"duration": 6}, {"duration": 5}, {"duration": 16}],
        )
        self.assertTrue(any("必须把这一镜拆开" in item for item in split_errors), split_errors)
        self.assertFalse(any("时长被压缩" in item for item in split_errors), split_errors)
        self.assertIn(
            "short-drama-director/references/h3-adapter.md",
            packing_repair_targets({"errors": split_errors}, h3=True),
        )
        first_critic = calls.index("critic")
        repair = calls[first_critic + 1:]
        repair_nodes = [item for item in repair[: repair.index("critic")] if item != "director"]
        self.assertIn("sd-feed-engine/references/feed-mode-and-gates.md", repair_nodes)
        self.assertIn("sd-feed-engine/SKILL.md", repair_nodes)
        self.assertIn("short-drama-director/references/prompt-feeding-checklist.md", repair_nodes)
        self.assertIn("short-drama-director/references/h3-adapter.md", repair_nodes)
        self.assertNotIn("sd-feed-engine/references/asset-mapping-and-rules.md", repair_nodes)
        self.assertNotIn("short-drama-director/references/segment-splicing.md", repair_nodes)
        self.assertEqual(repair.count("director"), 1)
