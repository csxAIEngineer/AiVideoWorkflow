"""The critic's skill is the stage machine check, not the writing pack."""

import unittest

from Agents.workers.asset import critic_messages as asset_messages
from Agents.workers.critic import critic_user, machine_check_skill
from Agents.workers.feed import critic_messages as feed_messages
from Agents.workers.feed import run_feed_loop
from Agents.workers.script import critic_messages as script_messages
from Agents.workers.script import run_script_loop
from Agents.workers.storyboard import critic_messages as storyboard_messages

WRITING = "WRITING-PACK-SENTINEL-写作原文"
REWRITE = "CRITIC-REWRITE-SENTINEL-不要采用"


def _user(messages):
    return messages[-1]["content"]


class CriticSkillTest(unittest.TestCase):
    def test_four_critics_share_one_machine_check_skill(self):
        from Agents.workers import asset, feed, script, storyboard

        self.assertIs(asset.critic_user, critic_user)
        self.assertIs(script.critic_user, critic_user)
        self.assertIs(storyboard.critic_user, critic_user)
        self.assertIs(feed.critic_user, critic_user)
        for module in (asset, script, storyboard, feed):
            source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn("critic_user(", source)
            self.assertNotIn("skill_reference(", source)
            self.assertNotIn("def machine_check_skill", source)

    def test_each_stage_skill_is_its_machine_check(self):
        script = machine_check_skill("script")
        self.assertIn("check_script_draft", script)
        self.assertIn("已核验", script)
        self.assertIn("invented_upload", script)
        self.assertNotIn("short-drama-director/SKILL.md", script)

        asset = machine_check_skill("asset")
        self.assertIn("check_asset_blocks.py", asset)
        self.assertIn("A1 ", asset)
        self.assertIn("A8 ", asset)
        self.assertNotIn("validate_prompt.py", asset)

        board = machine_check_skill("storyboard")
        self.assertIn("分镜是空的", board)
        self.assertIn("镜头不一致", board)
        self.assertIn("S01", board)
        self.assertIn("hold", board)

        feed = machine_check_skill("feed")
        self.assertIn("validate_prompt.py", feed)
        self.assertIn("| H1 |", feed)
        self.assertIn("| H9 |", feed)
        self.assertIn("不得短于分镜", feed)
        self.assertIn("lips", feed.lower())
        self.assertLess(len(feed), 80000)

    def test_critic_prompts_use_the_machine_check_and_not_the_writing_pack(self):
        state = {
            "skill_pack": WRITING,
            "draft": "待审正文",
            "script": "剧本",
            "source": "原文",
        }
        samples = {
            "script": _user(script_messages(state)),
            "asset": _user(asset_messages(state)),
            "storyboard": _user(storyboard_messages(state)),
            "feed": _user(feed_messages(state)),
        }
        for task, text in samples.items():
            self.assertNotIn(WRITING, text, task)
            self.assertIn("机检技能", text, task)
            self.assertIn("不要改稿", machine_check_skill(task))
        self.assertIn("A1 ", samples["asset"])
        self.assertIn("已核验", samples["script"])
        self.assertIn("镜头不一致", samples["storyboard"])
        self.assertIn("validate_prompt.py", samples["feed"])

    def test_a_failing_draft_does_not_pass_when_the_critic_accepts(self):
        def complete(role, messages):
            if role == "specialist":
                return "节点"
            if role == "director":
                return '{"script":"画面已核验。"}'
            self.assertNotIn(WRITING, messages[-1]["content"])
            self.assertIn("已核验", messages[-1]["content"])
            return '{"verdict":"accept","issues":[],"script":"%s"}' % REWRITE

        result = run_script_loop(
            "她把药盒放在桌上。",
            skill_pack=WRITING,
            max_attempts=1,
            complete=complete,
        )
        self.assertEqual(result["status"], "fail")
        self.assertNotEqual(result["stop_reason"], "machine_check_pass")
        self.assertNotIn(REWRITE, result["draft"])
        self.assertTrue(any("已核验" in item for item in result["machine_report"]["errors"]))

    def test_feed_warnings_do_not_replace_the_duration_gate(self):
        skill = machine_check_skill("feed")
        self.assertIn("WARN", skill)
        self.assertIn("feed_duration_failures", skill)

        def complete(role, messages):
            if role == "critic":
                self.assertIn("feed_duration_failures", messages[-1]["content"])
                self.assertNotIn(WRITING, messages[-1]["content"])
                return '{"verdict":"accept","issues":[],"feed":"%s"}' % REWRITE
            return '{"feed":"只有一段文字"}'

        result = run_feed_loop("分镜正文", skill_pack=WRITING, max_attempts=1, complete=complete)
        self.assertEqual(result["status"], "fail")
        self.assertNotIn(REWRITE, result["draft"])


if __name__ == "__main__":
    unittest.main()
