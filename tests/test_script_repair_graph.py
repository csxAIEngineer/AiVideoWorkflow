"""Offline contract for the script repair StateGraph. Fakes are not accuracy."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.script_repair import load_candidate, official_script, promote_candidate
from end.script_review import content_hash, save_reviews
from end.server import RpcError, handle_agent_task, rpc_send_message

_REQUIREMENTS = "古塔长发困人。柳公子手掌和衣袖粘着发束悬在半空。阿青在塔顶。"
_SCRIPT = "古塔里长发把人困住。柳公子的手掌和衣袖粘在发束上，人悬在半空。阿青站在塔顶。"
_REACH = "柳公子已经悬在发束旁边，手掌就贴着发根。"


def _issue(severity="block", revision="可交代他已经够得到发根，不要另加坠落缓冲"):
    return {
        "location": "塔外",
        "evidence": "手掌和衣袖粘在发束上",
        "category": "causal",
        "severity": severity,
        "reason": "洗发没有交代怎么够到",
        "owner": "script",
        "revision": revision,
    }


class GraphShapeTest(unittest.TestCase):
    def test_graph_has_four_separate_nodes(self):
        from AI.qwen_api.script_repair_graph import build_script_repair_graph

        graph = build_script_repair_graph(lambda _node, _messages: "{}")
        self.assertEqual(set(graph.nodes), {"locate", "plan", "revise", "review"})
        edges = {(item[0], item[1]) for item in graph.edges}
        self.assertIn(("__start__", "locate"), edges)
        self.assertIn(("locate", "plan"), edges)
        self.assertIn(("plan", "revise"), edges)
        self.assertIn(("revise", "review"), edges)

    def test_compiled_graph_invokes_four_calls_and_passes_state(self):
        from importlib.metadata import version

        from AI.qwen_api.script_repair_graph import compile_script_repair_graph

        seen = []

        def complete(node, messages):
            seen.append((node, messages))
            if node == "locate":
                return json.dumps({"locations": [{"issue_id": "S01", "paragraphs": ["人悬在半空"]}]}, ensure_ascii=False)
            if node == "plan":
                return json.dumps({"changes": [{"issue_id": "S01", "direction": "补触达", "linked": True}], "constraints": []}, ensure_ascii=False)
            if node == "revise":
                return json.dumps({"script": _SCRIPT + _REACH, "added": [{"issue_id": "S01", "text": _REACH, "basis": "悬在半空"}]}, ensure_ascii=False)
            if node == "review":
                return json.dumps({"passed": True, "theme_ok": True, "resolutions": [{"id": "S01", "resolved": True, "evidence": _REACH}], "unsupported": [], "new_contradictions": []}, ensure_ascii=False)
            raise AssertionError(node)

        app = compile_script_repair_graph(complete)
        state = app.invoke({
            "requirements": _REQUIREMENTS,
            "script": _SCRIPT,
            "issues": [{"id": "S01", "severity": "block", "evidence": "手掌和衣袖粘在发束上"}],
            "model": "fake-model",
            "calls": [],
            "status": "",
        })
        self.assertTrue(version("langgraph"))
        self.assertEqual([item[0] for item in seen], ["locate", "plan", "revise", "review"])
        self.assertEqual(state["calls"], ["locate", "plan", "revise", "review"])
        self.assertIn("人悬在半空", seen[1][1][1]["content"])
        self.assertIn("补触达", seen[2][1][1]["content"])
        self.assertIn(_REACH, seen[3][1][1]["content"])
        self.assertIn("独立审稿", seen[3][1][0]["content"])
        self.assertEqual(state["candidate"], _SCRIPT + _REACH)
        self.assertTrue(state["review_notes"]["passed"])


class RepairContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.calls = []
        self.mode = "good"
        self._patches = [
            patch("subprocess.Popen", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client._spawn", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client.call", side_effect=AssertionError("offline: no rpc")),
            patch("end.ai_client.ask_text", side_effect=AssertionError("offline: no model")),
            patch("end.ai_client.template_render", return_value={}),
            patch("end.server.normalize_model", side_effect=lambda value=None: value or "fake-model"),
            patch("end.server.model_label", side_effect=lambda _value=None: "fake"),
            patch("end.server.normalize_template", side_effect=lambda value=None: value or "short-drama-director"),
            patch("end.server.template_label", side_effect=lambda _value=None: "短剧"),
            patch("end.server.submit_agent_task", side_effect=self._capture),
        ]
        for item in self._patches:
            item.start()
        self.tasks = []

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _capture(self, task):
        self.tasks.append(task)
        return True

    def _complete(self, node, messages):
        self.calls.append((node, messages))
        if node == "locate":
            if self.mode == "bad-locate":
                return "这不是 JSON"
            return json.dumps({"locations": [{"issue_id": "S01", "paragraphs": ["人悬在半空", "阿青站在塔顶"]}]}, ensure_ascii=False)
        if node == "plan" and self.mode == "bad-plan":
            return "方案坏了"
        if node == "plan":
            return json.dumps({
                "changes": [{"issue_id": "S01", "direction": "只补足已经悬在旁边的手怎么碰到发根", "linked": True}],
                "constraints": [{"text": "静态困境不可移动", "quote": "静态困境不可移动"}],
            }, ensure_ascii=False)
        if node == "revise":
            return self._revise()
        if node == "review":
            if self.mode == "boom":
                raise RuntimeError("审稿调用失败")
            return self._review()
        raise AssertionError(node)

    def _revise(self):
        if self.mode == "invent":
            text = _SCRIPT + "他捧起石槽积水洗发根。塔底铺着软垫。"
            added = [{"issue_id": "S01", "text": "塔底铺着软垫", "basis": ""}]
        elif self.mode == "meta":
            text = _SCRIPT + "机检没过。只改点名的条目。"
            added = []
        elif self.mode == "preference":
            text = _SCRIPT + "天空降下净水自动把头发洗干净。"
            added = [{"issue_id": "S01", "text": "天空降下净水自动把头发洗干净", "basis": "静态困境不可移动"}]
        else:
            text = _SCRIPT + _REACH
            added = [{"issue_id": "S01", "text": _REACH, "basis": "悬在半空"}]
        return json.dumps({"script": text, "added": added}, ensure_ascii=False)

    def _review(self):
        passed = self.mode == "good"
        evidence = _REACH if passed else ""
        return json.dumps({
            "passed": passed,
            "theme_ok": passed,
            "resolutions": [{"id": "S01", "resolved": passed, "evidence": evidence}],
            "unsupported": [] if passed else ["塔底软垫"],
            "new_contradictions": [],
        }, ensure_ascii=False)

    def _project(self, severity="block"):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": "a1b2c3d4e511",
            "brief": _REQUIREMENTS,
            "messages": [{"role": "user", "content": _REQUIREMENTS}],
            "title": "古塔",
            "status": "gate",
            "stage": "剧本",
            "script": _SCRIPT,
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "events": [],
            "tasks": [],
            "progress": {
                "percent": 40,
                "phase": "gate_script",
                "message": "语义审查",
                "lock_machine": True,
                "pipeline": {
                    "current": "script",
                    "action": "wait",
                    "passed": True,
                    "semantic_scope": "script",
                    "semantic_status": "blocked" if severity == "block" else "suspect",
                    "repair": "",
                    "accepted": [],
                },
                "question": {
                    "id": "script",
                    "kind": "single",
                    "reply": "语义审查未通过",
                    "options": ["根据建议修改"],
                    "cards": [],
                },
            },
            "cloud_log": "",
            "prompt_id": "",
            "video_file": "",
            "model": "fake-model",
            "template": "short-drama-director",
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        })
        folder = project_dir("a1b2c3d4e511")
        internal = folder / "_内部_"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": _SCRIPT}, ensure_ascii=False), encoding="utf-8")
        save_reviews(folder, {"script": {"status": "blocked", "issues": [_issue(severity)], "script_hash": content_hash(_SCRIPT), "context_hash": "x"}})
        return folder

    def _run(self):
        view = rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(self.tasks[-1].kind, "script_repair")
        self.assertEqual(view["progress"]["pipeline"].get("repair"), "")
        with patch("end.script_repair.default_complete", side_effect=lambda _model: self._complete):
            handle_agent_task(self.tasks[-1])
        return project_dir("a1b2c3d4e511")

    def test_route_and_independent_calls_keep_the_official_script(self):
        folder = self._project()
        before = (folder / "_内部_" / "剧本.md").read_text(encoding="utf-8")
        folder = self._run()
        row = load_candidate(folder)
        self.assertEqual([item[0] for item in self.calls], ["locate", "plan", "revise", "review"])
        review_system = self.calls[-1][1][0]["content"]
        revise_system = self.calls[2][1][0]["content"]
        self.assertIn("独立审稿", review_system)
        self.assertNotIn("独立审稿", revise_system)
        self.assertIn(_REQUIREMENTS, self.calls[0][1][1]["content"])
        self.assertIn(_SCRIPT, self.calls[0][1][1]["content"])
        self.assertEqual(row["status"], "passed")
        self.assertIn(_REACH, row["candidate"])
        self.assertIn("人悬在半空", row["candidate"])
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"), before)
        self.assertEqual(row["plan"]["uncertain"][0]["text"], "静态困境不可移动")
        self.assertEqual(row["plan"]["sourced"], [])
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "gate")
        self.assertIn("采用这版剧本", read_project("a1b2c3d4e511")["progress"]["question"]["options"])

    def test_invented_fix_preference_and_meta_do_not_replace_the_script(self):
        for mode in ("invent", "preference", "meta", "boom"):
            self.calls = []
            self.mode = mode
            folder = self._project("preference" if mode == "preference" else "block")
            if mode == "preference":
                save_reviews(folder, {"script": {"status": "suspect", "issues": [_issue("preference")], "script_hash": content_hash(_SCRIPT), "context_hash": "x"}})
            before = official_script(folder)
            self._run()
            row = load_candidate(folder)
            self.assertNotEqual(row["status"], "passed", mode)
            self.assertEqual(official_script(folder), before, mode)
            self.assertNotIn("塔底软垫", official_script(folder))

    def test_confirm_promotes_and_a_stale_candidate_does_not(self):
        from end.script_review import set_reviewer

        set_reviewer(lambda _messages: '{"issues":[]}')
        try:
            folder = self._project()
            self._run()
            view = rpc_send_message({"id": "a1b2c3d4e511", "text": "继续"})
            self.assertEqual(official_script(folder), _SCRIPT)
            self.assertEqual(view["status"], "gate")
            view = rpc_send_message({"id": "a1b2c3d4e511", "text": "采用这版剧本"})
            self.assertEqual(view["status"], "gate")
            self.assertIn(_REACH, official_script(folder))
            mutated = official_script(folder) + "又改了一句。"
            (folder / "_内部_" / "剧本.md").write_text(mutated + "\n", encoding="utf-8")
            ok, reason = promote_candidate(folder, _REQUIREMENTS)
            self.assertFalse(ok)
            self.assertIn("原稿已经变了", reason)
            self.assertEqual(official_script(folder), mutated)
            self.assertNotEqual(content_hash(mutated), load_candidate(folder)["source_hash"])
        finally:
            set_reviewer(None)

    def test_retry_stops_at_the_attempt_limit(self):
        self.mode = "invent"
        folder = self._project()
        self._run()
        view = rpc_send_message({"id": "a1b2c3d4e511", "text": "再修一次"})
        self.assertEqual(self.tasks[-1].kind, "script_repair")
        with patch("end.script_repair.default_complete", side_effect=lambda _model: self._complete):
            handle_agent_task(self.tasks[-1])
        options = read_project("a1b2c3d4e511")["progress"]["question"]["options"]
        self.assertNotIn("再修一次", options)
        self.assertIn("保持原稿", options)
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual(len(self.tasks), 2)
        del view

    def test_meta_label_cannot_pass_on_a_real_basis(self):
        from end.script_repair import judge_candidate

        original = "阿青丝站在塔顶。"
        candidate = original + "【机检阻断修复·物理因果】滴落后用石槽积水冲洗。"
        status, reason = judge_candidate(
            "阿青丝在塔顶。",
            original,
            candidate,
            [{"id": "S01", "severity": "block"}],
            [{"issue_id": "S01", "text": "【机检阻断修复·物理因果】滴落后用石槽积水冲洗", "basis": "阿青丝"}],
            {
                "passed": True,
                "theme_ok": True,
                "resolutions": [{"id": "S01", "resolved": True, "evidence": "滴落后用石槽积水冲洗"}],
                "unsupported": [],
                "new_contradictions": [],
            },
        )
        self.assertEqual(status, "blocked")
        self.assertIn("机检", reason)

    def test_attempt_budget_is_shared_and_keeping_the_script_does_not_reset_it(self):
        from end.script_repair import _save_candidate, budget_identity, issue_rows, load_candidate

        folder = self._project()
        identity = budget_identity(official_script(folder), _REQUIREMENTS, issue_rows(folder))
        _save_candidate(folder, {"status": "blocked", "attempts": 2, "candidate": "", **identity})
        view = rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(self.tasks, [])
        self.assertIn("上限", view["messages"][-1]["content"])
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        project = read_project("a1b2c3d4e511")
        project["progress"]["question"] = {"id": "script_candidate", "kind": "single", "reply": "候选", "options": ["保持原稿"], "cards": []}
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        write_project(project)
        kept = rpc_send_message({"id": "a1b2c3d4e511", "text": "保持原稿"})
        self.assertEqual(kept["progress"]["question"]["options"], [])
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        again = rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(self.tasks, [])
        self.assertEqual(official_script(folder), _SCRIPT)
        (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "改了一处。\n", encoding="utf-8")
        fresh = read_project("a1b2c3d4e511")
        fresh["progress"]["question"]["options"] = ["根据建议修改"]
        fresh["progress"]["pipeline"]["semantic_scope"] = "script"
        fresh["progress"]["pipeline"]["semantic_status"] = "blocked"
        fresh["status"] = "gate"
        fresh["progress"]["phase"] = "gate_script"
        write_project(fresh)
        opened = rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(self.tasks[-1].kind, "script_repair")
        self.assertEqual(opened["status"], "writing")

    def test_bad_locate_or_plan_stops_and_missing_requirements_ask_a_person(self):
        self.mode = "bad-locate"
        folder = self._project()
        self._run()
        row = load_candidate(folder)
        self.assertEqual([item[0] for item in self.calls], ["locate"])
        self.assertEqual(row["status"], "blocked")
        self.assertIn("没有有效定位", row["reason"])
        self.assertEqual(official_script(folder), _SCRIPT)
        project = read_project("a1b2c3d4e511")
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"]["semantic_scope"] = "script"
        project["progress"]["pipeline"]["semantic_status"] = "blocked"
        project["progress"]["question"] = {"id": "script", "kind": "single", "reply": "审", "options": ["根据建议修改"], "cards": []}
        write_project(project)
        self.calls = []
        self.mode = "bad-plan"
        self._run()
        self.assertEqual([item[0] for item in self.calls], ["locate", "plan"])
        self.assertIn("不是合格 JSON", load_candidate(folder)["reason"])
        project = read_project("a1b2c3d4e511")
        project["brief"] = ""
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"]["semantic_scope"] = "script"
        project["progress"]["pipeline"]["semantic_status"] = "blocked"
        project["progress"]["question"] = {"id": "script", "options": ["根据建议修改"], "reply": "审", "kind": "single", "cards": []}
        write_project(project)
        before = len(self.tasks)
        view = rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(len(self.tasks), before)
        self.assertIn("需要人工确认", view["messages"][-1]["content"])
        self.assertNotIn("theme_ok", json.dumps(load_candidate(folder), ensure_ascii=False))

    def test_resume_restores_the_candidate_gate_without_a_generic_write(self):
        from end.script_repair import _save_candidate, budget_identity, issue_rows
        from end.server import resume_agent_tasks

        folder = self._project()
        identity = budget_identity(official_script(folder), _REQUIREMENTS, issue_rows(folder))
        _save_candidate(folder, {"status": "passed", "candidate": _SCRIPT + _REACH, "attempts": 1, **identity})
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"]["action"] = "script_repair"
        write_project(project)
        resume_agent_tasks()
        self.assertEqual(self.tasks, [])
        saved = read_project("a1b2c3d4e511")
        self.assertEqual(saved["status"], "gate")
        self.assertIn("采用这版剧本", saved["progress"]["question"]["options"])
        self.assertEqual(official_script(folder), _SCRIPT)
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"]["action"] = "script_repair"
        write_project(project)
        (folder / "_内部_" / "剧本候选.json").unlink()
        resume_agent_tasks()
        self.assertEqual([task.kind for task in self.tasks], ["script_repair"])

    def test_new_identity_starts_attempts_at_one_after_a_real_run(self):
        from end.script_repair import _save_candidate, budget_identity, issue_rows, load_candidate, run_script_repair

        def open_gate():
            project = read_project("a1b2c3d4e511")
            project["status"] = "gate"
            project["progress"]["phase"] = "gate_script"
            project["progress"]["pipeline"]["semantic_scope"] = "script"
            project["progress"]["pipeline"]["semantic_status"] = "blocked"
            project["progress"]["pipeline"]["action"] = "wait"
            project["progress"]["question"] = {
                "id": "script",
                "kind": "single",
                "reply": "审",
                "options": ["根据建议修改"],
                "cards": [],
            }
            write_project(project)

        for kind in ("script", "requirements", "issues"):
            self.calls = []
            self.tasks = []
            self.mode = "invent"
            folder = self._project()
            identity = budget_identity(official_script(folder), _REQUIREMENTS, issue_rows(folder))
            _save_candidate(folder, {"status": "blocked", "attempts": 2, "candidate": "", **identity})
            if kind == "script":
                (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "原稿换了。\n", encoding="utf-8")
            elif kind == "requirements":
                project = read_project("a1b2c3d4e511")
                project["brief"] = _REQUIREMENTS + "要求换了。"
                write_project(project)
            else:
                save_reviews(folder, {"script": {"status": "blocked", "issues": [_issue("block", "问题换了")], "script_hash": "x", "context_hash": "x"}})
            open_gate()
            self._run()
            self.assertEqual(load_candidate(folder)["attempts"], 1, kind)
            self.assertEqual([item[0] for item in self.calls], ["locate", "plan", "revise", "review"], kind)
            view = rpc_send_message({"id": "a1b2c3d4e511", "text": "再修一次"})
            self.assertEqual(self.tasks[-1].kind, "script_repair")
            with patch("end.script_repair.default_complete", side_effect=lambda _model: self._complete):
                handle_agent_task(self.tasks[-1])
            self.assertEqual(load_candidate(folder)["attempts"], 2, kind)
            before_calls = len(self.calls)
            before_tasks = len(self.tasks)
            rpc_send_message({"id": "a1b2c3d4e511", "text": "再修一次"})
            kept = rpc_send_message({"id": "a1b2c3d4e511", "text": "保持原稿"})
            self.assertNotIn("根据建议修改", kept["progress"]["question"]["options"])
            self.assertEqual(load_candidate(folder)["attempts"], 2, kind)
            open_gate()
            rpc_send_message({"id": "a1b2c3d4e511", "text": "根据建议修改"})
            requirements = read_project("a1b2c3d4e511").get("brief") or ""
            run_script_repair(folder, requirements, self._complete, model="fake-model")
            self.assertEqual(len(self.calls), before_calls, kind)
            self.assertEqual(len(self.tasks), before_tasks, kind)
            self.assertEqual(load_candidate(folder)["attempts"], 2, kind)
            del view

    def test_promote_waits_and_rejects_a_concurrent_edit(self):
        import threading
        from end.render_loop import project_lock
        from end.script_repair import _save_candidate, budget_identity, issue_rows

        folder = self._project()
        identity = budget_identity(official_script(folder), _REQUIREMENTS, issue_rows(folder))
        _save_candidate(folder, {"status": "passed", "candidate": _SCRIPT + _REACH, "attempts": 1, **identity})
        started = threading.Event()

        def editor():
            with project_lock("a1b2c3d4e511"):
                started.set()
                import time
                time.sleep(0.2)
                (folder / "_内部_" / "剧本.md").write_text("并发改过的原稿。\n", encoding="utf-8")

        threading.Thread(target=editor).start()
        self.assertTrue(started.wait(2))
        ok, reason = promote_candidate(folder, _REQUIREMENTS, project_id="a1b2c3d4e511")
        self.assertFalse(ok)
        self.assertIn("原稿已经变了", reason)
        self.assertEqual(official_script(folder), "并发改过的原稿。")

    def test_format_rewrite_guard_does_not_run_the_repair_graph(self):
        from end.stage_runner import execute

        called = []
        folder = self._project()
        project = read_project("a1b2c3d4e511")
        project["progress"]["pipeline"]["action"] = "script_repair"
        outcome = execute(project, folder, {}, lambda *args, **kwargs: called.append(args), lambda *args, **kwargs: "")
        self.assertEqual(called, [])
        self.assertEqual(outcome["terminal"], "gate")
        self.assertEqual(official_script(folder), _SCRIPT)


if __name__ == "__main__":
    unittest.main()
