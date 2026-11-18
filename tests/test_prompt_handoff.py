"""Feed units and 图 blocks are the strings image and video submit."""

import unittest
from unittest.mock import patch

from tests.offline_guard import install, uninstall


def setUpModule():
    install()


def tearDownModule():
    uninstall()

from end.board_shots import apply_feed_binding, apply_storyboard_shots, shots_from_storyboard
from end.prompt_gate import feed_text
from end.render_loop import NO_ONSCREEN_CAPTION, WAN_REFERENCE_SHEET_GUIDE, delivery_prompt, image_prompt, subtitle_cues
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
H3_CAPTION_LOCK = (
    "The frame shows the scene and the performers. "
    "No readable on-screen subtitles, captions, or overlaid dialogue text. "
    "Spoken lines are audio only and must not be painted into the frame "
    "as Chinese caption glyphs, bottom white text, or any other burned-in dialogue."
)


def _wan_unit(text):
    """What Wan should receive: spoken text stays, <d> and [Chinese] do not."""
    import re
    text = re.sub(r"(?is)<d\b[^>]*>(.*?)</d>", r"\1", text)
    return re.sub(r"\[Chinese\][ \t]*", "", text)
SEEDANCE_CAPTION_LOCK = NO_ONSCREEN_CAPTION
WAN_NEGATIVE = "负向清单：不要字幕，不要水印，不要复杂文字。"


def _assert_wan_negative(test, text):
    test.assertEqual(NO_ONSCREEN_CAPTION, WAN_NEGATIVE)
    test.assertTrue(text.endswith(WAN_NEGATIVE))
    for phrase in ("不要字幕", "不要水印", "不要复杂文字"):
        test.assertEqual(text.count(phrase), 1, phrase)


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
        self.assertEqual(sent, _wan_unit(UNIT.strip()) + "\n" + WAN_REFERENCE_SHEET_GUIDE + "\n" + H3_CAPTION_LOCK + "\n" + WAN_NEGATIVE)
        _assert_wan_negative(self, sent)
        self.assertNotIn("本镜无对白", sent)
        self.assertNotIn("non_diegetic_music: N/A", sent)
        self.assertNotIn("从这张首帧", sent)
        self.assertIn("non_diegetic_music:", sent)
        self.assertIn("等我。", sent)
        self.assertNotIn("[Chinese]", sent)
        self.assertNotIn("<d>", sent)

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

    def test_a_full_unit_keeps_its_body_and_bans_onscreen_captions(self):
        sent = delivery_prompt({"prompt": UNIT}, [], 1, True, "first_frame")
        self.assertEqual(sent, _wan_unit(UNIT.strip()) + "\n" + H3_CAPTION_LOCK + "\n" + WAN_NEGATIVE)
        _assert_wan_negative(self, sent)
        self.assertNotIn("从这张首帧", sent)
        self.assertNotIn("本镜无对白", sent)

    def test_seedance_unit_keeps_its_body_and_bans_onscreen_captions(self):
        seedance = "【画幅风格】竖屏\n【时间轴分镜】0-4秒 她回头。\n"
        sent = delivery_prompt({"prompt": seedance}, [], 0, False, "reference")
        self.assertEqual(sent, seedance.strip() + "\n" + SEEDANCE_CAPTION_LOCK)
        _assert_wan_negative(self, sent)
        self.assertNotIn("本镜无对白", sent)
        self.assertNotIn("No readable on-screen subtitles", sent)

    def test_full_delivery_bans_painted_captions_without_touching_ass(self):
        spoken = [{"speaker": "她", "line": "这句不该写进画面。"}]
        for mode in ("reference", "first_frame", "first_last_frame"):
            sent = delivery_prompt({"prompt": UNIT}, spoken, 1, True, mode)
            self.assertEqual(sent, _wan_unit(UNIT.strip()) + "\n" + H3_CAPTION_LOCK + "\n" + WAN_NEGATIVE)
            _assert_wan_negative(self, sent)
            self.assertEqual(sent.count("等我。"), 1)
            self.assertNotIn("[Chinese]", sent)
            self.assertNotIn("<d>", sent)
            self.assertNotIn("这句不该写进画面", sent)
            self.assertNotIn("从这张首帧", sent)
            self.assertNotIn("本镜无对白", sent)
            self.assertNotIn("non_diegetic_music: N/A", sent)
        again = delivery_prompt({"prompt": sent}, spoken, 0, False, "reference")
        self.assertEqual(again, sent)
        english_only = _wan_unit(UNIT.strip()) + "\n" + H3_CAPTION_LOCK
        self.assertEqual(
            delivery_prompt({"prompt": english_only}, [], 0, False, "reference"),
            english_only + "\n" + WAN_NEGATIVE,
        )
        already = english_only + "\n" + WAN_NEGATIVE
        self.assertEqual(delivery_prompt({"prompt": already}, [], 0, False, "reference"), already)
        seedance = "【画幅风格】竖屏\n【时间轴分镜】0-4秒 她回头。\n" + SEEDANCE_CAPTION_LOCK
        self.assertEqual(
            delivery_prompt({"prompt": seedance}, [], 0, False, "reference"),
            seedance.strip(),
        )
        self.assertEqual(
            subtitle_cues([{"duration": 8, "prompt": UNIT, "narration": "", "spoken": []}]),
            [],
        )
        burned = subtitle_cues([
            {
                "duration": 8,
                "prompt": sent,
                "narration": "",
                "spoken": [{"speaker": "苏晚", "line": "再站一会儿就好。"}],
            }
        ])
        self.assertEqual([cue["text"] for cue in burned], ["再站一会儿就好"])
        voiced = subtitle_cues([
            {
                "duration": 8,
                "prompt": sent,
                "narration": "夜色已经很深了",
                "spoken": [{"speaker": "她", "line": "等我。"}],
            }
        ])
        self.assertEqual([cue["text"] for cue in voiced], ["夜色已经很深了"])

    def test_minimax_only_h3_keeps_the_english_lock(self):
        def describe(profile, env_path=None):
            del env_path
            return profile, {}, {"model": "minimax-h3"}

        def resolve(token, env_path=None):
            del token, env_path
            return "MiniMax/MiniMax-H3"

        with patch("AI.qwen_api.video_catalog.describe_profile", describe), patch(
            "AI.qwen_api.video_catalog.resolve_model_id", resolve
        ):
            sent = delivery_prompt({"prompt": UNIT}, [], 0, False, "first_frame")
            seedance = "【画幅风格】竖屏\n【时间轴分镜】0-4秒 她回头。\n"
            chinese = delivery_prompt({"prompt": seedance}, [], 0, False, "reference")
            short = delivery_prompt({"prompt": "她回头"}, [], 0, False, "reference")
        self.assertEqual(sent, UNIT.strip() + "\n" + H3_CAPTION_LOCK)
        self.assertNotIn("负向清单", sent)
        self.assertNotIn("从这张首帧", sent)
        self.assertIn("<d>[Chinese] 等我。</d>", sent)
        _assert_wan_negative(self, chinese)
        self.assertTrue(short.endswith(WAN_NEGATIVE))
        self.assertIn("本镜无对白", short)

    def test_a_wan_route_still_appends_the_negative_list(self):
        def describe(profile, env_path=None):
            del env_path
            model = "wan3.0-video" if profile == "generate" else "minimax-h3"
            return profile, {}, {"model": model}

        def resolve(token, env_path=None):
            del env_path
            if token == "wan3.0-video":
                return "wan3.0-video"
            return "MiniMax/MiniMax-H3"

        with patch("AI.qwen_api.video_catalog.describe_profile", describe), patch(
            "AI.qwen_api.video_catalog.resolve_model_id", resolve
        ):
            sent = delivery_prompt({"prompt": UNIT}, [], 0, False, "reference")
        _assert_wan_negative(self, sent)
        self.assertIn(H3_CAPTION_LOCK, sent)
        self.assertEqual(sent.count("等我。"), 1)
        self.assertNotIn("[Chinese]", sent)
        self.assertNotIn("<d>", sent)

    def test_wan_submit_keeps_spoken_lines_without_d_tags(self):
        """Wan paints garbled captions from <d> tags; the spoken line itself must stay."""
        body = UNIT.replace(
            "<d>[Chinese] 等我。</d>",
            "<d>[Chinese] 真的不痒了……感觉好平静。</d>\n<d>[Chinese] 无</d>",
        )
        sent = delivery_prompt({"prompt": body}, [], 0, False, "first_frame")
        self.assertIn("真的不痒了……感觉好平静。", sent)
        self.assertIsNone(__import__("re").search(r"(?m)^无$", sent))
        self.assertIn("says", sent)
        self.assertNotIn("[Chinese]", sent)
        self.assertNotIn("<d>", sent)
        self.assertNotIn("</d>", sent)
        _assert_wan_negative(self, sent)

    def test_wan_submit_strips_chinese_tags_and_keeps_spoken_lines(self):
        """Wan ignores [Chinese]; the marker is dropped and the spoken line stays."""
        body = "\n".join((
            "## 【封装单元 1/6】",
            "duration: 15",
            "subject_definitions:",
            "<Subject 1> = Jingfang Daxia (Picture 1)",
            "detailed_description:",
            "[Shot 1] In the misty, rain-slicked alley.",
            "<d>[Chinese] 情绪：冷雨中一点不动声色的暖意</d>",
            "<d>[Chinese] 无</d>",
            "<d>[Chinese] 荆防大侠：看您冻得发抖，鼻塞难忍？莫硬扛。</d>",
            "overall_soundscape:",
            "Rain.",
            "non_diegetic_music:",
            "None",
        ))
        sent = delivery_prompt({"prompt": body}, [], 0, False, "first_frame")
        self.assertNotIn("<d>", sent)
        self.assertNotIn("</d>", sent)
        self.assertNotIn("[Chinese]", sent)
        self.assertIn("风格/情绪：冷雨中一点不动声色的暖意", sent)
        self.assertIsNone(__import__("re").search(r"(?m)^无$", sent))
        self.assertIn('台词：荆防大侠说："看您冻得发抖，鼻塞难忍？莫硬扛。"', sent)
        self.assertIn("Chinese caption glyphs", sent)
        self.assertIn("In the misty, rain-slicked alley.", sent)
        _assert_wan_negative(self, sent)

    def test_wan_submit_places_mood_plot_and_lines_in_official_slots(self):
        """Wan 3.0 Prompt Guide: keep 情绪/情节, put them in official slots, format 台词."""
        body = "\n".join((
            "## 【封装单元 2/3】",
            "duration: 15",
            "subject_definitions:",
            "<Subject 1> = Jingfang Daxia (Picture 1)",
            "detailed_description:",
            "[Shot 1] The young swordsman with a low, flat, cool voice (S1) says at an even pace: 夜凉透骨，莫让寒气趁虚而入。",
            "<d>[Chinese] 情绪：安静里被截住的一缕寒 / 情节：荆防大侠静立房中，窗外雨声渐密。 / 对白（荆防大侠）：夜凉透骨，莫让寒气趁虚而入。</d>",
            "<d>[Chinese] 无</d>",
            "overall_soundscape:",
            "Rain.",
            "non_diegetic_music:",
            "None.",
        ))
        sent = delivery_prompt({"prompt": body}, [], 0, False, "first_frame")
        self.assertNotIn("<d>", sent)
        self.assertNotIn("</d>", sent)
        self.assertNotIn("[Chinese]", sent)
        self.assertIn('台词：荆防大侠说："夜凉透骨，莫让寒气趁虚而入。"', sent)
        self.assertIn("风格/情绪：安静里被截住的一缕寒", sent)
        self.assertIn("荆防大侠静立房中，窗外雨声渐密。", sent)
        self.assertNotIn("对白（荆防大侠）", sent)
        self.assertNotIn("情绪：安静里被截住的一缕寒 / 情节：", sent)
        self.assertIsNone(__import__("re").search(r"(?m)^无$", sent))
        self.assertNotIn("无台词", sent)
        self.assertIn("The young swordsman", sent)
        _assert_wan_negative(self, sent)

    def test_wan_submit_writes_no_dialogue_when_there_is_no_speech(self):
        """Wan 3.0 Prompt Guide: if there is no speech, write 无台词 instead of a bare 无."""
        body = "\n".join((
            "## 【封装单元 1/3】",
            "duration: 15",
            "subject_definitions:",
            "<Subject 1> = Jingfang Daxia (Picture 1)",
            "detailed_description:",
            "[Shot 1] The scholar sleeps under the window.",
            "<d>[Chinese] 情绪：雨夜里的一室安静</d>",
            "<d>[Chinese] 无</d>",
            "overall_soundscape:",
            "Rain.",
            "non_diegetic_music:",
            "None.",
        ))
        sent = delivery_prompt({"prompt": body}, [], 0, False, "first_frame")
        self.assertIn("台词：无台词", sent)
        self.assertEqual(sent.count("无台词"), 1)
        self.assertIsNone(__import__("re").search(r"(?m)^无$", sent))
        self.assertIn("风格/情绪：雨夜里的一室安静", sent)
        self.assertNotIn("<d>", sent)
        self.assertNotIn("[Chinese]", sent)
        _assert_wan_negative(self, sent)

    def test_wan_submit_keeps_master_sheets_from_becoming_the_frame(self):
        """Wan 3.0 Prompt Guide: 母版/九宫格 are guidance only, not a single generated frame."""
        body = "\n".join((
            "## 【封装单元 2/3】",
            "duration: 15",
            "subject_definitions:",
            "<Subject 1> = Jingfang Daxia (Picture 1)",
            "detailed_description:",
            "[Shot 1] The scholar sleeps in the inn.",
            "overall_soundscape:",
            "Rain.",
            "non_diegetic_music:",
            "None.",
        ))
        sent = delivery_prompt({"prompt": body, "images": ["图14"]}, [], 0, False, "reference")
        self.assertIn("使用故事板作为分镜指导，禁止将故事板作为单张图像生成视频", sent)
        self.assertIn("不要把图上的标题、印章、边框、图注", sent)
        self.assertTrue(sent.endswith(WAN_NEGATIVE))
        self.assertLess(sent.index("禁止将故事板作为单张图像生成视频"), sent.index(WAN_NEGATIVE))
        plain = delivery_prompt({"prompt": body}, [], 0, False, "first_frame")
        self.assertNotIn("禁止将故事板作为单张图像生成视频", plain)
        _assert_wan_negative(self, sent)

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
        self.assertEqual(bound["shots"][0]["prompt"], "旧")
        self.assertEqual(bound["shots"][0]["images"], ["A1"])
        self.assertIn("<Subject 1> (图3)", bound["feed"])
        self.assertIn("<Subject 2> (图4)", bound["feed"])
        self.assertIn("<Subject 3> (图1)", bound["feed"])
        self.assertNotIn("<Picture", bound["feed"])
        self.assertIn("<Subject 1>: fully_preserved", bound["feed"])
        again = apply_feed_binding(bound)
        self.assertEqual(again["feed"], bound["feed"])
        self.assertEqual(again["shots"][0]["prompt"], "旧")
        self.assertEqual(again["shots"][0]["images"], ["A1"])
        _assets, shots = collect_steps(spec)
        self.assertEqual(shots[0]["images"], ["A1"])
        self.assertIn("<Picture 1>", shots[0]["prompt"])
