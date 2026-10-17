"""Narration timeline, mix levels, and the cache that skips a second recording."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from end.image_node import video_nodes
from end.render_loop import delivery_prompt
from end.soundtrack import (
    AMBIENT_GAIN,
    MUSIC_GAIN,
    instrumental_prompt,
    lay_soundtrack,
    mix_command,
    schedule_voice,
)
from end.workflow_from_spec import task_plan


SHOTS = [
    {"id": "S01", "duration": 15, "narration": "第一句"},
    {"id": "S02", "duration": 15, "narration": ""},
    {"id": "S03", "duration": 15, "narration": "下一句"},
]


class FakeMini(object):
    def __init__(self):
        self.lines = []
        self.music = []

    def synthesize(self, text, dest, voice_id=None):
        self.lines.append(text)
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"voice")

    def generate_music(self, prompt, dest):
        self.music.append(prompt)
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"music")


def _duration_of(path):
    return {"S01.mp3": 8.0, "S03.mp3": 2.5, "S09.mp3": 12.0}.get(Path(path).name, 4.0)


class SoundtrackTimelineTest(unittest.TestCase):
    def test_in_point_follows_the_real_clip_not_the_storyboard(self):
        cues = schedule_voice(
            [
                {"id": "S01", "duration": 15, "narration": ""},
                {"id": "S02", "duration": 15, "narration": "她在等"},
            ],
            [4.25, 3.0],
            [{"shot_id": "S02", "audio_duration": 1.5}],
        )
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0]["start"], 4.25)
        self.assertAlmostEqual(cues[0]["end"], 5.75)

    def test_a_line_can_cross_a_silent_shot(self):
        cues = schedule_voice(
            SHOTS[:2] + [{"id": "S03", "duration": 15, "narration": ""}],
            [2.0, 3.0, 4.0],
            [{"shot_id": "S01", "audio_duration": 6.0}],
        )
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[0]["end"], 6.0)
        self.assertGreater(cues[0]["end"], 2.0)
        self.assertGreater(cues[0]["end"], 5.0)

    def test_the_next_line_cuts_this_one_and_the_film_end_trims(self):
        cues = schedule_voice(
            SHOTS,
            [2.0, 3.0, 4.0],
            [
                {"shot_id": "S01", "audio_duration": 20.0},
                {"shot_id": "S03", "audio_duration": 2.5},
            ],
        )
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[0]["end"], 5.0)
        self.assertAlmostEqual(cues[1]["start"], 5.0)
        self.assertAlmostEqual(cues[1]["end"], 7.5)
        trimmed = schedule_voice(
            [{"id": "S09", "duration": 15, "narration": "收到结尾"}],
            [3.0],
            [{"shot_id": "S09", "audio_duration": 12.0}],
        )
        self.assertAlmostEqual(trimmed[0]["end"], 3.0)

    def test_unchanged_text_is_not_recorded_again(self):
        mixes = []

        def mixer(video, music, cues, voice_dir, target, film_end):
            mixes.append([(cue["shot_id"], cue["start"], cue["end"]) for cue in cues])
            Path(target).write_bytes(b"mixed")

        def boom(*_args, **_kwargs):
            raise AssertionError("不应访问网络")

        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            video = work / "concat.mp4"
            video.write_bytes(b"picture")
            client = FakeMini()
            with patch("urllib.request.urlopen", side_effect=boom):
                first = lay_soundtrack(
                    work,
                    SHOTS,
                    [2.0, 3.0, 4.0],
                    "music_narration",
                    video,
                    brief="雨夜的车",
                    client=client,
                    mixer=mixer,
                    duration_of=_duration_of,
                )
                second = lay_soundtrack(
                    work,
                    SHOTS,
                    [2.5, 3.0, 4.0],
                    "music_narration",
                    video,
                    brief="雨夜的车",
                    client=client,
                    mixer=mixer,
                    duration_of=_duration_of,
                )
            self.assertEqual(client.lines, ["第一句", "下一句"])
            self.assertEqual(first["voice_requests"], 2)
            self.assertEqual(second["voice_requests"], 0)
            self.assertEqual(first["music_requests"], 0)
            self.assertEqual(client.music, [])
            self.assertEqual(len(mixes), 2)
            self.assertAlmostEqual(mixes[0][0][2], 5.0)
            self.assertAlmostEqual(mixes[1][0][2], 5.5)
            saved = json.loads((work / "outputs" / "voice" / "cues.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["lines"][0]["text"], "第一句")
            self.assertTrue((work / "outputs" / "voice" / "S01.mp3").is_file())

            client.lines.clear()
            changed = [dict(shot) for shot in SHOTS]
            changed[0]["narration"] = "改过的一句"
            third = lay_soundtrack(
                work,
                changed,
                [2.5, 3.0, 4.0],
                "music_narration",
                video,
                brief="雨夜的车",
                client=client,
                mixer=mixer,
                duration_of=_duration_of,
            )
            self.assertEqual(client.lines, ["改过的一句"])
            self.assertEqual(third["voice_requests"], 1)
            self.assertEqual(third["music_requests"], 0)

    def test_music_only_and_empty_narration_do_not_record_speech(self):
        def mixer(_video, _music, _cues, _voice_dir, target, _film_end):
            Path(target).write_bytes(b"mixed")

        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            video = work / "concat.mp4"
            video.write_bytes(b"picture")
            client = FakeMini()
            music_only = lay_soundtrack(
                work,
                SHOTS,
                [2.0, 3.0, 4.0],
                "music",
                video,
                brief="城市夜路",
                client=client,
                mixer=mixer,
                duration_of=_duration_of,
            )
            self.assertEqual(client.lines, [])
            self.assertEqual(music_only["note"], "")
            self.assertEqual(music_only["timeline"], [])
            quiet = lay_soundtrack(
                work,
                [{"id": "S01", "duration": 4, "narration": ""}],
                [4.0],
                "music_narration",
                video,
                brief="城市夜路",
                client=client,
                mixer=mixer,
                duration_of=_duration_of,
            )
            self.assertEqual(client.lines, [])
            self.assertEqual(quiet["note"], "没有旁白")

    def test_voice_mix_skips_music_and_keeps_a_silent_picture(self):
        mixes = []

        def mixer(video, music, cues, voice_dir, target, film_end):
            mixes.append((music, list(cues)))
            Path(target).write_bytes(b"voiced")

        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            video = work / "concat.mp4"
            video.write_bytes(b"picture")
            client = FakeMini()
            quiet = lay_soundtrack(
                work,
                [{"id": "S01", "duration": 4, "narration": ""}],
                [4.0],
                "narration",
                video,
                client=client,
                mixer=mixer,
                duration_of=_duration_of,
                target=work / "outputs" / "mixed.mp4",
            )
            self.assertEqual(quiet["note"], "没有旁白")
            self.assertEqual(quiet["music_requests"], 0)
            self.assertEqual(client.music, [])
            self.assertEqual(mixes, [])
            self.assertEqual((work / "outputs" / "mixed.mp4").read_bytes(), b"picture")

            voiced = lay_soundtrack(
                work,
                SHOTS,
                [2.5, 3.0, 4.0],
                "music_narration",
                video,
                client=client,
                mixer=mixer,
                duration_of=_duration_of,
            )
            self.assertEqual(voiced["music_requests"], 0)
            self.assertIsNone(mixes[-1][0])
            self.assertEqual(len(mixes[-1][1]), 2)
            bare = mix_command("ffmpeg", "concat.mp4", None, mixes[-1][1], "voice", "mixed.mp4", 9)
            self.assertNotIn("-stream_loop", bare)
            self.assertIn("[1:a]", bare[bare.index("-filter_complex") + 1])

    def test_mix_ducks_ambient_lowers_music_and_leaves_narration(self):
        cues = [{"shot_id": "S01", "file": "S01.mp3", "start": 2.5, "end": 6.0}]
        command = mix_command("ffmpeg", "concat.mp4", "music.mp3", cues, "voice", "mixed.mp4", 9)
        graph = command[command.index("-filter_complex") + 1]
        narration = [part for part in graph.split(";") if part.startswith("[2:a]")][0]
        self.assertNotIn("volume=", narration)
        self.assertIn("adelay=2500|2500", narration)
        self.assertIn("atrim=0:3.500", narration)
        self.assertIn("volume=%s" % AMBIENT_GAIN, graph)
        self.assertIn("volume=%s" % MUSIC_GAIN, graph)
        self.assertLess(float(MUSIC_GAIN), float(AMBIENT_GAIN))
        self.assertIn("normalize=0", graph)
        self.assertNotIn("-shortest", command)
        self.assertEqual(command[command.index("-t") + 1], "9.000")
        music_at = command.index("music.mp3")
        self.assertEqual(command[music_at - 3:music_at - 1], ["-stream_loop", "-1"])

    def test_prompt_drops_offscreen_narration_when_the_soundtrack_is_on(self):
        shot = {"prompt": "她抬头", "narration": "夜色已经很深了"}
        spoken = [{"speaker": "她", "line": "走吧"}]
        kept = delivery_prompt(shot, spoken, 0, False)
        self.assertIn("画外旁白", kept)
        for mode in ("narration", "music_narration"):
            prompt = delivery_prompt(shot, spoken, 0, False, soundtrack=mode)
            self.assertNotIn("画外旁白", prompt)
            self.assertNotIn("在画内开口", prompt)
            self.assertIn("保留环境声", prompt)
        sentence = instrumental_prompt("雨夜的车要开过这座桥")
        self.assertTrue(sentence.startswith("纯器乐汽车配乐"))
        self.assertNotIn("\n", sentence)

    def test_task_and_canvas_gain_an_audio_step_only_when_enabled(self):
        spec = {
            "title": "雨夜",
            "assets": [{"id": "A1", "title": "车", "prompt": "一辆车"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "开走", "images": ["A1"]}],
        }
        self.assertEqual([task["id"] for task in task_plan(spec)[-2:]], ["join", "subtitles"])
        voiced = task_plan(spec, soundtrack="narration")
        self.assertEqual([task["id"] for task in voiced[-3:]], ["join", "audio", "subtitles"])
        self.assertEqual(voiced[-2]["title"], "声轨")
        self.assertEqual(voiced[-2]["detail"], "MiniMax 旁白")
        legacy = task_plan(spec, soundtrack="music_narration")
        self.assertEqual(legacy[-2]["detail"], "MiniMax 旁白")
        plain = [node["id"] for node in video_nodes(spec) if node["role"] == "function"]
        self.assertEqual(plain, ["script", "storyboard", "image", "join", "subtitles", "film"])
        with_audio = [node["id"] for node in video_nodes(spec, soundtrack="narration") if node["role"] == "function"]
        self.assertEqual(with_audio, ["script", "storyboard", "image", "join", "audio", "subtitles", "film"])
        self.assertEqual(next(node["title"] for node in video_nodes(spec, soundtrack="music_narration") if node["id"] == "audio"), "声轨")


if __name__ == "__main__":
    unittest.main()
