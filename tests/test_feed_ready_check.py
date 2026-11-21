"""Finish and rule recheck must use the same Wan feed check as packing."""

import tempfile
import unittest
from pathlib import Path

from tests.offline_guard import install, uninstall


def setUpModule():
    install()


def tearDownModule():
    uninstall()


from end.gates import check_prompt
from end.server import _recheck_after_rules
from end.stage_runner import _finish


WAN_FEED = "\n".join((
    "## 【封装单元 1/1】",
    "duration: 8",
    "总体描述:",
    "雨夜客厅，女主独自等待。",
    "参考素材引用:",
    "图1（角色-女主外貌服装）",
    "分镜:",
    "分镜1（00:00.000-00:08.000）：女主坐在客厅里。",
    "台词:",
    "画外音说：\"妈妈？\"",
    "音效 / BGM:",
    "雨声。",
    "风格 / 情绪:",
    "冷色写实",
    "负向清单:",
    "不要字幕、不要水印、不要复杂文字",
    "",
))


H3_INCOMPLETE = "\n".join((
    "## 【封装单元 1/1】",
    "duration: 8",
    "subject_definitions:",
    "<Subject 1> is a woman in a black coat.",
    "",
))


def _folder(text):
    folder = Path(tempfile.mkdtemp())
    internal = folder / "_内部_"
    internal.mkdir()
    (internal / "feed.md").write_text(text, encoding="utf-8")
    return folder


def _locks():
    return {
        "model": "h3",
        "track": "1",
        "material_mode": "M1",
        "delivery": "2",
    }


class WanReadyCheckTest(unittest.TestCase):
    def test_a_wan_feed_can_finish_without_h3_english_fields(self):
        folder = _folder(WAN_FEED)
        path = folder / "_内部_" / "feed.md"
        leftover_code, leftover_report = check_prompt(str(path), "h3", "1")
        self.assertEqual(leftover_code, 1)
        self.assertIn("integrated_multimodal_description", leftover_report)
        self.assertIn("H9", leftover_report)

        result = _finish(folder, _locks(), {"current": "feed", "accepted": ["feed"]})
        self.assertEqual(result.get("status"), "done")
        self.assertNotIn("integrated_multimodal_description", str(result))

    def test_a_wan_feed_passes_rule_recheck_without_h3_english_fields(self):
        folder = _folder(WAN_FEED)
        path = folder / "_内部_" / "feed.md"
        leftover_code, leftover_report = check_prompt(str(path), "h3", "1")
        self.assertEqual(leftover_code, 1)
        self.assertIn("integrated_multimodal_description", leftover_report)
        self.assertIn("H9", leftover_report)

        passed, report = _recheck_after_rules(
            folder,
            _locks(),
            {"check": "machine", "current": "feed"},
            path,
            "雨夜",
        )
        self.assertTrue(passed)
        self.assertNotIn("integrated_multimodal_description", report or "")
        self.assertNotIn("H1 基础模式", report or "")

    def test_an_incomplete_h3_feed_still_fails_finish_and_recheck(self):
        folder = _folder(H3_INCOMPLETE)
        result = _finish(folder, _locks(), {"current": "feed", "accepted": ["feed"]})
        self.assertEqual(result.get("status"), "gate")
        self.assertIn("H1", str(result))
        self.assertIn("detailed_description", str(result))

        passed, report = _recheck_after_rules(
            folder,
            _locks(),
            {"check": "machine", "current": "feed"},
            folder / "_内部_" / "feed.md",
            "雨夜",
        )
        self.assertFalse(passed)
        self.assertIn("H1", report or "")
        self.assertIn("detailed_description", report or "")
