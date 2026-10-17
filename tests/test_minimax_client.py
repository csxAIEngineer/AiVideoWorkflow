"""MiniMax speech and music requests. No network."""

import os
import tempfile
import unittest
from pathlib import Path

from AI.minimax_api.client import (
    MUSIC_MODEL,
    MUSIC_PATH,
    SPEECH_MODEL,
    SPEECH_PATH,
    VOICE_PATH,
    MiniMaxClient,
    MiniMaxError,
    choose_chinese_voice,
    remember_voice_example,
)


class _Client(MiniMaxClient):
    def __init__(self, example_path):
        self.api_key = "test"
        self.base_url = "https://api.minimax.io/v1"
        self.example_path = Path(example_path)
        self._voice_id = ""
        self.calls = []

    def _request(self, method, path, body, timeout):
        self.calls.append((method, path, body, timeout))
        if path == VOICE_PATH:
            return {
                "system_voice": [
                    {"voice_id": "English_Graceful_Lady", "voice_name": "Grace"},
                    {
                        "voice_id": "Chinese (Mandarin)_Lyrical_Voice",
                        "voice_name": "抒情女声",
                        "description": ["标准普通话"],
                    },
                ],
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        return {
            "data": {"audio": "ff00", "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }


class MiniMaxClientTest(unittest.TestCase):
    def test_chinese_voice_skips_english(self):
        chosen = choose_chinese_voice(
            [
                {"voice_id": "English_radiant_girl", "voice_name": "Radiant"},
                {"voice_id": "Chinese (Mandarin)_News_Anchor", "voice_name": "新闻女声"},
            ]
        )
        self.assertEqual(chosen, "Chinese (Mandarin)_News_Anchor")
        self.assertEqual(choose_chinese_voice([{"voice_id": "English_Graceful_Lady"}]), "")

    def test_speech_and_music_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _Client(root / ".env.example")
            voice = root / "S01.mp3"
            client.synthesize("夜色已经很深了", voice, voice_id="Chinese (Mandarin)_Lyrical_Voice")
            self.assertEqual(voice.read_bytes(), bytes.fromhex("ff00"))
            method, path, body, _timeout = client.calls[-1]
            self.assertEqual((method, path), ("POST", SPEECH_PATH))
            self.assertEqual(body["model"], SPEECH_MODEL)
            self.assertEqual(body["voice_setting"]["voice_id"], "Chinese (Mandarin)_Lyrical_Voice")
            self.assertEqual(body["audio_setting"]["format"], "mp3")
            self.assertFalse(body["stream"])

            music = root / "music.mp3"
            client.generate_music("纯器乐汽车配乐，没有人声，没有歌词，雨夜。", music)
            method, path, body, _timeout = client.calls[-1]
            self.assertEqual((method, path), ("POST", MUSIC_PATH))
            self.assertEqual(body["model"], MUSIC_MODEL)
            self.assertTrue(body["is_instrumental"])
            self.assertNotIn("lyrics", body)
            self.assertNotIn("lyrics_optimizer", body)

    def test_missing_voice_picks_chinese_and_writes_the_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            example = Path(tmp) / ".env.example"
            example.write_text("MINIMAX_API_KEY=\nMINIMAX_VOICE_ID=\n", encoding="utf-8")
            client = _Client(example)
            previous = os.environ.pop("MINIMAX_VOICE_ID", None)
            try:
                self.assertEqual(client.voice_id(), "Chinese (Mandarin)_Lyrical_Voice")
            finally:
                if previous is None:
                    os.environ.pop("MINIMAX_VOICE_ID", None)
                else:
                    os.environ["MINIMAX_VOICE_ID"] = previous
            self.assertIn("MINIMAX_VOICE_ID=Chinese (Mandarin)_Lyrical_Voice", example.read_text(encoding="utf-8"))
            self.assertEqual(client.calls[0][1], VOICE_PATH)
            self.assertEqual(client.calls[0][2], {"voice_type": "system"})
            self.assertFalse(remember_voice_example("other-voice", example))
            self.assertNotIn("other-voice", example.read_text(encoding="utf-8"))

    def test_api_error_is_raised(self):
        client = _Client(Path("/nonexistent/.env.example"))

        def fail(method, path, body, timeout):
            return {"base_resp": {"status_code": 2013, "status_msg": "invalid params"}}

        client._request = fail
        with self.assertRaises(MiniMaxError):
            client._check(client._request("POST", SPEECH_PATH, {}, 1))


if __name__ == "__main__":
    unittest.main()
