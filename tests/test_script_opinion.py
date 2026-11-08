"""Opinion drafts stay off the official script until an explicit save confirms them."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.script_opinion import MODEL_LIMIT, set_opinion_model
from end.script_repair import _save_candidate, load_candidate, official_script
from end.script_review import save_reviews, set_reviewer
from end.server import RpcError, agent_queue, rpc_call

_SCRIPT = "她留在镇上教书。"
_DRAFT = "未采用。她留在镇上教书。"


class OpinionRpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.model_calls = []
        self.reviews = []
        self.tasks = []
        self.fail_submit = 0
        real_submit = __import__("end.server", fromlist=["submit_agent_task"]).submit_agent_task

        def submit(task):
            if self.fail_submit:
                self.fail_submit -= 1
                raise RuntimeError("队列拒绝")
            accepted = real_submit(task)
            if accepted:
                self.tasks.append(task.kind)
            return accepted

        self._patches = [
            patch("subprocess.Popen", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client._spawn", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client.call", side_effect=AssertionError("offline: no rpc")),
            patch("end.ai_client.ask_text", side_effect=AssertionError("offline: no model")),
            patch("end.server.submit_agent_task", side_effect=submit),
        ]
        for item in self._patches:
            item.start()
        set_opinion_model(self._model)
        set_reviewer(self._review)

    def tearDown(self):
        set_opinion_model(None)
        set_reviewer(None)
        agent_queue().stop()
        for item in reversed(self._patches):
            item.stop()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _model(self, messages):
        self.model_calls.append(messages)
        opinion = messages[-1]["content"]
        if "离开小镇" in opinion:
            return "她离开小镇去看海。"
        return "她仍留在镇上教书。"

    def _review(self, messages):
        self.reviews.append(messages[-1]["content"])
        return json.dumps({"issues": []}, ensure_ascii=False)

    def _project(self, project_id="a1b2c3d4e511"):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": project_id,
            "brief": "原来她留在镇上。",
            "messages": [],
            "title": "意见",
            "status": "gate",
            "stage": "剧本候选",
            "script": _SCRIPT,
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "events": [],
            "tasks": [],
            "progress": {
                "percent": 48,
                "phase": "gate_script",
                "message": "剧本候选",
                "pipeline": {"current": "script", "action": "wait", "passed": False},
                "question": {"id": "script_candidate", "kind": "single", "reply": "看候选", "options": ["保持原稿"], "cards": []},
            },
            "model": "fake-model",
            "template": "short-drama-director",
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        })
        folder = project_dir(project_id)
        (folder / "_内部_").mkdir(parents=True, exist_ok=True)
        (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": _SCRIPT}, ensure_ascii=False), encoding="utf-8")
        _save_candidate(folder, {
            "status": "blocked",
            "reason": "需要人工确认",
            "candidate": "",
            "rejected_text": _DRAFT,
            "attempts": 2,
            "needs_human": True,
            "source_hash": "x",
            "requirements_hash": "y",
            "issues_hash": "z",
        })
        return folder

    def _get(self):
        return rpc_call("projects.script_revision.get", {"id": "a1b2c3d4e511"})

    def _opinion(self, token, key, opinion="让她离开小镇。", base="official"):
        return rpc_call("projects.script_revision.opinion", {
            "id": "a1b2c3d4e511",
            "base": base,
            "base_token": token,
            "opinion": opinion,
            "idempotency_key": key,
        })

    def test_get_does_not_call_the_model_and_submit_keeps_the_repair_budget(self):
        folder = self._project()
        before = (folder / "_内部_" / "剧本候选.json").read_bytes()
        view = self._get()
        self.assertEqual(view["opinion_state"], "none")
        self.assertFalse(view["can_confirm_opinion"])
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), before)
        self.assertEqual(self.model_calls, [])
        token = view["base_token"]
        first = self._opinion(token, "op-1")
        self.assertEqual(first["opinion_state"], "running")
        self.assertFalse(first["official_saved"])
        self.assertEqual(self._opinion(token, "op-1"), first)
        self.assertEqual(self.tasks.count("opinion_revise"), 1)
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.model_calls), 1)
        self.assertNotIn(_SCRIPT, self.model_calls[0][-1]["content"].split("意见：", 1)[-1])
        ready = self._get()
        self.assertEqual(ready["opinion_state"], "ready")
        self.assertEqual(ready["opinion_candidate_text"], "她离开小镇去看海。")
        self.assertTrue(ready["can_confirm_opinion"])
        self.assertFalse(ready["opinion_stale"])
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), before)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(load_candidate(folder)["status"], "blocked")

    def test_same_opinion_cannot_bypass_the_model_cap_with_a_new_key(self):
        self._project()
        token = self._get()["base_token"]
        self._opinion(token, "one")
        self.assertTrue(agent_queue().wait_until_idle(5))
        self._opinion(token, "two")
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.model_calls), MODEL_LIMIT)
        with self.assertRaises(RpcError) as caught:
            self._opinion(token, "three")
        self.assertEqual(caught.exception.data["reason"], "budget_exhausted")
        self.assertEqual(len(self.model_calls), MODEL_LIMIT)

    def test_queue_failure_does_not_spend_a_model_call_and_the_same_key_recovers(self):
        folder = self._project()
        token = self._get()["base_token"]
        self.fail_submit = 1
        with self.assertRaises(RpcError) as caught:
            self._opinion(token, "queue")
        self.assertEqual(caught.exception.data["reason"], "opinion_not_queued")
        self.assertTrue(caught.exception.data["saved"])
        self.assertFalse(caught.exception.data["official_saved"])
        self.assertTrue(caught.exception.data["retry_same_key"])
        self.assertEqual(self.model_calls, [])
        view = self._get()
        self.assertEqual(view["opinion_state"], "pending")
        self.assertTrue(view["opinion_retryable"])
        self.assertNotEqual(view["opinion_state"], "running")
        recovered = self._opinion(token, "queue")
        self.assertEqual(recovered["opinion_state"], "running")
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(official_script(folder), _SCRIPT)

    def test_confirm_uses_ids_not_text_and_plain_save_does_not_inherit_the_opinion(self):
        folder = self._project()
        token = self._get()["base_token"]
        self._opinion(token, "confirm")
        self.assertTrue(agent_queue().wait_until_idle(5))
        view = self._get()
        edited = "她离开小镇去看海。船上还有一只猫。"
        with self.assertRaises(RpcError) as caught:
            rpc_call("projects.script_revision.save", {
                "id": "a1b2c3d4e511",
                "text": edited,
                "base_token": token,
                "idempotency_key": "save-half",
                "opinion_id": view["opinion_id"],
            })
        self.assertEqual(caught.exception.data["reason"], "invalid_request")
        self.assertEqual(official_script(folder), _SCRIPT)
        saved = rpc_call("projects.script_revision.save", {
            "id": "a1b2c3d4e511",
            "text": edited,
            "base_token": token,
            "idempotency_key": "save-yes",
            "source": "human_edit",
            "opinion_id": view["opinion_id"],
            "opinion_revision": view["opinion_revision"],
        })
        self.assertTrue(saved["saved"])
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(official_script(folder), edited)
        self.assertIn("让她离开小镇", self.reviews[-1])
        self.assertEqual(load_candidate(folder)["status"], "blocked")
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        project = read_project("a1b2c3d4e511")
        project["brief"] = "改成另一个要求。"
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        project["progress"]["question"] = {"id": "script", "kind": "single", "reply": "", "options": [], "cards": []}
        project["progress"]["pipeline"] = {"current": "script", "action": "wait", "passed": False}
        write_project(project)
        stale = self._get()
        self.assertTrue(stale["opinion_stale"])
        self.assertFalse(stale["can_confirm_opinion"])
        with self.assertRaises(RpcError) as caught:
            rpc_call("projects.script_revision.save", {
                "id": "a1b2c3d4e511",
                "text": "又一篇。",
                "base_token": stale["base_token"],
                "idempotency_key": "save-stale",
                "opinion_id": view["opinion_id"],
                "opinion_revision": view["opinion_revision"],
            })
        self.assertEqual(caught.exception.data["reason"], "opinion_stale")
        self.assertEqual(official_script(folder), edited)

    def test_failed_same_key_replays_and_a_new_key_spends_the_next_model_call(self):
        folder = self._project()
        token = self._get()["base_token"]

        def explode(_messages):
            self.model_calls.append("fail")
            raise RuntimeError("模型失败")

        set_opinion_model(explode)
        self._opinion(token, "fail-1")
        self.assertTrue(agent_queue().wait_until_idle(5))
        failed = self._get()
        self.assertEqual(failed["opinion_state"], "failed")
        self.assertFalse(failed["opinion_retryable"])
        self.assertTrue(failed["opinion_model_retryable"])
        self.assertEqual(failed["opinion_model_remaining"], 1)
        self.assertEqual(len(self.model_calls), 1)
        replay = self._opinion(token, "fail-1")
        self.assertEqual(replay["opinion_state"], "failed")
        self.assertEqual(len(self.model_calls), 1)
        with self.assertRaises(RpcError) as caught:
            rpc_call("projects.script_revision.opinion_retry", {"id": "a1b2c3d4e511", "base_token": token})
        self.assertEqual(caught.exception.data["reason"], "opinion_failed")
        self.assertFalse(caught.exception.data["retry_same_key"])
        self.assertTrue(caught.exception.data["saved"])
        self.assertFalse(caught.exception.data["official_saved"])
        self.assertEqual(caught.exception.data["model_remaining"], 1)
        self.assertEqual(len(self.model_calls), 1)
        self._opinion(token, "fail-2")
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.model_calls), 2)
        spent = self._get()
        self.assertEqual(spent["opinion_model_remaining"], 0)
        self.assertFalse(spent["opinion_model_retryable"])
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        with self.assertRaises(RpcError) as caught:
            self._opinion(token, "fail-3")
        self.assertEqual(caught.exception.data["reason"], "budget_exhausted")
        self.assertEqual(len(self.model_calls), 2)

    def test_later_repair_review_receives_the_confirmed_intent(self):
        folder = self._project()
        from end.script_opinion import attach_confirmed_intent
        from end.script_repair import run_script_repair

        attach_confirmed_intent(folder, _SCRIPT, {
            "opinion_id": "op",
            "opinion_revision": 1,
            "opinion": "让她离开小镇。",
            "intent": "user_directed",
            "requirements_hash": "r",
            "base": "official",
            "base_hash": "b",
        })
        save_reviews(folder, {"script": {"status": "blocked", "issues": [{
            "id": "S01",
            "location": "镇上",
            "evidence": "她留在镇上教书",
            "category": "causal",
            "severity": "block",
            "reason": "要改",
            "owner": "script",
            "revision": "离开",
        }]}})
        seen = []

        def complete(node, messages):
            seen.append((node, messages[-1]["content"]))
            if node == "locate":
                return json.dumps({"locations": [{"issue_id": "S01", "paragraphs": ["她留在镇上教书"]}]}, ensure_ascii=False)
            if node == "plan":
                return json.dumps({"changes": [{"issue_id": "S01", "direction": "按意见让她离开小镇，不要压回留下。", "linked": True}], "constraints": []}, ensure_ascii=False)
            if node == "revise":
                return json.dumps({"script": "她离开小镇去看海。", "added": [{"issue_id": "S01", "before": "留在镇上教书", "after": "离开小镇去看海", "declaration": "让她离开小镇。"}]}, ensure_ascii=False)
            if node == "review":
                return json.dumps({"passed": True, "theme_ok": True, "resolutions": [{"id": "S01", "resolved": True, "evidence": "离开小镇", "change": "离开小镇", "aligned": True}], "unsupported": [], "new_contradictions": []}, ensure_ascii=False)
            raise AssertionError(node)

        run_script_repair(folder, "原来她留在镇上。", complete)
        review = [body for node, body in seen if node == "review"][0]
        self.assertIn("user_directed", review)
        self.assertIn("让她离开小镇", review)


if __name__ == "__main__":
    unittest.main()
