"""Agent layout, task queue, and the End handoff onto that queue."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from agent.layout import AgentLayout
from agent.model import LLMModel
from end.pipeline import call_drill as pipeline_drill
from end.server import (
    call_deepseek,
    handle_agent_task,
    resume_agent_tasks,
    run_drill,
    run_project,
    start_drill,
)
from task_queue import Task, TaskQueue
from tests.test_project_api import make_project


class Reply(object):
    def __init__(self, content="", reasoning_content=""):
        self.content = content
        self.reasoning_content = reasoning_content or ""


class ScriptedModel(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, model, max_tokens, timeout):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        return Reply(self.replies.pop(0))


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
        model = ScriptedModel([ASK])
        layout = AgentLayout(model=model)
        result = layout.drill([{"role": "user", "content": "雨夜车站"}], model="deepseek-v4-pro")
        self.assertEqual(result["phase"], "ask")
        self.assertEqual(result["reply"], "先定谁在雨里")
        self.assertEqual(result["options"][-1], "其他")
        self.assertEqual(model.calls[0]["max_tokens"], 4000)
        self.assertEqual(model.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(model.calls[0]["messages"][-1]["content"], "雨夜车站")
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

    def test_thinking_model_on_cr_gets_a_larger_budget(self):
        model = ScriptedModel([ASK])
        AgentLayout(model=model).drill([{"role": "user", "content": "雨"}], model="glm-5.2")
        self.assertEqual(model.calls[0]["max_tokens"], 8000)
        self.assertEqual(model.calls[0]["model"], "glm-5.2")

    def test_drill_repairs_a_non_json_reply(self):
        model = ScriptedModel(["先随便说一句", ASK])
        folder = Path(tempfile.mkdtemp())
        result = AgentLayout(model=model).drill(
            [{"role": "user", "content": "雨夜车站"}],
            debug_dir=folder,
            model="deepseek-v4-pro",
        )
        self.assertEqual(result["phase"], "ask")
        self.assertEqual(len(model.calls), 2)
        self.assertIn("收成规定的 JSON", model.calls[1]["messages"][-1]["content"])
        self.assertTrue((folder / "drill_response.txt").is_file())
        self.assertTrue((folder / "drill_response_repaired.txt").is_file())

    def test_write_returns_the_spec(self):
        model = ScriptedModel([SPEC])
        spec = AgentLayout(model=model).write("雨夜车站", "规则", model="deepseek-v4-pro")
        self.assertEqual(spec["title"], "雨夜")
        self.assertEqual(model.calls[0]["max_tokens"], 16000)
        self.assertEqual(model.calls[0]["timeout"], 1200)
        self.assertEqual(model.calls[0]["messages"][-1]["content"], "雨夜车站")

    def test_write_repairs_then_keeps_both_replies(self):
        model = ScriptedModel(["不是 JSON", SPEC])
        folder = Path(tempfile.mkdtemp())
        spec = AgentLayout(model=model).write("雨夜车站", "规则", debug_dir=folder, model="gpt-5.4")
        self.assertEqual(spec["script"], "第一场")
        self.assertIn("不是 JSON", (folder / "raw_response.txt").read_text(encoding="utf-8"))
        self.assertIn("第一场", (folder / "raw_response_repaired.txt").read_text(encoding="utf-8"))

    def test_empty_write_reports_a_missing_body(self):
        layout = AgentLayout(model=ScriptedModel([""]))
        with self.assertRaises(RuntimeError) as caught:
            layout.write("雨夜", "规则", model="deepseek-v4-pro")
        self.assertIn("没有返回正文", str(caught.exception))

    def test_model_error_keeps_the_prompt(self):
        class Boom(object):
            def complete(self, messages, model, max_tokens, timeout):
                raise RuntimeError("down")

        folder = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            AgentLayout(model=Boom()).write("雨夜车站", "规则", debug_dir=folder)
        self.assertIn("雨夜车站", (folder / "user_brief.txt").read_text(encoding="utf-8"))

    def test_repair_call_failure_keeps_the_first_reply(self):
        class ThenBoom(object):
            def __init__(self):
                self.calls = 0

            def complete(self, messages, model, max_tokens, timeout):
                self.calls += 1
                if self.calls == 1:
                    return Reply("不是 JSON")
                raise RuntimeError("down")

        folder = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            AgentLayout(model=ThenBoom()).write("雨夜车站", "规则", debug_dir=folder)
        self.assertIn("不是 JSON", (folder / "raw_response.txt").read_text(encoding="utf-8"))

    def test_official_model_uses_deepseek(self):
        with patch("deepseek_api.DeepSeekClient") as client:
            client.return_value.chat.return_value = Reply("ok")
            LLMModel().complete(
                [{"role": "user", "content": "hi"}],
                model="deepseek-v4-pro",
                max_tokens=20,
                timeout=5,
            )
        kwargs = client.return_value.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek-v4-pro")
        self.assertEqual(kwargs["thinking"], {"type": "disabled"})
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_other_models_use_cr(self):
        with patch("cr_api.CRClient") as client:
            client.return_value.chat.return_value = Reply("ok")
            LLMModel().complete(
                [{"role": "user", "content": "hi"}],
                model="gpt-5.4",
                max_tokens=20,
                timeout=5,
            )
        kwargs = client.return_value.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.4")
        self.assertNotIn("thinking", kwargs)

    def test_pipeline_drill_uses_the_layout(self):
        with patch("agent.layout.get_layout") as get_layout:
            get_layout.return_value.drill.return_value = {"phase": "ask", "reply": "问", "options": []}
            self.assertEqual(pipeline_drill([{"role": "user", "content": "雨"}])["phase"], "ask")
        get_layout.return_value.drill.assert_called_once()


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

        def fake(messages, debug_dir=None, model=None):
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

        def fake(messages, debug_dir=None, model=None):
            return {"phase": "ready", "reply": "开始写", "brief": "锁定的简报", "options": []}

        with patch("end.server.call_drill", side_effect=fake), patch(
            "end.server.submit_agent_task", side_effect=submitted.append
        ):
            run_drill(project_id)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].kind, "write")
        self.assertEqual(submitted[0].project_id, project_id)
        self.assertTrue(submitted[0].payload["debug_llm"] is False)
        saved = db.read_project(project_id)
        self.assertEqual(saved["brief"], "锁定的简报")
        self.assertEqual(saved["status"], "writing")
        self.assertEqual(saved["progress"]["phase"], "writing")
        self.assertEqual(saved["messages"][-1]["content"], "开始写")

    def test_finished_script_hands_render_to_another_thread(self):
        project_id = "a6e170000014"
        make_project(project_id, status="writing")
        spec = {
            "title": "新标题",
            "script": "写完的剧本",
            "storyboard": "写完的分镜",
            "image_prompts": "提示",
            "assets": [{"id": "A1", "title": "主角", "prompt": "一个女人"}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "离开", "images": ["A1"]}],
        }
        with patch("end.server.call_deepseek", return_value=spec), patch(
            "end.server.start_render_thread"
        ) as start:
            run_project(project_id)
        start.assert_called_once_with(project_id, replace=True)
        self.assertEqual(db.read_project(project_id)["status"], "building")
        self.assertEqual(db.read_project(project_id)["script"], "写完的剧本")

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

    def test_server_write_uses_the_layout(self):
        with patch("end.server.get_layout") as get_layout:
            get_layout.return_value.write.return_value = {"title": "雨"}
            self.assertEqual(call_deepseek("简报", "规则", model="gpt-5.4")["title"], "雨")
        get_layout.return_value.write.assert_called_once()
