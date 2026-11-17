"""The four stage workers share one generator layout.

Specialists each see one original. The director merges. A repair reruns only
the node the critic or the machine check named.
"""

import json
import unittest
from pathlib import Path

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


def _semantic_pass(_draft, _state):
    return {"status": "passed", "issues": [], "reason": ""}


def _module_source(module):
    path = Path(module.__file__)
    if path.name == "__init__.py":
        return "\n".join(item.read_text(encoding="utf-8") for item in sorted(path.parent.glob("*.py")))
    return path.read_text(encoding="utf-8")


def _routing(stage):
    return "阶段：%s\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n" % stage


class SharedLayoutTest(unittest.TestCase):
    def test_four_workers_call_the_same_generator(self):
        self.assertIs(asset.run_generator, generator.run_generator)
        self.assertIs(script.run_generator, generator.run_generator)
        self.assertIs(storyboard.run_generator, generator.run_generator)
        self.assertIs(feed.run_generator, generator.run_generator)
        for module in (asset, script, storyboard, feed):
            source = _module_source(module)
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
        self.assertIn("你是汇总节点", calls[-1][1])
        self.assertNotIn("总控", calls[-1][1])
        self.assertNotIn("short-drama-director/SKILL.md", calls[-1][1])
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
                self.assertIn("你是汇总节点", messages[0]["content"])
                self.assertNotIn("总控", messages[0]["content"])
                self.assertNotIn("short-drama-director/SKILL.md", messages[0]["content"])
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

        result = run_script_loop(
            "她把药盒放在桌上。",
            max_attempts=2,
            complete=complete,
            checker=checker,
            reviewer=_semantic_pass,
        )
        self.assertEqual(result["status"], "pass", result)
        first = roles.index("critic")
        self.assertEqual(roles[:first].count("specialist"), len(files))
        self.assertEqual(roles[:first].count("director"), 1)
        self.assertEqual(roles[first + 1:], ["specialist", "director", "critic"])
        self.assertTrue(all(item.count("本节点原文") == 1 for item in specialist_systems))
        self.assertEqual(specialist_systems[-1].count(named), 1)
        joined = "\n".join(specialist_systems)
        self.assertLess(joined.count(files[0]), 3)
        self.assertNotIn("# 本节点原文 sd-script-engine/references/story-bible-and-ip.md", joined)
        self.assertNotIn("# 本节点原文 sd-script-engine/references/episode-planning.md", joined)
        self.assertNotIn("source-registration.md", joined)
        self.assertIn("formal-script.md", joined)
        self.assertIn("dialogue-naturalization.md", joined)
        self.assertIn("dialogue-doctor-7d.md", joined)
        self.assertIn("roundtable-review.md", joined)
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
        script_result = run_script_loop(
            "她把药盒放在桌上。",
            complete=complete,
            checker=lambda text, state: {
                "ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False,
            },
            reviewer=_semantic_pass,
        )
        board_result = run_storyboard_loop("她把药盒放在桌上。", known_ids=["A1"], complete=complete, checker=accept_board)
        feed_result = run_feed_loop("分镜", complete=complete, checker=accept_feed)
        self.assertEqual(asset_result["status"], "pass", asset_result.get("machine_report"))
        self.assertEqual(script_result["status"], "pass", script_result)
        self.assertEqual(board_result["status"], "pass", board_result.get("machine_report"))
        self.assertEqual(feed_result["status"], "pass", feed_result)

OPENING_MARK = "成片时长秒：60"
CATALOG_MARK = "图1 = 书院正门"
OPENING_BRIEF = "\n".join((
    "任务：分镜",
    OPENING_MARK,
    "已锁定资产 id 对照（资产字段只写左边的完整 id）：",
    CATALOG_MARK,
    "图2 = 学堂内景",
    "图4 = 走廊",
    "图6 = 后山",
    "图7 = 祠堂",
    "剧本：\n她推开书院的门。",
))
BAD_BOARD = "\n".join((
    "## S01",
    "动作：推门",
    "景别机位：中景",
    "时长：6",
    "资产：图1，图2_3，图4_3",
))
GOOD_BOARD = "\n".join((
    "## S01",
    "动作：推门",
    "景别机位：中景",
    "时长：6",
    "资产：图1，图2，图4",
))


def _unmatched_checker(fail_text, ok_when):
    def checker(text, state=None, min_blocks=None):
        if ok_when(text):
            return {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False, "report": ""}
        return {
            "ok": False,
            "errors": [fail_text],
            "warnings": [],
            "fatal": "",
            "needs_human": False,
            "report": fail_text,
        }
    return checker


class RepairLoopPromptTest(unittest.TestCase):
    """Loop repairs keep the first-write brief and do not restuff it as a merge."""

    def test_unmatched_repair_keeps_opening_and_does_not_repeat_the_brief(self):
        previous = [{"skill": PROP, "text": "旧道具专员稿"}]
        calls = []

        def complete(role, messages):
            calls.append((role, messages[0]["content"], messages[-1]["content"]))
            return json.dumps({"storyboard": GOOD_BOARD}, ensure_ascii=False)

        produced = run_generator(
            "storyboard",
            OPENING_BRIEF,
            role="角色",
            complete=complete,
            partials=previous,
            repair_text="\n".join((
                "任务：建议修补",
                "阶段：storyboard",
                "上一稿：\n%s" % BAD_BOARD,
                "机检失败与警告（FAIL 必须清掉。只有警告、没有 FAIL 不算失败）：",
                "分镜未完成：S01 资产不在对照表：图2_3，图4_3",
            )),
            files=FILES,
        )
        self.assertEqual([role for role, _system, _user in calls], ["director"])
        system, user = calls[0][1], calls[0][2]
        self.assertIn(OPENING_MARK, user)
        self.assertIn(CATALOG_MARK, user)
        self.assertEqual(user.count("任务：建议修补"), 1)
        self.assertEqual(user.count("上一稿："), 1)
        self.assertIn("图2_3", user)
        self.assertNotIn("请汇总成这一阶段的一份稿", user)
        self.assertNotIn("各节点正文", user)
        self.assertNotIn("旧道具专员稿", user)
        self.assertIn("以「上一稿」为底", user)
        self.assertNotIn("资产：图1，图1_3，图2_3", system)
        self.assertIn("资产：图1，图2，图4", system)
        self.assertIn("不要自造", system)
        self.assertIn("资产：图1，图2，图4", produced["raw"])

    def test_storyboard_loop_repair_passes_opening_apart_from_revision(self):
        calls = []

        def complete(role, messages):
            calls.append((role, messages[0]["content"], messages[-1]["content"]))
            if role == "specialist":
                return json.dumps({"text": "专员稿"}, ensure_ascii=False)
            if role == "director":
                if any("建议修补" in item[2] for item in calls if item[0] == "director"):
                    return json.dumps({"storyboard": GOOD_BOARD}, ensure_ascii=False)
                return json.dumps({"storyboard": BAD_BOARD}, ensure_ascii=False)
            return '{"verdict":"accept","issues":[]}'

        result = run_storyboard_loop(
            "她推开书院的门。",
            opening=OPENING_BRIEF,
            known_ids=["图1", "图2", "图4", "图6", "图7"],
            max_attempts=2,
            complete=complete,
            checker=_unmatched_checker(
                "分镜未完成：S01 资产不在对照表：图2_3，图4_3",
                lambda text: "图2_3" not in text and "资产：图1，图2，图4" in text,
            ),
        )
        self.assertEqual(result["status"], "pass", result)
        repair_users = [user for role, _system, user in calls if role == "director" and "建议修补" in user]
        self.assertEqual(len(repair_users), 1)
        user = repair_users[0]
        self.assertIn(OPENING_MARK, user)
        self.assertIn(CATALOG_MARK, user)
        self.assertEqual(user.count("任务：建议修补"), 1)
        self.assertEqual(user.count("上一稿："), 1)
        self.assertIn(BAD_BOARD.splitlines()[-1], user)
        self.assertNotIn("请汇总成这一阶段的一份稿", user)
        self.assertNotIn("各节点正文", user)
        self.assertNotIn("专员稿", user)

    def test_each_worker_loop_keeps_the_opening_brief_on_repair(self):
        cases = (
            (
                "asset",
                lambda complete, checker: run_p2_asset_loop(
                    "她把药盒放在桌上。锁记：成片时长秒：60",
                    max_attempts=2,
                    complete=complete,
                    checker=checker,
                ),
                "#### 图1 · 林浅\n16:9\n通过块不要动",
                "#### 图1 · 林浅\n16:9\n通过块不要动\n已补风格",
                "图1 缺风格锁定",
                lambda text: "已补风格" in text,
            ),
            (
                "script",
                lambda complete, checker: run_script_loop(
                    "她把药盒放在桌上。",
                    opening="任务：剧本\n成片时长秒：60\n用户原文\n她把药盒放在桌上。",
                    max_attempts=2,
                    complete=complete,
                    checker=checker,
                    reviewer=_semantic_pass,
                ),
                "她把药盒放在桌上。",
                "她把药盒放在桌上，盖上盖子。",
                "对白缺结尾",
                lambda text: "盖上盖子" in text,
            ),
            (
                "feed",
                lambda complete, checker: run_feed_loop(
                    "分镜",
                    opening="任务：提示词封装\n成片时长秒：60\n分镜：\n分镜",
                    max_attempts=2,
                    complete=complete,
                    checker=checker,
                ),
                "封装初稿",
                "封装修补稿",
                "封装单元 1/1 缺画面",
                lambda text: "封装修补稿" in text,
            ),
        )
        for task, runner, first_draft, second_draft, fail_text, ok_when in cases:
            with self.subTest(task=task):
                calls = []

                def complete(role, messages, first=first_draft, second=second_draft):
                    calls.append((role, messages[0]["content"], messages[-1]["content"]))
                    if role == "specialist":
                        return json.dumps({"text": "专员稿"}, ensure_ascii=False)
                    if role == "director":
                        payload = {
                            "image_prompts": second if any("建议修补" in item[2] for item in calls if item[0] == "director") else first,
                            "script": second if any("建议修补" in item[2] for item in calls if item[0] == "director") else first,
                            "feed": second if any("建议修补" in item[2] for item in calls if item[0] == "director") else first,
                        }
                        return json.dumps(payload, ensure_ascii=False)
                    return '{"verdict":"accept","issues":[]}'

                if task == "asset":
                    def checker(text, min_blocks=None):
                        if ok_when(text):
                            return {
                                "ok": True,
                                "errors": [],
                                "warnings": [],
                                "exit_code": 0,
                                "fatal": "",
                                "stats": {},
                                "blocks": 1,
                                "report": "",
                            }
                        return {
                            "ok": False,
                            "errors": [fail_text],
                            "warnings": [],
                            "exit_code": 1,
                            "fatal": "",
                            "stats": {},
                            "blocks": 1,
                            "report": fail_text,
                        }
                else:
                    checker = _unmatched_checker(fail_text, ok_when)

                result = runner(complete, checker)
                self.assertEqual(result["status"], "pass", result)
                repair_users = [user for role, _system, user in calls if role == "director" and "建议修补" in user]
                self.assertEqual(len(repair_users), 1, repair_users)
                user = repair_users[0]
                self.assertIn(OPENING_MARK, user)
                self.assertEqual(user.count("任务：建议修补"), 1)
                self.assertEqual(user.count("上一稿："), 1)
                self.assertNotIn("请汇总成这一阶段的一份稿", user)
                self.assertNotIn("各节点正文", user)



if __name__ == "__main__":
    unittest.main()
