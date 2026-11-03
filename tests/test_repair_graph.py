"""Repair graph: one node per failed item, merge only collects those items."""

import json
import unittest

from AI.qwen_api.repair_graph import build_repair_graph, describe_repair_graph, repair_job_nodes, run_repair
from AI.skills.registry import suite_task_for
from end.repair_units import apply_repairs, repair_brief, units_for


class _Model(object):
    def __init__(self, empty_first=False):
        self.users = []
        self.systems = []
        self.calls = []
        self.empty_first = empty_first

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        self.users.append(messages[-1]["content"])
        self.systems.append(messages[0]["content"])
        self.calls.append((max_tokens, timeout))

        class Reply(object):
            def __init__(self, content):
                self.content = content

        if self.empty_first and len(self.calls) == 1:
            return Reply("")
        return Reply(json.dumps({"text": "改过：" + messages[-1]["content"].splitlines()[-1]}, ensure_ascii=False))


class RepairGraphTest(unittest.TestCase):
    def test_each_node_sees_only_its_own_block(self):
        units = [
            {"id": "图-0", "label": "图1 · 林浅", "suggestion": "PASS 的不要出现", "body": "通过块不要动"},
            {"id": "图-1", "label": "图8 · 预演", "suggestion": "FAIL: 图8 缺五官", "body": "旧失败块"},
        ]
        brief = repair_brief("asset", [units[1]])
        self.assertEqual(suite_task_for(brief + "\n任务：资产锁定"), "repair")
        self.assertEqual([item["name"] for item in repair_job_nodes(brief)], ["图8 · 预演", "汇总"])
        model = _Model()
        described = describe_repair_graph(build_repair_graph([units[1]], model, "write"), [units[1]])
        self.assertIn("fix-0  图8 · 预演", described)
        self.assertIn("merge  汇总", described)
        result = run_repair(brief, model, model_name="qwen3.8-flash")
        self.assertEqual(len(model.users), 1)
        self.assertIn("旧失败块", model.users[0])
        self.assertIn("FAIL: 图8 缺五官", model.users[0])
        self.assertNotIn("通过块不要动", model.users[0])
        self.assertNotIn("任务：资产锁定", model.users[0])
        self.assertEqual(result["repairs"][0]["id"], "图-1")
        self.assertIn("旧失败块", result["repairs"][0]["text"])
        self.assertEqual(model.calls, [(2000, 120)])

    def test_storyboard_repair_keeps_the_write_budget(self):
        brief = repair_brief("storyboard", [{"id": "段-1", "label": "段1", "suggestion": "缺六问", "body": "失败段"}])
        model = _Model()
        run_repair(brief, model, model_name="qwen3.8-flash")
        self.assertEqual(model.calls, [(16000, 1200)])
        system = model.systems[0]
        for bit in (
            "## 段 N",
            "本段要验什么",
            "风险提示",
            "故事板提示词",
            "第 1 格",
            "看图先看这三点",
            "谁在哪",
            "朝哪",
            "看哪",
            "做什么",
            "镜头在哪",
            "光从哪来",
        ):
            self.assertIn(bit, system)
        asset = repair_brief("asset", [{"id": "图-0", "label": "图1", "suggestion": "缺一句", "body": "旧块"}])
        asset_model = _Model()
        run_repair(asset, asset_model, model_name="qwen3.8-flash")
        self.assertNotIn("本段要验什么", asset_model.systems[0])

    def test_empty_asset_repair_retries_once_at_4000(self):
        brief = repair_brief("asset", [{"id": "图-0", "label": "图1", "suggestion": "缺一句", "body": "旧块"}])
        model = _Model(empty_first=True)
        run_repair(brief, model, model_name="qwen3.8-flash")
        self.assertEqual(model.calls, [(2000, 120), (4000, 120)])

    def test_two_previs_segments_keep_the_one_that_passed(self):
        previs = "## 段 1\n失败段\n\n## 段 2\n通过的段不要动\n"
        report = "FAIL: P453 段1 第1格缺六问"
        units = units_for("storyboard", report, previs=previs)
        self.assertEqual([unit["id"] for unit in units], ["段-1"])
        self.assertNotIn("通过的段不要动", repair_brief("storyboard", units))
        applied = apply_repairs(
            "storyboard",
            units,
            [{"id": "段-1", "text": "## 段 1\n补上六问"}],
            previs=previs,
            storyboard="分镜不要动",
        )
        self.assertEqual(applied["storyboard"], "分镜不要动")
        self.assertIn("补上六问", applied["previs"])
        self.assertIn("通过的段不要动", applied["previs"])

    def test_named_failure_does_not_pull_neighbor_segments(self):
        previs = "\n\n".join((
            "## 段 1\n失败段正文",
            "## 段 2\n通过的邻居不要进修复",
            "## 段 3\n另一段邻居",
        ))
        report = "FAIL: P453 段1 第1格缺六问\nFAIL: P454 术语没有段号"
        units = units_for("storyboard", report, previs=previs)
        self.assertEqual([unit["id"] for unit in units], ["段-1"])
        brief = repair_brief("storyboard", units)
        self.assertIn("失败段正文", brief)
        self.assertNotIn("通过的邻居不要进修复", brief)
        self.assertNotIn("另一段邻居", brief)

    def test_loose_marker_repairs_only_the_unit_that_contains_it(self):
        feed = "\n\n".join((
            "## 【投喂单元 1/2】\nsubject_definitions:\nclean unit",
            "## 【投喂单元 2/2】\nsubject_definitions:\n【画幅风格】 leaked",
        ))
        report = "FAIL: H8 七段式栏名泄漏 【画幅风格】"
        units = units_for("feed", report, feed=feed)
        self.assertEqual([unit["number"] for unit in units], [2])
        brief = repair_brief("feed", units)
        self.assertIn("leaked", brief)
        self.assertNotIn("clean unit", brief)


if __name__ == "__main__":
    unittest.main()
