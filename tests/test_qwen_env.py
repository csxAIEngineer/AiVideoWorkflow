"""The page can switch the Qwen host between test and production."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
