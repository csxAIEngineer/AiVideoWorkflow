"""The four stage workers share one generator layout.

Specialists each see one original. The director merges. A repair reruns only
the node the critic or the machine check named.
"""

import json
import unittest

from Agents.workers import asset, feed, generator, script, storyboard
from Agents.workers.asset import run_p2_asset_loop
from Agents.workers.feed import run_feed_loop
from Agents.workers.generator import run_generator, select_nodes
from Agents.workers.script import run_script_loop
from Agents.workers.storyboard import run_storyboard_loop
from AI.skills.registry import suite_files

PROP = "sd-asset-engine/references/prop-template.md"
PORTRAIT = "sd-asset-engine/references/character-portrait-templates.md"
FILES = [PROP, PORTRAIT]
PROP_MARK = "工作流 Step 4 道具模板"
SENTINEL = "CRITIC-REWRITE-SENTINEL-不要采用"


def _routing(stage):
    return "阶段：%s\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n" % stage


class SharedLayoutTest(unittest.TestCase):
    def test_four_workers_call_the_same_generator(self):
        self.assertIs(asset.run_generator, generator.run_generator)
        self.assertIs(script.run_generator, generator.run_generator)
        self.assertIs(storyboard.run_generator, generator.run_generator)
        self.assertIs(feed.run_generator, generator.run_generator)
        for module in (asset, script, storyboard, feed):
            with open(module.__file__, encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("def run_generator", source)
            self.assertNotIn("def select_nodes", source)
            self.assertIn("run_generator(", source)

    def test_first_generate_runs_every_node_then_the_director(self):
        calls = []

        def complete(role, messages):
            calls.append((role, messages[0]["content"], messages[-1]["content"]))
            if role == "specialist":
                path = messages[0]["content"].split("本节点原文", 1)[1].split()[0]
                return json.dumps({"text": "NOTE-" + path}, ensure_ascii=False)
            return json.dumps({"image_prompts": "MERGED"}, ensure_ascii=False)

        produced = run_generator(
            "asset",
            "写两份资产。",
            role="角色包装",
            complete=complete,
            files=FILES,
        )
        self.assertEqual([role for role, _system, _user in calls], ["specialist", "specialist", "director"])
        systems = [system for role, system, _user in calls if role == "specialist"]
        self.assertEqual(systems[0].count("本节点原文"), 1)
        self.assertIn(PROP, systems[0])
        self.assertNotIn(PORTRAIT, systems[0])
        self.assertIn(PROP_MARK, systems[0])
        self.assertNotIn(PROP_MARK, systems[1])
        self.assertNotIn(PROP_MARK, calls[-1][1])
        self.assertIn("总控", calls[-1][1])
        self.assertIn("short-drama-director/SKILL.md", calls[-1][1])
        self.assertNotIn("本节点原文", calls[-1][1])
        self.assertIn("NOTE-" + PROP, calls[-1][2])
        self.assertIn("NOTE-" + PORTRAIT, calls[-1][2])
        self.assertNotIn(SENTINEL, calls[-1][2])
        self.assertEqual(produced["partials"][0]["text"], "NOTE-" + PROP)
        self.assertEqual(produced["raw"].count("MERGED") > 0, True)

    def test_repair_reruns_only_the_named_node(self):
        previous = [
            {"skill": PROP, "text": "旧道具"},
            {"skill": PORTRAIT, "text": "旧肖像"},
        ]
        calls = []

        def complete(role, messages):
            calls.append((role, messages[0]["content"], messages[-1]["content"]))
            if role == "specialist":
                return json.dumps({"text": "新道具"}, ensure_ascii=False)
            return "MERGED"

        produced = run_generator(
            "asset",
            "改点名的节点。",
            role="角色包装",
            complete=complete,
            partials=previous,
            repair_text="失败项在 %s 。审查员夹了改写：%s" % (PROP, SENTINEL),
            files=FILES,
        )
        self.assertEqual([role for role, _system, _user in calls], ["specialist", "director"])
        self.assertIn(PROP, calls[0][1])
        self.assertNotIn(PORTRAIT, calls[0][1])
        self.assertNotIn(PROP_MARK, calls[1][1])
        self.assertIn("旧肖像", calls[1][2])
        self.assertIn("新道具", calls[1][2])
        self.assertIn(PROP, produced["partials"][0]["skill"])
        self.assertEqual(produced["partials"][0]["text"], "新道具")
        self.assertEqual(produced["partials"][1]["text"], "旧肖像")

    def test_a_single_original_still_uses_the_node_and_the_director(self):
        calls = []

        def complete(role, messages):
            calls.append(role)
            return "稿"

        run_generator("script", "只有一份原文。", role="角色", complete=complete, files=[PROP])
        self.assertEqual(calls, ["specialist", "director"])

    def test_unmatched_repair_does_not_rerun_every_node(self):
        chosen = select_nodes(FILES, "图1 不要写同上", [{"skill": PROP, "text": "旧"}])
        self.assertEqual(chosen, [])


class WorkerDispatchTest(unittest.TestCase):
    def test_script_loop_dispatches_the_stage_then_repairs_one_node(self):
        files = suite_files("script", _routing("P1"))
        self.assertGreater(len(files), 1)
        named = files[-1]
        roles = []
        specialist_systems = []

        def complete(role, messages):
            roles.append(role)
            if role == "specialist":
                specialist_systems.append(messages[0]["content"])
                return json.dumps({"text": "节点"}, ensure_ascii=False)
            if role == "director":
                self.assertIn("总控", messages[0]["content"])
                self.assertNotIn("本节点原文", messages[0]["content"])
                self.assertIn("节点", messages[-1]["content"])
                self.assertNotIn(SENTINEL, messages[-1]["content"])
                return json.dumps({"script": "她把药盒放在桌上。"}, ensure_ascii=False)
            return json.dumps({
                "verdict": "revise",
                "script": SENTINEL,
                "issues": [{"target": named, "severity": "blocking", "detail": "这一份要改"}],
            }, ensure_ascii=False)

        def checker(text, state):
            if int(state.get("attempt") or 0) < 2:
                return {
                    "ok": False,
                    "errors": ["%s 没写完" % named],
                    "warnings": [],
                    "fatal": "",
                    "needs_human": False,
                }
            return {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False}

        result = run_script_loop("她把药盒放在桌上。", max_attempts=2, complete=complete, checker=checker)
        self.assertEqual(result["status"], "pass", result)
        first = roles.index("critic")
        self.assertEqual(roles[:first].count("specialist"), len(files))
        self.assertEqual(roles[:first].count("director"), 1)
        self.assertEqual(roles[first + 1:], ["specialist", "director", "critic"])
        self.assertTrue(all(item.count("本节点原文") == 1 for item in specialist_systems))
        self.assertEqual(specialist_systems[-1].count(named), 1)
        joined = "\n".join(specialist_systems)
        self.assertLess(joined.count(files[0]), 3)
        self.assertNotIn(SENTINEL, result["draft"])

    def test_each_worker_generate_ends_at_the_director(self):
        def complete(role, messages):
            if role == "specialist":
                return json.dumps({"text": "节点"}, ensure_ascii=False)
            if role == "director":
                return json.dumps({
                    "image_prompts": "#### 图1 · 林浅\n16:9\n通过块不要动",
                    "script": "她把药盒放在桌上。",
                    "storyboard": "## S01\n动作：推门\n景别机位：中景\n时长：6\n资产：A1\n",
                    "feed": "feed",
                }, ensure_ascii=False)
            return '{"verdict":"accept","issues":[]}'

        def accept_board(text, state):
            return {"ok": "## S01" in text and "时长：6" in text, "errors": [], "warnings": [], "fatal": "", "needs_human": False}

        def accept_feed(text, state):
            return {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False}

        asset_result = run_p2_asset_loop("她把药盒放在桌上。", complete=complete, checker=lambda text, min_blocks: {
            "ok": "通过块不要动" in text,
            "errors": [],
            "warnings": [],
            "exit_code": 0,
            "fatal": "",
            "stats": {},
            "blocks": 1,
            "report": "",
        })
        script_result = run_script_loop("她把药盒放在桌上。", complete=complete, checker=lambda text, state: {
            "ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False,
        })
        board_result = run_storyboard_loop("她把药盒放在桌上。", known_ids=["A1"], complete=complete, checker=accept_board)
        feed_result = run_feed_loop("分镜", complete=complete, checker=accept_feed)
        self.assertEqual(asset_result["status"], "pass", asset_result.get("machine_report"))
        self.assertEqual(script_result["status"], "pass", script_result)
        self.assertEqual(board_result["status"], "pass", board_result.get("machine_report"))
        self.assertEqual(feed_result["status"], "pass", feed_result)


if __name__ == "__main__":
    unittest.main()
