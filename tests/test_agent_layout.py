"""Agent layout, task queue, and the End handoff onto that queue."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from AI.agent.agent import CONTINUE_LEAD
from AI.agent.batch import CATALOG_LEAD, CATALOG_TOKENS, REPAIR_CATALOG_LEAD, batch_tokens, overlong_chars
from AI.agent.layout import AgentLayout
from AI.skills.registry import LOCK_FILES
from AI.agent.model import LLMModel
from AI.script import call_drill as pipeline_drill
from end.server import (
    call_deepseek,
    handle_agent_task,
    public_view,
    resume_agent_tasks,
    rpc_create_project,
    run_drill,
    run_project,
    start_drill,
)
from end.task_queue import Task, TaskQueue
from tests.test_project_api import make_project

LOCKED_BRIEF = "\n".join(
    [
        "赛道：1",
        "素材来源：1",
        "模型：MiniMax H3",
        "画幅：16:9",
        "交付：③",
        "段缝：①",
        "画风：冷色写实",
        "动作强度：R1",
    ]
)


class Reply(object):
    def __init__(self, content="", reasoning_content="", incomplete_reason=""):
        self.content = content
        self.reasoning_content = reasoning_content or ""
        self.incomplete_reason = incomplete_reason or ""
        self.raw = {"incompleteReason": self.incomplete_reason} if self.incomplete_reason else {}


class ScriptedModel(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "kind": kind,
                "effort": effort,
            }
        )
        item = self.replies.pop(0)
        if isinstance(item, Reply):
            return item
        return Reply(item)


def _call_text(call):
    return "\n".join(item.get("content") or "" for item in call["messages"])


ASK = '{"phase":"ask","reply":"先定谁在雨里","options":["她在等车","他在找人"]}'
SPEC = json.dumps(
    {"title": "雨夜", "script": "第一场", "assets": [{"id": "A1", "title": "她", "prompt": "女人"}]},
    ensure_ascii=False,
)


def _agent_threads():
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("aivideo-agent-") and thread.is_alive()
    ]


class LayoutTest(unittest.TestCase):
    def test_drill_goes_agent_then_model_then_agent(self):
        model = ScriptedModel([ASK] * (len(LOCK_FILES) + 1))
        layout = AgentLayout(model=model)
        result = layout.drill([{"role": "user", "content": "雨夜车站"}], model="deepseek-v4-pro")
        self.assertEqual(result["phase"], "ask")
        self.assertEqual(result["reply"], "先定谁在雨里")
        self.assertEqual(result["options"][-1], "其他")
        self.assertEqual(len(model.calls), len(LOCK_FILES) + 1)
        self.assertEqual(model.calls[0]["max_tokens"], 4000)
        self.assertEqual(model.calls[0]["kind"], "drill")
        self.assertEqual(model.calls[0]["model"], "qwen3.8-flash")
        self.assertEqual(model.calls[0]["messages"][0]["role"], "system")
        self.assertIn("雨夜车站", model.calls[0]["messages"][-1]["content"])
        self.assertNotIn("# 总控 short-drama-director/SKILL.md", model.calls[0]["messages"][0]["content"])
        self.assertIn("你是汇总节点", model.calls[-1]["messages"][0]["content"])
        self.assertIn("# 总控 short-drama-director/SKILL.md", model.calls[-1]["messages"][0]["content"])
        traced = AgentLayout(model=ScriptedModel([ASK]))
        state = traced.graph.invoke(
            {
                "kind": "drill",
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "雨夜车站"}],
                "brief": "",
                "skill_pack": "",
                "debug_dir": None,
                "request": None,
                "result": None,
                "output": None,
                "repair": False,
            }
        )
        self.assertEqual(state["trail"], ["agent", "llm", "agent"])

    def test_unknown_model_uses_qwen_flash(self):
        model = ScriptedModel([ASK] * (len(LOCK_FILES) + 1))
        AgentLayout(model=model).drill([{"role": "user", "content": "雨"}], model="glm-5.2")
        self.assertEqual(model.calls[0]["max_tokens"], 4000)
        self.assertEqual(model.calls[0]["model"], "qwen3.8-flash")
        self.assertEqual(model.calls[0]["kind"], "drill")

    def test_drill_keeps_a_plain_specialist_note_for_the_merge(self):
        model = ScriptedModel(["先随便说一句"] * len(LOCK_FILES) + [ASK])
        result = AgentLayout(model=model).drill(
            [{"role": "user", "content": "雨夜车站"}],
            model="deepseek-v4-pro",
        )
        self.assertEqual(result["phase"], "ask")
        self.assertEqual(len(model.calls), len(LOCK_FILES) + 1)
        self.assertIn("先随便说一句", model.calls[-1]["messages"][-1]["content"])

    def test_write_returns_the_spec(self):
        model = ScriptedModel([SPEC])
        spec = AgentLayout(model=model).write("雨夜车站", "规则", model="deepseek-v4-pro")
        self.assertEqual(spec["title"], "雨夜")
        self.assertEqual(model.calls[0]["max_tokens"], 16000)
        self.assertEqual(model.calls[0]["timeout"], 1200)
        self.assertEqual(model.calls[0]["kind"], "write")
        self.assertEqual(model.calls[0]["model"], "qwen3.8-flash")
        self.assertEqual(model.calls[0]["messages"][-1]["content"], "雨夜车站")

    def test_write_repairs_then_keeps_both_replies(self):
        model = ScriptedModel(["不是 JSON", SPEC])
        folder = Path(tempfile.mkdtemp())
        spec = AgentLayout(model=model).write("雨夜车站", "规则", debug_dir=folder, model="gpt-5.4")
        self.assertEqual(spec["script"], "第一场")
        self.assertIn("不是 JSON", (folder / "raw_response.txt").read_text(encoding="utf-8"))
        self.assertIn("第一场", (folder / "raw_response_repaired.txt").read_text(encoding="utf-8"))
        self.assertEqual(model.calls[1]["max_tokens"], 16000)
        self.assertIsNone(model.calls[1]["effort"])
        self.assertIn("收成一个 JSON", model.calls[1]["messages"][0]["content"])

    def test_write_keeps_a_stage_object_without_rewriting(self):
        from AI.script import _extract_json

        for key in ("script", "assets", "shots", "image_prompts", "storyboard", "feed", "previs", "intel", "internal"):
            value = ["一项"] if key in ("assets", "shots") else "正文"
            parsed = _extract_json(json.dumps({key: value}, ensure_ascii=False))
            self.assertEqual(parsed[key], value)
        raw = '{"storyboard":"第 1 格：门口\n她往外看"}'
        self.assertEqual(_extract_json(raw)["storyboard"], "第 1 格：门口\n她往外看")
        quoted = '{"script":"女主觉醒后"一击灭敌"也遵循写实逻辑","title":"破·疼"}'
        parsed = _extract_json(quoted)
        self.assertEqual(parsed["script"], '女主觉醒后"一击灭敌"也遵循写实逻辑')
        self.assertEqual(parsed["title"], "破·疼")
        quoted_model = ScriptedModel([quoted])
        quoted_spec = AgentLayout(model=quoted_model).write("雨夜车站", "规则")
        self.assertEqual(quoted_spec["script"], parsed["script"])
        self.assertEqual(len(quoted_model.calls), 1)
        already = json.dumps({"script": '她说"好"'}, ensure_ascii=False)
        self.assertEqual(_extract_json(already)["script"], '她说"好"')
        model = ScriptedModel(['{"image_prompts":"#### 图1 · 女"}'])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["image_prompts"], "#### 图1 · 女")
        self.assertEqual(len(model.calls), 1)

    def test_truncated_write_continues_instead_of_rewriting(self):
        partial = '{"title":"雨夜","script":"第一场'
        suffix = '还在下雨","assets":[{"id":"A1","title":"她","prompt":"女人"}]}'
        model = ScriptedModel([partial, suffix])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["script"], "第一场还在下雨")
        self.assertEqual(spec["title"], "雨夜")
        self.assertEqual(len(model.calls), 2)
        second = model.calls[1]
        self.assertGreater(second["max_tokens"], 16000)
        self.assertEqual(second["effort"], "low")
        self.assertEqual(second["messages"][0]["content"], CONTINUE_LEAD)
        self.assertEqual(second["messages"][-1]["content"], partial)
        self.assertNotIn("收成一个 JSON", second["messages"][0]["content"])

    def test_overlong_truncation_merges_batches(self):
        prefix = '{"script":"'
        marker = "TAILMARKER"
        room = overlong_chars() - len(prefix) - len(marker)
        partial = prefix + ("雨" * room) + marker
        self.assertEqual(len(partial), overlong_chars())
        catalog = json.dumps(
            {
                "batches": [
                    {"id": "cast", "field": "image_prompts", "ask": "只写角色顾行和女主"},
                    {"id": "place", "field": "image_prompts", "ask": "只写雨夜车站"},
                ]
            },
            ensure_ascii=False,
        )
        cast = json.dumps(
            {
                "image_prompts": "#### 图1 · 顾行",
                "assets": [{"id": "A1", "title": "顾行", "prompt": "旧"}],
            },
            ensure_ascii=False,
        )
        place = json.dumps(
            {
                "image_prompts": "#### 图2 · 车站",
                "assets": [
                    {"id": "A1", "title": "顾行", "prompt": "新"},
                    {"id": "A2", "title": "车站", "prompt": "雨"},
                ],
            },
            ensure_ascii=False,
        )
        skill = "决策树标记XYZ"
        model = ScriptedModel([partial, catalog, cast, place])
        spec = AgentLayout(model=model).write("雨夜车站", skill)
        self.assertEqual(spec["image_prompts"], "#### 图1 · 顾行\n\n#### 图2 · 车站")
        self.assertEqual([row["id"] for row in spec["assets"]], ["A1", "A2"])
        self.assertEqual(spec["assets"][0]["prompt"], "新")
        self.assertEqual(len(model.calls), 4)
        self.assertIn(skill, model.calls[0]["messages"][0]["content"])
        catalog_call = model.calls[1]
        self.assertEqual(catalog_call["messages"][0]["content"], CATALOG_LEAD)
        self.assertEqual(catalog_call["max_tokens"], CATALOG_TOKENS)
        self.assertIn(partial[:80], catalog_call["messages"][-1]["content"])
        self.assertNotIn(marker, _call_text(catalog_call))
        self.assertNotIn(CONTINUE_LEAD, _call_text(catalog_call))
        for call in model.calls[2:]:
            text = _call_text(call)
            self.assertEqual(call["max_tokens"], batch_tokens())
            self.assertEqual(batch_tokens(), 16000)
            self.assertIsNone(call["effort"])
            self.assertEqual(call["messages"][-1]["content"], "雨夜车站")
            self.assertIn("正文只放在 image_prompts", call["messages"][0]["content"])
            self.assertNotIn(skill, text)
            self.assertNotIn(marker, text)
            self.assertNotIn(CONTINUE_LEAD, text)
            self.assertNotIn("雨" * 200, text)

    def test_token_cap_with_an_inner_brace_batches_instead_of_rewriting(self):
        partial = Reply(
            '{"title":"疼过之后","assets":[{"id":"fig7","title":"男性角色","subject_kind":"character","view":"single"}',
            incomplete_reason="max_output_tokens",
        )
        catalog = json.dumps(
            {"batches": [{"id": "prop", "field": "image_prompts", "ask": "只写图9药盒"}]},
            ensure_ascii=False,
        )
        piece = json.dumps({"image_prompts": "#### 图9 · 药盒"}, ensure_ascii=False)
        model = ScriptedModel([partial, catalog, piece])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["image_prompts"], "#### 图9 · 药盒")
        self.assertEqual(model.calls[1]["messages"][0]["content"], CATALOG_LEAD)
        self.assertNotIn("收成一个 JSON", _call_text(model.calls[1]))

    def test_repair_catalog_leaves_the_script_alone(self):
        partial = Reply('{"previs":"## 段 1', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {"batches": [{"id": "seg", "field": "previs", "ask": "只补段1六问"}]},
            ensure_ascii=False,
        )
        piece = json.dumps({"previs": "## 段 1\n第 1 格：看哪"}, ensure_ascii=False)
        brief = "阶段：P3\n\n这次是返修。只改机检点名的失败项。\nFAIL: 段1 缺六问"
        model = ScriptedModel([partial, catalog, piece])
        spec = AgentLayout(model=model).write(brief, "规则")
        self.assertIn("第 1 格：看哪", spec["previs"])
        self.assertEqual(model.calls[1]["messages"][0]["content"], REPAIR_CATALOG_LEAD)
        self.assertIn("不要另开一批", model.calls[1]["messages"][0]["content"])

    def test_max_output_stop_batches_a_short_cut(self):
        partial = Reply('{"script":"第一场', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {"batches": [{"id": "scene", "field": "script", "ask": "只写第一场"}]},
            ensure_ascii=False,
        )
        scene = json.dumps({"script": "第一场 雨还在下"}, ensure_ascii=False)
        model = ScriptedModel([partial, catalog, scene])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["script"], "第一场 雨还在下")
        self.assertEqual(len(model.calls), 3)
        self.assertNotIn(CONTINUE_LEAD, _call_text(model.calls[1]))
        self.assertEqual(model.calls[1]["max_tokens"], CATALOG_TOKENS)

    def test_truncated_batch_splits_once(self):
        partial = Reply('{"image_prompts":"####', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {
                "batches": [
                    {
                        "id": "cast",
                        "field": "image_prompts",
                        "ask": "只写角色顾行和女主",
                        "items": ["顾行", "女主"],
                    }
                ]
            },
            ensure_ascii=False,
        )
        cut = '{"image_prompts":"#### 图1 · 顾'
        first = json.dumps({"image_prompts": "#### 图1 · 顾行"}, ensure_ascii=False)
        second = json.dumps({"image_prompts": "#### 图2 · 女主"}, ensure_ascii=False)
        model = ScriptedModel([partial, catalog, cut, first, second])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["image_prompts"], "#### 图1 · 顾行\n\n#### 图2 · 女主")
        self.assertEqual(len(model.calls), 5)
        self.assertIn("只写角色顾行和女主", model.calls[2]["messages"][0]["content"])
        self.assertIn("只写这一项：顾行", model.calls[3]["messages"][0]["content"])
        self.assertIn("只写这一项：女主", model.calls[4]["messages"][0]["content"])
        self.assertEqual(model.calls[3]["max_tokens"], batch_tokens())
        self.assertNotIn(cut, model.calls[3]["messages"][-1]["content"])
        for call in model.calls:
            self.assertNotIn(CONTINUE_LEAD, call["messages"][0]["content"])

    def test_split_batch_still_truncated_names_the_batch(self):
        partial = Reply('{"image_prompts":"####', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {
                "batches": [
                    {
                        "id": "cast",
                        "field": "image_prompts",
                        "ask": "只写角色顾行和女主",
                        "items": ["顾行", "女主"],
                    }
                ]
            },
            ensure_ascii=False,
        )
        cut = '{"image_prompts":"#### 图1 · 顾'
        again = '{"image_prompts":"#### 图1 · 仍'
        model = ScriptedModel([partial, catalog, cut, again])
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=model).write("雨夜车站", "规则")
        text = str(caught.exception)
        self.assertIn("批次 cast", text)
        self.assertIn("截断", text)
        self.assertNotIn("续写", text)
        self.assertEqual(len(model.calls), 4)
        for call in model.calls:
            self.assertNotIn(CONTINUE_LEAD, call["messages"][0]["content"])

    def test_one_listed_item_is_not_split_from_the_ask(self):
        partial = Reply('{"image_prompts":"####', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {
                "batches": [
                    {
                        "id": "cast",
                        "field": "image_prompts",
                        "ask": "只写角色顾行、女主",
                        "items": ["顾行"],
                    }
                ]
            },
            ensure_ascii=False,
        )
        model = ScriptedModel([partial, catalog, '{"image_prompts":"#### 图1 · 顾'])
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertIn("批次 cast", str(caught.exception))
        self.assertEqual(len(model.calls), 3)

    def test_empty_items_split_on_the_ask(self):
        partial = Reply('{"image_prompts":"####', incomplete_reason="max_output_tokens")
        catalog = json.dumps(
            {
                "batches": [
                    {
                        "id": "cast",
                        "field": "image_prompts",
                        "ask": "顾行、女主",
                        "items": [],
                    }
                ]
            },
            ensure_ascii=False,
        )
        cut = '{"image_prompts":"#### 图1 · 顾'
        first = json.dumps({"image_prompts": "#### 图1 · 顾行"}, ensure_ascii=False)
        second = json.dumps({"image_prompts": "#### 图2 · 女主"}, ensure_ascii=False)
        model = ScriptedModel([partial, catalog, cut, first, second])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["image_prompts"], "#### 图1 · 顾行\n\n#### 图2 · 女主")
        self.assertIn("只写这一项：顾行", model.calls[3]["messages"][0]["content"])
        self.assertIn("只写这一项：女主", model.calls[4]["messages"][0]["content"])

    def test_six_batches_fit_under_the_call_cap(self):
        partial = Reply('{"script":"第一场', incomplete_reason="max_output_tokens")
        rows = [
            {"id": "b%s" % index, "field": "script", "ask": "第%s场" % index}
            for index in range(1, 7)
        ]
        rows.append({"id": "extra", "field": "script", "ask": "不要写第七场"})
        catalog = json.dumps({"batches": rows}, ensure_ascii=False)
        scenes = [
            json.dumps({"script": "第%s场正文" % index}, ensure_ascii=False)
            for index in range(1, 7)
        ]
        model = ScriptedModel([partial, catalog] + scenes)
        spec = AgentLayout(model=model).write("雨夜", "规则")
        self.assertEqual(len(model.calls), 8)
        self.assertEqual(
            spec["script"],
            "\n\n".join("第%s场正文" % index for index in range(1, 7)),
        )
        blob = "\n".join(_call_text(call) for call in model.calls)
        self.assertNotIn("不要写第七场", blob)
        self.assertNotIn(CONTINUE_LEAD, blob)

    def test_ninth_call_stops_before_another_split(self):
        partial = Reply('{"script":"第一场', incomplete_reason="max_output_tokens")
        rows = [
            {"id": "b%s" % index, "field": "script", "ask": "第%s场" % index}
            for index in range(1, 6)
        ]
        rows.append(
            {
                "id": "b6",
                "field": "script",
                "ask": "第六场甲和乙",
                "items": ["甲", "乙"],
            }
        )
        catalog = json.dumps({"batches": rows}, ensure_ascii=False)
        scenes = [
            json.dumps({"script": "第%s场正文" % index}, ensure_ascii=False)
            for index in range(1, 6)
        ]
        model = ScriptedModel([partial, catalog] + scenes + ['{"script":"第六场'])
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=model).write("雨夜", "规则")
        text = str(caught.exception)
        self.assertIn("超过 8 次", text)
        self.assertIn("b6", text)
        self.assertEqual(len(model.calls), 8)

    def test_closed_but_invalid_json_is_rewritten(self):
        for broken in ('{"script": "第一场",}', '{"script": "第一场"}}'):
            model = ScriptedModel([broken, SPEC])
            spec = AgentLayout(model=model).write("雨夜车站", "规则")
            self.assertEqual(spec["script"], "第一场")
            self.assertEqual(model.calls[1]["max_tokens"], 16000)
            self.assertIsNone(model.calls[1]["effort"])
            self.assertIn("收成一个 JSON", model.calls[1]["messages"][0]["content"])

    def test_cut_inside_a_string_still_continues(self):
        partial = '{"script":"第一场}还没'
        suffix = '写完","assets":[{"id":"A1","title":"她","prompt":"女人"}]}'
        model = ScriptedModel([partial, suffix])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["script"], "第一场}还没写完")
        self.assertEqual(model.calls[1]["effort"], "low")
        self.assertGreater(model.calls[1]["max_tokens"], 16000)

    def test_truncated_write_adopts_a_finished_continuation(self):
        partial = '{"title":"雨夜","script":"第一场'
        model = ScriptedModel([partial, SPEC])
        spec = AgentLayout(model=model).write("雨夜车站", "规则")
        self.assertEqual(spec["script"], "第一场")
        self.assertEqual(model.calls[1]["effort"], "low")
        self.assertGreater(model.calls[1]["max_tokens"], 16000)

    def test_empty_write_reports_a_missing_body(self):
        layout = AgentLayout(model=ScriptedModel([""]))
        with self.assertRaises(RuntimeError) as caught:
            layout.write("雨夜", "规则", model="deepseek-v4-pro")
        self.assertIn("没有返回正文", str(caught.exception))

    def test_prose_reply_names_the_missing_brace(self):
        prose = '打开·音量不大但穿透力强）："不疼了。"  00:27-00:45 [镜头17]'
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=ScriptedModel([prose, prose])).write("雨夜", "规则")
        text = str(caught.exception)
        self.assertIn("这一步：改写", text)
        self.assertIn("左花括号", text)
        self.assertIn("定位：", text)
        self.assertIn("开头：", text)
        self.assertIn("打开·音量", text)
        self.assertNotIn("回复开头是", text)

    def test_cut_continuation_points_at_the_end(self):
        prose = '打开·音量不大但穿透力强）："不疼了。" 镜头继续往后拉。' * 6
        partial = '{"storyboard":"' + prose
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=ScriptedModel([partial, prose])).write("雨夜", "规则")
        text = str(caught.exception)
        self.assertIn("这一步：续写", text)
        self.assertIn("没有 }", text)
        self.assertIn("文末：", text)
        self.assertIn("对象开头：", text)
        self.assertIn("定位：", text)
        self.assertNotIn("回复开头是", text)
        from AI.server import _error_message

        served = _error_message(caught.exception)
        self.assertIn("代码：", served)
        self.assertIn("_extract_json", served)
        self.assertIn("原因：", served)

    def test_missing_spec_keys_lists_what_came_back(self):
        raw = '{"title":"只有标题"}'
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=ScriptedModel([raw, raw])).write("雨夜", "规则")
        text = str(caught.exception)
        self.assertIn("这一步：改写", text)
        self.assertIn("没有规格字段", text)
        self.assertIn("对象里的键：title", text)
        self.assertIn("定位：", text)

    def test_syntax_error_includes_the_decoder_spot(self):
        raw = '{"storyboard": "第一格",}'
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout(model=ScriptedModel([raw, raw])).write("雨夜", "规则")
        text = str(caught.exception)
        self.assertIn("这一步：改写", text)
        self.assertIn("语法不对", text)
        self.assertIn("解析器：", text)
        self.assertIn("定位：", text)
        self.assertNotIn("回复开头是", text)

    def test_model_error_keeps_the_prompt(self):
        class Boom(object):
            def complete(self, messages, model, max_tokens, timeout, kind=None):
                raise RuntimeError("down")

        folder = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            AgentLayout(model=Boom()).write("雨夜车站", "规则", debug_dir=folder)
        self.assertIn("雨夜车站", (folder / "user_brief.txt").read_text(encoding="utf-8"))

    def test_repair_call_failure_keeps_the_first_reply(self):
        class ThenBoom(object):
            def __init__(self):
                self.calls = 0

            def complete(self, messages, model, max_tokens, timeout, kind=None):
                self.calls += 1
                if self.calls == 1:
                    return Reply("不是 JSON")
                raise RuntimeError("down")

        folder = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            AgentLayout(model=ThenBoom()).write("雨夜车站", "规则", debug_dir=folder)
        self.assertIn("不是 JSON", (folder / "raw_response.txt").read_text(encoding="utf-8"))

    def test_drill_uses_the_intent_graph(self):
        client = _FakeResponses(ASK)
        result = LLMModel(client=client).complete(
            [{"role": "user", "content": "hi"}],
            model="deepseek-v4-pro",
            max_tokens=20,
            timeout=5,
            kind="drill",
        )
        kwargs = client.calls[0]
        self.assertEqual(kwargs["model"], "qwen3.8-flash")
        self.assertNotIn("reasoning", kwargs)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})
        self.assertEqual(kwargs["max_output_tokens"], 20)
        self.assertEqual(kwargs["timeout"], 5.0)
        self.assertEqual(kwargs["tool_choice"], "required")
        self.assertEqual(kwargs["tools"][0]["name"], "submit_intent")
        self.assertEqual(json.loads(result.content)["phase"], "ask")

    def test_write_uses_the_text_graph(self):
        client = _FakeResponses("ok")
        result = LLMModel(client=client).complete(
            [{"role": "user", "content": "hi"}],
            model="gpt-5.4",
            max_tokens=20,
            timeout=5,
            kind="write",
        )
        kwargs = client.calls[0]
        self.assertEqual(kwargs["model"], "qwen3.8-flash")
        self.assertNotIn("reasoning", kwargs)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})
        self.assertNotIn("tools", kwargs)
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.model, "qwen3.8-flash")
        self.assertEqual(result.incomplete_reason, "")

    def test_max_output_stop_reaches_the_chat_result(self):
        class Stopped(_FakeResponses):
            def create(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "output_text": "写到一半",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "choices": [{"finish_reason": "length"}],
                }

        result = LLMModel(client=Stopped("unused")).complete(
            [{"role": "user", "content": "hi"}],
            model="gpt-5.4",
            max_tokens=20,
            timeout=5,
            kind="write",
        )
        self.assertEqual(result.content, "写到一半")
        self.assertEqual(result.incomplete_reason, "max_output_tokens")
        self.assertEqual(result.raw.get("incompleteReason"), "max_output_tokens")
        self.assertEqual(result.raw.get("finishReason"), "length")

    def test_pipeline_drill_uses_the_layout(self):
        with patch("AI.agent.layout.get_layout") as get_layout:
            get_layout.return_value.drill.return_value = {"phase": "ask", "reply": "问", "options": []}
            self.assertEqual(pipeline_drill([{"role": "user", "content": "雨"}])["phase"], "ask")
        get_layout.return_value.drill.assert_called_once()


class _FakeResponses(object):
    def __init__(self, text):
        self.calls = []
        self._text = text
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            return {
                "output": [
                    {"type": "function_call", "name": "submit_intent", "arguments": self._text}
                ]
            }
        return {"output_text": self._text}


class AudioLayoutTest(unittest.TestCase):
    def test_speech_calls_qwen_and_music_is_refused(self):
        dest = Path(tempfile.mkdtemp()) / "outputs" / "voice" / "S01.mp3"
        with patch("AI.qwen_api.audio.synthesize", return_value=dest) as spoken:
            path = AgentLayout().speak("夜色已经很深了", dest, voice_id="other-voice")
        spoken.assert_called_once_with("夜色已经很深了", dest, voice_id="other-voice")
        self.assertEqual(path, str(dest))
        with self.assertRaises(RuntimeError) as caught:
            AgentLayout().compose("汽车配乐", dest)
        self.assertIn("不能生成配乐", str(caught.exception))

    def test_layout_audio_enters_the_graph(self):
        from end.render_loop import LayoutAudio

        class Layout(object):
            def speak(self, text, dest, voice_id=None):
                self.spoken = (text, str(dest), voice_id)
                return dest

            def compose(self, prompt, dest):
                self.score = (prompt, str(dest))
                return dest

        layout = Layout()
        audio = LayoutAudio(layout)
        audio.synthesize("夜色已经很深了", "outputs/voice/S01.mp3", voice_id="other")
        audio.generate_music("汽车配乐，雨夜", "outputs/voice/music.mp3")
        self.assertEqual(layout.spoken, ("夜色已经很深了", "outputs/voice/S01.mp3", "other"))
        self.assertEqual(layout.score, ("汽车配乐，雨夜", "outputs/voice/music.mp3"))


class QueueTest(unittest.TestCase):
    def test_workers_stay_within_the_pool(self):
        entered = []
        lock = threading.Lock()
        peak = {"n": 0, "current": 0}
        release = threading.Event()

        def handler(task):
            with lock:
                peak["current"] += 1
                peak["n"] = max(peak["n"], peak["current"])
                entered.append(task.project_id)
            release.wait(2)
            with lock:
                peak["current"] -= 1

        queue = TaskQueue(handler, workers=2)
        try:
            for index in range(4):
                self.assertTrue(queue.submit(Task("drill", "p%s" % index)))
            deadline = time.time() + 2
            while time.time() < deadline:
                with lock:
                    if len(entered) >= 2:
                        break
                time.sleep(0.01)
            time.sleep(0.05)
            with lock:
                self.assertEqual(len(entered), 2)
                self.assertEqual(peak["n"], 2)
            release.set()
            self.assertTrue(queue.wait_until_idle(2))
            with lock:
                self.assertEqual(len(entered), 4)
        finally:
            release.set()
            queue.stop()
        self.assertFalse(_agent_threads())

    def test_same_project_task_is_not_queued_twice(self):
        seen = []
        started = threading.Event()
        release = threading.Event()

        def handler(task):
            seen.append(task.kind)
            started.set()
            release.wait(2)

        queue = TaskQueue(handler, workers=1)
        try:
            self.assertTrue(queue.submit(Task("drill", "same")))
            self.assertTrue(started.wait(2))
            self.assertFalse(queue.submit(Task("drill", "same")))
            self.assertTrue(queue.submit(Task("write", "same")))
            release.set()
            self.assertTrue(queue.wait_until_idle(2))
            self.assertEqual(seen, ["drill", "write"])
        finally:
            release.set()
            queue.stop()


class EndHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_start_drill_only_enqueues(self):
        project_id = "a6e170000011"
        project = make_project(project_id, status="drilling")
        submitted = []
        with patch("end.server.submit_agent_task", side_effect=submitted.append), patch(
            "end.server.call_drill"
        ) as drill:
            start_drill(project)
            drill.assert_not_called()
        self.assertEqual(submitted[0].kind, "drill")
        self.assertEqual(submitted[0].project_id, project_id)
        saved = db.read_project(project_id)
        self.assertEqual(saved["status"], "drilling")
        self.assertEqual(saved["progress"]["phase"], "drill_think")

    def test_queue_worker_runs_the_drill(self):
        project_id = "a6e170000012"
        make_project(project_id, status="drilling")
        done = threading.Event()

        def fake(messages, debug_dir=None, model=None, template=None):
            done.set()
            return {"phase": "ask", "reply": "选一个", "options": ["甲", "其他"]}

        queue = TaskQueue(handle_agent_task, workers=1)
        try:
            with patch("end.server.call_drill", side_effect=fake):
                self.assertTrue(queue.submit(Task("drill", project_id)))
                self.assertTrue(done.wait(3))
                self.assertTrue(queue.wait_until_idle(2))
        finally:
            queue.stop()
        saved = db.read_project(project_id)
        self.assertEqual(saved["messages"][-1]["content"], "选一个")
        self.assertEqual(saved["progress"]["phase"], "drill_wait")

    def test_ready_drill_enqueues_the_script(self):
        project_id = "a6e170000013"
        make_project(project_id, status="drilling")
        submitted = []

        def fake(messages, debug_dir=None, model=None, template=None):
            return {"phase": "ready", "reply": "开始写", "brief": LOCKED_BRIEF, "options": []}

        with patch("end.server.call_drill", side_effect=fake), patch(
            "end.server.submit_agent_task", side_effect=submitted.append
        ):
            run_drill(project_id)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].kind, "write")
        self.assertEqual(submitted[0].project_id, project_id)
        self.assertTrue(submitted[0].payload["debug_llm"] is False)
        saved = db.read_project(project_id)
        self.assertEqual(saved["brief"], LOCKED_BRIEF)
        self.assertEqual(saved["status"], "writing")
        self.assertEqual(saved["progress"]["phase"], "writing")
        self.assertEqual(saved["messages"][-1]["content"], "开始写")

    def test_intent_debug_stops_before_prompts_and_images(self):
        project_id = "a6e17000001a"
        make_project(
            project_id,
            status="drilling",
            script="",
            storyboard="",
            progress={
                "percent": 0,
                "phase": "drill_think",
                "message": "正在想方向",
                "debug_llm": False,
                "debug_stop": "intent",
            },
        )
        answers = [
            {"phase": "ask", "reply": "谁来用", "options": ["她", "其他"], "brief": ""},
            {"phase": "ready", "reply": "就按这个做", "brief": LOCKED_BRIEF, "options": []},
        ]
        seen = []

        def fake(messages, debug_dir=None, model=None, template=None):
            seen.append(debug_dir)
            return answers.pop(0)

        submitted = []
        with patch("end.server.call_drill", side_effect=fake), patch(
            "end.server.submit_agent_task", side_effect=submitted.append
        ):
            run_drill(project_id)
            asked = db.read_project(project_id)
            self.assertEqual(asked["status"], "drilling")
            self.assertEqual(asked["progress"]["phase"], "drill_wait")
            self.assertEqual(asked["progress"]["debug_stop"], "intent")
            run_drill(project_id)
        self.assertEqual(submitted, [])
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertTrue(all(path is not None for path in seen))
        saved = db.read_project(project_id)
        view = public_view(saved)
        self.assertEqual(saved["brief"], LOCKED_BRIEF)
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["script"], "")
        self.assertEqual(saved["progress"]["phase"], "intent_debug")
        self.assertEqual(saved["progress"]["debug_stop"], "intent")
        self.assertFalse(saved["progress"]["debug_llm"])
        self.assertEqual(view["debug_stop"], "intent")
        self.assertFalse(view["debug_llm"])
        self.assertFalse(view["can_reply"])
        self.assertEqual(view["stage"], "Debug：已停在意图解析")

    def test_create_keeps_intent_stop_ahead_of_image_debug(self):
        old = os.environ.get("AIVIDEO_DEBUG_LLM")
        os.environ["AIVIDEO_DEBUG_LLM"] = "1"
        try:
            with patch("end.server.start_drill", side_effect=db.write_project):
                created = rpc_create_project(
                    {"brief": "银色耳机", "debug_stop": "intent", "debug_llm": True}
                )
        finally:
            if old is None:
                os.environ.pop("AIVIDEO_DEBUG_LLM", None)
            else:
                os.environ["AIVIDEO_DEBUG_LLM"] = old
        self.assertEqual(created["debug_stop"], "intent")
        self.assertFalse(created["debug_llm"])
        self.assertEqual(created["status"], "drilling")
        saved = db.read_project(created["id"])
        self.assertEqual(saved["progress"]["debug_stop"], "intent")
        self.assertFalse(saved["progress"]["debug_llm"])

    def test_prompt_debug_writes_the_spec_and_skips_images(self):
        project_id = "a6e17000001b"
        make_project(
            project_id,
            status="drilling",
            script="",
            storyboard="",
            progress={
                "percent": 0,
                "phase": "drill_wait",
                "message": "选一个方向",
                "debug_llm": False,
                "debug_stop": "prompts",
            },
        )
        submitted = []

        def fake_drill(messages, debug_dir=None, model=None, template=None):
            self.assertIsNotNone(debug_dir)
            return {"phase": "ready", "reply": "开始写", "brief": LOCKED_BRIEF, "options": []}

        with patch("end.server.call_drill", side_effect=fake_drill), patch(
            "end.server.submit_agent_task", side_effect=submitted.append
        ):
            run_drill(project_id)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].kind, "write")
        self.assertTrue(submitted[0].payload["debug_llm"] is False)
        queued = db.read_project(project_id)
        self.assertEqual(queued["status"], "writing")
        self.assertEqual(queued["progress"]["debug_stop"], "prompts")
        spec = {
            "title": "新标题",
            "script": "写完的剧本",
            "storyboard": "写完的分镜",
            "image_prompts": "提示",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人"}],
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "\n".join([
                    "integrated_multimodal_description: A woman in a blue coat stands on a wet platform. [Shot 1] She turns and walks toward the train.",
                    "overall_soundscape: Rain and a distant train brake.",
                    "non_diegetic_music: Low strings, slow.",
                    "duration_seconds: 5",
                ]),
                "images": ["A1"],
            }],
        }
        seen = {}

        def fake_write(brief, skill_pack, debug_dir=None, model=None, template=None):
            seen["debug_dir"] = debug_dir
            return spec

        old = os.environ.get("AIVIDEO_DEBUG_LLM")
        os.environ["AIVIDEO_DEBUG_LLM"] = "1"
        try:
            with patch("end.server.call_deepseek", side_effect=fake_write), patch(
                "end.server.start_render_thread"
            ) as start:
                run_project(project_id, debug_llm=True)
        finally:
            if old is None:
                os.environ.pop("AIVIDEO_DEBUG_LLM", None)
            else:
                os.environ["AIVIDEO_DEBUG_LLM"] = old
        start.assert_not_called()
        self.assertIsNotNone(seen["debug_dir"])
        self.assertEqual(resume_agent_tasks(), 0)
        saved = db.read_project(project_id)
        view = public_view(saved)
        self.assertEqual(saved["script"], "写完的剧本")
        self.assertEqual(saved["storyboard"], "写完的分镜")
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompt_debug")
        self.assertEqual(saved["progress"]["debug_stop"], "prompts")
        self.assertFalse(saved["progress"]["debug_llm"])
        self.assertTrue(saved["tasks"])
        self.assertTrue(all(task["state"] == "muted" for task in saved["tasks"]))
        self.assertEqual(view["debug_stop"], "prompts")
        self.assertFalse(view["debug_llm"])
        self.assertEqual(view["stage"], "Debug：已停在提示词")

    def test_create_keeps_prompt_stop_ahead_of_image_debug(self):
        old = os.environ.get("AIVIDEO_DEBUG_LLM")
        os.environ["AIVIDEO_DEBUG_LLM"] = "1"
        try:
            with patch("end.server.start_drill", side_effect=db.write_project):
                created = rpc_create_project(
                    {"brief": "银色耳机", "debug_stop": "prompts", "debug_llm": True}
                )
        finally:
            if old is None:
                os.environ.pop("AIVIDEO_DEBUG_LLM", None)
            else:
                os.environ["AIVIDEO_DEBUG_LLM"] = old
        self.assertEqual(created["debug_stop"], "prompts")
        self.assertFalse(created["debug_llm"])
        saved = db.read_project(created["id"])
        self.assertEqual(saved["progress"]["debug_stop"], "prompts")
        self.assertFalse(saved["progress"]["debug_llm"])

    def test_finished_script_hands_render_to_another_thread(self):
        project_id = "a6e170000014"
        make_project(project_id, status="writing")
        spec = {
            "title": "新标题",
            "script": "写完的剧本",
            "storyboard": "写完的分镜",
            "image_prompts": "提示",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人"}],
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "\n".join([
                    "integrated_multimodal_description: A woman in a blue coat stands on a wet platform. [Shot 1] She turns and walks toward the train.",
                    "overall_soundscape: Rain and a distant train brake.",
                    "non_diegetic_music: Low strings, slow.",
                    "duration_seconds: 5",
                ]),
                "images": ["A1"],
            }],
        }
        with patch("end.server.call_deepseek", return_value=spec), patch(
            "end.server.start_render_thread"
        ) as start:
            run_project(project_id)
        start.assert_not_called()
        saved = db.read_project(project_id)
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompts_ready")
        self.assertEqual(saved["script"], "写完的剧本")

    def test_restart_requeues_an_open_model_call(self):
        make_project(
            "a6e170000015",
            status="drilling",
            progress={"percent": 0, "phase": "drill_think", "message": "正在想方向"},
        )
        make_project(
            "a6e170000016",
            status="drilling",
            progress={"percent": 0, "phase": "drill_wait", "message": "选一个方向"},
        )
        make_project("a6e170000017", status="writing")
        submitted = []

        def capture(task):
            submitted.append(task)
            return True

        with patch("end.server.submit_agent_task", side_effect=capture):
            started = resume_agent_tasks()
        self.assertEqual(started, 2)
        queued = sorted((task.kind, task.project_id) for task in submitted)
        self.assertEqual(queued, [("drill", "a6e170000015"), ("write", "a6e170000017")])

    def test_server_write_calls_ai(self):
        with patch("end.server.ai_write", return_value={"title": "雨"}) as write:
            self.assertEqual(call_deepseek("简报", "规则", model="gpt-5.4")["title"], "雨")
        write.assert_called_once()
