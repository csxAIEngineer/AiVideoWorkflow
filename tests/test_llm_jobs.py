"""Model calls return a local job, then a short status query carries the body."""

import json
import threading
import unittest
from unittest.mock import patch

from AI.api import llm_status, write
from AI.qwen_api.jobs import node_name, snapshot, submit
from AI.qwen_api.suite_graph import compile_suite_graph
from AI.skills.registry import suite_files
from end.ai_client import RemoteError, job_message
from end.ai_client import write as client_write


class _Reply(object):
    def __init__(self, content):
        self.content = content


class _BlockingModel(object):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        self.entered.set()
        self.release.wait(5)
        system = messages[0]["content"]
        if "你是汇总节点" in system:
            return _Reply(json.dumps({"script": "正式剧本"}, ensure_ascii=False))
        return _Reply(json.dumps({"text": "片段"}, ensure_ascii=False))


class JobStoreTest(unittest.TestCase):
    def test_submit_is_waiting_until_the_work_moves_a_node(self):
        entered = threading.Event()
        release = threading.Event()

        def work(job_id):
            entered.set()
            from AI.qwen_api.jobs import set_node

            set_node(job_id, "write", "进行中")
            release.wait(5)
            set_node(job_id, "write", "完成")
            return {"text": "正文"}

        snap = submit([{"id": "write", "name": "写稿"}], work)
        self.assertEqual(snap["status"], "running")
        self.assertEqual(snap["nodes"][0]["status"], "等待")
        self.assertTrue(entered.wait(2))
        self.assertEqual(snapshot(snap["job_id"])["nodes"][0]["status"], "进行中")
        release.set()
        self.assertTrue(_wait_status(snap["job_id"], "succeeded"))
        done = snapshot(snap["job_id"])
        self.assertEqual(done["result"]["text"], "正文")
        self.assertEqual(done["nodes"][0]["status"], "完成")

    def test_unknown_job_is_lost(self):
        lost = snapshot("missing")
        self.assertEqual(lost["status"], "lost")
        self.assertEqual(lost["nodes"], [])


class WriteJobTest(unittest.TestCase):
    def test_write_returns_before_the_suite_finishes(self):
        release = threading.Event()
        files = suite_files("script", "任务：剧本")

        def fake_suite(task, brief, model, model_name=None, template=None, trace_path="", kind="write", job_id=""):
            from AI.qwen_api.jobs import set_node

            set_node(job_id, files[0], "进行中")
            release.wait(5)
            set_node(job_id, files[0], "完成")
            return {"script": "正文", "partials": []}

        with patch("AI.agent.layout.run_suite", fake_suite):
            snap = write({"brief": "任务：剧本", "model": "qwen3.8-flash"})
            self.assertEqual(snap["status"], "running")
            self.assertTrue(snap["nodes"])
            self.assertTrue(all(item["status"] == "等待" for item in snap["nodes"]))
            self.assertEqual(snap["nodes"][-1]["name"], "汇总")
            self.assertTrue(_wait_node(snap["job_id"], files[0], "进行中"))
            release.set()
            self.assertTrue(_wait_status(snap["job_id"], "succeeded"))
        done = llm_status({"job_id": snap["job_id"]})
        self.assertEqual(done["result"]["script"], "正文")


class SuiteStatusTest(unittest.TestCase):
    def test_nodes_move_from_running_to_done(self):
        files = suite_files("script", "任务：剧本")[:2]
        model = _BlockingModel()
        nodes = [{"id": path, "name": node_name(path)} for path in files]
        nodes.append({"id": "merge", "name": "汇总"})
        app = compile_suite_graph(files, "script", model, "write")

        def work(job_id):
            state = app.invoke(
                {
                    "kind": "write",
                    "brief": "任务：剧本",
                    "task": "script",
                    "model": "qwen3.8-flash",
                    "trace_path": "",
                    "job_id": job_id,
                    "partials": [],
                    "output": "",
                    "messages": [],
                }
            )
            return {"script": state.get("output") or ""}

        snap = submit(nodes, work)
        self.assertTrue(model.entered.wait(3))
        mid = snapshot(snap["job_id"])
        self.assertTrue(any(item["status"] == "进行中" for item in mid["nodes"]))
        model.release.set()
        self.assertTrue(_wait_status(snap["job_id"], "succeeded"))
        done = snapshot(snap["job_id"])
        self.assertTrue(all(item["status"] == "完成" for item in done["nodes"]))
        self.assertIn("正式剧本", done["result"]["script"])


class ClientPollTest(unittest.TestCase):
    def test_write_uses_short_calls_and_returns_the_body(self):
        seen = []

        def fake_call(method, params=None, timeout=60):
            seen.append((method, timeout))
            if method == "write":
                return {"job_id": "job-1", "status": "running", "nodes": [], "result": None, "error": ""}
            return {
                "job_id": "job-1",
                "status": "succeeded",
                "nodes": [],
                "result": {"script": "正文"},
                "error": "",
            }

        with patch("end.ai_client.call", fake_call), patch("end.ai_client.time.sleep"):
            result = client_write("任务：剧本", "")
        self.assertEqual(result["script"], "正文")
        self.assertEqual(seen[0][0], "write")
        self.assertLessEqual(seen[0][1], 30)
        self.assertEqual(seen[1][0], "llm.status")
        self.assertLessEqual(seen[1][1], 15)
        self.assertNotIn(4000, [timeout for _method, timeout in seen])

    def test_lost_job_raises(self):
        def fake_call(method, params=None, timeout=60):
            if method == "write":
                return {"job_id": "job-2", "status": "running", "nodes": [], "result": None, "error": ""}
            return {"job_id": "job-2", "status": "lost", "nodes": [], "result": None, "error": "任务已丢失"}

        with patch("end.ai_client.call", fake_call), patch("end.ai_client.time.sleep"):
            with self.assertRaises(RemoteError):
                client_write("任务：剧本", "")

    def test_message_names_the_active_node(self):
        nodes = [{"name": "a", "status": "完成"}, {"name": "character-portrait-templates", "status": "进行中"}]
        self.assertEqual(job_message("资产锁定", nodes), "资产锁定 1/2 · character-portrait-templates 进行中")


class DeadlineTest(unittest.TestCase):
    def test_a_stuck_model_call_closes_the_socket(self):
        from AI.qwen_api.graph import _call_with_deadline

        class Http(object):
            def __init__(self):
                self.closed = threading.Event()

            def close(self):
                self.closed.set()

        class Responses(object):
            def __init__(self, http):
                self.http = http

            def create(self, **kwargs):
                if not self.http.closed.wait(2):
                    raise AssertionError("deadline did not close the client")
                raise RuntimeError("closed")

        class Client(object):
            def __init__(self):
                self._client = Http()
                self.responses = Responses(self._client)

        with self.assertRaises(RuntimeError):
            _call_with_deadline(Client(), {}, 0.2)


def _wait_status(job_id, status):
    for _ in range(50):
        if snapshot(job_id)["status"] == status:
            return True
        threading.Event().wait(0.02)
    return False


def _wait_node(job_id, node_id, status):
    for _ in range(50):
        nodes = snapshot(job_id)["nodes"]
        if any(item["id"] == node_id and item["status"] == status for item in nodes):
            return True
        threading.Event().wait(0.02)
    return False


if __name__ == "__main__":
    unittest.main()
