"""Asset grid prompts paste the A3 lock sentences and forbid paraphrase."""

import unittest
from pathlib import Path

from AI.skills.registry import cohort_prompt, merge_prompt, suite_files
from end.stage_runner import stage_instruction

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT
    / "AI/skills/manju-laoli-skill/sd-asset-engine/references/scene-grid-template.md"
)
SKILL = ROOT / "AI/skills/manju-laoli-skill/sd-asset-engine/SKILL.md"
CHECKER = (
    ROOT
    / "AI/skills/manju-laoli-skill/short-drama-director/scripts/check_asset_blocks.py"
)
GRID = "sd-asset-engine/references/scene-grid-template.md"
MODE_A = "仅允许摄影机移动"
MODE_B = "允许按子区域切换机位"
MODE_B_SENTENCE = "允许按子区域切换机位与视角"
BAN = "禁止意译、近义改写、拆字、加空格"
ASSET_BRIEF = "\n".join(("阶段：P2", "赛道：1", "来源：1", "模型：h3", "素材形态：M1", "任务：资产锁定"))


def _require(text, label):
    missing = [
        phrase
        for phrase in (MODE_A, MODE_B, MODE_B_SENTENCE, BAN)
        if phrase not in text
    ]
    if missing:
        raise AssertionError("%s 缺少：%s" % (label, "、".join(missing)))


class A3LockPromptTest(unittest.TestCase):
    def test_template_and_skill_paste_the_sentences(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        skill = SKILL.read_text(encoding="utf-8")
        _require(template, "scene-grid-template.md")
        _require(skill, "sd-asset-engine/SKILL.md")
        self.assertNotIn("【参考场景锁定模式】……", template)
        self.assertIn("【参考场景锁定模式】允许按子区域切换机位与视角。", template)

    def test_both_writer_prompts_paste_the_sentences(self):
        _require(stage_instruction("asset"), "stage_instruction(asset)")
        _require(merge_prompt("asset"), "merge_prompt(asset)")

    def test_asset_pack_shows_the_template_to_the_model(self):
        files = suite_files("asset", ASSET_BRIEF)
        self.assertIn(GRID, files)
        self.assertIn("sd-asset-engine/SKILL.md", files)
        _require(cohort_prompt([GRID]), "cohort_prompt(scene-grid-template)")

    def test_checker_still_requires_one_contiguous_sentence(self):
        source = CHECKER.read_text(encoding="utf-8")
        self.assertIn(
            'if "仅允许摄影机移动" not in body and "允许按子区域切换机位" not in body:',
            source,
        )
        self.assertIn(
            'errors.append(f"A3 {tag} 九宫格缺【参考场景锁定模式】正文（模式A档或模式B档原文）")',
            source,
        )


if __name__ == "__main__":
    unittest.main()
