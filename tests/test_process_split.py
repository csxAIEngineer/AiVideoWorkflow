"""The three code trees do not import each other. Model work crosses JSON-RPC."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__"}


def _modules(path):
    for file_path in path.rglob("*.py"):
        if SKIP_PARTS.intersection(file_path.parts):
            continue
        yield file_path


def _imported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    return found


class ProcessSplitTest(unittest.TestCase):
    def test_root_code_dirs(self):
        names = {path.name for path in ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")}
        self.assertIn("front", names)
        self.assertIn("end", names)
        self.assertIn("AI", names)
        for gone in ("agent", "cr_api", "icreat_api", "deepseek_api", "minimax_api", "skills", "studio", "task_queue", "image_store"):
            self.assertNotIn(gone, names)

    def test_end_does_not_import_ai_packages(self):
        banned = ("AI", "agent", "cr_api", "icreat_api", "deepseek_api", "minimax_api", "skills")
        for path in _modules(ROOT / "end"):
            for name in _imported(path):
                head = name.split(".", 1)[0]
                self.assertNotIn(head, banned, "%s imports %s" % (path, name))

    def test_ai_does_not_import_end(self):
        for path in _modules(ROOT / "AI"):
            for name in _imported(path):
                self.assertNotEqual(name.split(".", 1)[0], "end", "%s imports %s" % (path, name))

    def test_front_python_does_not_import_end_or_ai(self):
        for path in _modules(ROOT / "front"):
            for name in _imported(path):
                head = name.split(".", 1)[0]
                self.assertNotIn(head, ("end", "AI", "agent", "skills"), "%s imports %s" % (path, name))

    def test_catalog_round_trip(self):
        from end.ai_client import catalog, normalize_model, normalize_template

        info = catalog()
        self.assertIn("deepseek-v4-pro", {item["id"] for item in info["models"]})
        self.assertEqual(normalize_model("nope"), info["default_model"])
        self.assertEqual(normalize_template("apple-product"), "short-drama-director")
        self.assertEqual({item["id"] for item in info["templates"]}, {"short-drama-director"})
