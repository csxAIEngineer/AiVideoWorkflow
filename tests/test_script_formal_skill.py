"""剧本专员只挂精简正式剧本 skill，不再整份塞入六阶段总流程。"""

import unittest

from AI.skills.registry import SUITE, _slice_module, cohort_prompt, suite_files


SLIM_SKILL = "sd-script-engine/references/formal-script.md"
ORIGINAL = "sd-script-engine/references/six-stage-pipeline.md"

# Distinctive machinery from the full six-stage host. The write-script
# specialist should not be handed these sections.
UNUSED_HOST_MARKERS = (
    "STEPWISE",
    "FULL_SCRIPT_PIPELINE",
    "SOURCE_MANIFEST.json",
    "SERIES_SCRIPT_STRATEGY",
    "授权后精修或重写",
    "五视角审稿",
    "每次输出必须附【创作笔记】",
    "长剧分批生产合同",
    "READY_FOR_04R",
)


class FormalScriptSkillTests(unittest.TestCase):
    def test_the_formal_script_skill_exists_and_stays_compact(self):
        path = SUITE / SLIM_SKILL
        self.assertTrue(path.is_file())
        self.assertLess(len(path.read_text(encoding="utf-8")), 6000)

    def test_script_mounts_the_slim_skill_instead_of_the_six_stage_host(self):
        files = suite_files("script", "任务：剧本")
        self.assertIn(SLIM_SKILL, files)
        self.assertNotIn(ORIGINAL, files)

    def test_the_script_prompt_does_not_inline_the_six_stage_host(self):
        files = suite_files("script", "任务：剧本")
        prompt = cohort_prompt([SLIM_SKILL])
        self.assertIn(SLIM_SKILL, prompt)
        self.assertNotIn("# 本节点原文 %s" % ORIGINAL, prompt)
        self.assertNotIn("# 本节点原文 %s" % ORIGINAL, cohort_prompt(files))
        for marker in UNUSED_HOST_MARKERS:
            self.assertNotIn(marker, prompt)

    def test_the_original_six_stage_file_stays_in_the_pack(self):
        path = SUITE / ORIGINAL
        self.assertTrue(path.is_file())
        body = path.read_text(encoding="utf-8")
        self.assertIn("## 六、阶段⑤正式剧本：原作忠实剧本主编", body)
        routed = _slice_module().resolve_files("P1")
        self.assertIn(ORIGINAL, routed)
        self.assertNotIn(SLIM_SKILL, routed)


if __name__ == "__main__":
    unittest.main()
