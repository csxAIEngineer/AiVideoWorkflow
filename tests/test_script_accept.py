"""Offline contract for accepting one named manuscript. Fakes are not accuracy."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.script_repair import _save_candidate, load_candidate, official_script
from end.script_review import (
    content_hash,
    downstream_allowed,
    issue_fingerprint,
    load_reviews,
    review_delivery,
    review_script,
    save_reviews,
    set_reviewer,
    story_context,
)
from end.server import RpcError, resume_agent_tasks, rpc_call

_SCRIPT = "正式原稿。她抬手，指尖凝聚出一瓶。"
_DRAFT = "未采用候选。她抬手，从袖中取出一瓶。"
_ISSUE = {
    "location": "第1场",
    "evidence": "指尖凝聚出一瓶",
    "category": "causal",
    "severity": "block",
    "reason": "因果不一致",
    "owner": "script",
    "revision": "只改这一处，不要重写全文",
}


class ScriptAcceptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.tasks = []
        self.fail_submit = 0
        real_render = __import__("end.server", fromlist=["start_render_thread"]).start_render_thread

        def submit(task):
            if task.kind != "write":
                raise AssertionError("accept queued %s" % task.kind)
            if self.fail_submit:
                self.fail_submit -= 1
                raise RuntimeError("队列拒绝")
            self.tasks.append((task.kind, task.project_id))
            return True

        def no_render(*_args, **_kwargs):
            raise AssertionError("paid render")

        self._patches = [
            patch("subprocess.Popen", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client._spawn", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client.call", side_effect=AssertionError("offline: no rpc")),
            patch("end.ai_client.ask_text", side_effect=AssertionError("offline: no model")),
            patch("end.server.submit_agent_task", side_effect=submit),
            patch("end.server.start_render_thread", side_effect=no_render),
            patch("end.server._start_explicit_render", side_effect=no_render),
        ]
        del real_render
        for item in self._patches:
            item.start()
        set_reviewer(lambda _messages: json.dumps({"issues": []}, ensure_ascii=False))

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

    def _project(self):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": "a1b2c3d4e511",
            "brief": "取出小茶瓶。",
            "messages": [],
            "title": "按稿",
            "status": "gate",
            "stage": "剧本",
            "script": "项目字段里的旧稿",
            "storyboard": "",
            "image_prompts": "",
            "error": None,
            "events": [],
            "tasks": [],
            "progress": {
                "percent": 48,
                "phase": "gate_script",
                "message": "剧本",
                "pipeline": {"current": "script", "action": "wait", "passed": False, "semantic_status": "blocked", "semantic_scope": "script"},
                "question": {"id": "script", "kind": "single", "reply": "审稿未通过", "options": ["根据建议修改"], "cards": []},
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
        (folder / "spec.json").write_text(json.dumps({"script": _SCRIPT}, ensure_ascii=False), encoding="utf-8")
        context = content_hash(story_context(folder, "取出小茶瓶。"))
        save_reviews(folder, {
            "script": {
                "status": "blocked",
                "issues": [_ISSUE],
                "script_hash": content_hash(_SCRIPT),
                "context_hash": context,
                "reason": "因果不一致",
            },
        })
        _save_candidate(folder, {
            "status": "blocked",
            "reason": "需要人工确认",
            "validation": {"stage": "judge", "source": "unbound_declaration", "evidence": "旧声明"},
            "candidate": "",
            "rejected_text": _DRAFT,
            "attempts": 2,
            "source_hash": "x",
            "requirements_hash": "y",
            "issues_hash": "z",
        })
        return folder

    def _get(self):
        return rpc_call("projects.script_revision.get", {"id": "a1b2c3d4e511"})

    def _accept(self, **extra):
        params = {"id": "a1b2c3d4e511", "acknowledge_risk": True}
        params.update(extra)
        return rpc_call("projects.script_revision.accept_as_is", params)

    def test_get_lists_sources_issues_and_refuses_a_passed_review(self):
        folder = self._project()
        view = self._get()
        official = view["accept_sources"][0]
        self.assertEqual(official["source"], "official")
        self.assertTrue(official["can_accept"])
        self.assertEqual(official["review_status"], "blocked")
        self.assertEqual(official["issue_state"], "reviewed")
        self.assertEqual(official["issues"][0]["evidence"], "指尖凝聚出一瓶")
        self.assertEqual(official["issues"][0]["fingerprint"], issue_fingerprint(_ISSUE))
        draft = view["accept_sources"][1]
        self.assertEqual(draft["source"], "draft")
        self.assertEqual(draft["draft_source"], "rejected_text")
        self.assertTrue(draft["can_accept"])
        self.assertIsNone(draft["issues"])
        self.assertEqual(draft["issue_state"], "unreviewed")
        self.assertEqual(draft["risk"]["from"], "repair_candidate")
        self.assertEqual(draft["risk"]["status"], "blocked")
        self.assertEqual(draft["risk"]["reason"], "需要人工确认")
        self.assertEqual(draft["risk"]["validation"]["evidence"], "旧声明")
        self.assertEqual(draft["related_review"]["applies_to"], "official")
        self.assertEqual(draft["related_review"]["status"], "blocked")
        self.assertEqual(view["accept_sources"][2]["can_accept"], False)
        self.assertTrue(view["editor_accept"]["can_accept"])
        self.assertTrue(view["editor_accept"]["saves_without_review"])
        self.assertFalse(view["human_accepted"])
        self.assertFalse(view["review_passed"])
        state = load_reviews(folder)
        state["script"]["status"] = "passed"
        save_reviews(folder, state)
        passed = self._get()["accept_sources"][0]
        self.assertFalse(passed["can_accept"])
        self.assertEqual(passed["unavailable_reason"], "review_passed")

    def test_dirty_editor_is_not_saved_or_accepted(self):
        folder = self._project()
        token = self._get()["base_token"]
        script_bytes = (folder / "_内部_" / "剧本.md").read_bytes()
        candidate_bytes = (folder / "_内部_" / "剧本候选.json").read_bytes()
        with self.assertRaises(RpcError) as caught:
            self._accept(
                base_token=token,
                idempotency_key="dirty-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
                editor_baseline="official",
                editor_text_hash=content_hash(_SCRIPT + "未保存"),
            )
        self.assertEqual(caught.exception.data["reason"], "unsaved_edit")
        self.assertFalse(caught.exception.data["saved"])
        self.assertFalse(caught.exception.data["official_saved"])
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_bytes)
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), candidate_bytes)
        self.assertEqual(self.tasks, [])
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertNotIn("human_accept", load_reviews(folder))

    def test_editor_save_is_explicit_and_does_not_queue_review(self):
        folder = self._project()
        token = self._get()["base_token"]
        edited = _SCRIPT + "就按编辑区这份。"
        with self.assertRaises(RpcError) as missing:
            self._accept(
                base_token=token,
                idempotency_key="edit-1",
                source="editor",
                text=edited,
                text_hash=content_hash(edited),
            )
        self.assertEqual(missing.exception.data["reason"], "confirm_save_required")
        self.assertEqual(official_script(folder), _SCRIPT)
        view = self._accept(
            base_token=token,
            idempotency_key="edit-1",
                source="editor",
                text=edited,
                text_hash=content_hash(edited),
                accept_unreviewed_edit=True,
            )
        self.assertTrue(view["official_saved"])
        self.assertFalse(view["review_queued"])
        self.assertFalse(view["continue_paid"])
        self.assertEqual(view["continue_stage"], "asset")
        self.assertEqual(view["source"], "editor")
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        self.assertEqual(official_script(folder), edited)
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "writing")
        self.assertNotEqual(read_project("a1b2c3d4e511")["progress"]["pipeline"].get("semantic_status"), "passed")
        project = read_project("a1b2c3d4e511")
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"] = {"current": "script", "action": "wait", "passed": False, "semantic_status": "blocked"}
        project["progress"]["question"] = {"id": "script", "kind": "single", "reply": "审", "options": [], "cards": []}
        write_project(project)
        with patch("end.script_revision._write_script", side_effect=OSError("disk")):
            with self.assertRaises(RpcError) as failed_write:
                self._accept(
                    base_token=self._get()["base_token"],
                    idempotency_key="edit-rollback",
                    source="editor",
                    text=edited + "再改。",
                    text_hash=content_hash(edited + "再改。"),
                    accept_unreviewed_edit=True,
                )
        self.assertEqual(failed_write.exception.data["reason"], "archive_failed")
        self.assertFalse(failed_write.exception.data["saved"])
        self.assertEqual(official_script(folder), edited)
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertEqual(load_reviews(folder)["script"]["script_hash"], content_hash(_SCRIPT))
        self.assertIsNone(load_reviews(folder)["human_accept"]["issues"])
        self.assertEqual(load_reviews(folder)["human_accept"]["issue_state"], "unreviewed")
        self.assertEqual(load_candidate(folder)["validation"]["evidence"], "旧声明")
        self.assertTrue(view["unreviewed_edit"])
        self.assertEqual(load_candidate(folder)["attempts"], 2)

    def test_official_accept_keeps_the_blocked_review_and_queues_one_write(self):
        folder = self._project()
        token = self._get()["base_token"]
        candidate_bytes = (folder / "_内部_" / "剧本候选.json").read_bytes()
        view = self._accept(
            base_token=token,
            idempotency_key="keep-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertFalse(view["official_saved"])
        self.assertFalse(view["review_queued"])
        self.assertFalse(view["continue_paid"])
        self.assertEqual(view["continue_state"], "queued")
        self.assertEqual(view["unresolved_issues"][0]["fingerprint"], issue_fingerprint(_ISSUE))
        again = self._accept(
            base_token=token,
            idempotency_key="keep-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(again["message"], view["message"])
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertEqual(load_reviews(folder)["script"]["issues"][0]["evidence"], "指尖凝聚出一瓶")
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), candidate_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertTrue(downstream_allowed(folder, brief="取出小茶瓶。")[0])
        self.assertFalse(downstream_allowed(folder, brief="换成另一条要求")[0])

    def test_draft_promotion_does_not_cover_the_candidate_record(self):
        folder = self._project()
        token = self._get()["base_token"]
        candidate_bytes = (folder / "_内部_" / "剧本候选.json").read_bytes()
        view = self._accept(
            base_token=token,
            idempotency_key="draft-1",
                source="draft",
                text_hash=content_hash(_DRAFT),
                editor_baseline="official",
                editor_text_hash=content_hash(_SCRIPT),
            )
        self.assertTrue(view["official_saved"])
        self.assertEqual(view["source"], "draft")
        self.assertEqual(official_script(folder), _DRAFT)
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), candidate_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertIsNone(load_reviews(folder)["human_accept"]["issues"])
        self.assertEqual(load_candidate(folder)["validation"]["evidence"], "旧声明")
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])

    def test_submit_failure_stays_pending_and_retry_queues_once(self):
        folder = self._project()
        token = self._get()["base_token"]
        self.fail_submit = 1
        with self.assertRaises(RpcError) as caught:
            self._accept(
                base_token=token,
                idempotency_key="queue-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(caught.exception.data["reason"], "continue_not_queued")
        self.assertTrue(caught.exception.data["saved"])
        self.assertTrue(caught.exception.data["retry_same_key"])
        self.assertFalse(caught.exception.data["official_saved"])
        parked = read_project("a1b2c3d4e511")
        self.assertEqual(parked["status"], "writing")
        self.assertTrue((parked.get("progress") or {}).get("lock_machine"))
        self.assertTrue(((parked.get("progress") or {}).get("pipeline") or {}).get("human_release"))
        self.assertEqual(self._get()["continue_state"], "pending")
        self.assertTrue(self._get()["continue_retryable"])
        self.assertEqual(self.tasks, [])
        retried = rpc_call("projects.script_revision.accept_retry", {"id": "a1b2c3d4e511", "base_token": token})
        self.assertEqual(retried["continue_state"], "queued")
        self.assertFalse(retried["continue_paid"])
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertEqual(load_candidate(folder)["attempts"], 2)

    def test_settled_script_is_not_rereviewed_and_delivery_keeps_new_conflicts(self):
        folder = self._project()
        calls = []

        def reviewer(messages):
            calls.append(messages)
            return json.dumps({"issues": []}, ensure_ascii=False)

        set_reviewer(reviewer)
        state = load_reviews(folder)
        state["human_accept"] = {
            "acknowledged": True,
            "script_hash": content_hash(_SCRIPT),
            "context_hash": content_hash(story_context(folder, "取出小茶瓶。")),
            "source": "official",
            "issue_state": "reviewed",
            "issues": [_ISSUE],
        }
        save_reviews(folder, state)
        skipped = review_script(folder, {"script": _SCRIPT}, brief="取出小茶瓶。", force=True)
        self.assertTrue(skipped["skipped_repeat"])
        self.assertEqual(calls, [])
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        changed = _SCRIPT + "她改了主意。"
        (folder / "_内部_" / "剧本.md").write_text(changed + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": changed}, ensure_ascii=False), encoding="utf-8")
        review_script(folder, {"script": changed}, brief="取出小茶瓶。", force=True)
        self.assertEqual(len(calls), 1)
        (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": _SCRIPT}, ensure_ascii=False), encoding="utf-8")
        save_reviews(folder, state)
        calls.clear()
        review_script(folder, {"script": _SCRIPT}, brief="换成另一条要求", force=True)
        self.assertEqual(len(calls), 1)
        save_reviews(folder, state)

        reworded = dict(_ISSUE)
        reworded["evidence"] = "瓶是凭空来的"
        reworded["reason"] = "因果说不通"
        missing = dict(_ISSUE)
        missing["evidence"] = "提示词里没有小茶瓶"
        missing["reason"] = "已定稿里的道具在提示词中缺失"
        missing["owner"] = "asset"
        seen = []

        def delivery_reviewer(messages):
            seen.append("\n".join(item.get("content") or "" for item in messages))
            return json.dumps({"issues": [reworded, missing]}, ensure_ascii=False)

        set_reviewer(delivery_reviewer)
        with patch("end.script_review.generation_bundle", return_value=([{"text": "提示词", "hash": "h"}], "bundle")):
            row = review_delivery(folder, {"script": _SCRIPT}, brief="取出小茶瓶。", force=True)
        self.assertIn("不要因为措辞相近", seen[0])
        self.assertIn("指尖凝聚出一瓶", seen[0])
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(len(row["issues"]), 2)
        self.assertNotIn("waived_issues", row)
        self.assertEqual(load_reviews(folder)["script"]["issues"][0]["evidence"], "指尖凝聚出一瓶")
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")

    def test_opinion_source_names_the_candidate_and_does_not_call_a_model(self):
        from end.script_opinion import intent_for_script, set_opinion_model

        folder = self._project()
        token = self._get()["base_token"]
        body = "意见候选。她放下瓶子离开。"
        set_opinion_model(lambda _text, _opinion: (_ for _ in ()).throw(AssertionError("opinion model")))
        payload = {
            "active_id": "op1",
            "calls": {"same": 1},
            "items": {
                "op1": {
                    "opinion_id": "op1",
                    "opinion_revision": 1,
                    "state": "ready",
                    "base": "official",
                    "base_token": token,
                    "requirements_hash": content_hash("取出小茶瓶。"),
                    "candidate_text": body,
                    "opinion": "让她离开",
                    "identity": "same",
                }
            },
            "confirmed": {},
            "idempotency": {},
        }
        (folder / "_内部_" / "剧情意见.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        opinion_source = self._get()["accept_sources"][2]
        self.assertTrue(opinion_source["can_accept"])
        self.assertEqual(opinion_source["issue_state"], "unknown")
        self.assertIsNone(opinion_source["issues"])
        self.assertEqual(opinion_source["related_review"]["applies_to"], "official")
        self.assertEqual(opinion_source["related_review"]["status"], "blocked")
        with self.assertRaises(RpcError) as missing:
            self._accept(
                base_token=token,
                idempotency_key="op-1",
                source="opinion",
                text_hash=content_hash(body),
            )
        self.assertEqual(missing.exception.data["reason"], "invalid_request")
        self.assertEqual(official_script(folder), _SCRIPT)
        view = self._accept(
            base_token=token,
            idempotency_key="op-1",
            source="opinion",
            text_hash=content_hash(body),
            opinion_id="op1",
            opinion_revision=1,
        )
        self.assertEqual(view["source"], "opinion")
        self.assertTrue(view["official_saved"])
        self.assertFalse(view["review_queued"])
        self.assertFalse(view["continue_paid"])
        self.assertEqual(official_script(folder), body)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(json.loads((folder / "_内部_" / "剧情意见.json").read_text(encoding="utf-8"))["calls"]["same"], 1)
        self.assertEqual(intent_for_script(folder, content_hash(body))["opinion"], "让她离开")
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")

    def test_risk_flag_cross_project_and_passed_review_do_not_write(self):
        folder = self._project()
        token = self._get()["base_token"]
        with self.assertRaises(RpcError) as risk:
            self._accept(
                base_token=token,
                idempotency_key="risk-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
                acknowledge_risk=False,
            )
        self.assertEqual(risk.exception.data["reason"], "risk_not_acknowledged")
        from end.script_revision import revision_token
        other = revision_token("b1b2c3d4e512", _SCRIPT, load_candidate(folder))
        with self.assertRaises(RpcError) as foreign:
            self._accept(
                base_token=other,
                idempotency_key="other-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(foreign.exception.data["reason"], "token_mismatch")
        self.assertFalse(foreign.exception.data["saved"])
        state = load_reviews(folder)
        state["script"]["status"] = "passed"
        save_reviews(folder, state)
        with self.assertRaises(RpcError) as passed:
            self._accept(
                base_token=self._get()["base_token"],
                idempotency_key="passed-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(passed.exception.data["reason"], "review_passed")
        self.assertEqual(self.tasks, [])
        self.assertEqual(official_script(folder), _SCRIPT)

    def test_resume_after_the_write_does_not_submit_again(self):
        import end.server as server

        self._project()
        token = self._get()["base_token"]
        self._accept(
            base_token=token,
            idempotency_key="once-1",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        project = read_project("a1b2c3d4e511")
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_asset"
        project["progress"]["pipeline"]["action"] = "wait"
        project["progress"]["pipeline"]["current"] = "asset"
        write_project(project)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        stale = read_project("a1b2c3d4e511")
        stale["status"] = "writing"
        stale["progress"]["pipeline"]["action"] = "write"
        stale["progress"]["pipeline"]["human_release"] = True
        real_list = server.list_projects

        def racing_list():
            fresh = read_project("a1b2c3d4e511")
            fresh["status"] = "gate"
            fresh["progress"]["pipeline"]["action"] = "wait"
            write_project(fresh)
            return [stale]

        with patch("end.server.list_projects", side_effect=racing_list):
            self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(self.tasks, [("write", "a1b2c3d4e511")])
        del real_list


class HumanReleaseWorkerTest(unittest.TestCase):
    """The accept write runs on the real queue. submit is not mocked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.tasks = []
        self.fail_submit = 0
        self.calls = []
        self._patches = [
            patch("subprocess.Popen", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client._spawn", side_effect=AssertionError("offline: no process")),
            patch("end.ai_client.call", side_effect=AssertionError("offline: no rpc")),
            patch("end.ai_client.ask_text", side_effect=AssertionError("offline: no model")),
            patch("end.server.start_render_thread", side_effect=AssertionError("paid render")),
            patch("end.server._start_explicit_render", side_effect=AssertionError("paid render")),
            patch("end.server.load_skill_pack", return_value="pack"),
            patch("end.server.template_policy", return_value=None),
        ]
        for item in self._patches:
            item.start()
        set_reviewer(lambda _messages: json.dumps({"issues": []}, ensure_ascii=False))

    def _project(self):
        return ScriptAcceptTest._project(self)

    def _get(self):
        return ScriptAcceptTest._get(self)

    def _accept(self, **extra):
        return ScriptAcceptTest._accept(self, **extra)

    def _writer(self, passed):
        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            self.calls.append(brief)
            entered = getattr(self, "writer_entered", None)
            release = getattr(self, "writer_release", None)
            if entered is not None:
                entered.set()
            if release is not None and not release.wait(5):
                raise AssertionError("writer barrier was not released")
            return {"image_prompts": "#### 图1\n小茶瓶\n", "assets": []}

        self._check = patch("end.stage_runner.check_stage", return_value=(passed, "" if passed else "图1 机检未通过"))
        self._model = patch("end.server.call_deepseek", side_effect=writer)
        self._write = patch("end.ai_client.write", side_effect=writer)
        self._check.start()
        self._model.start()
        self._write.start()

    def tearDown(self):
        for extra in (getattr(self, "_write", None), getattr(self, "_model", None), getattr(self, "_check", None)):
            if extra is not None:
                extra.stop()
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

    def _wait(self):
        from end.server import agent_queue

        self.assertTrue(agent_queue().wait_until_idle(5))

    def test_passing_asset_stops_on_the_asset_gate(self):
        from end.server import resume_agent_tasks

        self._writer(True)
        folder = self._project()
        token = self._get()["base_token"]
        view = self._accept(
            base_token=token,
            idempotency_key="real-pass",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self.assertEqual(view["continue_stage"], "asset")
        self.assertFalse(view["continue_paid"])
        self._wait()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("任务：资产锁定", self.calls[0])
        self.assertFalse(any("任务：分镜" in call for call in self.calls))
        saved = read_project("a1b2c3d4e511")
        err_log = folder / "error.log"
        detail = err_log.read_text(encoding="utf-8")[-1800:] if err_log.is_file() else saved.get("error")
        self.assertEqual(saved["status"], "gate", detail)
        self.assertEqual(saved["progress"]["phase"], "gate_asset")
        self.assertEqual(saved["progress"]["pipeline"]["current"], "asset")
        self.assertNotIn("human_release", saved["progress"]["pipeline"])
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual(load_reviews(folder)["script"]["status"], "blocked")
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_failing_asset_stays_on_the_asset_gate_inside_the_repair_budget(self):
        from end.stage_runner import REPAIR_LIMIT

        self._writer(False)
        self._project()
        token = self._get()["base_token"]
        self._accept(
            base_token=token,
            idempotency_key="real-fail",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self._wait()
        saved = read_project("a1b2c3d4e511")
        pipeline = saved["progress"]["pipeline"]
        self.assertEqual(saved["status"], "gate")
        self.assertEqual(saved["progress"]["phase"], "gate_asset")
        self.assertEqual(pipeline["current"], "asset")
        self.assertFalse(pipeline.get("passed"))
        self.assertEqual(pipeline.get("repairs"), REPAIR_LIMIT)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("任务：资产锁定", self.calls[0])
        self.assertTrue(all("任务：分镜" not in call and "任务：提示词封装" not in call for call in self.calls))

    def test_a_running_write_is_not_submitted_twice(self):
        from end.server import agent_queue, resume_agent_tasks

        self.writer_entered = threading.Event()
        self.writer_release = threading.Event()
        self._writer(True)
        self._project()
        token = self._get()["base_token"]
        view = self._accept(
            base_token=token,
            idempotency_key="real-hold",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self.assertEqual(view["continue_state"], "queued")
        self.assertTrue(self.writer_entered.wait(5))
        self.assertEqual(resume_agent_tasks(), 0)
        with self.assertRaises(RpcError) as busy:
            self._accept(
                base_token=token,
                idempotency_key="real-hold-2",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(busy.exception.data["reason"], "invalid_phase")
        self.assertFalse(busy.exception.data["saved"])
        self.writer_release.set()
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_flags_are_on_disk_before_submit_even_if_persist_is_slow(self):
        import time

        import end.server as server

        self._writer(True)
        real_remember = server.remember
        real_submit = server.submit_agent_task
        seen = {}

        def slow_remember(*args, **kwargs):
            time.sleep(0.3)
            return real_remember(*args, **kwargs)

        def submit(task):
            project = read_project(task.project_id)
            progress = project.get("progress") or {}
            seen["status"] = project.get("status")
            seen["lock_machine"] = bool(progress.get("lock_machine"))
            seen["human_release"] = bool((progress.get("pipeline") or {}).get("human_release"))
            seen["current"] = (progress.get("pipeline") or {}).get("current")
            return real_submit(task)

        self._project()
        token = self._get()["base_token"]
        with patch("end.server.remember", side_effect=slow_remember), patch(
            "end.server.submit_agent_task", side_effect=submit
        ):
            view = self._accept(
                base_token=token,
                idempotency_key="order-1",
                source="official",
                text_hash=content_hash(_SCRIPT),
            )
        self.assertEqual(view["continue_stage"], "asset")
        self.assertEqual(seen["status"], "writing")
        self.assertTrue(seen["lock_machine"])
        self.assertTrue(seen["human_release"])
        self.assertEqual(seen["current"], "asset")
        self._wait()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("任务：资产锁定", self.calls[0])
        saved = read_project("a1b2c3d4e511")
        self.assertEqual(saved["status"], "gate", saved.get("error"))
        self.assertEqual(saved["progress"]["phase"], "gate_asset")

    def test_ack_failure_does_not_queue_a_second_write(self):
        import end.script_accept as accept_mod

        self._writer(True)
        real_store = accept_mod._store

        def store(folder, entries):
            if any(isinstance(item, dict) and item.get("queued") for item in (entries or {}).values()):
                raise OSError("ack")
            return real_store(folder, entries)

        self._project()
        token = self._get()["base_token"]
        with patch("end.script_accept._store", side_effect=store):
            with self.assertRaises(RpcError) as caught:
                self._accept(
                    base_token=token,
                    idempotency_key="ack-1",
                    source="official",
                    text_hash=content_hash(_SCRIPT),
                )
        self.assertEqual(caught.exception.data["reason"], "continue_ack_unconfirmed")
        self.assertTrue(caught.exception.data["saved"])
        self.assertTrue(caught.exception.data["retry_same_key"])
        self._wait()
        self.assertEqual(len(self.calls), 1)
        saved = read_project("a1b2c3d4e511")
        self.assertEqual(saved["progress"]["phase"], "gate_asset", saved.get("error"))
        again = rpc_call("projects.script_revision.accept_retry", {"id": "a1b2c3d4e511", "base_token": token})
        self.assertEqual(again["continue_state"], "done")
        self._wait()
        self.assertEqual(len(self.calls), 1)

    def test_pending_restart_does_not_run_until_explicit_retry(self):
        import end.server as server

        self._writer(True)
        real_submit = server.submit_agent_task
        state = {"n": 0}

        def submit(task):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("队列拒绝")
            return real_submit(task)

        self._project()
        token = self._get()["base_token"]
        with patch("end.server.submit_agent_task", side_effect=submit):
            with self.assertRaises(RpcError) as caught:
                self._accept(
                    base_token=token,
                    idempotency_key="pending-1",
                    source="official",
                    text_hash=content_hash(_SCRIPT),
                )
        self.assertEqual(caught.exception.data["reason"], "continue_not_queued")
        self.assertEqual(self._get()["continue_state"], "pending")
        self.assertTrue(self._get()["continue_retryable"])
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "writing")
        self.assertEqual(server.resume_agent_tasks(), 0)
        self.assertEqual(self.calls, [])
        retried = rpc_call("projects.script_revision.accept_retry", {"id": "a1b2c3d4e511", "base_token": token})
        self.assertEqual(retried["continue_state"], "queued")
        self._wait()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("任务：资产锁定", self.calls[0])
        self.assertEqual(read_project("a1b2c3d4e511")["progress"]["phase"], "gate_asset")
        self.assertEqual(server.resume_agent_tasks(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_false_submit_means_the_same_write_is_already_pending(self):
        from end.server import submit_agent_task
        from end.task_queue.queue import Task

        self.writer_entered = threading.Event()
        self.writer_release = threading.Event()
        self._writer(True)
        self._project()
        token = self._get()["base_token"]
        view = self._accept(
            base_token=token,
            idempotency_key="dedupe-1",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self.assertEqual(view["continue_state"], "queued")
        self.assertTrue(self.writer_entered.wait(5))
        self.assertFalse(submit_agent_task(Task("write", "a1b2c3d4e511", {})))
        self.writer_release.set()
        self._wait()
        self.assertEqual(len(self.calls), 1)

    def test_confirmed_queued_restart_runs_one_write(self):
        from end.server import agent_queue, resume_agent_tasks

        self._writer(True)
        self._project()
        token = self._get()["base_token"]
        self._accept(
            base_token=token,
            idempotency_key="queued-restart",
            source="official",
            text_hash=content_hash(_SCRIPT),
        )
        self._wait()
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["lock_machine"] = True
        project["progress"]["pipeline"]["action"] = "write"
        project["progress"]["pipeline"]["current"] = "asset"
        project["progress"]["pipeline"]["human_release"] = True
        write_project(project)
        agent_queue().stop()
        self.assertEqual(resume_agent_tasks(), 1)
        self._wait()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("任务：资产锁定", self.calls[0])
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_a_normal_asset_write_still_continues_to_storyboard(self):
        from end.server import run_project

        self._writer(True)
        folder = self._project()
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["lock_machine"] = True
        project["progress"]["pipeline"] = {
            "current": "asset",
            "accepted": ["script"],
            "action": "write",
            "passed": True,
            "only": "",
        }
        write_project(project)
        run_project("a1b2c3d4e511")
        self.assertTrue(any("任务：资产锁定" in call for call in self.calls))
        self.assertTrue(any("任务：分镜" in call for call in self.calls), [call[:80] for call in self.calls])
        self.assertNotEqual(read_project("a1b2c3d4e511")["progress"]["pipeline"]["current"], "asset")
        self.assertEqual(official_script(folder), _SCRIPT)


if __name__ == "__main__":
    unittest.main()
