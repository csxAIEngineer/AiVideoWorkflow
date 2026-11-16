"""Pre-designed templates are filled by rule-based code, then sent."""

import json
import unittest

from Agents.templates import (
    TASKS,
    TURNS,
    critic_messages,
    director_messages,
    fill,
    list_templates,
    load_template,
    parse_messages,
    skill_contract,
    specialist_messages,
    template_path,
    truthy,
)
from Agents.workers.generator import run_generator
from Agents.workers.seam import SKILL_FILE as SEAM_SKILL


PROP = "sd-asset-engine/references/prop-template.md"
PORTRAIT = "sd-asset-engine/references/character-portrait-templates.md"
PROP_MARK = "工作流 Step 4 道具模板"


class FillerTest(unittest.TestCase):
    def test_slots_and_conditionals(self):
        template = "A={{name}}\n{{#note}}NOTE={{note}}\n{{/note}}END"
        self.assertEqual(fill(template, {"name": "Lin"}), "A=Lin\nEND")
        self.assertEqual(
            fill(template, {"name": "Lin", "note": "keep"}),
            "A=Lin\nNOTE=keep\nEND",
        )

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
        for task in TASKS:
            for turn in TURNS:
                name = "%s/%s.md" % (task, turn)
                self.assertIn(name, listed, name)
                text = load_template(task, turn)
                self.assertIn("===SYSTEM===", text)
                self.assertIn("===USER===", text)
                self.assertIn("YOUR TASK", text)
        self.assertTrue(template_path("unknown-task", "specialist").as_posix().endswith("default/specialist.md"))

    def test_templates_encode_stage_rules(self):
        script = load_template("script", "specialist")
        self.assertIn("不要自己加文风门", script)
        self.assertIn("本节点原文 {{skill_path}}", script)
        self.assertIn("{{skill_contract}}", script)
        self.assertIn("{{brief}}", script)

        asset = load_template("asset", "specialist")
        self.assertIn("不要写成封装稿", asset)
        self.assertIn("已经通过的图块原样保留", asset)

        board = load_template("storyboard", "specialist")
        self.assertIn("不要写预演故事板", board)
        self.assertIn("只补那个字段", board)

        seam = load_template("seam", "specialist")
        self.assertIn("只填或修改骨架里的槽", seam)
        self.assertIn("不要改 shot_id", seam)

        feed = load_template("feed", "specialist")
        self.assertIn("时长不能缩小，故事不能压缩", feed)
        self.assertIn("禁止把声明时长钳成 15", feed)
        self.assertIn("不要自己发明 Ref2VA 版式", feed)

        critic = load_template("feed", "critic")
        self.assertIn("禁止改写封装稿", critic)
        self.assertIn("不要改稿", critic)


class RenderTest(unittest.TestCase):
    def test_specialist_fills_skill_contract_and_brief(self):
        messages = specialist_messages(
            "asset",
            role="角色包装",
            skill_path=PROP,
            brief="写两份资产。",
            contract=skill_contract(PROP),
        )
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertEqual(system.count("本节点原文"), 1)
        self.assertIn(PROP, system.split("本节点原文", 1)[1].split()[0])
        self.assertIn(PROP_MARK, system)
        self.assertIn("写两份资产。", user)
        self.assertIn("YOUR TASK", user)
        self.assertNotIn("## REPAIR", user)
        self.assertNotIn(PORTRAIT, system)

    def test_repair_block_only_appears_when_filled(self):
        messages = specialist_messages(
            "asset",
            role="角色包装",
            skill_path=PROP,
            brief="改点名的节点。",
            repair_text="失败项在 %s" % PROP,
            contract=skill_contract(PROP),
        )
        self.assertIn("## REPAIR", messages[1]["content"])
        self.assertIn(PROP, messages[1]["content"])
        self.assertIn("只改点到本节点原文的失败项", messages[1]["content"])

    def test_director_merges_notes_and_omits_skill_originals(self):
        messages = director_messages(
            "asset",
            brief="写两份资产。",
            partials=[
                {"skill": PROP, "text": "NOTE-PROP"},
                {"skill": PORTRAIT, "text": "NOTE-PORTRAIT"},
            ],
            merge_instruction="你是汇总节点。只根据各节点正文和简报交稿。",
        )
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertIn("你是汇总节点", system)
        self.assertNotIn("本节点原文", system)
        self.assertNotIn(PROP_MARK, system)
        self.assertNotIn("short-drama-director/SKILL.md", system)
        self.assertIn("NOTE-PROP", user)
        self.assertIn("NOTE-PORTRAIT", user)
        self.assertIn("写两份资产。", user)
        self.assertIn("请汇总成这一阶段的一份稿。", user)

    def test_critic_reviews_and_does_not_rewrite(self):
        messages = critic_messages(
            "script",
            role="你是剧本审查员。你只审查，只交报告。",
            user_body="机检技能（只读。这是你的评判标准）：\n空稿\n待审稿：\n她把药盒放在桌上。",
        )
        self.assertIn("只审查", messages[0]["content"])
        self.assertIn("机检技能", messages[1]["content"])
        self.assertIn("不要改稿", messages[1]["content"])
        self.assertIn("禁止改写剧本", messages[1]["content"])
        self.assertIn("她把药盒放在桌上。", messages[1]["content"])


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
        self.assertIn(PROP_MARK, first_system)
        self.assertIn("SKILL CONTRACT", first_system)
        self.assertNotIn(PORTRAIT, first_system)
        self.assertIn("写两份资产。", calls[0][2])
        self.assertIn("YOUR TASK", calls[0][2])
        director_system, director_user = calls[-1][1], calls[-1][2]
        self.assertIn("你是汇总节点", director_system)
        self.assertNotIn("本节点原文", director_system)
        self.assertNotIn(PROP_MARK, director_system)
        self.assertIn("NOTE-" + PROP, director_user)
        self.assertIn("NOTE-" + PORTRAIT, director_user)
        self.assertEqual(produced["partials"][0]["text"], "NOTE-" + PROP)

    def test_seam_specialist_uses_the_agent_layer_skill(self):
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


if __name__ == "__main__":
    unittest.main()
