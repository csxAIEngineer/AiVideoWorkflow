"""The packing stage writes one agreed style sentence into every H3 unit.

The sentence comes from the script line 画风, not from the model. The machine
check fails a unit that lacks that exact line or carries another style.

    python3 -m unittest tests.test_feed_style_lock
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.workers.critic import machine_check_skill
from Agents.workers.feed import (
    agreed_style,
    apply_packing_plan,
    critic_messages,
    generator_messages,
    h3_packing_scaffold,
    inject_style_lock,
    packing_groups,
    packing_plan_note,
    packing_repair_targets,
    style_lock_failures,
    style_lock_line,
)
from Agents.workers.generator import select_nodes
from Agents.checks import check_feed_draft
from AI.skills.registry import suite_files
from end.stage_runner import check_stage

INK = "2D中式水墨氛围 · 干净线条 · 留白 · 淡雅"
LOCK = "画面风格：" + INK
SCRIPT = "画风：%s\n她把瓶子放回架上。\n" % INK
FALLBACK = "自然光，写实肤色，浅景深"
ACCEPT = '{"verdict":"accept","issues":[]}'
WORKFLOW = ("末态继承", "拼接节点", "换机位", "同机位硬接", "【画幅风格】")


def _unit(index, total, body, sound="Room tone."):
    return "\n".join((
        "## 【封装单元 %s/%s】" % (index, total),
        "duration: 8",
        "subject_definitions:",
        body,
        "detailed_description:",
        "[Shot 1] <Subject 1> stays in the room.",
        "overall_soundscape:",
        sound,
        "non_diegetic_music:",
        "N/A",
    ))


def _feed(bodies):
    total = len(bodies)
    return "\n\n".join(_unit(index, total, body) for index, body in enumerate(bodies, 1)) + "\n"


def _first_subject_line(chunk):
    body = chunk.split("subject_definitions:\n", 1)[1]
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _chunks(text):
    from end.product_names import UNIT_HEAD_ANY

    heads = list(UNIT_HEAD_ANY.finditer(text))
    found = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        found.append(text[match.start():end])
    return found


class StyleSourceTest(unittest.TestCase):
    def test_script_line_wins_over_the_project_fallback(self):
        state = {
            "script": "画风正文：%s\n%s" % (FALLBACK, SCRIPT),
            "locks": {"style_look": FALLBACK, "style": FALLBACK, "model": "h3"},
        }
        self.assertEqual(agreed_style(state), INK)
        self.assertNotIn(FALLBACK, style_lock_line(agreed_style(state)))
        self.assertNotIn("自然光", style_lock_line(agreed_style(state)))

    def test_prose_that_mentions_style_is_not_a_script_line(self):
        state = {
            "script": "观众觉得画风很好，雨被 washed away。\n",
            "locks": {"style_look": "冷青灰低饱和", "model": "h3"},
        }
        self.assertEqual(agreed_style(state), "冷青灰低饱和")

    def test_no_style_does_not_invent_a_default(self):
        state = {"script": "她推开门。\n", "locks": {"model": "h3"}}
        self.assertEqual(agreed_style(state), "")
        self.assertEqual(style_lock_line(""), "")
        draft = _feed(["<Subject 1>: a woman."])
        self.assertEqual(inject_style_lock(draft, ""), draft)
        self.assertEqual(style_lock_failures(draft, ""), [])


class StyleInjectionTest(unittest.TestCase):
    def _state(self):
        return {
            "script": SCRIPT,
            "locks": {"model": "h3", "style_look": FALLBACK, "style": FALLBACK},
            "storyboard": "\n".join("## S0%s\n时长：8" % index for index in range(1, 6)),
            "shots": [{"duration": 8} for _ in range(5)],
        }

    def test_every_unit_receives_the_same_lock_when_the_model_omits_it(self):
        bodies = [
            "<Subject 1>: a room.\n<Subject 2>: a glass bottle rendered in 2D Chinese ink-wash style.",
            "<Subject 1>: a glass bottle rendered in 2D Chinese ink-wash style.",
            "<Subject 1>: a woman holding a bottle.",
            "<Subject 1>: a woman at the door.",
            "<Subject 1>: a woman turns away.",
        ]
        draft = _feed(bodies)
        fixed = apply_packing_plan(draft, self._state())
        chunks = _chunks(fixed)
        self.assertEqual(len(chunks), 5)
        self.assertEqual({_first_subject_line(chunk) for chunk in chunks}, {LOCK})
        for chunk in chunks:
            self.assertEqual(chunk.split("subject_definitions:\n", 1)[1].splitlines()[0], LOCK)
            self.assertIn("<Subject 1>:", chunk)
            self.assertIn("duration: 8", chunk)
        self.assertNotIn("【画幅风格】", fixed)
        for word in WORKFLOW:
            self.assertNotIn(word, LOCK)
        again = apply_packing_plan(fixed, self._state())
        self.assertEqual(again, fixed)

    def test_a_wrong_slot_is_replaced_and_an_extra_sentence_stays(self):
        draft = _unit(
            1,
            1,
            "画面风格：实拍\n<Subject 1>: a woman rendered in a live-action style.",
        )
        fixed = inject_style_lock(draft, INK)
        self.assertTrue(fixed.split("subject_definitions:\n", 1)[1].startswith(LOCK + "\n"))
        self.assertNotIn("画面风格：实拍", fixed)
        self.assertIn("rendered in a live-action style", fixed)
        self.assertIn("<Subject 1>:", fixed)

    def test_seedance_is_left_alone(self):
        draft = _feed(["<Subject 1>: a woman."])
        state = {"script": SCRIPT, "locks": {"model": "2.5", "style_look": INK}}
        self.assertEqual(apply_packing_plan(draft, state), draft)
        self.assertNotIn("画面风格：", generator_messages(dict(state, attempt=0, source="分镜"))[0]["content"])


class StyleGateTest(unittest.TestCase):
    def _state(self, model="h3"):
        return {
            "script": SCRIPT,
            "locks": {"model": model, "material_mode": "M1", "track": "1", "style_look": FALLBACK},
            "storyboard": "",
            "shots": [],
        }

    def test_a_missing_lock_fails_and_a_subject_only_mention_does_not_pass(self):
        draft = _feed([
            "<Subject 1>: a room rendered in 2D Chinese ink-wash style.",
            "<Subject 1>: a glass bottle rendered in 2D Chinese ink-wash style.",
            "<Subject 1>: a woman.",
            "<Subject 1>: a woman. The ink was washed away.",
            "<Subject 1>: a woman at the door.",
        ])
        errors = style_lock_failures(draft, INK)
        missing = [item for item in errors if "缺少锁定句" in item]
        self.assertEqual(len(missing), 5)
        for index, error in enumerate(missing, 1):
            self.assertIn("封装单元 %s/5" % index, error)
            self.assertIn(LOCK, error)
            self.assertIn("房间或道具", error)
        self.assertTrue(any("rendered in 2D Chinese ink-wash style" in item for item in errors))
        washed = next(item for item in missing if "封装单元 4/5" in item)
        self.assertNotIn("washed away", washed)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, self._state())
        self.assertFalse(report["ok"])
        self.assertTrue(any("画风锁" in item for item in report["errors"]))
        with patch("end.gates.check_feed", return_value=(0, "")):
            seedance = check_feed_draft(draft, self._state("2.5"))
        self.assertTrue(seedance["ok"], seedance)

    def test_a_different_style_fails_the_gate(self):
        draft = _feed([
            LOCK + "\n<Subject 1>: a woman.",
            "画面风格：实拍\n<Subject 1>: a woman.",
            LOCK + "\n<Subject 1>: a woman in a live-action style.",
        ])
        errors = style_lock_failures(draft, INK)
        joined = "\n".join(errors)
        self.assertNotIn("封装单元 1/3", joined)
        self.assertIn("封装单元 2/3", joined)
        self.assertIn("别的风格", joined)
        self.assertIn("画面风格：实拍", joined)
        self.assertIn("封装单元 3/3", joined)
        self.assertIn("live-action style", joined)
        self.assertIn(LOCK, joined)
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, self._state())
        self.assertFalse(report["ok"])
        self.assertIn("封装单元 2/3", report["report"])
        self.assertIn("封装单元 3/3", report["report"])

    def test_the_exact_line_passes_and_washed_away_does_not_fail_it(self):
        draft = _feed([
            LOCK + "\n<Subject 1>: a woman. The ink was washed away by the rain.",
            LOCK + "\n<Subject 2>: a bottle on the shelf.",
        ])
        self.assertEqual(style_lock_failures(draft, INK), [])
        with patch("end.gates.check_feed", return_value=(0, "")):
            report = check_feed_draft(draft, self._state())
        self.assertTrue(report["ok"], report)

    def test_check_stage_uses_the_script_line(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text(SCRIPT, encoding="utf-8")
        (internal / "feed.md").write_text(
            _unit(1, 1, "<Subject 1>: a room rendered in 2D Chinese ink-wash style.") + "\n",
            encoding="utf-8",
        )
        with patch("end.stage_runner.check_feed", return_value=(0, "")):
            passed, report = check_stage(
                folder,
                "feed",
                {"model": "h3", "material_mode": "M1", "track": "1", "style_look": FALLBACK},
            )
        self.assertFalse(passed)
        self.assertIn(LOCK, report)
        self.assertNotIn(FALLBACK, report)
        self.assertIn("房间或道具", report)


class StyleBriefTest(unittest.TestCase):
    def test_generator_and_critic_are_told_the_sentence_is_already_written(self):
        state = {
            "attempt": 0,
            "source": "## S01\n时长：8\n",
            "storyboard": "## S01\n时长：8\n",
            "script": SCRIPT,
            "shots": [{"duration": 8}],
            "locks": {"model": "h3", "style_look": FALLBACK},
        }
        system = generator_messages(state)[0]["content"]
        brief = generator_messages(state)[-1]["content"]
        self.assertIn("不要自己写画风", system)
        self.assertIn(LOCK, system)
        self.assertNotIn("请写画风", system)
        self.assertIn("程序会把下面这一句写进", brief)
        scaffold = h3_packing_scaffold(packing_groups(state), INK)
        self.assertIn(scaffold, packing_plan_note(state))
        self.assertIn("subject_definitions:\n%s" % LOCK, scaffold)
        self.assertNotIn("【画幅风格】", scaffold)
        critic = critic_messages(dict(state, draft=_feed([LOCK + "\n<Subject 1>: a woman."])))[-1]["content"]
        self.assertIn(LOCK, critic)
        self.assertIn("blocking", critic)
        self.assertIn("不要改稿", critic)
        self.assertNotIn("请写画风", critic)
        skill = machine_check_skill("feed")
        self.assertIn("画风锁", skill)
        self.assertIn("style_lock_failures", skill)
        self.assertIn("房间或道具", skill)
        self.assertIn("washed away", skill)
        self.assertNotIn("请写画风", skill)

    def test_a_style_failure_sends_the_packing_writer_back_to_the_locked_sentence(self):
        state = {
            "attempt": 1,
            "draft": _unit(1, 1, "<Subject 1>: a bottle rendered in 2D Chinese ink-wash style."),
            "storyboard": "## S01\n时长：8\n",
            "script": SCRIPT,
            "shots": [{"duration": 8}],
            "locks": {"model": "h3"},
            "critic_report": {},
            "machine_report": {
                "errors": [
                    "画风锁：封装单元 1/1 缺少锁定句「%s」。只写在房间或道具主体上不算。" % LOCK,
                ],
                "warnings": [],
            },
        }
        brief = generator_messages(state)[-1]["content"]
        self.assertIn("不要改写画风", brief)
        self.assertIn(LOCK, brief)
        self.assertIn("sd-feed-engine/references/feed-mode-and-gates.md", brief)
        self.assertIn("short-drama-director/references/h3-adapter.md", brief)
        self.assertIn("short-drama-director/references/prompt-feeding-checklist.md", brief)
        self.assertNotIn("时长或对白没过", brief)
        self.assertNotIn("请写画风", brief)
        self.assertEqual(
            packing_repair_targets(state["machine_report"], h3=True),
            [
                "sd-feed-engine/references/feed-mode-and-gates.md",
                "short-drama-director/references/h3-adapter.md",
                "short-drama-director/references/prompt-feeding-checklist.md",
            ],
        )
        self.assertEqual(packing_repair_targets({"errors": ["FAIL: H1 缺段"]}, h3=True), [])
        files = suite_files("feed", "阶段：P4\n模型：h3\n" + brief)
        previous = [{"skill": relative, "text": "旧节点"} for relative in files]
        nodes = select_nodes(files, brief, previous)
        self.assertIn("short-drama-director/references/h3-adapter.md", nodes)
        self.assertNotIn("sd-feed-engine/references/asset-mapping-and-rules.md", nodes)


class StyleLoopTest(unittest.TestCase):
    def test_injection_survives_a_repair_that_omits_the_sentence(self):
        from Agents.workers.feed import run_feed_loop

        dirty = _unit(1, 1, "<Subject 1>: a bottle rendered in 2D Chinese ink-wash style.")
        clean = _unit(1, 1, "<Subject 1>: a woman holds the bottle.")
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
            errors = style_lock_failures(text, agreed_style(state))
            return {
                "ok": not errors,
                "errors": errors,
                "warnings": [],
                "fatal": "",
                "needs_human": False,
            }

        result = run_feed_loop(
            "## S01\n时长：8\n",
            shots=[{"duration": 8}],
            script=SCRIPT,
            locks={"model": "h3", "style_look": FALLBACK},
            max_attempts=2,
            complete=complete,
            checker=checker,
        )
        self.assertEqual(result["status"], "pass", result.get("machine_report"))
        self.assertEqual(result["attempts"], 2)
        self.assertIn(LOCK, result["feed"])
        self.assertNotIn("rendered in", result["feed"])
        self.assertNotIn("【画幅风格】", result["feed"])
        self.assertNotIn(FALLBACK, result["feed"])
        self.assertIn("<Subject 1>:", result["feed"])
        self.assertIn("duration: 8", result["feed"])
        repair = next(item for item in prompts if "不要改写画风" in item)
        self.assertIn(LOCK, repair)
        self.assertIn("short-drama-director/references/h3-adapter.md", repair)


if __name__ == "__main__":
    unittest.main()
