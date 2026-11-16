"""When AIVIDEO_DEBUG_TRACE is on, the entire worker prompt is saved."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AI.qwen_api.graph import bind_trace_prompts, log_prompt
from Agents.text import complete_text
from end.stage_runner import _worker_complete
from end.trace import begin_trace


class WorkerPromptLogTest(unittest.TestCase):
    def test_flag_off_does_not_open_a_prompt_log(self):
        folder = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "0"}):
            self.assertEqual(begin_trace(folder, "feed", False, "简报"), "")
        self.assertFalse((folder / "debug" / "trace").exists())

    def test_log_prompt_keeps_the_entire_messages(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "prompts.jsonl"
        messages = [
            {"role": "system", "content": "规则全文，不要截断。\n第二段规则。"},
            {"role": "user", "content": "写这一段完整任务。"},
        ]
        with bind_trace_prompts(str(path)):
            log_prompt(messages)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["messages"], messages)
        self.assertIn("规则全文，不要截断。", record["prompt"])
        self.assertIn("写这一段完整任务。", record["prompt"])

    def test_complete_text_writes_the_full_prompt_when_traced(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "prompts.jsonl"

        class FakeResponses(object):
            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                return {"output_text": "稿"}

        messages = [
            {"role": "system", "content": "封装工人完整规则"},
            {"role": "user", "content": "请填写封装骨架。"},
        ]
        complete_text(
            "specialist",
            messages,
            client=FakeResponses(),
            trace_path=str(path),
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["messages"], messages)
        self.assertEqual(record["role"], "specialist")
        self.assertIn("封装工人完整规则", record["prompt"])

    def test_worker_complete_also_traces_the_critic_when_the_flag_is_on(self):
        calls = []

        def fake_complete_node(role, messages, **kwargs):
            calls.append((role, kwargs.get("trace_prompts") or ""))
            return "ok"

        with patch("end.ai_client.complete_node", side_effect=fake_complete_node):
            complete = _worker_complete({"model": "qwen4-max"}, "/tmp/proj", None, "/tmp/trace.prompts.jsonl")
            complete("specialist", [{"role": "user", "content": "写"}])
            complete("critic", [{"role": "user", "content": "审"}])
        self.assertEqual(calls[0][0], "specialist")
        self.assertEqual(calls[0][1], "/tmp/trace.prompts.jsonl")
        self.assertEqual(calls[1][0], "critic")
        self.assertEqual(calls[1][1], "/tmp/trace.prompts.jsonl")

    def test_worker_complete_writes_nothing_without_a_trace_path(self):
        calls = []

        def fake_complete_node(role, messages, **kwargs):
            calls.append(kwargs.get("trace_prompts") or "")
            return "ok"

        with patch("end.ai_client.complete_node", side_effect=fake_complete_node):
            complete = _worker_complete({"model": "qwen4-max"}, "/tmp/proj", None, "")
            complete("specialist", [{"role": "user", "content": "写"}])
        self.assertEqual(calls, [""])


if __name__ == "__main__":
    unittest.main()
