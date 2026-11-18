"""Wan 3.0 packing fills toward 30s; a span over 30s is split and first_frame-stitched."""

import unittest

from Agents.workers.feed.common import packing_groups
from Agents.workers.storyboard_skeleton import storyboard_skeleton
from end.locks import clip_limit, pack_limit, segment_limit
from end.stage_runner import feed_duration_failures, group_shot_durations


BOARD_25 = """## EP001-SC01｜外·雨巷·日（0s - 25s）

出场：大侠
情节：雨巷相遇。
对白：大侠：接住！
"""


class ClipLimitTest(unittest.TestCase):
    def test_h3_and_wan3_locks_allow_30s_and_pack_toward_30s(self):
        self.assertEqual(clip_limit("h3"), 30)
        self.assertEqual(clip_limit("wan3"), 30)
        self.assertEqual(clip_limit(locks={"model": "h3"}), 30)
        self.assertEqual(clip_limit(locks={}), 30)
        self.assertEqual(pack_limit("h3"), 30)
        self.assertEqual(pack_limit("wan3"), 30)
        self.assertEqual(pack_limit(locks={}), 30)
        self.assertEqual(segment_limit("h3"), 30)
        self.assertEqual(segment_limit("wan3"), 30)
        self.assertEqual(segment_limit(""), 0)
        self.assertEqual(clip_limit("2.0"), 15)
        self.assertEqual(pack_limit("2.0"), 15)
        self.assertEqual(clip_limit("2.5"), 30)
        self.assertEqual(pack_limit("2.5"), 30)

    def test_25s_scene_stays_one_shot_under_30s(self):
        skeleton = storyboard_skeleton({
            "source": BOARD_25,
            "locks": {"duration_seconds": 25, "model": "h3", "aspect": "16:9"},
        })
        shots = skeleton["shots"]
        self.assertEqual([shot["duration"] for shot in shots], [25])

    def test_20s_scene_stays_one_shot(self):
        script = (
            "## EP001-SC01｜外·雨巷·日（0s - 20s）\n\n"
            "出场：大侠\n"
            "情节：雨巷相遇。\n"
        )
        skeleton = storyboard_skeleton({
            "source": script,
            "locks": {"duration_seconds": 20, "model": "h3", "aspect": "16:9"},
        })
        self.assertEqual([shot["duration"] for shot in skeleton["shots"]], [20])

    def test_packing_fills_toward_30s_and_does_not_merge_past_it(self):
        self.assertEqual(group_shot_durations([25], locks={"model": "h3"}), [[25]])
        self.assertEqual(group_shot_durations([13, 12], locks={"model": "h3"}), [[13, 12]])
        self.assertEqual(group_shot_durations([20, 5], locks={"model": "h3"}), [[20, 5]])
        self.assertEqual(group_shot_durations([5, 5, 6], locks={"model": "h3"}), [[5, 5, 6]])
        self.assertEqual(group_shot_durations([10, 10], locks={"model": "h3"}), [[10, 10]])
        self.assertEqual(group_shot_durations([30, 10], locks={"model": "h3"}), [[30], [10]])
        self.assertEqual(
            packing_groups({"shots": [{"id": "S01", "duration": 25}], "locks": {"model": "h3"}}),
            [[25]],
        )

    def test_473b_packing_fills_up_to_30(self):
        shots = [5, 5, 5, 5, 5, 5, 5, 5, 4, 4, 3, 3, 3, 3]
        self.assertEqual(
            [sum(group) for group in group_shot_durations(shots, locks={"model": "h3"})],
            [30, 30],
        )

    def test_25s_declaration_is_not_a_duration_failure(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 25\n",
            shots=[{"duration": 25}],
            locks={"model": "h3"},
        )
        self.assertEqual(errors, [])

    def test_20s_declaration_is_not_a_duration_failure(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 20\n",
            shots=[{"duration": 20}],
            locks={"model": "h3"},
        )
        self.assertEqual(errors, [])

    def test_30s_declaration_is_not_a_duration_failure(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 30\n",
            shots=[{"duration": 30}],
            locks={"model": "h3"},
        )
        self.assertEqual(errors, [])

    def test_40s_scene_fills_30_then_leftover(self):
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
        self.assertEqual([shot["duration"] for shot in shots], [30, 10])
        self.assertFalse(bool(shots[0].get("action_continuous")))
        self.assertTrue(shots[1].get("action_continuous"))

    def test_over_30s_split_stitches_with_first_frame(self):
        from end.seam_ledger import apply_submit_params, packed_units

        shots = [
            {"id": "S01", "duration": 30, "images": ["图1"]},
            {"id": "S02", "duration": 10, "images": ["图1"], "action_continuous": True},
        ]
        feed = "\n\n".join((
            "## 【封装单元 1/2】\nduration: 30\ndetailed_description:\n[Shot 1] The alley.",
            "## 【封装单元 2/2】\nduration: 10\ndetailed_description:\n[Shot 1] He stops.",
        ))
        units = packed_units({"feed": feed, "shots": shots})
        self.assertTrue(units[1].get("action_continuous"))
        entries = apply_submit_params([
            {"shot_id": units[0]["id"], "prev_id": "", "d_lock": "none"},
            {"shot_id": units[1]["id"], "prev_id": units[0]["id"], "d_lock": "2"},
        ], units)
        self.assertEqual(entries[0]["video_mode"], "reference")
        self.assertEqual(entries[1]["video_mode"], "first_frame")
        self.assertEqual(entries[1]["first_frame_source"], "previous_tail")

    def test_31s_shot_is_flagged_and_not_clamped_to_15(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 30\n",
            shots=[{"duration": 31}],
            locks={"model": "h3"},
        )
        joined = "\n".join(errors)
        self.assertIn("31", joined)
        self.assertIn("必须把这一镜拆开", joined)
        self.assertNotIn("钳成 15", joined)
        self.assertNotIn("超过 15s", joined)

    def test_feed_merge_prompt_last_cut_follows_pack_limit(self):
        from AI.skills.registry import merge_prompt

        h3 = merge_prompt("feed", "模型：MiniMax H3")
        self.assertIn("最后一刀不超过 30 秒", h3)
        self.assertNotIn("最后一刀不超过 15 秒", h3)
        self.assertIn("最后一刀不超过 30 秒", merge_prompt("feed", ""))
        seedance = merge_prompt("feed", "模型：Seedance 2.0")
        self.assertIn("最后一刀不超过 15 秒", seedance)


    def test_assemble_steps_feed_duration_follows_clip_limit(self):
        from end.workflow_from_spec import assemble_steps

        feed = (
            "## 【封装单元 1/1】\n"
            "duration: 30\n"
            "subject_definitions:\n"
            "A man in the rain.\n"
            "detailed_description:\n"
            "[Shot 1] Rain falls in the alley.\n"
            "overall_soundscape:\n"
            "Rain.\n"
            "non_diegetic_music:\n"
            "None.\n"
        )
        spec = {
            "assets": [{"id": "图1", "title": "巷", "prompt": "雨巷"}],
            "shots": [{"id": "S01", "duration": 10, "prompt": "雨巷", "images": ["图1"]}],
            "feed": feed,
            "locks": {"model": "h3"},
        }
        _assets, shots, _sources = assemble_steps(spec)
        self.assertEqual([shot["duration"] for shot in shots], [30])

        spec["locks"] = {"model": "2.0"}
        _assets, shots, _sources = assemble_steps(spec)
        self.assertEqual([shot["duration"] for shot in shots], [15])

        spec["feed"] = feed.replace("duration: 30", "duration: 15")
        _assets, shots, _sources = assemble_steps(spec)
        self.assertEqual([shot["duration"] for shot in shots], [15])
