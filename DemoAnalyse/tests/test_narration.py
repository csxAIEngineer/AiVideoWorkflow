import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyse import envelope
from narration import clean_transcript, public_error, text_from_completion
from narration import _read_sse
from server import _safe_run_file


class NarrationParseTest(unittest.TestCase):
    def test_completion_text(self):
        payload = {"choices": [{"message": {"content": "神行者8即将预售。"}}]}
        self.assertEqual(text_from_completion(payload), "神行者8即将预售。")

    def test_empty_transcript_is_blank(self):
        self.assertEqual(clean_transcript("   "), "")
        self.assertEqual(clean_transcript("……"), "")
        self.assertEqual(clean_transcript("豪华藏进细节里"), "豪华藏进细节里")

    def test_sse_ignores_reasoning(self):
        raw = "\n".join(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"先听一下"}}]}',
                'data: {"choices":[{"delta":{"content":"旁白原文"}}]}',
                "data: [DONE]",
            ]
        )
        self.assertEqual(_read_sse(raw), "旁白原文")

    def test_secrets_are_redacted(self):
        text = public_error("失败 Bearer sk-abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("sk-abc", text)
        self.assertIn("***", text)

    def test_envelope(self):
        self.assertEqual(envelope({"shot_count": 1}), {"success": True, "data": {"shot_count": 1}, "error": None})
        self.assertFalse(envelope(error="失败")["success"])

    def test_poster_path_stays_inside_runs(self):
        self.assertIsNone(_safe_run_file("/runs/../test.mp4"))
        self.assertIsNone(_safe_run_file("/runs/abc/source.mp4"))
        self.assertIsNone(_safe_run_file("/runs/abc/shot-01.jpg/../../.env"))


if __name__ == "__main__":
    unittest.main()
