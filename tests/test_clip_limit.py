"""Wan 3.0 hard clip limit is 30s; packing aims to stay within 20s."""

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
    def test_h3_and_wan3_locks_allow_30s_and_pack_within_20s(self):
        self.assertEqual(clip_limit("h3"), 30)
        self.assertEqual(clip_limit("wan3"), 30)
        self.assertEqual(clip_limit(locks={"model": "h3"}), 30)
        self.assertEqual(clip_limit(locks={}), 30)
        self.assertEqual(pack_limit("h3"), 20)
        self.assertEqual(pack_limit("wan3"), 20)
        self.assertEqual(pack_limit(locks={}), 20)
        self.assertEqual(segment_limit("h3"), 30)
        self.assertEqual(segment_limit("wan3"), 30)
        self.assertEqual(segment_limit(""), 0)
        self.assertEqual(clip_limit("2.0"), 15)
        self.assertEqual(pack_limit("2.0"), 15)
        self.assertEqual(clip_limit("2.5"), 30)
        self.assertEqual(pack_limit("2.5"), 20)

    def test_25s_scene_is_split_at_20s_pack_target(self):
        skeleton = storyboard_skeleton({
            "source": BOARD_25,
            "locks": {"duration_seconds": 25, "model": "h3", "aspect": "16:9"},
        })
        shots = skeleton["shots"]
        self.assertEqual([shot["duration"] for shot in shots], [20, 5])

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

    def test_packing_stays_within_20s_and_does_not_merge_past_it(self):
        self.assertEqual(group_shot_durations([25], locks={"model": "h3"}), [[25]])
        self.assertEqual(group_shot_durations([13, 12], locks={"model": "h3"}), [[13], [12]])
        self.assertEqual(group_shot_durations([20, 5], locks={"model": "h3"}), [[20], [5]])
        self.assertEqual(group_shot_durations([5, 5, 6], locks={"model": "h3"}), [[5, 5, 6]])
        self.assertEqual(group_shot_durations([10, 10], locks={"model": "h3"}), [[10, 10]])
        self.assertEqual(
            packing_groups({"shots": [{"id": "S01", "duration": 25}], "locks": {"model": "h3"}}),
            [[25]],
        )

    def test_473b_packing_fills_up_to_20(self):
        shots = [5, 5, 5, 5, 5, 5, 5, 5, 4, 4, 3, 3, 3, 3]
        self.assertEqual(
            [sum(group) for group in group_shot_durations(shots, locks={"model": "h3"})],
            [20, 20, 20],
        )

    def test_25s_shot_must_be_split_but_not_clamped(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 25\n",
            shots=[{"duration": 25}],
            locks={"model": "h3"},
        )
        joined = "\n".join(errors)
        self.assertTrue(errors)
        self.assertIn("25", joined)
        self.assertIn("20", joined)
        self.assertIn("必须把这一镜拆开", joined)
        self.assertNotIn("钳成 15", joined)

    def test_20s_declaration_is_not_a_duration_failure(self):
        errors = feed_duration_failures(
            "## 【封装单元 1/1】\nduration: 20\n",
            shots=[{"duration": 20}],
            locks={"model": "h3"},
        )
        self.assertEqual(errors, [])

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
