"""Chat hides the generated-image gallery and the uploaded material strip."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
INDEX = ROOT / "front" / "index.html"


def extract_function(source, name):
    marker = "function " + name + "("
    start = source.index(marker)
    candidates = [
        item for item in (
            source.find("\nfunction ", start + 1),
            source.find("\nasync function ", start + 1),
        )
        if item >= 0
    ]
    if not candidates:
        raise AssertionError("missing function after %s" % name)
    return source[start:min(candidates)].strip() + "\n"


def run_js(body):
    source = APP.read_text(encoding="utf-8")
    helpers = "\n".join(
        extract_function(source, name)
        for name in (
            "showImageGallery",
            "imageGallery",
            "showMaterialStrip",
            "materialStrip",
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


class ChatMediaDisplayTest(unittest.TestCase):
    def test_source_keeps_the_helpers_but_does_not_mount_them_blindly(self):
        app = APP.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/app.js?v=94", index)
        self.assertIn("function showImageGallery(", app)
        self.assertIn("function imageGallery(", app)
        self.assertIn("function showMaterialStrip(", app)
        self.assertIn("function materialStrip(", app)
        self.assertIn("生成的图片", app)
        gallery = extract_function(app, "imageGallery")
        strip = extract_function(app, "materialStrip")
        self.assertIn("showImageGallery(project)", gallery)
        self.assertIn("showMaterialStrip(project)", strip)
        paint = app.split("function paintTranscript", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("materialStrip(current)", paint)
        self.assertIn("imageGallery(current)", app)

    def test_gallery_and_strip_are_hidden_when_assets_exist(self):
        report = run_js(
            r"""
            const project = {
              images: [
                { url: "/media/u1.png", name: "U1.png" },
                { url: "/media/u2.png", name: "U2.png" },
              ],
              materials: [
                { id: "U1", title: "菜花", url: "/media/u1.png" },
                { id: "U2", title: "新海诚-男主", url: "/media/u2.png" },
              ],
            };
            console.log(JSON.stringify({
              showGallery: showImageGallery(project),
              showStrip: showMaterialStrip(project),
              gallery: imageGallery(project),
              strip: materialStrip(project),
            }));
            """
        )
        self.assertFalse(report["showGallery"])
        self.assertFalse(report["showStrip"])
        self.assertIsNone(report["gallery"])
        self.assertIsNone(report["strip"])

    def test_helpers_are_named_functions(self):
        source = APP.read_text(encoding="utf-8")
        for name in ("showImageGallery", "imageGallery", "showMaterialStrip", "materialStrip"):
            self.assertIsNotNone(re.search(r"^function %s\(" % name, source, re.M), name)


if __name__ == "__main__":
    unittest.main()
