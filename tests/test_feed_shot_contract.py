"""One storyboard shot is one feed unit. Merged units fail the gate."""

import json
import tempfile
import unittest
from pathlib import Path

from end.gates import check_feed
from end.prompt_gate import ONE_SHOT_FEED_RULE, feed_storyboard_gap, unit_duration
from end.storyboard_contract import shot_durations
from end.workflow_from_spec import collect_steps

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures"
BOARD_PATH = FIXTURE / "c9fc9803093e-storyboard.md"
FEED_PATH = FIXTURE / "c9fc9803093e-feed.md"


def setUpModule():
    from tests.offline_guard import install

    install()


def tearDownModule():
    from tests.offline_guard import uninstall

    uninstall()


def _unit(index, total, seconds, shot_id, extra=""):
    return "\n".join((
        "## 【投喂单元 %s/%s】" % (index, total),
        "duration: %s" % seconds,
        "subject_definitions:",
        "<Subject 1> (%s): the young woman; voice: calm." % shot_id,
        "summary:",
        "One storyboard shot.",
        "retention_analysis:",
        "<Subject 1>: fully_preserved.",
        "detailed_description:",
        "[Shot 1] The action of %s.%s" % (shot_id, extra),
        "overall_soundscape:",
        "Room tone.",
        "non_diegetic_music:",
        "none.",
    ))


def _board(*pairs):
    chunks = []
    for shot_id, seconds in pairs:
        chunks.append("\n".join((
            "## %s" % shot_id,
            "动作：这一镜的动作",
            "景别机位：近景",
            "时长：%s" % seconds,
            "资产：图1",
        )))
    return "\n\n".join(chunks) + "\n"


class FeedShotContractTest(unittest.TestCase):
    def test_prompts_and_skill_forbid_merging_shots(self):
        from end.stage_runner import stage_instruction
        from AI.skills.registry import merge_prompt

        h3 = stage_instruction("feed", {"model": "h3"})
        seedance = stage_instruction("feed", {"model": "2.5"})
        brief = "\n".join(("阶段：P4", "模型：h3", "任务：提示词封装"))
        for text in (h3, seedance, merge_prompt("feed", brief), ONE_SHOT_FEED_RULE):
            self.assertIn("不要把多个分镜合进同一单元", text)
            self.assertNotIn("从 [Shot 2] 起", text)
        skill = (ROOT / "AI/skills/manju-laoli-skill/sd-feed-engine/SKILL.md").read_text(encoding="utf-8")
        gates = (ROOT / "AI/skills/manju-laoli-skill/sd-feed-engine/references/feed-mode-and-gates.md").read_text(encoding="utf-8")
        adapter = (ROOT / "AI/skills/manju-laoli-skill/short-drama-director/references/h3-adapter.md").read_text(encoding="utf-8")
        self.assertIn("禁止把多个分镜并进同一单元", skill)
        self.assertNotIn("并镜头", skill)
        self.assertIn("分镜镜号优先（一镜一单元）", gates)
        self.assertIn("duration` 写成 4", adapter)

    def test_saved_four_unit_feed_is_blocked(self):
        board = BOARD_PATH.read_text(encoding="utf-8")
        feed = FEED_PATH.read_text(encoding="utf-8")
        self.assertEqual(len(shot_durations(board)), 11)
        gap = feed_storyboard_gap(feed, board)
        self.assertIn("分镜有 11 镜", gap)
        self.assertIn("4 个单元", gap)
        folder = Path(tempfile.mkdtemp())
        path = folder / "feed.md"
        path.write_text(feed, encoding="utf-8")
        code, report = check_feed(path, "h3", "M1", "1", board)
        self.assertEqual(code, 1)
        self.assertIn("分镜有 11 镜", report)
        self.assertIn("4 个单元", report)

        from end.render_loop import run_local_pipeline

        spec = {
            "aspect_ratio": "16:9",
            "storyboard": board,
            "feed": feed,
            "assets": [{"id": "图1", "title": "小君", "prompt": "人物"}],
            "shots": [{"id": "S01", "duration": 8, "prompt": "分镜", "images": ["图1"]}],
        }
        with self.assertRaises(RuntimeError) as caught:
            run_local_pipeline(spec, folder / "render")
        self.assertIn("分镜有 11 镜", str(caught.exception))

    def test_eleven_storyboard_shots_render_eleven_clips_near_55_seconds(self):
        board = BOARD_PATH.read_text(encoding="utf-8")
        rows = shot_durations(board)
        self.assertEqual([item[0] for item in rows], ["S%02d" % index for index in range(1, 12)])
        self.assertEqual(sum(seconds for _shot, seconds in rows), 55)
        expected = [unit_duration(seconds) for _shot, seconds in rows]
        self.assertEqual(sum(expected), 56)
        self.assertEqual(expected[6], 4)
        units = [
            _unit(index, len(rows), seconds, shot_id)
            for index, ((shot_id, _raw), seconds) in enumerate(zip(rows, expected), 1)
        ]
        feed = "\n\n".join(units)
        self.assertEqual(feed_storyboard_gap(feed, board), "")
        spec = {
            "storyboard": board,
            "feed": feed,
            "assets": [],
            "shots": [
                {"id": shot_id, "duration": raw, "prompt": "分镜 %s" % shot_id, "images": []}
                for shot_id, raw in rows
            ],
        }
        _assets, shots = collect_steps(spec)
        self.assertEqual([shot["id"] for shot in shots], [shot_id for shot_id, _raw in rows])
        self.assertEqual([shot["duration"] for shot in shots], expected)
        self.assertEqual(sum(shot["duration"] for shot in shots), 56)
        self.assertEqual(shots[6]["duration"], 4)
        self.assertIn("【投喂单元 7/11】", shots[6]["prompt"])
        self.assertNotIn("【投喂单元 1/4】", feed)

    def test_a_three_second_shot_is_raised_and_not_merged(self):
        board = _board(("S01", 8), ("S02", 3))
        merged = _unit(1, 1, 11, "S01")
        self.assertIn("分镜有 2 镜", feed_storyboard_gap(merged, board))
        short = "\n\n".join((
            _unit(1, 2, 8, "S01"),
            _unit(2, 2, 3, "S02"),
        ))
        self.assertIn("S02 分镜 3 秒", feed_storyboard_gap(short, board))
        self.assertIn("应为 4 秒", feed_storyboard_gap(short, board))
        raised = "\n\n".join((
            _unit(1, 2, 8, "S01"),
            _unit(2, 2, 4, "S02"),
        ))
        self.assertEqual(feed_storyboard_gap(raised, board), "")
        _assets, shots = collect_steps({
            "feed": raised,
            "shots": [
                {"id": "S01", "duration": 8, "prompt": "甲", "images": []},
                {"id": "S02", "duration": 3, "prompt": "乙", "images": []},
            ],
        })
        self.assertEqual([shot["duration"] for shot in shots], [8, 4])

    def test_a_long_shot_stays_one_unit_at_15_seconds(self):
        board = _board(("S01", 16))
        split = "\n\n".join((_unit(1, 2, 8, "S01"), _unit(2, 2, 8, "S01b")))
        self.assertIn("分镜有 1 镜", feed_storyboard_gap(split, board))
        self.assertIn("2 个单元", feed_storyboard_gap(split, board))
        held = _unit(1, 1, 15, "S01")
        self.assertEqual(feed_storyboard_gap(held, board), "")
        _assets, shots = collect_steps({
            "feed": held,
            "shots": [{"id": "S01", "duration": 16, "prompt": "长镜", "images": []}],
        })
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["duration"], 15)

    def test_a_second_cut_inside_one_unit_fails(self):
        board = _board(("S01", 8))
        unit = _unit(1, 1, 8, "S01", extra="\n[Shot 2] At 00:04.000, the camera cuts to the door.")
        gap = feed_storyboard_gap(unit, board)
        self.assertIn("[Shot 2]", gap)
        self.assertIn("只写 [Shot 1]", gap)

    def test_check_stage_uses_the_saved_storyboard(self):
        from end.stage_runner import check_stage

        board = _board(("S01", 6), ("S02", 6))
        feed = _unit(1, 1, 12, "S01")
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "feed.md").write_text(feed, encoding="utf-8")
        (internal / "分镜.md").write_text(board, encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps({"storyboard": board, "assets": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        passed, report = check_stage(folder, "feed", {"model": "h3", "material_mode": "M1", "track": "1"})
        self.assertFalse(passed)
        self.assertIn("分镜有 2 镜", report)
        self.assertIn("1 个单元", report)
