"""The seam ledger is a program-owned skeleton. The model fills slots.

    python3 -m unittest tests.test_seam_skeleton
"""

import json
import unittest

from Agents.workers.seam import (
    GENERATOR_SYSTEM,
    format_seam_skeleton,
    generator_messages,
)
from end.seam_ledger import (
    SCENE_RELATIONS,
    SEAM_SLOT_KEYS,
    SIZE_LADDER,
    check_seam_ledger,
    fold_seam_entries,
)


def _units():
    return [
        {"id": "封装单元 1/3", "duration": 8, "images": ["图1"]},
        {"id": "封装单元 2/3", "duration": 8, "images": ["图2"]},
        {"id": "封装单元 3/3", "duration": 7, "images": []},
    ]


def _state(draft=""):
    return {
        "attempt": 0,
        "units": _units(),
        "source": "剧本",
        "storyboard": "分镜",
        "feed": "",
        "draft": draft,
        "critic_report": {},
        "machine_report": {"errors": [], "warnings": []},
    }


class SeamFoldTest(unittest.TestCase):
    def test_one_entry_fills_its_unit_and_the_others_stay_slots(self):
        folded = fold_seam_entries([
            {
                "shot_id": "封装单元 1/3",
                "d_lock": "2",
                "reason": "只写了第一段",
                "scene_relation": "客厅",
                "start_size": "特写镜头",
                "end_size": "并不存在的景别",
                "action_continuous": False,
            },
        ], _units())
        self.assertEqual([row["shot_id"] for row in folded], [unit["id"] for unit in _units()])
        self.assertEqual(folded[0]["d_lock"], "none")
        self.assertEqual(folded[0]["reason"], "只写了第一段")
        self.assertEqual(folded[0]["scene_relation"], "opening")
        self.assertEqual(folded[0]["start_size"], "特写")
        self.assertEqual(folded[0]["end_size"], "并不存在的景别")
        self.assertEqual(folded[0]["video_mode"], "reference")
        self.assertEqual(folded[0]["first_frame_source"], "none")
        self.assertEqual(folded[0]["images"], ["图1"])
        self.assertEqual(folded[0]["prev_id"], "")
        for row in folded[1:]:
            for key in SEAM_SLOT_KEYS:
                self.assertIn(key, row)
            self.assertEqual(row["d_lock"], "2")
            self.assertEqual(row["reason"], "")
            self.assertEqual(row["scene_relation"], "same_scene")
            self.assertEqual(row["start_size"], "")
            self.assertEqual(row["end_size"], "")
            self.assertEqual(row["video_mode"], "reference")
            self.assertNotIn("只写了第一段", row["reason"])
        self.assertEqual(folded[1]["prev_id"], "封装单元 1/3")
        self.assertEqual(folded[1]["images"], ["图2"])
        self.assertEqual(folded[2]["images"], [])
        report = check_seam_ledger({"entries": folded}, _units())
        self.assertFalse(report["ok"])
        self.assertIn("封装单元 1/3 的末镜景别不在景别阶梯上", report["report"])
        self.assertEqual(folded[0]["end_size"], "并不存在的景别")
        again = fold_seam_entries(folded, _units())
        self.assertEqual(again, folded)

    def test_a_named_unit_keeps_its_row_and_a_ladder_size_is_kept(self):
        folded = fold_seam_entries([
            {
                "shot_id": "封装单元 2/3",
                "d_lock": "2",
                "reason": "换机位",
                "scene_relation": "time_jump",
                "start_size": "中景",
                "end_size": "近景",
                "camera_change": True,
                "camera_from": "左",
                "camera_to": "右",
                "action_continuous": False,
            },
        ], _units())
        self.assertEqual(folded[1]["reason"], "换机位")
        self.assertEqual(folded[1]["d_lock"], "2")
        self.assertEqual(folded[1]["scene_relation"], "time_jump")
        self.assertEqual(folded[1]["start_size"], "中景")
        self.assertEqual(folded[1]["end_size"], "近景")
        self.assertEqual(folded[1]["video_mode"], "reference")
        self.assertEqual(folded[0]["reason"], "")
        self.assertEqual(folded[2]["d_lock"], "2")
        self.assertNotIn("换机位", folded[0]["reason"])
        self.assertNotIn("换机位", folded[2]["reason"])

    def test_a_long_unit_cannot_keep_an_empty_shot_lock(self):
        folded = fold_seam_entries([
            {"shot_id": "封装单元 1/3", "d_lock": "none"},
            {
                "shot_id": "封装单元 2/3",
                "d_lock": "3",
                "empty_use": "时空转场",
                "reason": "地点跳了",
                "scene_relation": "place_jump",
                "camera_change": True,
                "camera_from": "左",
                "camera_to": "右",
            },
        ], _units())
        self.assertEqual(folded[1]["d_lock"], "2")
        self.assertEqual(folded[1]["empty_use"], "")

    def test_a_one_to_two_second_unit_can_keep_an_empty_shot_lock(self):
        units = [
            {"id": "封装单元 1/2", "duration": 18, "images": ["图1"]},
            {"id": "封装单元 2/2", "duration": 2, "images": []},
        ]
        folded = fold_seam_entries([
            {"shot_id": "封装单元 1/2", "d_lock": "none"},
            {
                "shot_id": "封装单元 2/2",
                "d_lock": "3",
                "empty_use": "时空转场",
                "reason": "空镜过渡",
            },
        ], units)
        self.assertEqual(folded[1]["d_lock"], "3")
        self.assertEqual(folded[1]["empty_use"], "时空转场")

    def test_a_long_unit_keeps_a_continue_or_named_transition_lock(self):
        folded = fold_seam_entries([
            {"shot_id": "封装单元 1/3", "d_lock": "none", "end_size": "中近景"},
            {
                "shot_id": "封装单元 2/3",
                "d_lock": "1",
                "action_continuous": True,
                "start_size": "特写",
                "end_size": "近景",
            },
            {
                "shot_id": "封装单元 3/3",
                "d_lock": "4",
                "transition": "甩镜",
            },
        ], _units())
        self.assertEqual(folded[1]["d_lock"], "1")
        self.assertEqual(folded[2]["d_lock"], "4")

    def test_a_long_unit_defaults_to_a_hard_cut_when_the_lock_is_empty(self):
        units = [
            {"id": "封装单元 1/3", "duration": 21, "images": ["图1"]},
            {"id": "封装单元 2/3", "duration": 21, "images": ["图2"]},
            {"id": "封装单元 3/3", "duration": 2, "images": []},
        ]
        folded = fold_seam_entries([
            {"shot_id": "封装单元 1/3", "d_lock": "none"},
        ], units)
        self.assertEqual(folded[0]["d_lock"], "none")
        self.assertEqual(folded[1]["d_lock"], "2")
        self.assertEqual(folded[2]["d_lock"], "")

    def test_prompts_show_the_skeleton_and_the_size_ladder(self):
        draft = json.dumps({
            "seam_ledger": {
                "entries": fold_seam_entries([
                    {
                        "shot_id": "封装单元 1/3",
                        "d_lock": "none",
                        "reason": "开场已经写好",
                        "start_size": "中景",
                        "end_size": "中景",
                    },
                    {
                        "shot_id": "封装单元 2/3",
                        "d_lock": "2",
                        "reason": "这一刀换了机位",
                        "start_size": "并不存在的景别",
                        "end_size": "近景",
                        "camera_change": True,
                        "camera_from": "左",
                        "camera_to": "右",
                    },
                ], _units()),
            },
        }, ensure_ascii=False)
        state = _state()
        first = generator_messages(state)[-1]["content"]
        repair = generator_messages({
            "attempt": 1,
            "units": _units(),
            "draft": draft,
            "source": "剧本",
            "storyboard": "分镜",
            "critic_report": {"verdict": "revise", "issues": []},
            "machine_report": {
                "errors": ["封装单元 2/3 的起镜景别不在景别阶梯上"],
                "warnings": [],
            },
        })[-1]["content"]
        opened = generator_messages(dict(state, opening="任务：分镜衔接\n已有简报"))[-1]["content"]
        for brief in (first, repair, opened):
            self.assertIn("当前段缝骨架如下", brief)
            self.assertIn("不要另写一套包装", brief)
            self.assertIn("封装单元 1/3", brief)
            self.assertIn("封装单元 3/3", brief)
            self.assertIn('"shot_id"', brief)
            self.assertIn('"start_size"', brief)
            self.assertIn('"end_size"', brief)
            self.assertIn('"video_mode"', brief)
            self.assertIn('"scene_relation"', brief)
            for token in SCENE_RELATIONS:
                self.assertIn(token, brief)
            for size in SIZE_LADDER:
                self.assertIn(size, brief)
        self.assertIn("开场已经写好", repair)
        self.assertIn("并不存在的景别", repair)
        self.assertIn("这一刀换了机位", repair)
        self.assertIn("只改机检点名的失败项", repair)
        self.assertIn("保留已经填对的槽", repair)
        self.assertIn("景别阶梯", GENERATOR_SYSTEM)
        skeleton = format_seam_skeleton(_state(draft))
        self.assertIn("开场已经写好", skeleton)
        self.assertEqual(skeleton.count("封装单元 1/3"), 2)

    def test_first_frame_start_size_is_locked_to_previous_end(self):
        folded = fold_seam_entries([
            {
                "shot_id": "封装单元 1/3",
                "d_lock": "none",
                "start_size": "全景",
                "end_size": "中近景",
            },
            {
                "shot_id": "封装单元 2/3",
                "d_lock": "1",
                "reason": "动作还在继续",
                "start_size": "特写",
                "end_size": "近景",
                "action_continuous": True,
                "camera_change": True,
                "camera_from": "侧面",
                "camera_to": "正面",
                "tail_croppable": True,
                "crop_plan": "人在场，朝向可用，背景锚点可见，无压屏",
            },
            {
                "shot_id": "封装单元 3/3",
                "d_lock": "2",
                "reason": "换场",
                "start_size": "中景",
                "end_size": "近景",
                "camera_change": True,
                "camera_from": "左",
                "camera_to": "右",
            },
        ], _units())
        self.assertEqual(folded[1]["d_lock"], "1")
        self.assertEqual(folded[1]["start_size"], "中近景")
        self.assertEqual(folded[1]["end_size"], "近景")
        self.assertEqual(folded[2]["start_size"], "中景")
        report = check_seam_ledger({"entries": folded}, _units())
        self.assertNotIn("两侧景别必须同档", report["report"], report["report"])

    def test_prompts_lock_first_frame_start_size_to_previous_end(self):
        draft = json.dumps({
            "seam_ledger": {
                "entries": fold_seam_entries([
                    {
                        "shot_id": "封装单元 1/3",
                        "d_lock": "none",
                        "start_size": "全景",
                        "end_size": "中近景",
                    },
                    {
                        "shot_id": "封装单元 2/3",
                        "d_lock": "1",
                        "start_size": "特写",
                        "end_size": "近景",
                        "action_continuous": True,
                        "camera_change": True,
                        "camera_from": "侧面",
                        "camera_to": "正面",
                        "tail_croppable": True,
                        "crop_plan": "人在场，朝向可用，背景锚点可见，无压屏",
                    },
                ], _units()),
            },
        }, ensure_ascii=False)
        brief = generator_messages({
            "attempt": 1,
            "units": _units(),
            "draft": draft,
            "source": "剧本",
            "storyboard": "分镜",
            "critic_report": {"verdict": "revise", "issues": []},
            "machine_report": {"errors": [], "warnings": []},
        })[-1]["content"]
        self.assertIn("① 的起镜景别由程序锁成上一单元的末镜景别", brief)
        self.assertIn('"start_size": "中近景"', brief)
        self.assertIn("① 的起镜景别由程序锁成上一单元的末镜景别", GENERATOR_SYSTEM)

    def test_prompts_lock_a_long_unit_away_from_an_empty_shot(self):
        draft = json.dumps({
            "seam_ledger": {
                "entries": [
                    {"shot_id": "封装单元 1/3", "d_lock": "none"},
                    {
                        "shot_id": "封装单元 2/3",
                        "d_lock": "3",
                        "empty_use": "时空转场",
                        "reason": "地点跳了",
                    },
                ],
            },
        }, ensure_ascii=False)
        skeleton = format_seam_skeleton(_state(draft))
        self.assertIn('"duration": 8', skeleton)
        self.assertIn('"d_lock": "2"', skeleton)
        self.assertNotIn('"d_lock": "3"', skeleton)
        self.assertNotIn("时空转场", skeleton)
        self.assertIn("单元时长由程序从封装单元写入", skeleton)
        self.assertIn("不得写 ③", skeleton)
        self.assertIn("程序会改回 ②", skeleton)
        self.assertIn("① 和 ④ 仍可按规程选", skeleton)
        brief = generator_messages({
            "attempt": 1,
            "units": _units(),
            "draft": draft,
            "source": "剧本",
            "storyboard": "分镜",
            "critic_report": {"verdict": "revise", "issues": []},
            "machine_report": {"errors": [], "warnings": []},
        })[-1]["content"]
        self.assertIn('"d_lock": "2"', brief)
        self.assertIn("程序会改回 ②", brief)
        self.assertIn("程序会改回 ②", GENERATOR_SYSTEM)



if __name__ == "__main__":
    unittest.main()
