"""赛道分流只挂精简路线 skill，不再整包塞 P0 原文。"""

import unittest

from AI.skills.registry import SUITE, _slice_module, cohort_prompt, suite_files


ROUTE_SKILL = "sd-route-engine/SKILL.md"

# Distinctive paths from the old P0 originals. None of these should be mounted
# or inlined when writing the route conclusion.
OLD_P0_ORIGINALS = (
    "short-drama-director/references/track-routing.md",
    "short-drama-director/references/asset-first-pipeline.md",
    "short-drama-director/references/asset-delivery-modes.md",
    "short-drama-director/references/aspect-ratio-adaptation.md",
    "sd-decision-engine/SKILL.md",
    "short-drama-director/references/context-mapping.md",
    "short-drama-director/references/emotion-anchor-table.md",
    "short-drama-director/references/forbidden-zone-whitelist.md",
    "sd-overseas-engine/SKILL.md",
    "sd-jieshuo-engine/SKILL.md",
    "sd-recon-engine/SKILL.md",
)


def _brief(**fields):
    values = {
        "阶段": "P0",
        "赛道": "1",
        "来源": "1",
        "动作档": "none",
        "模型": "h3",
        "素材形态": "M1",
    }
    values.update(fields)
    header = "\n".join("%s：%s" % (key, value) for key, value in values.items())
    return header + "\n\n任务：赛道分流"


class RouteSkillTests(unittest.TestCase):
    def test_the_route_skill_file_exists_and_stays_compact(self):
        path = SUITE / ROUTE_SKILL
        self.assertTrue(path.is_file())
        self.assertLess(len(path.read_text(encoding="utf-8")), 4000)

    def test_route_mounts_only_the_slim_skill_for_every_track_and_source(self):
        briefs = (
            _brief(赛道="1", 来源="1"),
            _brief(赛道="2", 来源="1"),
            _brief(赛道="3", 来源="1"),
            _brief(赛道="4", 来源="1"),
            _brief(赛道="1", 来源="3"),
            _brief(赛道="1", 来源="4"),
        )
        for brief in briefs:
            self.assertEqual(suite_files("route", brief), [ROUTE_SKILL])

    def test_the_route_prompt_does_not_inline_the_old_p0_originals(self):
        prompt = cohort_prompt(suite_files("route", _brief(赛道="2", 来源="3")))
        self.assertIn(ROUTE_SKILL, prompt)
        for relative in OLD_P0_ORIGINALS:
            self.assertNotIn("# 本节点原文 %s" % relative, prompt)
        self.assertLess(len(prompt), 8000)

    def test_the_slice_tool_still_lists_the_original_p0_files(self):
        files = _slice_module().resolve_files("P0", track="1", combat="none", model="h3", source="1")
        self.assertIn("short-drama-director/references/track-routing.md", files)
        self.assertNotIn(ROUTE_SKILL, files)


if __name__ == "__main__":
    unittest.main()
