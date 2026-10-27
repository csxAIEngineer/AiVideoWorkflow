"""Collaborative task graph: one node per original, then one merge."""

import json
import tempfile
import unittest
from pathlib import Path

from AI.qwen_api.suite_graph import _cohort_partials, compile_suite_graph, run_suite
from AI.skills.registry import (
    director_for_merge,
    director_text,
    merge_prompt,
    pack_cohorts,
    skill_node_prompt,
    suite_files,
    suite_original,
    suite_prompt_chars,
    suite_task_for,
)


class _Model(object):
    def __init__(self):
        self.systems = []

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        self.systems.append(system)
        self.user = user

        class Reply(object):
            def __init__(self, content):
                self.content = content

        if "你是汇总节点" in system:
            seen = user
            return Reply(json.dumps({"storyboard": "分镜正文", "previs": "预演正文", "seen": seen}, ensure_ascii=False))
        return Reply(json.dumps({"text": "片段"}, ensure_ascii=False))


class SuiteGraphTest(unittest.TestCase):
    def test_storyboard_nodes_keep_one_original_and_merge_sees_every_partial(self):
        brief = "\n".join((
            "阶段：P3",
            "赛道：1",
            "来源：1",
            "动作档：R2",
            "模型：h3",
            "素材形态：M2",
            "预演：1",
            "任务：分镜",
        ))
        files = suite_files("storyboard", brief)
        cohorts = pack_cohorts(files)
        self.assertEqual(len(files), 15)
        self.assertLess(len(cohorts), len(files))
        covered = [relative for cohort in cohorts for relative in cohort]
        self.assertEqual(covered, files)
        model = _Model()
        prompts = tempfile.NamedTemporaryFile(delete=False)
        prompts.close()
        result = run_suite(
            "storyboard",
            brief,
            model,
            model_name="deepseek-v4.1-flash",
            trace_path=prompts.name,
        )
        self.assertEqual(len(model.systems), len(cohorts) + 1)
        skill_prompts = [item for item in model.systems if "# 本节点原文 " in item]
        self.assertEqual(len(skill_prompts), len(cohorts))
        for cohort in cohorts:
            prompt = next(item for item in skill_prompts if "# 本节点原文 %s" % cohort[0] in item)
            self.assertNotIn("# 总控 short-drama-director/SKILL.md", prompt)
            for relative in cohort:
                self.assertIn("# 本节点原文 %s" % relative, prompt)
                self.assertIn(suite_original(relative)[:120], prompt)
            for other in files:
                if other in cohort:
                    continue
                self.assertNotIn("# 本节点原文 %s" % other, prompt)
        self.assertEqual(len(result["partials"]), len(cohorts))
        self.assertEqual(result["storyboard"], "分镜正文")
        for relative in files:
            self.assertIn(relative, result["seen"])
            self.assertEqual(result["seen"].count("# 本节点原文 %s" % relative), 0)
        graph = compile_suite_graph(files, "storyboard", model, "write")
        names = set(graph.nodes)
        self.assertIn("merge", names)
        self.assertEqual(sum(1 for name in names if str(name).startswith("skill-")), len(cohorts))
        logged = Path(prompts.name).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(logged), len(cohorts) + 1)

    def test_script_graph_returns_one_script_and_every_partial(self):
        files = suite_files("script", "任务：剧本")
        cohorts = pack_cohorts(files)
        self.assertEqual(len(files), 7)
        self.assertLessEqual(len(cohorts), len(files))

        class ScriptModel(_Model):
            def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
                system = messages[0]["content"]
                user = messages[-1]["content"]
                self.systems.append(system)
                self.user = user

                class Reply(object):
                    def __init__(self, content):
                        self.content = content

                if "你是汇总节点" in system:
                    return Reply(json.dumps({
                        "script": "正式剧本",
                        "title": "题",
                        "internal": "圆桌",
                        "seen": user,
                    }, ensure_ascii=False))
                return Reply(json.dumps({"text": "片段"}, ensure_ascii=False))

        model = ScriptModel()
        result = run_suite("script", "任务：剧本\n用户原文\n一句故事", model)
        self.assertEqual(len(model.systems), len(cohorts) + 1)
        self.assertEqual(result["script"], "正式剧本")
        self.assertEqual(len(result["partials"]), len(cohorts))
        for relative in files:
            self.assertIn(relative, result["seen"])
            self.assertEqual(result["seen"].count("# 本节点原文 %s" % relative), 0)

    def test_the_first_task_marker_wins_and_the_header_selects_files(self):
        self.assertEqual(suite_task_for("任务：剧本\n用户原文\n任务：分镜"), "script")
        route = "\n".join((
            "阶段：P0",
            "赛道：2",
            "来源：1",
            "动作档：none",
            "模型：h3",
            "素材形态：M1",
            "",
            "任务：赛道分流",
        ))
        self.assertIn("sd-overseas-engine/SKILL.md", suite_files("route", route))
        qc = "\n".join((
            "阶段：P5",
            "赛道：1",
            "来源：1",
            "动作档：none",
            "模型：h3",
            "素材形态：M1",
            "",
            "任务：独立质检",
        ))
        self.assertIn("short-drama-director/references/h3-adapter.md", suite_files("qc", qc))

    def test_skill_prompt_is_one_file_without_the_director(self):
        prompt = skill_node_prompt("sd-storyboard-engine/SKILL.md")
        self.assertNotIn("# 总控 short-drama-director/SKILL.md", prompt)
        self.assertIn("# 本节点原文 sd-storyboard-engine/SKILL.md", prompt)
        self.assertNotIn("# 本节点原文 sd-storyboard-engine/references/short-drama-camera-system.md", prompt)
        self.assertNotIn("先于后面所有问题", prompt)

    def test_sectioned_cohort_keeps_one_note_per_original(self):
        files = [
            "sd-script-engine/references/dialogue-naturalization.md",
            "short-drama-director/references/dialogue-doctor-7d.md",
        ]
        text = "\n".join((
            "## %s" % files[0],
            "台词要口语。",
            "## %s" % files[1],
            "七维体检。",
        ))
        partials = _cohort_partials(files, text)
        self.assertEqual([item["skill"] for item in partials], files)
        self.assertEqual(partials[0]["text"], "台词要口语。")
        self.assertEqual(partials[1]["text"], "七维体检。")

    def test_merge_director_keeps_the_laws_and_drops_the_index(self):
        full = director_text()
        excerpt = director_for_merge()
        self.assertLess(len(excerpt), len(full) * 0.6)
        self.assertIn("预算铁律", excerpt)
        self.assertIn("Asset-First", excerpt)
        self.assertIn("交付满额铁律", excerpt)
        self.assertIn("画幅铁律", excerpt)
        self.assertNotIn("套装目录总览", excerpt)
        self.assertNotIn("### 机检维度分组", excerpt)
        self.assertNotIn("### ★ 强制读取清单", excerpt)
        self.assertIn("分级处置", excerpt)
        self.assertIn("只适用于 P0", excerpt)
        prompt = merge_prompt("storyboard")
        self.assertIn("# 总控 short-drama-director/SKILL.md", prompt)
        self.assertIn("你是汇总节点", prompt)
        self.assertLess(len(prompt), len(full))

    def test_packing_cuts_prompt_copies_without_dropping_originals(self):
        brief = "\n".join((
            "阶段：P3",
            "赛道：1",
            "来源：1",
            "动作档：R2",
            "模型：h3",
            "素材形态：M2",
            "预演：1",
            "任务：分镜",
            "上一阶段正文：",
            "剧本和资产" * 800,
        ))
        measured = suite_prompt_chars("storyboard", brief)
        self.assertEqual(measured["files"], 15)
        self.assertLess(measured["calls_after"], measured["calls_before"])
        self.assertLess(measured["packed_chars"], measured["unpacked_chars"] * 0.75)
        lock = suite_prompt_chars("lock", "任务：立项锁定")
        self.assertEqual(lock["files"], 6)
        self.assertEqual(lock["cohorts"], 1)
        self.assertLess(lock["packed_chars"], lock["unpacked_chars"] * 0.6)

    def test_empty_node_retries_once_and_keeps_the_other_partial(self):
        files = [
            "sd-asset-engine/references/character-portrait-templates.md",
            "sd-asset-engine/references/scene-grid-template.md",
        ]
        self.assertEqual(len(pack_cohorts(files)), 2)
        empty_skill = files[0]
        model = _RetryModel(empty_skill)
        prompts = tempfile.NamedTemporaryFile(delete=False)
        prompts.close()
        state = _invoke_files(files, model, prompts.name)
        grouped = {}
        for call in model.calls:
            grouped.setdefault(call["marker"], []).append(call)
        self.assertEqual(len(grouped[empty_skill]), 2)
        self.assertEqual(grouped[empty_skill][0]["max_tokens"], 8000)
        self.assertEqual(grouped[empty_skill][1]["max_tokens"], 8000)
        self.assertIn("不要再写推理", grouped[empty_skill][1]["user"])
        self.assertEqual(len(grouped[files[1]]), 1)
        self.assertEqual(grouped[files[1]][0]["max_tokens"], 8000)
        self.assertEqual(len(grouped["merge"]), 1)
        partials = {item["skill"]: item["text"] for item in state["partials"]}
        self.assertEqual(partials[empty_skill], "补上")
        self.assertEqual(partials[files[1]], "原有")
        self.assertIn("正式剧本", state["output"])
        self.assertIn("不要再写推理", Path(prompts.name).read_text(encoding="utf-8"))

    def test_second_empty_reply_still_raises(self):
        files = [
            "sd-asset-engine/references/character-portrait-templates.md",
            "sd-asset-engine/references/scene-grid-template.md",
        ]
        model = _RetryModel(files[0], stay_empty=True)
        with self.assertRaises(RuntimeError) as caught:
            _invoke_files(files, model, "")
        self.assertEqual(str(caught.exception), "这一份原文没有正文")
        empty_calls = [item for item in model.calls if item["marker"] == files[0]]
        self.assertEqual(len(empty_calls), 2)


class _RetryModel(object):
    def __init__(self, empty_skill, stay_empty=False):
        self.empty_skill = empty_skill
        self.stay_empty = stay_empty
        self.calls = []

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        system = messages[0]["content"]
        marker = "merge"
        for line in system.splitlines():
            if line.startswith("# 本节点原文 "):
                marker = line[len("# 本节点原文 ") :].strip()
                break
        self.calls.append({
            "marker": marker,
            "max_tokens": max_tokens,
            "user": messages[-1]["content"],
        })

        class Reply(object):
            def __init__(self, content):
                self.content = content
                self.raw = {}

        if marker == "merge":
            return Reply(json.dumps({"script": "正式剧本"}, ensure_ascii=False))
        if marker == self.empty_skill:
            seen = sum(1 for item in self.calls if item["marker"] == self.empty_skill)
            if seen == 1 or self.stay_empty:
                return Reply("")
            return Reply(json.dumps({"text": "补上"}, ensure_ascii=False))
        return Reply(json.dumps({"text": "原有"}, ensure_ascii=False))


def _invoke_files(files, model, trace_path):
    app = compile_suite_graph(files, "script", model, "write")
    return app.invoke(
        {
            "kind": "write",
            "brief": "任务：剧本",
            "task": "script",
            "model": "deepseek-v4.1-flash",
            "trace_path": trace_path,
            "partials": [],
            "output": "",
            "messages": [],
        }
    )


if __name__ == "__main__":
    unittest.main()
