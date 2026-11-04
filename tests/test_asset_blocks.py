"""Asset-block machine check keeps an inner ## section with its 图N heading."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "AI" / "skills" / "manju-laoli-skill" / "short-drama-director" / "scripts" / "check_asset_blocks.py"


def _checker():
    spec = importlib.util.spec_from_file_location("check_asset_blocks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHEET = """#### 图6 · 毓婷药盒

## 毓婷药盒道具设定图
整板固定 16:9 横版。
道具设定图。禁止手持。
正面视图。45° 侧视图。顶部俯视图。关键细节特写。

## 分镜
第 1 格：人在门口。
"""


class AssetBlockSplitTest(unittest.TestCase):
    def test_inner_prop_heading_stays_in_the_figure_and_storyboard_does_not(self):
        checker = _checker()
        blocks = checker.split_blocks(SHEET)
        self.assertEqual(len(blocks), 1)
        number, title, body = blocks[0]
        self.assertEqual(number, "6")
        self.assertIn("毓婷药盒", title)
        self.assertIn("16:9", body)
        self.assertIn("道具设定图", body)
        self.assertNotIn("第 1 格", body)
        self.assertEqual(checker.classify(title, body), "道具")
        errors, _warns = checker.check_block(number, title, body, "道具")
        self.assertFalse(any(line.startswith("A2 ") for line in errors), errors)

    def test_a_level_one_storyboard_heading_still_ends_the_block(self):
        checker = _checker()
        text = SHEET.replace("## 分镜", "# 分镜")
        _number, _title, body = checker.split_blocks(text)[0]
        self.assertIn("16:9", body)
        self.assertNotIn("第 1 格", body)

    def test_the_next_figure_still_ends_the_previous_block(self):
        checker = _checker()
        text = """#### 图1 · 胶囊

## 胶囊道具设定图
16:9
道具设定图

#### 图2 · 客厅（场景母版）
16:9 无人空镜
"""
        blocks = checker.split_blocks(text)
        self.assertEqual([item[0] for item in blocks], ["1", "2"])
        self.assertIn("道具设定图", blocks[0][2])
        self.assertNotIn("客厅", blocks[0][2])
        self.assertNotIn("胶囊", blocks[1][2])


if __name__ == "__main__":
    unittest.main()
