"""H6 reads a separated retention mark. It does not split top-down."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from end.gates import check_feed

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "AI" / "skills" / "manju-laoli-skill" / "short-drama-director" / "scripts" / "validate_prompt.py"
FEED = Path(__file__).resolve().parent / "fixtures" / "h6-feed-2045bf16e56b.md"
_SPEC = importlib.util.spec_from_file_location("h6_validate_prompt", VALIDATOR)
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)
validate_h3 = _VALIDATOR.validate_h3

_REAL_RETENTION = (
    "Character consistency maintained via Picture 1 (A_Qingsi) and Picture 2 (Liu_Gongzi). "
    "Spatial layout locked by Picture 3 (Ancient_Tower_Interior). "
    "Lighting direction consistent: top-down natural light creating strong contrast. "
    "Hair texture state: initial greasy/clumped condition preserved throughout this unit. fully_preserved\n"
    "[Audio Retention]: fully_copy"
)


def _h3(retention):
    return "\n".join((
        "subject_definitions:",
        "<Subject 1> 为 <Picture 1> 中的人。",
        "<Audio 1> 为 <Subject 1> 的音色。",
        "",
        "summary:",
        "[reference generation] <Subject 1> stands.",
        "",
        "retention_analysis:",
        retention,
        "",
        "detailed_description:",
        "[Shot 1] <Subject 1> stands.",
        "",
        "overall_soundscape:",
        "room tone.",
        "",
        "non_diegetic_music:",
        "none.",
        "",
    ))


def _h6(retention):
    issues, _warns, _stats = validate_h3(_h3(retention))
    return [item for item in issues if item.startswith("H6")]


class H6RetentionTest(unittest.TestCase):
    def test_legal_marks_and_prose_compounds_are_not_illegal(self):
        samples = (
            "<Subject 1>: fully_preserved - coat.",
            "<Subject 1>：partially_preserved — coat.",
            "<Picture 1>: attribute_transfer – color.",
            "<Video 1>: weak_reference - motion.",
            "<Audio 1>: fully_copy - voice.",
            "<Audio 1>：partially_copy — voice.",
            "<Audio 1>: reference - voice.",
            "<Audio 1>：weak_reference – voice.",
            "Lighting direction consistent: top-down natural light creating strong contrast.",
            "Framing: top-left rim stays.",
            _REAL_RETENTION,
        )
        for sample in samples:
            self.assertEqual(_h6(sample), [], sample)

    def test_explicit_wrong_mark_still_fails(self):
        for sample in (
            "<Subject 1>: top - kept.",
            "<Subject 1>：top — kept.",
            "<Audio 1>: top – kept.",
            "<Subject 1>: preserved - kept.",
        ):
            found = _h6(sample)
            self.assertTrue(found, sample)
            self.assertTrue(all(item.startswith("H6 retention_analysis 非法保留度标记") for item in found))
            self.assertNotIn("top-down", " ".join(found))

    def test_local_feed_segment_path_drops_only_the_top_false_positive(self):
        self.assertIn("top-down natural light", FEED.read_text(encoding="utf-8"))
        folder = Path(tempfile.mkdtemp())
        dest = folder / "feed.md"
        shutil.copy(FEED, dest)
        code, report = check_feed(dest, "h3", "M1", "1")
        self.assertEqual(code, 0, report)
        self.assertNotIn("非法保留度标记「top」", report)
        self.assertIn("[段2] H4 正文出现中文", report)
        self.assertIn("[段2] H12a 首镜无具名主体锚", report)
        self.assertIn("[段3] H12a 首镜无具名主体锚", report)
        self.assertIn("[段4] H9 画外声未用官方固定短语", report)
        self.assertIn("[段4] H12a 首镜无具名主体锚", report)
        stored = dest.read_text(encoding="utf-8")
        self.assertNotIn("\nretention_analysis:", stored)
        self.assertNotIn("\nsummary:", stored)
        self.assertIn("\ndetailed_description:", stored)


if __name__ == "__main__":
    unittest.main()
