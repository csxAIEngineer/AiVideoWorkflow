"""Script stage walks the screenwriter chain one original at a time."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AI.skills.registry import build_system_prompt, load_skill_pack, script_step_pack
from end.stage_runner import execute
from end.trace import begin_trace, finish_trace, restore_trace


class ScriptStepSourceTest(unittest.TestCase):
    def test_each_step_reads_only_its_original(self):
        first = script_step_pack("1")
        self.assertTrue(first.startswith("# 来源登记与源头处理"))
        self.assertNotIn("观众留存分集规划", first)
        self.assertNotIn("# 下篇 · 阶段③全剧开发圣经", first)

        roles = script_step_pack("2")
        self.assertTrue(roles.startswith("# 上篇 · 阶段② IP 与角色开发"))
        self.assertNotIn("# 下篇 · 阶段③全剧开发圣经", roles)

        bible = script_step_pack("3")
        self.assertTrue(bible.startswith("# 下篇 · 阶段③全剧开发圣经"))
        self.assertNotIn("# 上篇 · 阶段② IP 与角色开发", bible)

        formal = script_step_pack("5")
        self.assertIn("## 六、阶段⑤正式剧本：原作忠实剧本主编", formal)
        self.assertIn("台词拟人化引擎", formal)
        self.assertIn("Dialogue Doctor", formal)
        self.assertNotIn("## 七、阶段⑥五席圆桌审核（摘要）", formal)
        self.assertNotIn("# 来源登记与源头处理", formal)
        self.assertNotIn("## 八、授权后精修或重写（04F）", formal)

        refined = script_step_pack("5", refine=True)
        self.assertIn("## 八、授权后精修或重写（04F）", refined)
        self.assertNotIn("## 九、全链禁止越界", refined)

        table = script_step_pack("6")
        self.assertTrue(table.startswith("# 五席圆桌审核制与叙事连续性"))

    def test_script_step_prompt_skips_the_decision_tree(self):
        brief = "剧本步：1\n成片时长秒：60\n成片画幅：16:9"
        pack = load_skill_pack(context=brief, stage="P1")
        self.assertNotIn("观众留存分集规划", pack)
        prompt = build_system_prompt(pack, brief=brief)
        self.assertIn("这一步的正文放在 text", prompt)
        self.assertNotIn("先于后面所有问题", prompt)
        self.assertNotIn("assets 数组", prompt)
        formal = build_system_prompt("原文", brief="剧本步：5")
        self.assertIn("正式剧本放在 script", formal)
        self.assertNotIn("先于后面所有问题", formal)
        ordinary = build_system_prompt("原文", brief="赛道：1")
        self.assertIn("先于后面所有问题", ordinary)

    def test_refine_pack_is_selected_from_the_brief(self):
        pack = load_skill_pack(context="剧本步：5\n精修原文：1", stage="P1")
        self.assertIn("## 八、授权后精修或重写（04F）", pack)
        plain = load_skill_pack(context="剧本步：5", stage="P1")
        self.assertNotIn("## 八、授权后精修或重写（04F）", plain)


class ScriptChainRunTest(unittest.TestCase):
    def _project(self, pipeline):
        return {
            "template": "short-drama-director",
            "brief": "独有的用户故事句子",
            "model": "deepseek-v4.1-flash",
            "progress": {"pipeline": pipeline},
        }

    def _writer(self, calls):
        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            calls.append(brief)
            if "任务：赛道分流" in brief:
                return {"text": "路线说明"}
            return {
                "title": "题",
                "script": "正式剧本正文",
                "internal": "圆桌意见",
                "partials": [
                    {"skill": "sd-script-engine/references/source-registration.md", "text": "第1步正文"},
                    {"skill": "sd-script-engine/references/story-bible-and-ip.md", "text": "第2步正文"},
                    {"skill": "sd-script-engine/references/roundtable-review.md", "text": "圆桌意见"},
                ],
            }

        return writer

    def test_six_steps_land_then_stop_at_the_script_gate(self):
        folder = Path(tempfile.mkdtemp())
        calls = []
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = execute(
                self._project({"action": "write", "current": "script", "accepted": [], "only": ""}),
                folder,
                {"duration_seconds": 60, "aspect": "16:9", "track": "1"},
                self._writer(calls),
                lambda *args, **kwargs: "pack",
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("任务：赛道分流", calls[0])
        self.assertIn("任务：剧本", calls[1])
        self.assertIn("独有的用户故事句子", calls[1])
        self.assertIn("用户原文", calls[1])
        self.assertNotIn("用户原文", calls[0])
        self.assertEqual((folder / "_内部_" / "路线.md").read_text(encoding="utf-8").strip(), "路线说明")
        self.assertEqual((folder / "_内部_" / "剧本链" / "02-IP与角色.md").read_text(encoding="utf-8").strip(), "第2步正文")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), "正式剧本正文")
        self.assertEqual((folder / "_内部_" / "剧本过程.md").read_text(encoding="utf-8").strip(), "圆桌意见")
        self.assertEqual(outcome["progress"]["phase"], "gate_script")
        self.assertEqual(outcome["progress"]["pipeline"]["action"], "wait")
        self.assertNotIn("script_from", outcome["progress"]["pipeline"])
        self.assertIn("继续", outcome["message"]["options"])

    def test_route_failure_stays_on_the_script_gate(self):
        folder = Path(tempfile.mkdtemp())

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            if "任务：赛道分流" in brief:
                raise RuntimeError("路线没有写完")
            return {"script": "不应写到"}

        outcome = execute(
            self._project({"action": "write", "current": "script", "accepted": [], "only": ""}),
            folder,
            {"duration_seconds": 60, "aspect": "16:9", "track": "1"},
            writer,
            lambda *args, **kwargs: "pack",
        )
        self.assertEqual(outcome["progress"]["phase"], "gate_script")
        self.assertIn("没写完", outcome["message"]["content"])
        self.assertIn("路线没有写完", outcome["message"]["content"])
        self.assertIn("继续", outcome["message"]["options"])

    def test_failed_check_reruns_only_the_script_and_the_table(self):
        folder = Path(tempfile.mkdtemp())
        calls = []
        state = {"n": 0}

        def check(folder, stage, locks, title):
            state["n"] += 1
            if state["n"] == 1:
                return False, "FAIL: 太短"
            return True, ""

        with patch("end.stage_runner.check_stage", check):
            execute(
                self._project({"action": "write", "current": "script", "accepted": [], "only": ""}),
                folder,
                {"duration_seconds": 60, "aspect": "16:9"},
                self._writer(calls),
                lambda *args, **kwargs: "pack",
            )
        self.assertEqual(len(calls), 3)
        self.assertIn("任务：剧本", calls[2])
        self.assertIn("机检没过", calls[2])
        self.assertIn("FAIL: 太短", calls[2])
        self.assertNotIn("精修原文：1", calls[2])
        self.assertIn("正式剧本正文", calls[2])

    def test_polish_reruns_the_script_with_the_revise_section(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("旧稿里有女主和胶囊。\n", encoding="utf-8")
        calls = []
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = execute(
                self._project(
                    {
                        "action": "rewrite",
                        "current": "script",
                        "accepted": [],
                        "only": "",
                        "passed": True,
                        "revise_note": "产品信息写清楚",
                    }
                ),
                folder,
                {"duration_seconds": 60, "aspect": "16:9"},
                self._writer(calls),
                lambda *args, **kwargs: "pack",
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("任务：剧本", calls[0])
        self.assertIn("精修原文：1", calls[0])
        self.assertIn("产品信息写清楚", calls[0])
        self.assertIn("旧稿里有女主和胶囊", calls[0])
        self.assertNotIn("独有的用户故事句子", calls[0])
        self.assertEqual(outcome["progress"]["phase"], "gate_script")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), "正式剧本正文")


class ScriptReplayTest(unittest.TestCase):
    def test_replaying_a_middle_step_continues_at_the_next_one(self):
        folder = Path(tempfile.mkdtemp())
        chain = folder / "_内部_" / "剧本链"
        chain.mkdir(parents=True)
        (chain / "01-来源登记.md").write_text("来源留下\n", encoding="utf-8")
        (chain / "02-IP与角色.md").write_text("角色留下\n", encoding="utf-8")
        (chain / "05-正式剧本.md").write_text("后面的剧本\n", encoding="utf-8")
        (folder / "_内部_" / "剧本.md").write_text("后面的剧本\n", encoding="utf-8")
        (folder / "_内部_" / "assets.md").write_text("旧资产\n", encoding="utf-8")
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "script", False, "角色简报", label="IP与角色", step="2")
            finish_trace(folder, {"text": "角色留下"})
            plan = restore_trace(folder, "1", {"track": "1"}, "")
        self.assertEqual(plan["script_from"], "3")
        self.assertEqual(plan["action"], "write")
        self.assertEqual(plan["current"], "script")
        self.assertEqual(plan["write_label"], "写故事圣经")
        self.assertNotIn("script", plan["accepted"])
        self.assertEqual((chain / "02-IP与角色.md").read_text(encoding="utf-8").strip(), "角色留下")
        self.assertTrue((chain / "01-来源登记.md").is_file())
        self.assertFalse((chain / "05-正式剧本.md").exists())
        self.assertFalse((folder / "_内部_" / "剧本.md").exists())
        self.assertFalse((folder / "_内部_" / "assets.md").exists())

        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            calls.append(brief)
            return {"title": "题", "script": "续写剧本", "internal": "续写圆桌"}

        project = {
            "template": "short-drama-director",
            "brief": "独有的用户故事句子",
            "progress": {
                "pipeline": {
                    "action": "write",
                    "current": "script",
                    "accepted": [],
                    "only": "",
                    "script_from": plan["script_from"],
                }
            },
        }
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            execute(project, folder, {"duration_seconds": 60, "aspect": "16:9"}, writer, lambda *args, **kwargs: "pack")
        self.assertEqual(len(calls), 1)
        self.assertIn("任务：剧本", calls[0])
        self.assertIn("角色留下", calls[0])
        self.assertNotIn("独有的用户故事句子", calls[0])
        self.assertNotIn("剧本步：1", calls[0])
        self.assertEqual((chain / "01-来源登记.md").read_text(encoding="utf-8").strip(), "来源留下")

    def test_replaying_the_table_waits_on_the_script_gate(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "_内部_").mkdir()
        (folder / "_内部_" / "剧本.md").write_text("已有剧本\n", encoding="utf-8")
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
            begin_trace(folder, "script", False, "圆桌简报", label="圆桌", step="6")
            finish_trace(folder, {"text": "圆桌留下", "internal": "圆桌留下"})
            plan = restore_trace(folder, "1", {}, "")
        self.assertEqual(plan["action"], "wait")
        self.assertEqual(plan["current"], "script")
        self.assertEqual(plan["next"], "")
        self.assertNotIn("script_from", plan)
        self.assertIn("script", plan["accepted"])
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), "已有剧本")
        self.assertEqual((folder / "_内部_" / "剧本过程.md").read_text(encoding="utf-8").strip(), "圆桌留下")
        self.assertEqual((folder / "_内部_" / "剧本链" / "06-圆桌.md").read_text(encoding="utf-8").strip(), "圆桌留下")


class ScriptReplayRpcTest(unittest.TestCase):
    def test_replay_rpc_keeps_the_next_script_step(self):
        import end.db as db
        from end.server import rpc_replay
        from tests.test_director_pipeline import make_project

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        try:
            project = make_project(
                "abc123abc123",
                status="gate",
                stage="剧本门",
                progress={
                    "percent": 20,
                    "phase": "gate_script",
                    "message": "剧本门",
                    "pipeline": {
                        "action": "wait",
                        "current": "script",
                        "accepted": ["script"],
                        "passed": True,
                        "only": "",
                    },
                },
            )
            folder = db.project_dir(project["id"])
            with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "1"}):
                begin_trace(folder, "script", False, "角色简报", label="IP与角色", step="2")
                saved = finish_trace(folder, {"text": "角色留下"})
                with patch("end.server.submit_agent_task") as submit:
                    view = rpc_replay({"id": project["id"], "trace_id": saved["id"]})
            self.assertEqual(submit.call_args.args[0].kind, "write")
            self.assertEqual(view["progress"]["pipeline"]["current"], "script")
            self.assertEqual(view["progress"]["pipeline"]["action"], "write")
            self.assertEqual(view["progress"]["pipeline"]["script_from"], "3")
            self.assertNotIn("script", view["progress"]["pipeline"]["accepted"])
            record = json.loads((folder / "debug" / "trace" / "0001.json").read_text(encoding="utf-8"))
            self.assertEqual(record["label"], "IP与角色")
            self.assertEqual(record["step"], "2")
        finally:
            conn = db._CONN
            db.DATA, db.DB_PATH, db._CONN = old
            if conn is not None and conn is not old[2]:
                conn.close()
            tmp.cleanup()


class RepairPromptTest(unittest.TestCase):
    def test_repair_pack_drops_the_banner_and_the_brief_drops_the_story(self):
        from AI.skills.registry import load_skill_pack

        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("旧稿里有女主。\n", encoding="utf-8")
        briefs = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            briefs.append(brief)
            return {"title": "题", "script": "改过的剧本", "internal": "圆桌意见"}

        project = {
            "template": "short-drama-director",
            "brief": "独有的用户故事句子",
            "model": "deepseek-v4.1-flash",
            "progress": {
                "pipeline": {
                    "action": "rewrite",
                    "current": "script",
                    "accepted": [],
                    "only": "",
                    "passed": True,
                    "revise_note": "产品信息写清楚",
                }
            },
        }
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            execute(project, folder, {"duration_seconds": 60, "aspect": "16:9"}, writer, lambda *args, **kwargs: "pack")
        self.assertEqual(len(briefs), 1)
        self.assertNotIn("用户原文", briefs[0])
        self.assertNotIn("独有的用户故事句子", briefs[0])
        self.assertIn("精修原文：1", briefs[0])
        self.assertIn("旧稿里有女主", briefs[0])
        from AI.skills.registry import suite_original

        original = suite_original("sd-script-engine/references/source-registration.md")
        self.assertIn("精编自《灵境AI剧本改编 skill》", original)
        stripped = suite_original("sd-script-engine/references/source-registration.md", repair=True)
        self.assertNotIn("精编自《灵境AI剧本改编 skill》", stripped)
        first = load_skill_pack(context="剧本步：1", stage="P1")
        self.assertIn("精编自《灵境AI剧本改编 skill》", first)

    def test_script_step_text_is_accepted_and_saved(self):
        from AI.agent.agent import Agent
        from AI.script import _extract_json

        parsed = _extract_json('{"text":"来源登记"}', "剧本步：1")
        self.assertEqual(parsed["text"], "来源登记")
        with self.assertRaises(ValueError):
            _extract_json('{"text":"不是正式剧本"}', "剧本步：5")
        formal = _extract_json('{"script":"正式剧本正文"}', "剧本步：5")
        self.assertEqual(formal["script"], "正式剧本正文")

        agent = Agent()
        request = agent._rewrite_request(
            {
                "brief": "剧本步：1\n用户原文\n独有的用户故事句子\n> 精编自《灵境AI剧本改编 skill》（6阶段编剧链）",
                "template": "short-drama-director",
            },
            '{"text":"来源登记"}',
        )
        blob = "\n".join(item["content"] for item in request["messages"])
        self.assertNotIn("独有的用户故事句子", blob)
        self.assertNotIn("精编自《灵境AI剧本改编 skill》", blob)
        self.assertIn("放在 text", blob)
        self.assertIn("来源登记", blob)

        folder = Path(tempfile.mkdtemp())

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            if "任务：赛道分流" in brief:
                return {"text": "路线"}
            return {
                "title": "题",
                "script": "正式剧本正文",
                "partials": [{"skill": "sd-script-engine/references/source-registration.md", "text": "来源登记"}],
            }

        project = {
            "template": "short-drama-director",
            "brief": "独有的用户故事句子",
            "progress": {"pipeline": {"action": "write", "current": "script", "accepted": [], "only": ""}},
        }
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            execute(project, folder, {"duration_seconds": 60, "aspect": "16:9"}, writer, lambda *args, **kwargs: "pack")
        saved = (folder / "_内部_" / "剧本链" / "01-来源登记.md").read_text(encoding="utf-8").strip()
        self.assertEqual(saved, "来源登记")

    def test_batched_internal_body_is_kept_for_a_script_step(self):
        from AI.agent.batch import catalog_request, merge_specs
        from end.stage_runner import _step_body

        merged = merge_specs([{"internal": "IP 正文"}])
        self.assertEqual(_step_body("2", merged), "IP 正文")
        request = catalog_request({"brief": "剧本步：2", "model_name": "m"}, "截断")
        self.assertIn("field 用 text", request["messages"][0]["content"])
