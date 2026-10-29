"""The page can switch the Qwen host between test and production."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end.minimax_video import local_base_url, read_minimax_video, write_minimax_video
from end.qwen_env import read_qwen_env, write_qwen_env


class QwenEnvFileTest(unittest.TestCase):
    def test_page_choice_is_prod_or_test(self):
        root = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"QWEN_ENV": "prod"}):
            self.assertEqual(read_qwen_env(root), "prod")
        self.assertEqual(write_qwen_env("生产", root), "prod")
        self.assertEqual((root / "data" / "qwen_env").read_text(encoding="utf-8").strip(), "prod")
        with patch.dict(os.environ, {"QWEN_ENV": "prod"}):
            self.assertEqual(read_qwen_env(root), "prod")
        self.assertEqual(write_qwen_env("test", root), "test")
        self.assertEqual(read_qwen_env(root), "test")
        with self.assertRaises(ValueError):
            write_qwen_env("其他", root)


class MinimaxVideoChoiceTest(unittest.TestCase):
    def test_page_choice_is_cloud_or_local_and_wins_over_env(self):
        root = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"MINIMAX_VIDEO": "local", "MINIMAX_H3_BASE_URL": ""}):
            self.assertEqual(read_minimax_video(root), "local")
            self.assertEqual(local_base_url(), "http://127.0.0.1:8765")
        with patch.dict(os.environ, {"MINIMAX_H3_BASE_URL": "http://127.0.0.1:9000/api/v1"}):
            self.assertEqual(local_base_url(), "http://127.0.0.1:9000")
        self.assertEqual(write_minimax_video("本地", root), "local")
        self.assertEqual((root / "data" / "minimax_video").read_text(encoding="utf-8").strip(), "local")
        with patch.dict(os.environ, {"MINIMAX_VIDEO": "cloud"}):
            self.assertEqual(read_minimax_video(root), "local")
        self.assertEqual(write_minimax_video("云端", root), "cloud")
        with patch.dict(os.environ, {"MINIMAX_VIDEO": "local"}):
            self.assertEqual(read_minimax_video(root), "cloud")
        with self.assertRaises(ValueError):
            write_minimax_video("其他", root)


if __name__ == "__main__":
    unittest.main()
