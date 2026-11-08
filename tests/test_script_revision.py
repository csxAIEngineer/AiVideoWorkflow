"""Offline contract for the script revision read/save RPCs. Fakes are not accuracy."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.history_keep import ArchiveError
from end.render_loop import project_lock
from end.script_repair import _save_candidate, load_candidate, official_script
from end.script_review import content_hash, load_reviews, save_reviews, set_reviewer
from end.server import RpcError, agent_queue, resume_agent_tasks, rpc_call

_SCRIPT = "正式原稿。她抬手，指尖凝聚出一瓶。"
_REJECTED = "未采用候选。她抬手，从袖中取出一瓶。"


class ScriptRevisionRpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
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
                self.tasks.append((task.kind, task.project_id))
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
        set_reviewer(self._review)

    def tearDown(self):
        set_reviewer(None)
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
        entered = getattr(self, "review_entered", None)
        release = getattr(self, "review_release", None)
        if entered is not None:
            entered.set()
        if release is not None and not release.wait(5):
            raise AssertionError("审稿屏障没有放开")
        return json.dumps({"issues": []}, ensure_ascii=False)

    def _project(self, project_id="a1b2c3d4e511", question_id="script_candidate"):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": project_id,
            "brief": "取出小茶瓶。",
            "messages": [],
            "title": "修订",
            "status": "gate",
            "stage": "剧本候选",
            "script": "项目字段里的旧稿，不是正式文件",
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
                "question": {"id": question_id, "kind": "single", "reply": "看候选", "options": ["保持原稿"], "cards": []},
            },
            "model": "fake-model",
            "template": "short-drama-director",
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        })
        folder = project_dir(project_id)
        internal = folder / "_内部_"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        (folder / "spec.json").write_text(json.dumps({"script": _SCRIPT}, ensure_ascii=False), encoding="utf-8")
        save_reviews(folder, {"script": {"status": "blocked", "issues": []}, "confirmed_script_hash": "old", "confirmed_context_hash": "old"})
        _save_candidate(folder, {
            "status": "blocked",
            "reason": "需要人工确认",
            "candidate": "",
            "rejected_text": _REJECTED,
            "attempts": 2,
            "needs_human": True,
            "validation": {"stage": "judge", "source": "unbound_declaration", "evidence": "旧声明"},
            "source_hash": "x",
            "requirements_hash": "y",
            "issues_hash": "z",
        })
        return folder

    def _get(self, project_id="a1b2c3d4e511"):
        return rpc_call("projects.script_revision.get", {"id": project_id})

    def _save(self, text, token, key, project_id="a1b2c3d4e511", source="human_edit"):
        return rpc_call("projects.script_revision.save", {
            "id": project_id,
            "text": text,
            "base_token": token,
            "idempotency_key": key,
            "source": source,
        })

    def test_get_reads_the_real_gate_question_and_does_not_write(self):
        folder = self._project()
        before = {
            "script": (folder / "_内部_" / "剧本.md").read_bytes(),
            "candidate": (folder / "_内部_" / "剧本候选.json").read_bytes(),
            "reviews": (folder / "reviews.json").read_bytes(),
        }
        view = self._get()
        self.assertEqual(read_project("a1b2c3d4e511")["progress"]["question"]["id"], "script_candidate")
        self.assertEqual(view["official_text"], _SCRIPT)
        self.assertNotEqual(view["official_text"], "项目字段里的旧稿，不是正式文件")
        self.assertEqual(view["draft_text"], _REJECTED)
        self.assertEqual(view["draft_source"], "rejected_text")
        self.assertEqual(view["status"], "blocked")
        self.assertEqual(view["reason"], "需要人工确认")
        self.assertEqual(view["validation"]["source"], "unbound_declaration")
        self.assertTrue(view["can_edit"])
        self.assertTrue(view["can_save"])
        self.assertTrue(view["base_token"].startswith("sr1."))
        self.assertEqual(self.reviews, [])
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), before["script"])
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), before["candidate"])
        self.assertEqual((folder / "reviews.json").read_bytes(), before["reviews"])
        project = read_project("a1b2c3d4e511")
        project["progress"]["question"]["id"] = "script"
        write_project(project)
        self.assertTrue(self._get()["can_edit"])
        project = read_project("a1b2c3d4e511")
        project["progress"]["question"]["id"] = "asset"
        write_project(project)
        blocked = self._get()
        self.assertFalse(blocked["can_edit"])
        self.assertEqual(blocked["phase_reason"], "invalid_phase")
        self.assertEqual(blocked["draft_text"], _REJECTED)

    def test_save_same_text_queues_nothing_and_keeps_the_budget(self):
        folder = self._project()
        token = self._get()["base_token"]
        result = self._save(_SCRIPT, token, "same-1")
        self.assertFalse(result["saved"])
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["review_queued"])
        self.assertEqual(result["message"], "稿件没有变化")
        self.assertEqual(self.tasks, [])
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(load_candidate(folder)["status"], "blocked")
        again = self._save(_SCRIPT, token, "same-1")
        self.assertEqual(again, result)
        self.assertEqual(self.tasks, [])

    def test_save_queues_one_review_and_replay_survives_a_restart(self):
        folder = self._project()
        token = self._get()["base_token"]
        candidate_bytes = (folder / "_内部_" / "剧本候选.json").read_bytes()
        edited = _SCRIPT + "她自己改了一句。"
        result = self._save(edited, token, "edit-1")
        self.assertTrue(result["saved"])
        self.assertTrue(result["review_queued"])
        self.assertEqual(result["message"], "已保存，正在重新审稿")
        self.assertNotIn("official_text", result)
        self.assertNotIn("draft_text", result)
        replay = self._save(edited, token, "edit-1")
        self.assertEqual(replay, result)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(self.reviews, [1])
        self.assertEqual(official_script(folder), edited)
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), candidate_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(load_candidate(folder)["status"], "blocked")
        self.assertNotIn("confirmed_script_hash", load_reviews(folder))
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "gate")
        self.assertNotIn("script_repair", [item[0] for item in self.tasks])
        self.assertNotIn("write", [item[0] for item in self.tasks])
        with self.assertRaises(RpcError) as caught:
            self._save(edited + "再改。", token, "edit-1")
        self.assertEqual(caught.exception.data["reason"], "idempotency_mismatch")
        self.assertFalse(caught.exception.data["retryable"])
        self.assertEqual(official_script(folder), edited)

    def test_restart_queues_a_review_once_and_the_handler_does_not_generate(self):
        folder = self._project()
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"]["action"] = "script_review"
        write_project(project)
        self.assertEqual(resume_agent_tasks(), 1)
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual(load_candidate(folder)["rejected_text"], _REJECTED)
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "gate")
        self.assertEqual(self.reviews, [1])
        self.assertNotIn("write", [item[0] for item in self.tasks])
        self.assertNotIn("script_repair", [item[0] for item in self.tasks])

    def test_conflict_busy_phase_and_archive_failure_keep_the_manuscript(self):
        folder = self._project()
        token = self._get()["base_token"]
        (folder / "_内部_" / "剧本.md").write_text("别人改过的原稿。\n", encoding="utf-8")
        with self.assertRaises(RpcError) as caught:
            self._save("编辑区全文。", token, "stale-1")
        self.assertEqual(caught.exception.data["reason"], "version_conflict")
        self.assertFalse(caught.exception.data["retryable"])
        self.assertEqual(official_script(folder), "别人改过的原稿。")
        (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        row = load_candidate(folder)
        row["rejected_text"] = "过期候选"
        _save_candidate(folder, row)
        with self.assertRaises(RpcError) as caught:
            self._save("编辑区全文。", token, "expired-1")
        self.assertEqual(caught.exception.data["reason"], "version_conflict")
        self.assertEqual(official_script(folder), _SCRIPT)
        other = self._project("b1b2c3d4e512", "script")
        other_token = rpc_call("projects.script_revision.get", {"id": "b1b2c3d4e512"})["base_token"]
        with self.assertRaises(RpcError) as caught:
            self._save("编辑区全文。", other_token, "cross-1")
        self.assertEqual(caught.exception.data["reason"], "token_mismatch")
        self.assertEqual(official_script(folder), _SCRIPT)
        self.assertEqual(official_script(other), _SCRIPT)
        project = read_project("a1b2c3d4e511")
        project["progress"]["question"]["id"] = "asset"
        write_project(project)
        fresh = self._get()["base_token"]
        with self.assertRaises(RpcError) as caught:
            self._save("编辑区全文。", fresh, "phase-1")
        self.assertEqual(caught.exception.data["reason"], "invalid_phase")
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"]["action"] = "script_repair"
        project["progress"]["question"] = {"id": "script_candidate", "kind": "single", "reply": "", "options": [], "cards": []}
        write_project(project)
        with self.assertRaises(RpcError) as caught:
            self._save("编辑区全文。", self._get()["base_token"], "busy-1")
        self.assertEqual(caught.exception.data["reason"], "busy")
        self.assertTrue(caught.exception.data["retryable"])
        self.assertEqual(official_script(folder), _SCRIPT)
        project = read_project("a1b2c3d4e511")
        project["status"] = "gate"
        project["progress"]["phase"] = "gate_script"
        project["progress"]["pipeline"]["action"] = "wait"
        write_project(project)
        with patch("end.script_revision.snapshot_project", side_effect=ArchiveError("磁盘满")):
            with self.assertRaises(RpcError) as caught:
                self._save("编辑区全文。", self._get()["base_token"], "archive-1")
        self.assertEqual(caught.exception.data["reason"], "archive_failed")
        self.assertTrue(caught.exception.data["retryable"])
        self.assertEqual(official_script(folder), _SCRIPT)
        with patch("end.script_revision._write_script", side_effect=OSError("写到一半")):
            with self.assertRaises(RpcError) as caught:
                self._save("编辑区全文。", self._get()["base_token"], "write-1")
        self.assertEqual(caught.exception.data["reason"], "archive_failed")
        self.assertEqual(official_script(folder), _SCRIPT)
        with self.assertRaises(RpcError) as caught:
            self._save("  ", self._get()["base_token"], "empty-1")
        self.assertEqual(caught.exception.data["reason"], "empty_text")
        self.assertEqual(self.tasks, [])

    def test_a_concurrent_edit_inside_the_lock_is_rejected(self):
        folder = self._project()
        token = self._get()["base_token"]
        release = threading.Event()
        started = threading.Event()

        def editor():
            with project_lock("a1b2c3d4e511"):
                started.set()
                (folder / "_内部_" / "剧本.md").write_text("锁内改过的原稿。\n", encoding="utf-8")
                release.wait(2)

        threading.Thread(target=editor).start()
        self.assertTrue(started.wait(2))
        seen = {}

        def later():
            try:
                seen["result"] = self._save("编辑区想写入。", token, "race-1")
            except RpcError as error:
                seen["error"] = error

        worker = threading.Thread(target=later)
        worker.start()
        release.set()
        worker.join(3)
        self.assertIn("error", seen)
        self.assertEqual(seen["error"].data["reason"], "version_conflict")
        self.assertEqual(official_script(folder), "锁内改过的原稿。")

    def test_a_failed_queue_is_not_reported_as_reviewing_and_the_same_key_retries_once(self):
        folder = self._project()
        token = self._get()["base_token"]
        edited = _SCRIPT + "她自己改了一句。"
        self.fail_submit = 1
        with self.assertRaises(RpcError) as caught:
            self._save(edited, token, "queue-1")
        self.assertEqual(caught.exception.data["reason"], "review_not_queued")
        self.assertTrue(caught.exception.data["retryable"])
        self.assertTrue(caught.exception.data["saved"])
        self.assertTrue(caught.exception.data["retry_same_key"])
        self.assertNotIn("正在重新审稿", caught.exception.message)
        self.assertEqual(official_script(folder), edited)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(load_candidate(folder)["status"], "blocked")
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.reviews, [])
        script_bytes = (folder / "_内部_" / "剧本.md").read_bytes()
        result = self._save(edited, token, "queue-1")
        self.assertTrue(result["saved"])
        self.assertTrue(result["review_queued"])
        self.assertEqual(result["message"], "已保存，正在重新审稿")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_bytes)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(self.reviews, [1])
        again = self._save(edited, token, "queue-1")
        self.assertEqual(again, result)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])

    def test_ack_write_failure_after_queue_accept_stays_structured_and_retries_once(self):
        from end import script_revision

        folder = self._project()
        token = self._get()["base_token"]
        edited = _SCRIPT + "确认记录写失败前的稿。"
        script_after = None
        real_store = script_revision._store_idempotency
        self.fail_ack = 1

        def store(folder, entries):
            if self.fail_ack and any(isinstance(item, dict) and item.get("queued") for item in entries.values()):
                self.fail_ack -= 1
                raise OSError("确认记录写失败")
            return real_store(folder, entries)

        with patch("end.script_revision._store_idempotency", side_effect=store):
            with self.assertRaises(RpcError) as caught:
                self._save(edited, token, "ack-1")
        self.assertEqual(caught.exception.data["reason"], "review_ack_unconfirmed")
        self.assertTrue(caught.exception.data["retryable"])
        self.assertTrue(caught.exception.data["saved"])
        self.assertTrue(caught.exception.data["retry_same_key"])
        self.assertNotIn("还没排上", caught.exception.message)
        self.assertNotIn("正在重新审稿", caught.exception.message)
        self.assertIn("可能已经排上", caught.exception.message)
        script_after = (folder / "_内部_" / "剧本.md").read_bytes()
        self.assertEqual(official_script(folder), edited)
        self.assertEqual(load_candidate(folder)["status"], "blocked")
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(self.reviews, [1])
        result = self._save(edited, token, "ack-1")
        self.assertTrue(result["review_queued"])
        self.assertEqual(result["message"], "已保存，正在重新审稿")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_after)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(self.reviews, [1])

    def _retry(self, token, project_id="a1b2c3d4e511"):
        return rpc_call("projects.script_revision.retry_review", {"id": project_id, "base_token": token})

    def test_refresh_without_the_key_can_retry_the_saved_review_once(self):
        folder = self._project()
        before = self._get()["base_token"]
        edited = _SCRIPT + "关闭浮层后的稿。"
        self.fail_submit = 1
        with self.assertRaises(RpcError) as caught:
            self._save(edited, before, "lost-key")
        self.assertEqual(caught.exception.data["reason"], "review_not_queued")
        disk = (
            (folder / "_内部_" / "剧本.md").read_bytes(),
            (folder / "_内部_" / "剧本候选.json").read_bytes(),
            (folder / "_内部_" / "剧本修订幂等.json").read_bytes(),
        )
        view = self._get()
        self.assertEqual(
            (
                (folder / "_内部_" / "剧本.md").read_bytes(),
                (folder / "_内部_" / "剧本候选.json").read_bytes(),
                (folder / "_内部_" / "剧本修订幂等.json").read_bytes(),
            ),
            disk,
        )
        self.assertEqual(view["review_state"], "pending")
        self.assertTrue(view["review_retryable"])
        self.assertEqual(view["phase_reason"], "review_pending")
        self.assertFalse(view["busy"])
        self.assertNotIn("lost-key", json.dumps(view, ensure_ascii=False))
        self.assertNotIn("idempotency_key", view)
        self.assertEqual(self.tasks, [])
        recovered = self._retry(view["base_token"])
        self.assertEqual(recovered["review_state"], "queued")
        self.assertEqual(recovered["message"], "已保存，正在重新审稿")
        self.assertTrue(recovered["unchanged"])
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), disk[0])
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        again = self._retry(view["base_token"])
        self.assertEqual(again["review_state"], "queued")
        self.assertEqual(again["message"], "审稿已在队列里")
        self.assertNotIn("正在重新审稿", again["message"])
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertTrue(agent_queue().wait_until_idle(5))
        done = self._retry(self._get()["base_token"])
        self.assertEqual(done["review_state"], "done")
        self.assertEqual(done["message"], "审稿已结束")
        self.assertFalse(done["review_queued"])
        self.assertNotIn("队列", done["message"])
        self.assertNotIn("正在", done["message"])
        self.assertEqual(self.reviews, [1])
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])

    def test_pending_retry_rejects_stale_history_other_projects_and_plain_writing(self):
        folder = self._project()
        token = self._get()["base_token"]
        self.fail_submit = 1
        edited = _SCRIPT + "待补排。"
        with self.assertRaises(RpcError):
            self._save(edited, token, "pending-1")
        current = self._get()["base_token"]
        (folder / "_内部_" / "剧本.md").write_text(_SCRIPT + "稿又变了。\n", encoding="utf-8")
        with self.assertRaises(RpcError) as caught:
            self._retry(current)
        self.assertEqual(caught.exception.data["reason"], "version_conflict")
        self.assertFalse(caught.exception.data["retry_same_key"])
        self.assertEqual(self.tasks, [])
        (folder / "_内部_" / "剧本.md").write_text(edited + "\n", encoding="utf-8")
        other = self._project("b1b2c3d4e512", "script")
        foreign = rpc_call("projects.script_revision.get", {"id": "b1b2c3d4e512"})["base_token"]
        with self.assertRaises(RpcError) as caught:
            self._retry(foreign)
        self.assertEqual(caught.exception.data["reason"], "token_mismatch")
        self.assertEqual(official_script(folder), edited)
        self.assertEqual(official_script(other), _SCRIPT)
        stale = {
            "entries": {
                "old-story": {
                    "saved_script_hash": "not-this-manuscript",
                    "queued": False,
                    "result": {"saved": True, "review_queued": True, "base_token": "sr1.stale"},
                }
            }
        }
        (folder / "_内部_" / "剧本修订幂等.json").write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        project = read_project("a1b2c3d4e511")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"] = {"current": "script", "action": "script_review", "passed": False}
        project["progress"]["question"] = None
        write_project(project)
        self.assertEqual(self._get()["review_state"], "none")
        with self.assertRaises(RpcError) as caught:
            self._retry(self._get()["base_token"])
        self.assertEqual(caught.exception.data["reason"], "nothing_pending")
        self.assertEqual(self.tasks, [])
        project = read_project("a1b2c3d4e511")
        project["progress"]["pipeline"]["action"] = "script_repair"
        write_project(project)
        self.assertEqual(self._get()["review_state"], "none")
        self.assertNotEqual(self._get()["phase_reason"], "review_pending")

    def test_manual_resume_holds_a_changed_manuscript_and_still_queues_a_match(self):
        folder = self._project()
        token = self._get()["base_token"]
        self.fail_submit = 1
        edited = _SCRIPT + "手动保存的稿。"
        with self.assertRaises(RpcError):
            self._save(edited, token, "manual-1")
        candidate_bytes = (folder / "_内部_" / "剧本候选.json").read_bytes()
        attempts = load_candidate(folder)["attempts"]
        (folder / "_内部_" / "剧本.md").write_text("这是后来改过的另一稿。\n", encoding="utf-8")
        self.assertEqual(resume_agent_tasks(), 1)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.reviews, [])
        self.assertEqual(official_script(folder), "这是后来改过的另一稿。")
        self.assertEqual((folder / "_内部_" / "剧本候选.json").read_bytes(), candidate_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], attempts)
        shown = read_project("a1b2c3d4e511")
        self.assertEqual(shown["status"], "gate")
        self.assertIn("重新审查", shown["progress"]["question"]["reply"])
        self.assertIn("再审一次", shown["progress"]["question"]["options"])
        self.assertEqual(self._get()["review_state"], "none")
        with self.assertRaises(RpcError) as caught:
            self._retry(self._get()["base_token"])
        self.assertEqual(caught.exception.data["reason"], "nothing_pending")
        self.assertEqual(self.tasks, [])

        folder = self._project()
        token = self._get()["base_token"]
        self.fail_submit = 1
        self.review_entered = threading.Event()
        self.review_release = threading.Event()
        with self.assertRaises(RpcError):
            self._save(edited, token, "manual-match")
        script_bytes = (folder / "_内部_" / "剧本.md").read_bytes()
        self.assertEqual(resume_agent_tasks(), 1)
        self.assertTrue(self.review_entered.wait(5))
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        running = read_project("a1b2c3d4e511")
        self.assertEqual(running["status"], "writing")
        self.assertEqual(running["progress"]["pipeline"]["action"], "script_review")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.review_release.set()
        self.assertTrue(agent_queue().wait_until_idle(5))
        settled = read_project("a1b2c3d4e511")
        self.assertEqual(settled["status"], "gate")
        self.assertEqual(settled["progress"]["phase"], "gate_script")
        self.assertEqual(settled["progress"]["pipeline"]["action"], "wait")
        self.assertEqual(load_reviews(folder)["script"]["status"], "passed")
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertEqual(read_project("a1b2c3d4e511")["status"], "gate")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        stored = json.loads((folder / "_内部_" / "剧本修订幂等.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["entries"]["manual-match"]["saved_script_hash"], content_hash(edited))

        self.review_entered = threading.Event()
        self.review_release = threading.Event()
        self.tasks.clear()
        self.reviews.clear()
        bare = self._project("c1b2c3d4e513", "script")
        project = read_project("c1b2c3d4e513")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"] = {"current": "script", "action": "script_review", "passed": False}
        write_project(project)
        self.assertFalse((bare / "_内部_" / "剧本修订幂等.json").is_file())
        self.assertEqual(resume_agent_tasks(), 1)
        self.assertTrue(self.review_entered.wait(5))
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertEqual(read_project("c1b2c3d4e513")["status"], "writing")
        self.review_release.set()
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(read_project("c1b2c3d4e513")["status"], "gate")
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertTrue(agent_queue().wait_until_idle(5))
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertEqual(official_script(bare), _SCRIPT)
        self.assertFalse((bare / "_内部_" / "剧本修订幂等.json").is_file())
        del attempts

    def test_stale_writing_snapshot_does_not_queue_a_second_review(self):
        import end.server as server

        def racing_resume():
            real_list = server.list_projects
            calls = {"n": 0}

            def racing_list():
                calls["n"] += 1
                rows = real_list()
                if calls["n"] >= 2:
                    self.review_release.set()
                    self.assertTrue(agent_queue().wait_until_idle(5))
                return rows

            with patch("end.server.list_projects", side_effect=racing_list):
                self.assertEqual(resume_agent_tasks(), 1)
                self.assertTrue(self.review_entered.wait(5))
                self.assertEqual(resume_agent_tasks(), 0)
            self.assertTrue(agent_queue().wait_until_idle(5))

        folder = self._project()
        token = self._get()["base_token"]
        edited = _SCRIPT + "手动保存的稿。"
        self.fail_submit = 1
        self.review_entered = threading.Event()
        self.review_release = threading.Event()
        with self.assertRaises(RpcError):
            self._save(edited, token, "manual-stale")
        script_bytes = (folder / "_内部_" / "剧本.md").read_bytes()
        racing_resume()
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        shown = read_project("a1b2c3d4e511")
        self.assertEqual(shown["status"], "gate")
        self.assertEqual(shown["progress"]["pipeline"]["action"], "wait")
        self.assertEqual(load_reviews(folder)["script"]["status"], "passed")
        self.assertEqual((folder / "_内部_" / "剧本.md").read_bytes(), script_bytes)
        self.assertEqual(load_candidate(folder)["attempts"], 2)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.reviews), 1)

        self.review_entered = threading.Event()
        self.review_release = threading.Event()
        self.tasks.clear()
        self.reviews.clear()
        bare = self._project("c1b2c3d4e513", "script")
        project = read_project("c1b2c3d4e513")
        project["status"] = "writing"
        project["progress"]["phase"] = "writing"
        project["progress"]["pipeline"] = {"current": "script", "action": "script_review", "passed": False}
        write_project(project)
        racing_resume()
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual([item[0] for item in self.tasks], ["script_review"])
        self.assertEqual(read_project("c1b2c3d4e513")["status"], "gate")
        self.assertEqual(official_script(bare), _SCRIPT)
        self.assertEqual(resume_agent_tasks(), 0)
        self.assertEqual(len(self.reviews), 1)


if __name__ == "__main__":
    unittest.main()
