"""Committed config names the Qwen host and the video cloud/local fallback."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end.minimax_video import local_base_url, read_minimax_video
from end.qwen_env import read_qwen_env


class QwenEnvFileTest(unittest.TestCase):
    def test_config_file_is_prod_or_test(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(read_qwen_env(root), "test")
        config = root / "config"
        config.mkdir()
        qwen = config / "qwen.json"
        qwen.write_text(json.dumps({"env": "生产"}), encoding="utf-8")
        self.assertEqual(read_qwen_env(root), "prod")
        qwen.write_text(json.dumps({"env": "test"}), encoding="utf-8")
        self.assertEqual(read_qwen_env(root), "test")
        qwen.write_text(json.dumps({"env": "其他"}), encoding="utf-8")
        self.assertEqual(read_qwen_env(root), "test")
        shipped = Path(__file__).resolve().parents[1] / "config" / "qwen.json"
        self.assertTrue(shipped.is_file())
        self.assertIn(read_qwen_env(), ("test", "prod"))


class MinimaxVideoChoiceTest(unittest.TestCase):
    def test_default_video_comes_from_the_catalog(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(read_minimax_video(root), "cloud")
        with patch.dict(os.environ, {"MINIMAX_H3_BASE_URL": ""}):
            self.assertEqual(local_base_url(), "http://127.0.0.1:8765")
        with patch.dict(os.environ, {"MINIMAX_H3_BASE_URL": "http://127.0.0.1:9000/api/v1"}):
            self.assertEqual(local_base_url(), "http://127.0.0.1:9000")
        config = root / "config"
        config.mkdir()
        catalog = config / "video_catalog.json"
        catalog.write_text(json.dumps({"default_video": "本地"}), encoding="utf-8")
        self.assertEqual(read_minimax_video(root), "local")
        catalog.write_text(json.dumps({"default_video": "云端"}), encoding="utf-8")
        self.assertEqual(read_minimax_video(root), "cloud")
        catalog.write_text(json.dumps({"default_video": "其他"}), encoding="utf-8")
        self.assertEqual(read_minimax_video(root), "cloud")
        shipped = Path(__file__).resolve().parents[1] / "config" / "video_catalog.json"
        self.assertEqual(read_minimax_video(), "cloud")
        self.assertTrue(shipped.is_file())


if __name__ == "__main__":
    unittest.main()
