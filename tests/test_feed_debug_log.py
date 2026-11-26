"""Packing debug log is written without AIVIDEO_DEBUG_TRACE.

    python3 -m unittest tests.test_feed_debug_log
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.text import complete_text
from Agents.workers.feed import (
    resolve_feed_debug_dir,
    run_feed_loop,
    write_feed_debug,
)
from end.db import ROOT
from end.product_names import unit_heading


ACCEPT = '{"verdict":"accept","issues":[]}'
PASS = {"ok": True, "errors": [], "warnings": [], "fatal": "", "needs_human": False, "report": ""}


class CompleteTextMetaTest(unittest.TestCase):
    def test_complete_text_still_returns_a_string(self):
        class FakeResponses(object):
            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                return {
                    "output_text": "封装正文",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "choices": [{"finish_reason": "length"}],
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 22,
                        "output_tokens_details": {"reasoning_tokens": 3},
                    },
                    "error": {"message": "truncated"},
                }

        text = complete_text(
            "generator",
            [{"role": "user", "content": "写"}],
            client=FakeResponses(),
        )
        self.assertIsInstance(text, str)
        self.assertEqual(text, "封装正文")
        meta = complete_text(
            "generator",
            [{"role": "user", "content": "写"}],
            client=FakeResponses(),
            return_meta=True,
        )
        self.assertEqual(meta["text"], "封装正文")
        self.assertEqual(meta["finish_reason"], "length")
        self.assertEqual(meta["incomplete_reason"], "max_output_tokens")
        self.assertEqual(meta["response_status"], "incomplete")
        self.assertEqual(meta["input_tokens"], 11)
        self.assertEqual(meta["output_tokens"], 22)
        self.assertEqual(meta["reasoning_tokens"], 3)
        self.assertEqual(meta["response_error"], "truncated")

    def test_complete_node_default_return_is_text(self):
        payload = {
            "text": "一节点",
            "finish_reason": "length",
            "incomplete_reason": "max_output_tokens",
            "response_status": "incomplete",
            "input_tokens": 4,
            "output_tokens": 9,
            "reasoning_tokens": 0,
            "response_error": "",
        }
        with patch("end.ai_client.await_job", return_value=payload):
            from end.ai_client import complete_node

            text = complete_node("director", [{"role": "user", "content": "汇总"}])
            meta = complete_node(
                "director",
                [{"role": "user", "content": "汇总"}],
                return_meta=True,
            )
        self.assertIsInstance(text, str)
        self.assertEqual(text, "一节点")
        self.assertEqual(meta["text"], "一节点")
        self.assertEqual(meta["finish_reason"], "length")
        self.assertEqual(meta["output_tokens"], 9)


class FeedDebugLogTest(unittest.TestCase):
    def test_logger_writes_a_record_and_sidecar(self):
        folder = Path(tempfile.mkdtemp())
        secret = "sk-" + ("a" * 20)
        signed = "https://bucket.example/feed.txt?X-Amz-Signature=abc123&X-Amz-Credential=hidden"
        raw = "\n".join((
            unit_heading(1, 2),
            "subject_definitions:",
            "画面风格：冷白",
            "Authorization: Bearer " + secret,
            "detailed_description:",
            "[Shot 1] 她推门",
            "[Shot 2] At 00:04.000 回头",
            signed,
            "",
        ))
        state = {
            "project_id": "proj-1",
            "project_folder": str(folder),
            "script": "画风：冷白",
            "locks": {"model": "h3"},
            "storyboard": "",
            # h3 现在向 30s 封装（cc9f410）；20+20 超 30 仍拆两个单元，保持 planned_units=2。
            "shots": [{"duration": 20}, {"duration": 20}],
            "partials": [],
        }
        path = write_feed_debug(
            state,
            1,
            "任务：提示词封装\n" + signed,
            [{
                "role": "director",
                "label": "director",
                "skill": "sd-feed-engine/references/feed-mode-and-gates.md",
                "text": raw,
                "meta": {
                    "finish_reason": "length",
                    "incomplete_reason": "max_output_tokens",
                    "response_status": "incomplete",
                    "input_tokens": 8,
                    "output_tokens": 40,
                    "reasoning_tokens": 0,
                    "response_error": "",
                },
            }],
            extracted=raw,
            planned_text=raw,
        )
        self.assertEqual(path, folder / "debug" / "feed-debug.jsonl")
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["project_id"], "proj-1")
        self.assertEqual(record["attempt"], 1)
        self.assertIs(record["repair"], False)
        self.assertNotIn("selected_nodes", record)
        call = record["calls"][0]
        self.assertEqual(call["role"], "director")
        self.assertEqual(call["skill"], "sd-feed-engine/references/feed-mode-and-gates.md")
        self.assertEqual(call["chars"], len(raw))
        self.assertEqual(call["finish_reason"], "length")
        self.assertEqual(call["incomplete_reason"], "max_output_tokens")
        self.assertEqual(call["response_status"], "incomplete")
        self.assertEqual(call["input_tokens"], 8)
        self.assertEqual(call["output_tokens"], 40)
        self.assertIn(unit_heading(1, 2), call["unit_headings"])
        sidecar = folder / call["text_path"]
        self.assertTrue(sidecar.is_file())
        stored = sidecar.read_text(encoding="utf-8")
        self.assertNotIn(secret, stored)
        self.assertNotIn("X-Amz-Signature=abc123", stored)
        self.assertNotIn(secret, lines[0])
        self.assertNotIn("X-Amz-Signature=abc123", lines[0])
        extract = record["extract_feed"]
        self.assertEqual(extract["planned_units"], 2)
        self.assertEqual(extract["heading_count"], 1)
        self.assertIs(extract["materialize_units"], True)
        unit = extract["units"][0]
        self.assertTrue(unit["fields"]["subject_definitions"])
        self.assertTrue(unit["fields"]["detailed_description"])
        self.assertFalse(unit["fields"]["overall_soundscape"])
        self.assertFalse(unit["fields"]["non_diegetic_music"])
        self.assertIs(unit["style_lock"], True)
        self.assertEqual(unit["shots"], ["[Shot 1] 她推门", "[Shot 2] At 00:04.000 回头"])
        plan = record["apply_packing_plan"]
        self.assertIs(plan["materialize_units"], True)
        self.assertEqual(plan["prepared_heading_count"], 1)
        self.assertEqual(plan["planned_units"], 2)
        self.assertTrue((folder / "debug" / "feed-attempt-1-director.txt").is_file())
        self.assertTrue((folder / "debug" / "feed-attempt-1-extract.txt").is_file())
        self.assertTrue((folder / "debug" / "feed-attempt-1-plan.txt").is_file())

    def test_repair_record_names_selected_nodes_and_machine_errors(self):
        from AI.skills.registry import suite_files

        folder = Path(tempfile.mkdtemp())
        route = "阶段：P4\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n"
        named = suite_files("feed", route)[0]
        state = {
            "project_id": "proj-2",
            "project_folder": str(folder),
            "script": "",
            "locks": {},
            "storyboard": "",
            "shots": [],
            "partials": [{"skill": named, "text": "旧节点"}],
            "machine_report": {"errors": ["画风锁：第一行不是锁定句"], "report": ""},
        }
        brief = "任务：建议修补\n重跑：\n%s\n" % named
        write_feed_debug(state, 2, brief, [], extracted="", planned_text="")
        record = json.loads((folder / "debug" / "feed-debug.jsonl").read_text(encoding="utf-8"))
        self.assertIs(record["repair"], True)
        self.assertEqual(record["attempt"], 2)
        self.assertEqual(record["selected_nodes"], [named])
        self.assertEqual(record["machine_errors"], "画风锁：第一行不是锁定句")

    def test_missing_folder_uses_repo_data_dir(self):
        self.assertEqual(resolve_feed_debug_dir(""), ROOT / "data")
        self.assertEqual(resolve_feed_debug_dir("/tmp/proj"), Path("/tmp/proj/debug"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch("end.db.ROOT", Path(tmp)):
                path = write_feed_debug(
                    {"project_id": "no-folder", "project_folder": ""},
                    1,
                    "简报",
                    [],
                )
            self.assertEqual(path, Path(tmp) / "data" / "feed-debug.jsonl")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["project_id"], "no-folder")

    def test_packing_loop_writes_debug_when_trace_is_off(self):
        folder = Path(tempfile.mkdtemp())
        feed = unit_heading(1, 1) + "\n第一版封装正文。\n"

        def complete(role, messages, return_meta=False):
            if role == "critic":
                body = ACCEPT
            elif role == "director":
                body = json.dumps({"feed": feed}, ensure_ascii=False)
            else:
                body = json.dumps({"text": "节点"}, ensure_ascii=False)
            if not return_meta:
                return body
            return {
                "text": body,
                "finish_reason": "stop",
                "incomplete_reason": "",
                "response_status": "completed",
                "input_tokens": 5,
                "output_tokens": 7,
                "reasoning_tokens": 1,
                "response_error": "",
            }

        with patch.dict(os.environ, {"AIVIDEO_DEBUG_TRACE": "0"}):
            result = run_feed_loop(
                "分镜正文",
                opening="任务：提示词封装\n分镜：\n她推门。",
                max_attempts=1,
                complete=complete,
                checker=lambda text, state: dict(PASS),
                project_id="proj-loop",
                project_folder=str(folder),
            )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["draft"], feed.strip())
        log = folder / "debug" / "feed-debug.jsonl"
        self.assertTrue(log.is_file())
        record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["project_id"], "proj-loop")
        self.assertEqual(record["attempt"], 1)
        self.assertIs(record["repair"], False)
        roles = [item["role"] for item in record["calls"]]
        self.assertIn("specialist", roles)
        self.assertIn("director", roles)
        specialists = [item for item in record["calls"] if item["role"] == "specialist"]
        self.assertTrue(all(item["skill"].endswith(".md") for item in specialists))
        director = [item for item in record["calls"] if item["role"] == "director"][0]
        self.assertEqual(director["finish_reason"], "stop")
        self.assertEqual(director["output_tokens"], 7)
        self.assertGreater(director["chars"], 0)
        self.assertTrue((folder / director["text_path"]).is_file())
        self.assertIn("planned_units", record["extract_feed"])
        self.assertIn("heading_count", record["extract_feed"])
        self.assertIn("materialize_units", record["apply_packing_plan"])
        self.assertIn("units", record["apply_packing_plan"])


if __name__ == "__main__":
    unittest.main()
