"""Brief tokens match official upload marks; suggested-ad copy is stripped."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from end.intake import (  # noqa: E402
    enrich_brief,
    intake_note,
    official_material_mark,
    rewrite_brief_material_tokens,
    strip_suggested_ad,
)

APP = ROOT / "front" / "app.js"


def extract_function(source, name):
    marker = "function " + name + "("
    start = source.index(marker)
    nxt = source.find("\nfunction ", start + 1)
    if nxt < 0:
        raise AssertionError("missing function after %s" % name)
    return source[start:nxt].strip() + "\n"


def run_js(body):
    source = APP.read_text(encoding="utf-8")
    helpers = "\n".join(
        extract_function(source, name)
        for name in (
            "stripSuggestedAd",
            "officialMaterialMark",
            "rewriteBriefMaterialTokens",
            "displayUserMessage",
        )
    )
    result = subprocess.run(
        ["node", "-e", helpers + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class IntakeBriefTokenTests(unittest.TestCase):
    def test_rewrites_filename_token_to_official_mark(self):
        materials = [{"id": "U1", "title": "菜花"}]
        files = [{"filename": "菜花.jpg"}]
        self.assertEqual(official_material_mark(materials[0]), "U1 菜花")
        self.assertEqual(
            rewrite_brief_material_tokens("猫咪【菜花.jpg】测试，", materials, files),
            "猫咪【U1 菜花】测试，",
        )
        self.assertEqual(
            rewrite_brief_material_tokens("猫咪【U1 菜花】测试，", materials, files),
            "猫咪【U1 菜花】测试，",
        )
        self.assertEqual(
            rewrite_brief_material_tokens("猫咪【菜花】测试，", materials, files),
            "猫咪【U1 菜花】测试，",
        )
        self.assertEqual(
            rewrite_brief_material_tokens("猫咪【菜花.JPG】测试，", materials, files),
            "猫咪【U1 菜花】测试，",
        )
        two = [
            {"id": "U1", "title": "菜花"},
            {"id": "U2", "title": "菜花2"},
        ]
        two_files = [{"filename": "菜花.jpg"}, {"filename": "菜花2.jpg"}]
        self.assertEqual(
            rewrite_brief_material_tokens("这是【菜花.jpg】和【菜花2.jpg】", two, two_files),
            "这是【U1 菜花】和【U2 菜花2】",
        )

    def test_strip_suggested_ad_drops_the_labeled_field(self):
        reading = (
            "主体种类：猫咪。\n"
            "**建议的广告**: 根据画面中放大的瞳孔和直视镜头的特写，这张图在建议宠物类广告，例如猫粮、猫罐头或猫咪相关用品。"
        )
        self.assertEqual(strip_suggested_ad(reading), "主体种类：猫咪。")
        self.assertNotIn("建议的广告", strip_suggested_ad(reading))
        # inline sentence from older readings is left alone
        leftover = "主体是一辆银色SUV，停在海边。建议一条海边公路的汽车广告。"
        self.assertEqual(strip_suggested_ad(leftover), leftover)

    def test_intake_note_and_enrich_brief_hide_suggested_ad(self):
        item = {
            "id": "U1",
            "title": "菜花",
            "narration": "",
            "reading": (
                "主体种类：猫咪。\n"
                "**建议的广告**: 这张图在建议宠物类广告。"
            ),
        }
        note = intake_note([item])
        brief = enrich_brief("猫咪【U1 菜花】测试，", [item])
        self.assertIn("主体种类：猫咪。", note)
        self.assertIn("猫咪【U1 菜花】测试，", brief)
        self.assertNotIn("建议的广告", note)
        self.assertNotIn("建议的广告", brief)
        self.assertNotIn("宠物类广告", note)
        self.assertNotIn("宠物类广告", brief)


class UserMessageDisplayTests(unittest.TestCase):
    def test_display_helpers_match_the_server(self):
        report = run_js(
            r"""
            const materials = [{ id: "U1", title: "菜花" }];
            const reading = "主体种类：猫咪。\n**建议的广告**: 根据画面中放大的瞳孔和直视镜头的特写，这张图在建议宠物类广告。";
            const stored = "猫咪【菜花.jpg】测试，\n\n已上传图素材：\n- U1 菜花\n  素材读图：" + reading;
            console.log(JSON.stringify({
              token: rewriteBriefMaterialTokens("猫咪【菜花.jpg】测试，", materials),
              stripped: stripSuggestedAd(reading),
              display: displayUserMessage(stored, { materials: materials }),
            }));
            """
        )
        self.assertEqual(report["token"], "猫咪【U1 菜花】测试，")
        self.assertEqual(report["stripped"], "主体种类：猫咪。")
        self.assertIn("猫咪【U1 菜花】测试，", report["display"])
        self.assertIn("主体种类：猫咪。", report["display"])
        self.assertNotIn("【菜花.jpg】", report["display"])
        self.assertNotIn("建议的广告", report["display"])

    def test_source_uses_display_helpers_in_the_user_bubble(self):
        app = APP.read_text(encoding="utf-8")
        self.assertIn("function stripSuggestedAd(", app)
        self.assertIn("function rewriteBriefMaterialTokens(", app)
        self.assertIn("function displayUserMessage(", app)
        start = app.split("function messageBubble(role, text)", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("displayUserMessage(text, current)", start)


if __name__ == "__main__":
    unittest.main()
