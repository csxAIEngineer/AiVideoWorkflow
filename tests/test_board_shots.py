"""Storyboard rows become the canvas shot list."""

import unittest

from end.board_shots import apply_storyboard_shots, shots_from_storyboard
from end.board_shots import bind_feed_unit, _wan_fields
from Agents.workers.feed.wan_format import WAN_SHOT_HEADING
from end.intake import merge_intake
from end.stage_runner import _spec_patch

BOARD = str(
    [
        {
            "shot_id": "镜头 01",
            "duration_sec": 3,
            "camera_spec": "中景",
            "visual_content": "林浅蜷在客厅沙发上。",
            "dialogue": "无",
            "blocking_note": "林浅朝南",
        },
        {
            "shot_id": "镜头 02",
            "duration_sec": 5,
            "camera_spec": "近景",
            "visual_content": "陈默递出温水。",
            "dialogue": "喝一口",
            "blocking_note": "陈默在右侧",
        },
    ]
)
ASSETS = [
    {"id": "图1", "title": "昏暗客厅", "subject": "客厅", "prompt": "客厅"},
    {"id": "图2", "title": "女主·林浅", "subject": "林浅", "prompt": "林浅"},
    {"id": "图3", "title": "男主·陈默", "subject": "陈默", "prompt": "陈默"},
    {"id": "A1", "title": "痛经胶囊", "subject": "痛经胶囊", "prompt": "包装", "references": ["U1"]},
]


class BoardShotsTest(unittest.TestCase):
    def test_python_literal_board_becomes_canvas_shots(self):
        shots = shots_from_storyboard(BOARD, ASSETS)
        self.assertEqual([shot["id"] for shot in shots], ["S01", "S02"])
        self.assertEqual(shots[0]["duration"], 4)
        self.assertEqual(shots[0]["dialogue"], "")
        self.assertIn("林浅蜷在客厅沙发上", shots[0]["prompt"])
        self.assertEqual(shots[0]["images"], ["图1", "图2"])
        self.assertEqual(shots[1]["dialogue"], "喝一口")
        self.assertEqual(shots[1]["images"], ["图3"])

    def test_placeholder_shot_is_replaced_and_a_real_shot_stays(self):
        spec = apply_storyboard_shots(
            {
                "storyboard": BOARD,
                "assets": ASSETS,
                "shots": [
                    {
                        "id": "S01",
                        "duration": 4,
                        "prompt": "按这张资产图演这一镜",
                        "images": ["A1"],
                        "dialogue": "",
                        "narration": "",
                    }
                ],
            }
        )
        self.assertEqual([shot["id"] for shot in spec["shots"]], ["S01", "S02"])
        kept = apply_storyboard_shots(
            {
                "storyboard": BOARD,
                "assets": ASSETS,
                "shots": [{"id": "S09", "duration": 6, "prompt": "用户改过这一镜", "images": []}],
            }
        )
        self.assertEqual(kept["shots"][0]["prompt"], "用户改过这一镜")

    def test_upload_does_not_add_a_shot_beside_the_board(self):
        spec = apply_storyboard_shots(
            {"storyboard": BOARD, "assets": list(ASSETS), "shots": []}
        )
        merged = merge_intake(spec, [{"id": "U1", "title": "痛经胶囊", "narration": ""}])
        self.assertEqual([shot["id"] for shot in merged["shots"]], ["S01", "S02"])
        self.assertTrue(all(shot["prompt"] != "按这张资产图演这一镜" for shot in merged["shots"]))

    def test_storyboard_stage_writes_the_shots(self):
        spec = _spec_patch(
            "storyboard",
            {"storyboard": BOARD, "previs": "## 段 1"},
            {"assets": ASSETS, "shots": []},
        )
        self.assertEqual([shot["id"] for shot in spec["shots"]], ["S01", "S02"])
        # The storyboard stage prepends the intentional 素材形态 header (M1 default).
        self.assertEqual(spec["previs"], "素材形态：M1\n\n## 段 1\n")

    def test_description_rows_become_canvas_shots(self):
        board = str(
            [
                {
                    "shot_id": "S01",
                    "time_range": "00:00-00:05",
                    "scene_ref": "图1",
                    "character_refs": ["图3"],
                    "description": "【全景】苏棠站在客厅。",
                    "action_phase": "起势",
                    "vfx": "无",
                }
            ]
        )
        shots = shots_from_storyboard(board, ASSETS)
        self.assertEqual(shots[0]["id"], "S01")
        self.assertEqual(shots[0]["duration"], 5)
        self.assertIn("苏棠站在客厅", shots[0]["prompt"])
        self.assertIn("起势", shots[0]["prompt"])
        self.assertEqual(shots[0]["images"][:2], ["图1", "图3"])

    def test_leftover_dialogue_line_inside_a_shot_body_stays_in_fenjing(self):
        # MO-7: a 台词： line the model left inside the 分镜 body must not be
        # read as a new section header and steal the later 分镜N blocks.
        unit = (
            "总体描述:\n一段客厅里的戏。\n"
            "参考素材引用:\n<Subject 1>: 女主\n"
            "分镜:\n"
            "分镜1（00:00.000-00:03.000）：林浅蜷在沙发上。\n"
            "台词：她低声说了一句。\n"
            "分镜2（00:03.000-00:06.000）：陈默递出温水。\n"
            "台词:\n陈默说：\"喝一口。\"\n"
            "音效 / BGM:\n安静。"
        )
        out, ids = bind_feed_unit(unit, [])
        self.assertEqual(ids, [])
        fields = _wan_fields(out)
        fenjing = fields["分镜"]
        self.assertIn("分镜1（00:00.000-00:03.000）：", fenjing)
        self.assertIn("分镜2（00:03.000-00:06.000）：", fenjing)
        self.assertIn("台词：她低声说了一句。", fenjing)
        shot_lines = [line for line in fenjing.splitlines() if WAN_SHOT_HEADING.match(line)]
        self.assertEqual(len(shot_lines), 2)
        # The real 台词 section keeps only the dialogue, no stolen shot blocks.
        self.assertEqual(fields["台词"], "陈默说：\"喝一口。\"")
        self.assertEqual([line for line in fields["台词"].splitlines() if WAN_SHOT_HEADING.match(line)], [])
        # Round-trip is stable: re-parsing the joined output keeps the same bodies.
        again = _wan_fields(out)
        self.assertEqual(again["分镜"], fenjing)
        self.assertEqual(again["台词"], fields["台词"])
