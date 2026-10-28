"""Feed units and 图 blocks are the strings image and video submit."""

import unittest

from end.board_shots import apply_feed_binding, apply_storyboard_shots, shots_from_storyboard
from end.prompt_gate import feed_text
from end.render_loop import delivery_prompt, image_prompt
from end.server import _merge_spec
from end.workflow_from_spec import collect_steps

BLOCK = "#### 图1 · 女主林浅\n雨夜车站，黑色大衣，正面半身，脸部清楚。\n"
UNIT = "\n".join((
    "## 【投喂单元 1/1 · 段1】",
    "duration: 8",
    "subject_definitions:",
    "<Subject 1> is a woman in a black coat.",
    "summary:",
    "She waits.",
    "retention_analysis:",
    "<Subject 1>: fully_preserved.",
    "detailed_description:",
    "[Shot 1] She says <d>[Chinese] 等我。</d>",
    "overall_soundscape:",
    "Rain.",
    "non_diegetic_music:",
    "none.",
))
UNIT_2 = UNIT.replace("1/1", "2/2").replace("等我。", "走吧。")
PLACEHOLDER = "按这张资产图演这一镜"


def _spec():
    return {
        "aspect_ratio": "9:16",
        "image_prompts": BLOCK,
        "feed": UNIT,
        "assets": [{"id": "图1", "title": "女主林浅", "prompt": "女主"}],
        "shots": [{
            "id": "S01",
            "duration": 5,
            "prompt": PLACEHOLDER,
            "images": ["图1"],
            "dialogue": "",
            "narration": "",
        }],
    }


class HandoffTest(unittest.TestCase):
    def test_feed_unit_is_the_video_text_without_room_tone(self):
        assets, shots = collect_steps(_spec())
        self.assertEqual(image_prompt(assets[0]), BLOCK.strip())
        self.assertEqual(shots[0]["prompt"], UNIT.strip())
        self.assertEqual(shots[0]["duration"], 8)
        sent = delivery_prompt(shots[0], [], 0, False, "first_frame")
        self.assertEqual(sent, UNIT.strip())
        self.assertNotIn("全程无字幕", sent)
        self.assertNotIn("本镜无对白", sent)
        self.assertNotIn("non_diegetic_music: N/A", sent)
        self.assertNotIn("从这张首帧", sent)
        self.assertIn("non_diegetic_music:", sent)
        self.assertIn("等我。", sent)

    def test_units_fill_shots_in_order(self):
        spec = _spec()
        spec["feed"] = UNIT + "\n\n" + UNIT_2
        spec["shots"].append({
            "id": "S02",
            "duration": 5,
            "prompt": "中景",
            "images": ["图1"],
        })
        _assets, shots = collect_steps(spec)
        self.assertEqual([shot["prompt"] for shot in shots], [UNIT.strip(), UNIT_2.strip()])
        self.assertNotIn("中景", shots[1]["prompt"])

    def test_duplicate_shot_ids_collapse_to_one_clip_each(self):
        """S01-01/S01-02 become the same S01 file; join must not play it thrice."""
        spec = _spec()
        spec["shots"] = [
            {"id": "S01", "duration": 4, "prompt": "a", "images": ["图1"]},
            {"id": "S01", "duration": 4, "prompt": "b", "images": ["图1"]},
            {"id": "S01", "duration": 4, "prompt": "c", "images": ["图1"]},
            {"id": "S02", "duration": 4, "prompt": "d", "images": ["图1"]},
        ]
        _assets, shots = collect_steps(spec)
        # One feed unit → one MiniMax call. Extra storyboard rows are not prompts.
        self.assertEqual([shot["id"] for shot in shots], ["S01"])
        self.assertEqual(shots[0]["prompt"], UNIT.strip())

    def test_feed_units_never_pad_with_storyboard_chinese(self):
        from end.prompt_gate import feed_unit_mismatch

        spec = _spec()
        spec["feed"] = UNIT.replace("1/1", "1/4")
        spec["shots"] = [
            {"id": "S01", "duration": 4, "prompt": "a", "images": ["图1"]},
            {"id": "S02", "duration": 4, "prompt": "影兽闯入", "images": ["图1"]},
        ]
        self.assertIn("*/4", feed_unit_mismatch(spec["feed"]).replace(" ", "") or feed_unit_mismatch(spec["feed"]))
        self.assertTrue(feed_unit_mismatch(spec["feed"]))
        _assets, shots = collect_steps(spec)
        self.assertEqual(len(shots), 1)
        self.assertIn("subject_definitions", shots[0]["prompt"])
        self.assertNotIn("影兽闯入", shots[0]["prompt"])

    def test_a_full_unit_already_on_the_shot_is_not_decorated(self):
        sent = delivery_prompt({"prompt": UNIT}, [], 1, True, "first_frame")
        self.assertEqual(sent, UNIT.strip())

    def test_seedance_unit_is_not_decorated(self):
        seedance = "【画幅风格】竖屏\n【时间轴分镜】0-4秒 她回头。\n"
        sent = delivery_prompt({"prompt": seedance}, [], 0, False, "reference")
        self.assertEqual(sent, seedance.strip())
        self.assertNotIn("本镜无对白", sent)

    def test_markdown_board_does_not_keep_the_placeholder_when_feed_exists(self):
        spec = apply_storyboard_shots({
            "storyboard": "## 镜 1\n中景，她回头。\n",
            "feed": UNIT,
            "shots": [{"id": "S01", "prompt": PLACEHOLDER, "images": ["图1"]}],
        })
        self.assertEqual(spec["shots"][0]["prompt"], UNIT.strip())
        self.assertNotIn(PLACEHOLDER, spec["shots"][0]["prompt"])

    def test_long_row_prompt_beats_the_short_columns(self):
        written = "完整的镜头正文" * 5
        shots = shots_from_storyboard(
            [{"prompt": written, "camera_spec": "中景", "visual_content": "短"}],
            [],
        )
        self.assertEqual(shots[0]["prompt"], written)

    def test_feed_survives_merge_and_feed_md(self):
        self.assertEqual(feed_text(_spec()), UNIT.strip())
        self.assertNotIn(PLACEHOLDER, feed_text(_spec()))
        merged = _merge_spec({"title": "旧"}, {"feed": UNIT, "previs": "## 段 1"})
        self.assertEqual(merged["feed"], UNIT)
        self.assertEqual(merged["previs"], "## 段 1")
        self.assertEqual(feed_text({"shots": [{"prompt": "短"}]}), "短")

    def test_shorter_asset_line_uses_the_figure_block(self):
        assets, _shots = collect_steps(_spec())
        self.assertEqual(assets[0]["prompt"], BLOCK.strip())
        edited = "用户后来改成的另一套外观描述。" * 8
        spec = _spec()
        spec["assets"][0]["prompt"] = edited
        assets, _shots = collect_steps(spec)
        self.assertEqual(assets[0]["prompt"], edited)

    def test_feed_unit_binds_subject_stills_and_drops_picture(self):
        feed = "\n".join((
            "## 【投喂单元 1/1】",
            "subject_definitions:",
            "<Subject 1> (Female Protagonist): a woman in a pink dress.",
            "<Subject 2> (Male Protagonist): a man in a blue shirt.",
            "<Subject 3> (Environment): the female protagonist waits in a modern apartment living room.",
            "<Picture 1>: the first keyframe of both people on the sofa.",
            "summary:",
            "reference generation",
            "retention_analysis:",
            "<Picture 1>: fully_preserved",
            "detailed_description:",
            "[Shot 1] <Subject 1> sits beside <Subject 2>. <Picture 1> stays in frame.",
            "overall_soundscape:",
            "Room tone.",
            "non_diegetic_music:",
            "none.",
        ))
        spec = {
            "feed": feed,
            "assets": [
                {"id": "图1", "title": "客厅母版", "subject_kind": "location", "prompt": "客厅"},
                {"id": "图2", "title": "客厅九宫格", "subject_kind": "location", "prompt": "九宫格"},
                {"id": "图3", "title": "女主角色四格", "subject_kind": "character", "prompt": "女主"},
                {"id": "图4", "title": "男主角色四格", "subject_kind": "character", "prompt": "男主"},
                {"id": "A1", "title": "痛经胶囊", "subject_kind": "other", "prompt": "胶囊"},
            ],
            "shots": [{"id": "S01", "duration": 4, "prompt": "旧", "images": ["A1"]}],
            "image_prompts": "#### 图1 · 客厅母版\n空房间。\n",
        }
        bound = apply_feed_binding(spec)
        self.assertEqual(bound["shots"][0]["images"], ["图3", "图4", "图1", "图2"])
        self.assertIn("<Subject 1> (图3)", bound["feed"])
        self.assertIn("<Subject 2> (图4)", bound["feed"])
        self.assertIn("<Subject 3> (图1)", bound["feed"])
        self.assertNotIn("<Picture", bound["feed"])
        self.assertIn("<Subject 1>: fully_preserved", bound["feed"])
        self.assertNotIn("A1", bound["shots"][0]["images"])
        again = apply_feed_binding(bound)
        self.assertEqual(again["feed"], bound["feed"])
        self.assertEqual(again["shots"][0]["images"], bound["shots"][0]["images"])
        _assets, shots = collect_steps(spec)
        self.assertEqual(shots[0]["images"], ["图3", "图4", "图1", "图2"])
        self.assertIn("<Picture 1>", shots[0]["prompt"])
