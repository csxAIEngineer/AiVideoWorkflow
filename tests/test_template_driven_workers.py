"""Pre-designed task skills are filled by rule-based code, then sent."""

import json
import unittest
from difflib import SequenceMatcher
from itertools import combinations

from Agents.templates import (
    TASKS,
    TURNS,
    critic_messages,
    director_messages,
    extract_brief,
    fill,
    list_templates,
    load_frontmatter,
    load_template,
    parse_messages,
    specialist_messages,
    template_path,
    truthy,
)
from Agents.workers.generator import run_generator
from Agents.workers.seam import SKILL_FILE as SEAM_SKILL


PROP = "sd-asset-engine/references/prop-template.md"
PORTRAIT = "sd-asset-engine/references/character-portrait-templates.md"
PROP_MARK = "工作流 Step 4 道具模板"
DIALOGUE_MARK = "台词拟人化过站"


def _body(task, turn):
    return load_template(task, turn)


class FillerTest(unittest.TestCase):
    def test_slots_and_conditionals(self):
        template = "A={{name}}\n{{#note}}NOTE={{note}}\n{{/note}}END"
        self.assertEqual(fill(template, {"name": "Lin"}), "A=Lin\nEND")
        self.assertEqual(
            fill(template, {"name": "Lin", "note": "keep"}),
            "A=Lin\nNOTE=keep\nEND",
        )

    def test_inverse_conditionals(self):
        template = "{{#repair}}REPAIR{{/repair}}{{^repair}}FIRST{{/repair}}"
        self.assertEqual(fill(template, {}), "FIRST")
        self.assertEqual(fill(template, {"repair": "x"}), "REPAIR")

    def test_payload_is_not_scanned_for_slots(self):
        filled = fill("X={{body}}Y={{other}}", {"body": "{{other}} injected", "other": "SAFE"})
        self.assertEqual(filled, "X={{other}} injectedY=SAFE")

    def test_missing_slots_are_empty(self):
        self.assertEqual(fill("{{missing}}", {}), "")
        self.assertFalse(truthy(""))
        self.assertFalse(truthy([]))
        self.assertFalse(truthy(None))
        self.assertTrue(truthy("0"))

    def test_parse_system_and_user(self):
        messages = parse_messages("===SYSTEM===\n系统\n===USER===\n用户")
        self.assertEqual(messages, [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "用户"},
        ])


class CatalogTest(unittest.TestCase):
    def test_every_worker_task_has_three_turns(self):
        listed = list_templates()
        self.assertNotIn("default/specialist.md", listed)
        for task in TASKS:
            for turn in TURNS:
                name = "%s/%s.md" % (task, turn)
                self.assertIn(name, listed, name)
                text = load_template(task, turn)
                meta = load_frontmatter(task, turn)
                self.assertIn("===SYSTEM===", text)
                self.assertIn("===USER===", text)
                self.assertIn("## 步骤", text)
                self.assertIn("完成标准", text)
                self.assertTrue(meta.get("name"), name)
                self.assertTrue(meta.get("description"), name)

    def test_missing_task_fails_loudly(self):
        with self.assertRaises(RuntimeError) as raised:
            template_path("unknown-task", "specialist")
        self.assertIn("缺少模板", str(raised.exception))

    def test_specialists_are_not_near_duplicates(self):
        texts = {}
        for task in TASKS:
            text = _body(task, "specialist")
            texts[task] = text
            self.assertIn("完成标准", text)
            self.assertNotIn("{{skill_contract}}", text)
            self.assertNotIn("## SKILL CONTRACT", text)
        def steps(text):
            start = text.find("## 步骤")
            end = text.find("===USER===")
            return text[start:end]

        for left, right in combinations(TASKS, 2):
            full = SequenceMatcher(None, texts[left], texts[right]).ratio()
            ratio = SequenceMatcher(None, steps(texts[left]), steps(texts[right])).ratio()
            self.assertLess(full, 0.80, "%s vs %s full: %.3f" % (left, right, full))
            self.assertLess(ratio, 0.60, "%s vs %s steps: %.3f" % (left, right, ratio))
        self.assertIn("对白自然化", texts["script"])
        self.assertIn("用户原文", texts["script"])
        self.assertIn("资产板", texts["asset"])
        self.assertIn("不要写成封装稿", texts["asset"])
        self.assertIn("通过的图块原样保留", texts["asset"])
        self.assertIn("## S01", texts["storyboard"])
        self.assertIn("预演故事板", texts["storyboard"])
        self.assertIn("一条封装单元恰好一条账", texts["seam"])
        self.assertIn("不要改 shot_id", texts["seam"])
        self.assertIn("封装骨架", texts["feed"])
        self.assertIn("时长不得缩短，故事不得压缩", texts["feed"])
        self.assertIn("画风锁", texts["feed"])

    def test_directors_and_critics_are_distinct_processes(self):
        for task in TASKS:
            director = _body(task, "director")
            critic = _body(task, "critic")
            specialist = _body(task, "specialist")
            self.assertIn("你是汇总节点", director)
            self.assertIn("评判器只审不改", director)
            self.assertNotIn("本节点原文", director)
            self.assertIn("评判器只审不改", critic)
            self.assertIn("只对照", critic)
            self.assertNotIn("本节点原文 {{skill_path}}", critic)
            self.assertIn("本节点原文 {{skill_path}}", specialist)
            ratio = SequenceMatcher(None, specialist, director).ratio()
            self.assertLess(ratio, 0.7, "%s specialist vs director: %.3f" % (task, ratio))
            ratio = SequenceMatcher(None, specialist, critic).ratio()
            self.assertLess(ratio, 0.55, "%s specialist vs critic: %.3f" % (task, ratio))

    def test_frontmatter_describes_different_scenarios(self):
        descriptions = [load_frontmatter(task, "specialist")["description"] for task in TASKS]
        self.assertEqual(len(set(descriptions)), 5)
        self.assertIn("P1", descriptions[0])
        self.assertIn("image_prompts", descriptions[1])
        self.assertIn("storyboard", descriptions[2].lower() + " " + descriptions[2])
        self.assertTrue(any("衔接" in item or "seam" in item.lower() for item in descriptions))
        self.assertTrue(any("封装" in item or "packing" in item.lower() for item in descriptions))


class RenderTest(unittest.TestCase):
    def test_specialist_fills_named_slots_and_keeps_the_path_pointer(self):
        messages = specialist_messages(
            "asset",
            role="角色包装",
            skill_path=PROP,
            brief="写两份资产。",
        )
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertEqual(system.count("本节点原文"), 1)
        self.assertIn(PROP, system.split("本节点原文", 1)[1].split()[0])
        self.assertIn("道具板四格", system)
        self.assertNotIn(PROP_MARK, system)
        self.assertNotIn("SKILL CONTRACT", system)
        self.assertIn("写两份资产。", user)
        self.assertIn("本次任务", user)
        self.assertNotIn("## 返修", user)
        self.assertNotIn("只改点到本节点原文的失败项", system)
        self.assertNotIn(PORTRAIT, system)

    def test_repair_reference_only_appears_when_filled(self):
        first = specialist_messages(
            "asset",
            role="角色包装",
            skill_path=PROP,
            brief="改点名的节点。",
        )
        messages = specialist_messages(
            "asset",
            role="角色包装",
            skill_path=PROP,
            brief="改点名的节点。",
            repair_text="失败项在 %s" % PROP,
        )
        self.assertNotIn("已经通过的图块原样保留。评判器只审不改，报告里夹带的改写稿作废", first[0]["content"])
        self.assertIn("### 返修", messages[1]["content"])
        self.assertIn(PROP, messages[1]["content"])
        self.assertIn("只改点到本节点原文的失败项", messages[0]["content"])
        self.assertIn("已经通过的图块原样保留", messages[0]["content"])

    def test_director_merges_notes_and_omits_skill_originals(self):
        messages = director_messages(
            "asset",
            brief="写两份资产。",
            partials=[
                {"skill": PROP, "text": "NOTE-PROP"},
                {"skill": PORTRAIT, "text": "NOTE-PORTRAIT"},
            ],
        )
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertIn("你是汇总节点", system)
        self.assertNotIn("本节点原文", system)
        self.assertNotIn(PROP_MARK, system)
        self.assertNotIn("short-drama-director/SKILL.md", system)
        self.assertIn("NOTE-PROP", user)
        self.assertIn("NOTE-PORTRAIT", user)
        self.assertIn("写两份资产。", user)

    def test_critic_reviews_and_does_not_rewrite(self):
        messages = critic_messages(
            "script",
            role="你是剧本审查员。你只审查，只交报告。",
            notes="阶段：剧本审查。只交 JSON 报告。",
            draft="她把药盒放在桌上。",
        )
        self.assertIn("只审查", messages[0]["content"])
        self.assertIn("评判器只审不改", messages[0]["content"])
        self.assertIn("机检技能", messages[1]["content"])
        self.assertIn("不要改稿", messages[0]["content"] + messages[1]["content"])
        self.assertIn("禁止改写剧本", messages[0]["content"])
        self.assertIn("她把药盒放在桌上。", messages[1]["content"])
        self.assertIn("待审稿", messages[1]["content"])

    def test_feed_discloses_style_lock_only_when_present(self):
        plain = specialist_messages(
            "feed",
            role="封装",
            skill_path="sd-feed-engine/SKILL.md",
            brief="请填写封装骨架。",
        )
        locked = specialist_messages(
            "feed",
            role="封装",
            skill_path="sd-feed-engine/SKILL.md",
            brief="请填写封装骨架。",
            style_lock="2D Chinese ink-wash, xuan paper texture.",
        )
        unique = "只出现在房间或道具主体上不算数"
        self.assertNotIn(unique, plain[0]["content"])
        self.assertIn(unique, locked[0]["content"])
        self.assertIn("2D Chinese ink-wash, xuan paper texture.", locked[1]["content"])
        self.assertIn("画风锁", locked[0]["content"])

    def test_brief_splits_into_named_slots(self):
        slots = extract_brief("\n".join((
            "阶段：P2",
            "赛道：1",
            "剧本：",
            "她把药盒放在桌上。",
            "锁：",
            "画风：水墨",
            "要写的资产：",
            "图1 药盒",
        )))
        self.assertIn("她把药盒放在桌上。", slots["source"])
        self.assertIn("画风：水墨", slots["locks"])
        self.assertIn("图1 药盒", slots["asset_list"])
        self.assertIn("阶段：P2", slots["route"])

    def test_originals_are_not_inlined_wholesale(self):
        messages = specialist_messages(
            "script",
            role="编剧",
            skill_path="sd-script-engine/references/dialogue-naturalization.md",
            brief="用户原文\n她把药盒放在桌上。",
        )
        system = messages[0]["content"]
        self.assertIn("对白自然化", system)
        self.assertNotIn(DIALOGUE_MARK, system)
        self.assertLess(len(system), 8000)


class GeneratorSendPathTest(unittest.TestCase):
    def test_run_generator_sends_filled_templates(self):
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
            files=[PROP, PORTRAIT],
        )
        self.assertEqual([role for role, _system, _user in calls], ["specialist", "specialist", "director"])
        first_system = calls[0][1]
        self.assertEqual(first_system.count("本节点原文"), 1)
        self.assertIn(PROP, first_system)
        self.assertIn("道具板四格", first_system)
        self.assertNotIn(PROP_MARK, first_system)
        self.assertNotIn("SKILL CONTRACT", first_system)
        self.assertNotIn(PORTRAIT, first_system)
        self.assertIn("写两份资产。", calls[0][2])
        self.assertIn("本次任务", calls[0][2])
        director_system, director_user = calls[-1][1], calls[-1][2]
        self.assertIn("你是汇总节点", director_system)
        self.assertNotIn("本节点原文", director_system)
        self.assertNotIn(PROP_MARK, director_system)
        self.assertIn("NOTE-" + PROP, director_user)
        self.assertIn("NOTE-" + PORTRAIT, director_user)
        self.assertEqual(produced["partials"][0]["text"], "NOTE-" + PROP)

    def test_seam_specialist_uses_the_agent_layer_skill_path(self):
        calls = []

        def complete(role, messages):
            calls.append(messages[0]["content"])
            return json.dumps({"text": "账"}, ensure_ascii=False) if role == "specialist" else "{}"

        run_generator(
            "seam",
            "任务：分镜衔接",
            role="衔接",
            complete=complete,
            files=[SEAM_SKILL],
        )
        self.assertIn("# 本节点原文 %s" % SEAM_SKILL, calls[0])
        self.assertIn("一条封装单元恰好一条账", calls[0])
        self.assertIn("video_mode", calls[0])


if __name__ == "__main__":
    unittest.main()
