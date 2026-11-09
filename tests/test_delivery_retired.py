"""Delivery semantic review is retired. Script review and technical checks stay."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.script_review import load_reviews, save_reviews, set_reviewer
from end.server import RpcError, _execute_render, resume_agent_tasks, resume_rendering_projects, rpc_call

_SCRIPT = "甲在车站等车。"


class DeliveryRetiredTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.reviews = []
        self.renders = []
        self._patches = [
            patch("subprocess.Popen", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client._spawn", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client.call", side_effect=AssertionError("offline: no rpc")),
            patch("end.ai_client.ask_text", side_effect=AssertionError("offline: no model")),
            patch("end.server.template_policy", return_value=None),
            patch("end.ai_client.template_render", return_value={"image": "", "image_from": "", "video_lock": "", "video_next": "", "video_tail": "", "video_from": ""}),
            patch("end.server.normalize_model", side_effect=lambda value: value or "fake-model"),
            patch("end.server.model_label", return_value="fake"),
            patch("end.server.normalize_template", side_effect=lambda value: value or "short-drama-director"),
            patch("end.server.template_label", return_value="fake"),
            patch("end.server.start_render_thread", side_effect=self._render),
        ]
        for item in self._patches:
            item.start()
        set_reviewer(self._review)

    def tearDown(self):
        set_reviewer(None)
        from end.server import agent_queue

        agent_queue().stop()
        for item in reversed(self._patches):
            item.stop()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _review(self, _messages):
        self.reviews.append(1)
        raise AssertionError("downstream semantic model")

    def _render(self, *_args, **_kwargs):
        self.renders.append(1)
        return True

    def _gate(self, status_name):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": "a1b2c3d4e511",
            "brief": "等车。",
            "messages": [{
                "role": "assistant",
                "content": "出片语义审查未通过",
                "options": ["再审一次", "接受可疑项并出片", "根据建议修改"],
            }],
            "title": "出片",
            "status": "gate",
            "stage": "出片审查",
            "script": _SCRIPT,
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "events": [],
            "tasks": [],
            "progress": {
                "percent": 80,
                "phase": "gate_feed",
                "message": "出片审查",
                "pipeline": {
                    "current": "feed",
                    "action": "wait",
                    "passed": False,
                    "semantic_scope": "delivery",
                    "semantic_status": status_name,
                },
                "question": {
                    "id": "feed",
                    "kind": "single",
                    "reply": "出片语义审查未通过",
                    "options": ["再审一次", "接受可疑项并出片", "根据建议修改"],
                    "cards": [],
                },
            },
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
        (folder / "spec.json").write_text(json.dumps({
            "script": _SCRIPT,
            "aspect_ratio": "9:16",
            "assets": [{"id": "A1", "title": "甲", "prompt": "车站"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": "她离开", "images": ["A1"]}],
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "locks.json").write_text(json.dumps({"aspect": "9:16", "model": "h3"}), encoding="utf-8")
        from end.script_review import content_hash, story_context

        script_hash = content_hash(_SCRIPT)
        context_hash = content_hash(story_context(folder, "等车。"))
        save_reviews(folder, {
            "script": {"status": "passed", "issues": [], "script_hash": script_hash, "context_hash": context_hash},
            "confirmed_script_hash": script_hash,
            "confirmed_context_hash": context_hash,
            "delivery": {"status": status_name, "reason": "旧出片审查", "issues": [{"evidence": "旧问题", "owner": "script"}]},
        })
        return folder

    def test_get_keeps_historical_delivery_and_names_explicit_render(self):
        for name in ("blocked", "suspect", "failed", "pending"):
            folder = self._gate(name)
            view = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
            self.assertEqual(view["status"], "gate")
            self.assertTrue(view["can_reply"])
            self.assertEqual(view["semantic_scope"], "delivery")
            self.assertEqual(view["semantic_status"], name)
            self.assertEqual(view["question"]["options"], ["再审一次", "接受可疑项并出片", "根据建议修改"])
            self.assertEqual(view["delivery_semantic_review"], "retired")
            self.assertFalse(view["delivery_blocks_render"])
            self.assertEqual(view["render_recovery"], "projects.render")
            self.assertEqual(view["generation_hold"], "")
            self.assertEqual(view["option_effects"]["再审一次"]["effect"], "reject")
            self.assertEqual(view["option_effects"]["接受可疑项并出片"]["reason"], "delivery_review_retired")
            self.assertEqual(view["option_effects"]["根据建议修改"]["effect"], "reject")
            self.assertFalse(view["option_effects"]["根据建议修改"]["render"])
            for label in ("继续", "接受", "确认", "*"):
                self.assertEqual(view["option_effects"][label]["reason"], "delivery_review_retired")
                self.assertEqual(view["option_effects"][label]["recovery"], "projects.render")
                self.assertFalse(view["option_effects"][label]["render"])
            self.assertEqual(view["semantic"]["delivery_history_status"], name)
            self.assertEqual(view["semantic"]["delivery_status"], name)
            self.assertEqual(view["semantic"]["delivery_semantic_review"], "retired")
            self.assertFalse(view["semantic"]["delivery_blocks_render"])
            self.assertEqual(load_reviews(folder)["delivery"]["status"], name)
            self.assertEqual(load_reviews(folder)["delivery"]["issues"][0]["evidence"], "旧问题")
            self.assertEqual(self.reviews, [])
            self.assertEqual(self.renders, [])

    def test_old_delivery_buttons_do_not_review_or_render(self):
        folder = self._gate("blocked")
        for text in ("再审一次", "接受可疑项并出片", "根据建议修改"):
            with self.assertRaises(RpcError) as caught:
                rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": text})
            self.assertEqual(caught.exception.data["reason"], "delivery_review_retired")
            self.assertFalse(caught.exception.data["render"])
            self.assertEqual(caught.exception.data["recovery"], "projects.render")
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "blocked")
        self.assertEqual(load_reviews(folder)["delivery"]["issues"][0]["evidence"], "旧问题")
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "gate")
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_rendering_projects(), 0)

    def test_machine_revise_is_not_rejected_as_delivery_semantic(self):
        self._gate("blocked")
        project = read_project("a1b2c3d4e511")
        project["progress"]["phase"] = "gate_machine"
        project["progress"]["pipeline"]["check"] = "machine"
        project["progress"]["pipeline"]["semantic_scope"] = "delivery"
        project["progress"]["pipeline"]["current"] = "feed"
        project["progress"]["pipeline"]["passed"] = False
        write_project(project)
        queued = []

        def submit(task):
            queued.append(task.kind)
            return True

        shown = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(shown["option_effects"]["根据建议修改"]["effect"], "technical_rewrite")
        self.assertEqual(shown["option_effects"]["再审一次"]["reason"], "delivery_review_retired")
        with self.assertRaises(RpcError) as caught:
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "再审一次"})
        self.assertEqual(caught.exception.data["reason"], "delivery_review_retired")
        with patch("end.server.submit_agent_task", side_effect=submit):
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])
        self.assertIn("write", queued)

    def test_script_rereview_still_calls_the_script_model(self):
        self._gate("blocked")
        project = read_project("a1b2c3d4e511")
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"] = {
            "current": "script",
            "action": "wait",
            "passed": False,
            "semantic_scope": "script",
            "semantic_status": "failed",
        }
        project["progress"]["question"] = {
            "id": "script",
            "kind": "single",
            "reply": "再审剧本",
            "options": ["再审一次"],
            "cards": [],
        }
        write_project(project)

        def reviewer(_messages):
            self.reviews.append("script")
            return json.dumps({"issues": []}, ensure_ascii=False)

        set_reviewer(reviewer)
        with patch("end.server.submit_agent_task", return_value=True):
            view = rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "再审一次"})
        self.assertEqual(self.reviews, ["script"])
        self.assertEqual(self.renders, [])
        shown = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(shown["semantic_scope"], "script")
        self.assertNotIn("再审一次", shown["option_effects"])
        self.assertEqual(load_reviews(project_dir("a1b2c3d4e511"))["delivery"]["status"], "blocked")
        self.assertNotEqual(view.get("status"), "rendering")

    def test_explicit_render_checks_technology_and_refresh_does_not_start_it(self):
        folder = self._gate("suspect")
        rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_rendering_projects(), 0)
        self.assertEqual(self.renders, [])
        self.assertEqual(self.reviews, [])
        view = rpc_call("projects.render", {"id": "a1b2c3d4e511"})
        self.assertEqual(self.renders, [1])
        self.assertEqual(self.reviews, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "suspect")
        self.assertIn("generation_technical", load_reviews(folder))
        self.assertNotEqual(view["status"], "gate")

    def test_explicit_render_still_blocks_when_prompts_are_missing(self):
        folder = self._gate("failed")
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        spec["assets"] = []
        spec["shots"] = []
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(RpcError) as caught:
            rpc_call("projects.render", {"id": "a1b2c3d4e511"})
        self.assertEqual(caught.exception.data["reason"], "technical_block")
        self.assertFalse(caught.exception.data["render"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.reviews, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "failed")

    def test_technical_failure_inside_render_does_not_become_semantic_gate(self):
        folder = self._gate("pending")
        state = load_reviews(folder)
        before = json.dumps(state["delivery"], ensure_ascii=False, sort_keys=True)
        state.pop("confirmed_script_hash", None)
        state.pop("confirmed_context_hash", None)
        save_reviews(folder, state)
        project = read_project("a1b2c3d4e511")
        project["status"] = "rendering"
        write_project(project)
        _execute_render("a1b2c3d4e511")
        view = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(view["status"], "gate")
        self.assertEqual(view["generation_hold"], "technical")
        self.assertEqual(view["semantic_scope"], "")
        self.assertEqual(view["progress"]["phase"], "gate_machine")
        self.assertTrue(view["can_reply"])
        self.assertEqual(view["render_recovery"], "projects.render")
        self.assertEqual(view["option_effects"]["再审一次"]["reason"], "technical_hold")
        self.assertEqual(view["option_effects"]["根据建议修改"]["effect"], "technical_rewrite")
        self.assertEqual(view["option_effects"]["接受可疑项并出片"]["effect"], "reject")
        self.assertEqual(view["semantic"]["delivery_history_status"], "pending")
        self.assertEqual(json.dumps(load_reviews(folder)["delivery"], ensure_ascii=False, sort_keys=True), before)
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_rendering_projects(), 0)
        with self.assertRaises(RpcError) as once:
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "再审一次"})
        self.assertEqual(once.exception.data["reason"], "technical_hold")
        self.assertFalse(once.exception.data["render"])
        with self.assertRaises(RpcError) as accept:
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "接受可疑项并出片"})
        self.assertEqual(accept.exception.data["reason"], "delivery_review_retired")
        queued = []
        with patch("end.server.submit_agent_task", side_effect=lambda task: queued.append(task.kind) or True):
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "根据建议修改"})
        self.assertEqual(queued, ["write"])
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])
        fresh = read_project("a1b2c3d4e511")
        fresh["status"] = "gate"
        fresh["progress"]["phase"] = "gate_machine"
        fresh["progress"]["pipeline"]["generation_hold"] = "technical"
        fresh["progress"]["pipeline"]["check"] = "machine"
        fresh["progress"]["pipeline"]["passed"] = False
        write_project(fresh)
        with self.assertRaises(RpcError) as blocked:
            rpc_call("projects.render", {"id": "a1b2c3d4e511"})
        self.assertEqual(blocked.exception.data["reason"], "technical_block")
        self.assertFalse(blocked.exception.data["render"])
        self.assertEqual(self.renders, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "pending")

    def test_retired_gate_generic_reply_does_not_queue_write(self):
        folder = self._gate("blocked")
        project = read_project("a1b2c3d4e511")
        project["messages"].insert(0, {"role": "assistant", "content": "更早的出片审查", "options": ["旧出片按钮"]})
        write_project(project)
        shown = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(shown["semantic_scope"], "delivery")
        self.assertEqual(shown["option_effects"]["旧出片按钮"]["effect"], "reject")
        self.assertEqual(shown["option_effects"]["继续"]["recovery"], "projects.render")
        self.assertEqual(shown["option_effects"]["*"]["reason"], "delivery_review_retired")
        writes = []

        def writer(*_args, **_kwargs):
            writes.append("write")
            raise AssertionError("write")

        from end.server import agent_queue

        with patch("end.server.call_deepseek", side_effect=writer), patch("end.ai_client.write", side_effect=writer):
            for text in ("继续", "接受", "确认", "旧出片按钮", "随便写一句"):
                with self.assertRaises(RpcError) as caught:
                    rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": text})
                self.assertEqual(caught.exception.data["reason"], "delivery_review_retired")
                self.assertFalse(caught.exception.data["render"])
                self.assertEqual(caught.exception.data["recovery"], "projects.render")
                self.assertEqual(caught.exception.data["semantic_scope"], "delivery")
                self.assertEqual(caught.exception.data["option"], text)
            self.assertTrue(agent_queue().wait_until_idle(2))
        saved = read_project("a1b2c3d4e511")
        self.assertEqual(saved["status"], "gate")
        self.assertEqual(saved["progress"]["pipeline"]["action"], "wait")
        self.assertEqual(saved["progress"]["pipeline"]["semantic_scope"], "delivery")
        self.assertEqual(writes, [])
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "blocked")
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_rendering_projects(), 0)

    def test_script_continue_is_not_blocked_by_delivery_words(self):
        self._gate("blocked")
        project = read_project("a1b2c3d4e511")
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"] = {
            "current": "script",
            "action": "wait",
            "passed": False,
            "semantic_scope": "script",
            "semantic_status": "blocked",
        }
        project["progress"]["question"] = {
            "id": "script",
            "kind": "single",
            "reply": "剧本未过",
            "options": ["继续", "根据建议修改"],
            "cards": [],
        }
        write_project(project)
        shown = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        self.assertEqual(shown["option_effects"], {})
        queued = []
        with patch("end.server.submit_agent_task", side_effect=lambda task: queued.append(task.kind) or True):
            rpc_call("projects.send_message", {"id": "a1b2c3d4e511", "text": "继续"})
        self.assertEqual(queued, ["write"])
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.renders, [])

    def test_blank_shot_prompt_blocks_before_generation(self):
        folder = self._gate("blocked")
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        spec["shots"][0]["prompt"] = "   "
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        external = []
        render_patch = self._patches[-1]
        render_patch.stop()
        from end.server import start_render_thread

        def started(*args, **kwargs):
            external.append("thread")
            return start_render_thread(*args, **kwargs)

        def call(method, params=None, timeout=None):
            external.append(method)
            raise AssertionError(method)

        try:
            with patch("end.server.start_render_thread", side_effect=started), patch("end.ai_client.call", side_effect=call):
                with self.assertRaises(RpcError) as caught:
                    rpc_call("projects.render", {"id": "a1b2c3d4e511"})
            self.assertEqual(caught.exception.data["reason"], "technical_block")
            self.assertEqual(caught.exception.data["owner"], "feed")
            self.assertFalse(caught.exception.data["render"])
            self.assertTrue(caught.exception.data["technical"])
            self.assertEqual(caught.exception.data["generation_hold"], "technical")
            self.assertEqual(caught.exception.data["recovery"], "projects.render")
            view = rpc_call("projects.get", {"id": "a1b2c3d4e511"})
        finally:
            render_patch.start()
        self.assertEqual(view["status"], "gate")
        self.assertEqual(view["generation_hold"], "technical")
        self.assertEqual(view["progress"]["phase"], "gate_machine")
        self.assertNotEqual(view["stage"], "出图出片")
        self.assertEqual(view["option_effects"]["根据建议修改"]["effect"], "technical_rewrite")
        self.assertEqual(view["semantic"]["delivery_history_status"], "blocked")
        self.assertEqual(external, [])
        self.assertEqual(self.reviews, [])
        self.assertEqual(load_reviews(folder)["delivery"]["status"], "blocked")
        self.assertNotIn("generation_technical", load_reviews(folder))


if __name__ == "__main__":
    unittest.main()
