"""Feel-driven retouch rewrites one block, keeps the diff, and previews from that node."""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.script_review import set_reviewer
from end.db import project_dir, read_project, write_project
from end.locks import write_locks
from end.render_loop import load_run_state
from end.rerun import steps_to_invalidate
from end.server import (
    _PIPELINE_HANDOFF,
    RpcError,
    fail_project,
    handle_agent_task,
    public_view,
    rpc_call,
)
from end.task_queue import Task
from end.workflow_from_spec import collect_steps
from tests.test_project_api import make_project


RENDER_SPEC = {
    "title": "雨夜",
    "script": "旧剧本",
    "storyboard": "旧分镜",
    "image_prompts": "#### 图1 · 主角\n主角提示\n\n#### 图2 · 侧面\n侧面提示\n\n#### 图3 · 雨\n雨提示\n",
    "assets": [
        {"id": "A1", "title": "主角", "prompt": "主角提示"},
        {"id": "A2", "title": "侧面", "prompt": "侧面提示", "depends_on": "A1"},
        {"id": "A3", "title": "雨", "prompt": "雨提示"},
    ],
    "feed": "## 【投喂单元 1/3】\n第一镜\n\n## 【投喂单元 2/3】\n第二镜\n\n## 【投喂单元 3/3】\n第三镜\n",
    "shots": [
        {"id": "S01", "duration": 5, "prompt": "第一镜", "images": ["A1"]},
        {"id": "S02", "duration": 5, "prompt": "第二镜", "images": ["A3"]},
        {"id": "S03", "duration": 5, "prompt": "第三镜", "images": ["A3"]},
    ],
}


@contextmanager
def quiet_catalog():
    """projects.get labels models through AI. These tests only check the retouch fields."""
    with patch("end.server.normalize_model", side_effect=lambda value: value or "qwen3.8-flash"), patch(
        "end.server.model_label", side_effect=lambda value: value or "Qwen"
    ), patch(
        "end.server.normalize_template",
        side_effect=lambda value: value or "short-drama-director",
    ), patch("end.server.template_label", side_effect=lambda value: value or "短剧"):
        yield


def _done(kind):
    return {
        "kind": kind,
        "status": "done",
        "remote_task_id": "",
        "poll_url": "",
        "idempotency_key": "",
        "attempts": 1,
        "epoch": 1,
        "output_file": "",
        "public_url": "",
        "local_override": False,
        "error": "",
    }


class RetouchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        self.ids = []
        from tests.offline_guard import install

        install()
        set_reviewer(lambda _messages: '{"issues":[]}')

    def tearDown(self):
        from tests.offline_guard import uninstall

        set_reviewer(None)
        uninstall()
        for project_id in self.ids:
            _PIPELINE_HANDOFF.pop(project_id, None)
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_prompts_ready_without_media_cannot_retouch(self):
        project_id = "a11c07e00001"
        self._ready(project_id, rendered=False)
        with patch("end.server.submit_agent_task") as submit, patch("end.revise.run_block_call") as block:
            with self.assertRaises(RuntimeError) as raised:
                self._accept(project_id, "asset", "A1", "更冷一点")
        self.assertIn("还没出过图", str(raised.exception))
        submit.assert_not_called()
        block.assert_not_called()
        self.assertNotIn("retouching", read_project(project_id)["progress"])
        with self.assertRaises(RpcError) as raised:
            rpc_call(
                "projects.retouch",
                {"id": project_id, "node": "shot", "target_id": "S01", "suggestion": "再近一点"},
            )
        self.assertEqual(raised.exception.code, 409)
        self.assertIn("还没出过图", str(raised.exception))

    def test_unfinished_prompts_are_rejected_before_a_missing_picture(self):
        project_id = "b011d07e0002"
        make_project(project_id, status="error")
        self.ids.append(project_id)
        with patch("end.server.submit_agent_task") as submit:
            with self.assertRaises(RuntimeError) as raised:
                self._accept(project_id, "asset", "A1", "更冷一点")
        self.assertIn("还没写完", str(raised.exception))
        submit.assert_not_called()

    def test_rpc_rejects_the_wrong_shape(self):
        project_id = "c0de07e00003"
        self._ready(project_id, rendered=True)
        with self.assertRaises(RpcError) as missing:
            rpc_call(
                "projects.retouch",
                {"id": "abcdefabcdef", "node": "asset", "target_id": "A1", "suggestion": "更冷"},
            )
        self.assertEqual(missing.exception.code, 404)
        with self.assertRaises(RpcError) as bad_node:
            rpc_call(
                "projects.retouch",
                {"id": project_id, "node": "script", "target_id": "", "suggestion": "雨更大"},
            )
        self.assertEqual(bad_node.exception.code, 400)
        self.assertIn("单张图或单镜头", str(bad_node.exception))
        with self.assertRaises(RpcError) as empty:
            rpc_call(
                "projects.retouch",
                {"id": project_id, "node": "shot", "target_id": "S01", "suggestion": "  "},
            )
        self.assertEqual(empty.exception.code, 400)
        self.assertIn("想怎么改", str(empty.exception))
        with self.assertRaises(RpcError) as unknown:
            rpc_call(
                "projects.retouch",
                {"id": project_id, "node": "asset", "target_id": "NOPE", "suggestion": "更冷"},
            )
        self.assertEqual(unknown.exception.code, 400)
        self.assertIn("没有资产", str(unknown.exception))
        with self.assertRaises(RpcError) as short:
            rpc_call("projects.retouch", {"id": "nope", "node": "asset", "suggestion": "更冷"})
        self.assertEqual(short.exception.code, 404)

    def test_busy_or_in_flight_retouch_is_rejected(self):
        project_id = "d00d07e00004"
        self._ready(project_id, rendered=True)
        _PIPELINE_HANDOFF[project_id] = "render"
        with patch("end.server.thread_alive", return_value=True), patch(
            "end.server.submit_agent_task"
        ) as submit:
            with self.assertRaises(RuntimeError) as raised:
                self._accept(project_id, "shot", "S02", "别这么油")
        self.assertIn("正在跑", str(raised.exception))
        submit.assert_not_called()
        _PIPELINE_HANDOFF[project_id] = "yielded"
        with patch("end.server.submit_agent_task", return_value=True):
            self._accept(project_id, "shot", "S02", "别这么油")
        with patch("end.server.submit_agent_task") as submit:
            with self.assertRaises(RuntimeError) as raised:
                self._accept(project_id, "shot", "S02", "再来一次")
        self.assertIn("正在跑", str(raised.exception))
        submit.assert_not_called()
        progress = read_project(project_id)["progress"]
        self.assertEqual(progress["retouching"]["phase"], "prompt")
        self.assertNotIn("revising", progress)

    def test_asset_retouch_previews_only_the_dependent_chain(self):
        project_id = "a55e707e0005"
        folder = self._ready(project_id, rendered=True)
        self._queue(project_id, "asset", "A3", "更冷一点，光再硬")

        def fake(kind, current, suggestion, _model):
            self.assertEqual(kind, "asset")
            self.assertIn("雨提示", current)
            self.assertEqual(suggestion, "更冷一点，光再硬")
            return "新雨"

        with quiet_catalog(), patch("end.revise.run_block_call", side_effect=fake), patch(
            "end.revise.run_suite_call"
        ) as suite, patch("end.server.start_render_thread") as render:
            from end.retouch import perform_retouch

            perform_retouch(project_id)
        suite.assert_not_called()
        render.assert_called_once()
        self.assertEqual(render.call_args.kwargs.get("video_submit_mode"), "preview")
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        assets, _shots = collect_steps(saved)
        self.assertEqual(assets[2]["prompt"], "新雨")
        self.assertEqual(assets[0]["prompt"], "主角提示")
        self.assertEqual(assets[1]["prompt"], "侧面提示")
        self.assertIn("新雨", saved["image_prompts"])
        self.assertIn("主角提示", saved["image_prompts"])
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertTrue((folder / "outputs" / "images" / "A2.png").is_file())
        self.assertFalse((folder / "outputs" / "images" / "A3.png").is_file())
        self.assertTrue((folder / "outputs" / "clips" / "S01.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S02.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S03.mp4").is_file())
        self.assertFalse((folder / "outputs" / "joined.mp4").is_file())
        state = load_run_state(folder)
        invalidated = [step_id for step_id, _kind in steps_to_invalidate(RENDER_SPEC, "asset", "A3")]
        for step_id in invalidated:
            self.assertEqual(state["steps"][step_id]["status"], "pending", step_id)
        self.assertEqual(state["steps"]["A1"]["status"], "done")
        self.assertEqual(state["steps"]["S01"]["status"], "done")
        project = read_project(project_id)
        self.assertEqual(project["status"], "building")
        tasks = {task["id"]: task["state"] for task in project["tasks"]}
        self.assertEqual(tasks["A1"], "done")
        self.assertEqual(tasks["A3"], "pending")
        self.assertEqual(tasks["S01"], "done")
        self.assertEqual(tasks["S02"], "pending")
        progress = project["progress"]
        self.assertEqual(progress["retouching"]["phase"], "render")
        self.assertEqual(progress["retouching"]["node"], "asset")
        self.assertEqual(progress["prompt_diff"]["before"], "雨提示")
        self.assertEqual(progress["prompt_diff"]["after"], "新雨")
        self.assertEqual(progress["prompt_diff"]["suggestion"], "更冷一点，光再硬")
        self.assertEqual(progress["video_submit_mode"], "preview")
        self.assertNotIn("retouch_error", progress)
        self.assertNotIn("revising", progress)
        self.assertNotIn("revise_error", progress)
        with quiet_catalog():
            view = public_view(project)
        self.assertEqual(view["progress"]["prompt_diff"]["after"], "新雨")
        self.assertEqual(view["progress"]["retouching"]["target_id"], "A3")

    def test_shot_retouch_rewrites_one_feed_unit_and_keeps_earlier_media(self):
        project_id = "5107707e0006"
        folder = self._ready(project_id, rendered=True)
        self._queue(project_id, "shot", "S02", "镜头再近")

        def fake(kind, current, suggestion, _model):
            self.assertEqual(kind, "shot")
            self.assertIn("第二镜", current)
            self.assertNotIn("第一镜", current)
            self.assertEqual(suggestion, "镜头再近")
            return "新的第二镜"

        with quiet_catalog(), patch("end.revise.run_block_call", side_effect=fake), patch(
            "end.server.start_render_thread"
        ) as render:
            from end.retouch import perform_retouch

            perform_retouch(project_id)
        self.assertEqual(render.call_args.kwargs.get("video_submit_mode"), "preview")
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        _assets, shots = collect_steps(saved)
        self.assertIn("第一镜", shots[0]["prompt"])
        self.assertNotIn("新的第二镜", shots[0]["prompt"])
        self.assertIn("新的第二镜", shots[1]["prompt"])
        self.assertNotIn("第二镜", [line.strip() for line in shots[1]["prompt"].splitlines()])
        self.assertIn("第三镜", shots[2]["prompt"])
        feed = (folder / "_内部_" / "feed.md").read_text(encoding="utf-8")
        self.assertIn("第一镜", feed)
        self.assertIn("新的第二镜", feed)
        self.assertIn("第三镜", feed)
        self.assertEqual(feed.count("投喂单元"), 3)
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertTrue((folder / "outputs" / "images" / "A3.png").is_file())
        self.assertTrue((folder / "outputs" / "clips" / "S01.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S02.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S03.mp4").is_file())
        state = load_run_state(folder)
        self.assertEqual(state["steps"]["A1"]["status"], "done")
        self.assertEqual(state["steps"]["S01"]["status"], "done")
        self.assertEqual(state["steps"]["S02"]["status"], "pending")
        progress = read_project(project_id)["progress"]
        self.assertEqual(progress["prompt_diff"]["node"], "shot")
        self.assertEqual(progress["prompt_diff"]["target_id"], "S02")
        self.assertIn("第二镜", progress["prompt_diff"]["before"])
        self.assertIn("新的第二镜", progress["prompt_diff"]["after"])
        self.assertNotIn("第一镜", progress["prompt_diff"]["after"])
        self.assertEqual(progress["video_submit_mode"], "preview")

    def test_model_failure_keeps_the_old_prompt_and_the_previous_diff(self):
        project_id = "e11a5707e007"
        folder = self._ready(project_id, rendered=True)
        self._queue(project_id, "asset", "A3", "更冷一点")
        project = read_project(project_id)
        progress = dict(project["progress"])
        progress["prompt_diff"] = {
            "node": "asset",
            "target_id": "A1",
            "before": "旧",
            "after": "上一版",
            "suggestion": "上次",
            "at": "2026-11-01T00:00:00+08:00",
        }
        project["progress"] = progress
        write_project(project)
        with patch("end.revise.run_block_call", side_effect=RuntimeError("没有生成新的提示词")), patch(
            "end.server.start_render_thread"
        ) as render:
            from end.retouch import perform_retouch

            perform_retouch(project_id)
        render.assert_not_called()
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        assets, _shots = collect_steps(saved)
        self.assertEqual(assets[2]["prompt"], "雨提示")
        progress = read_project(project_id)["progress"]
        self.assertNotIn("retouching", progress)
        self.assertEqual(progress["prompt_diff"]["after"], "上一版")
        self.assertEqual(progress["retouch_error"]["key"], "asset:A3")
        self.assertIn("没有生成", progress["retouch_error"]["message"])
        self.assertTrue((folder / "outputs" / "images" / "A3.png").is_file())

    def test_render_failure_keeps_the_new_prompt_and_the_diff(self):
        project_id = "f11a5707e008"
        folder = self._ready(project_id, rendered=True)
        self._queue(project_id, "shot", "S01", "她不要回头")
        with patch("end.revise.run_block_call", return_value="新的第一镜"), patch(
            "end.retouch.rerun_from", side_effect=RuntimeError("出片失败")
        ), patch("end.server.start_render_thread") as render:
            from end.retouch import perform_retouch

            perform_retouch(project_id)
        render.assert_not_called()
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        _assets, shots = collect_steps(saved)
        self.assertIn("新的第一镜", shots[0]["prompt"])
        self.assertIn("第二镜", shots[1]["prompt"])
        self.assertNotIn("新的第一镜", shots[1]["prompt"])
        progress = read_project(project_id)["progress"]
        self.assertNotIn("retouching", progress)
        self.assertIn("新的第一镜", progress["prompt_diff"]["after"])
        self.assertIn("第一镜", progress["prompt_diff"]["before"])
        self.assertEqual(progress["retouch_error"]["key"], "shot:S01")
        self.assertIn("出片失败", progress["retouch_error"]["message"])
        self.assertTrue((folder / "outputs" / "clips" / "S01.mp4").is_file())

    def test_render_thread_failure_keeps_the_diff(self):
        project_id = "0a175707e009"
        self._ready(project_id, rendered=True)
        project = read_project(project_id)
        project["status"] = "rendering"
        project["progress"] = {
            "percent": 40,
            "phase": "videos",
            "message": "出片中",
            "retouching": {
                "node": "asset",
                "target_id": "A3",
                "suggestion": "更冷一点",
                "phase": "render",
                "started_at": "2026-11-04T00:00:00+08:00",
            },
            "prompt_diff": {
                "node": "asset",
                "target_id": "A3",
                "before": "雨提示",
                "after": "新雨",
                "suggestion": "更冷一点",
                "at": "2026-11-04T00:00:00+08:00",
            },
            "video_submit_mode": "preview",
        }
        write_project(project)
        fail_project(project_id, RuntimeError("预览出片失败"))
        progress = read_project(project_id)["progress"]
        self.assertNotIn("retouching", progress)
        self.assertEqual(progress["prompt_diff"]["after"], "新雨")
        self.assertEqual(progress["retouch_error"]["key"], "asset:A3")
        self.assertIn("预览出片失败", progress["retouch_error"]["message"])
        self.assertEqual(read_project(project_id)["status"], "error")
        self.assertEqual(progress["video_submit_mode"], "preview")

    def test_agent_queue_runs_retouch_and_revise(self):
        project_id = "111107e0000a"
        self._ready(project_id, rendered=True)
        with patch("end.retouch.perform_retouch") as retouch, patch("end.revise.perform_revise") as revise:
            handle_agent_task(Task("retouch", project_id, {}))
            handle_agent_task(Task("revise", project_id, {}))
        retouch.assert_called_once_with(project_id)
        revise.assert_called_once_with(project_id)

    def test_prompt_phase_stays_on_get_while_the_project_is_done(self):
        project_id = "a11c07e0000b"
        self._ready(project_id, rendered=True)
        with patch("end.server.submit_agent_task", return_value=True), quiet_catalog():
            view = rpc_call(
                "projects.retouch",
                {
                    "id": project_id,
                    "node": "asset",
                    "target_id": "A1",
                    "suggestion": "更冷一点",
                    "grade": "generate",
                },
            )
        self.assertEqual(view["status"], "done")
        active = view["progress"]["retouching"]
        self.assertEqual(active["node"], "asset")
        self.assertEqual(active["target_id"], "A1")
        self.assertEqual(active["phase"], "prompt")
        self.assertEqual(active["suggestion"], "更冷一点")
        self.assertTrue(active["started_at"])
        self.assertNotIn("grade", active)
        project = read_project(project_id)
        progress = dict(project["progress"])
        progress["prompt_diff"] = {
            "node": "shot",
            "target_id": "S01",
            "before": "第一镜",
            "after": "上一版",
            "suggestion": "上次",
            "at": "2026-11-03T00:00:00+08:00",
        }
        project["progress"] = progress
        write_project(project)
        with quiet_catalog():
            again = public_view(read_project(project_id))
        self.assertEqual(again["status"], "done")
        self.assertEqual(again["progress"]["retouching"]["phase"], "prompt")
        self.assertEqual(again["progress"]["prompt_diff"]["after"], "上一版")

    def test_rerun_grade_selects_generate_and_leaves_the_prompt(self):
        project_id = "6e4e0700000c"
        folder = self._ready(project_id, rendered=True)
        before = (folder / "spec.json").read_text(encoding="utf-8")
        feed_before = json.loads(before)["feed"]
        with quiet_catalog(), patch("end.server.start_render_thread") as render, patch(
            "end.revise.run_block_call"
        ) as block:
            preview = rpc_call(
                "projects.rerun",
                {"id": project_id, "node": "shot", "target_id": "S02"},
            )
        block.assert_not_called()
        self.assertEqual(render.call_args.kwargs.get("video_submit_mode"), "preview")
        self.assertEqual(preview["progress"]["video_submit_mode"], "preview")
        self.assertNotIn("retouching", preview["progress"])
        self._settle(project_id)
        with quiet_catalog(), patch("end.server.start_render_thread") as render:
            produced = rpc_call(
                "projects.rerun",
                {"id": project_id, "node": "asset", "target_id": "A3", "grade": "generate"},
            )
        self.assertEqual(render.call_args.kwargs.get("video_submit_mode", "generate"), "generate")
        self.assertEqual(produced["progress"]["video_submit_mode"], "generate")
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["feed"], feed_before)
        assets, shots = collect_steps(saved)
        self.assertEqual(assets[2]["prompt"], "雨提示")
        self.assertIn("第二镜", shots[1]["prompt"])
        self.assertIn("第一镜", shots[0]["prompt"])
        self.assertIn("第三镜", shots[2]["prompt"])
        with self.assertRaises(RpcError) as bad_grade:
            rpc_call(
                "projects.rerun",
                {"id": project_id, "node": "shot", "target_id": "S02", "grade": "ultra"},
            )
        self.assertEqual(bad_grade.exception.code, 400)
        with self.assertRaises(RpcError) as blocked:
            rpc_call(
                "projects.rerun",
                {"id": project_id, "node": "storyboard", "target_id": "", "grade": "generate"},
            )
        self.assertEqual(blocked.exception.code, 400)
        self.assertIn("单张图或单镜头", str(blocked.exception))

    def _settle(self, project_id):
        project = read_project(project_id)
        project["status"] = "done"
        project["video_file"] = "joined.mp4"
        progress = dict(project.get("progress") or {})
        progress.pop("retouching", None)
        project["progress"] = progress
        write_project(project)
        _PIPELINE_HANDOFF[project_id] = "yielded"
        self._seed_outputs(project_dir(project_id))

    def _accept(self, project_id, node, target_id, suggestion):
        from end.retouch import accept_retouch

        return accept_retouch(project_id, node, target_id, suggestion)

    def _queue(self, project_id, node, target_id, suggestion):
        project = read_project(project_id)
        progress = dict(project["progress"])
        progress["retouching"] = {
            "node": node,
            "target_id": target_id,
            "suggestion": suggestion,
            "phase": "prompt",
            "started_at": "2026-11-04T00:00:00+08:00",
        }
        project["progress"] = progress
        write_project(project)

    def _ready(self, project_id, rendered):
        self.ids.append(project_id)
        make_project(project_id, status="done")
        project = read_project(project_id)
        project["status"] = "done"
        project["stage"] = "提示词已就绪"
        project["script"] = RENDER_SPEC["script"]
        project["storyboard"] = RENDER_SPEC["storyboard"]
        project["image_prompts"] = RENDER_SPEC["image_prompts"]
        project["video_file"] = "joined.mp4" if rendered else ""
        project["progress"] = {
            "percent": 100,
            "phase": "done" if rendered else "prompts_ready",
            "message": "成片已生成" if rendered else "提示词已就绪",
            "pipeline": {
                "only": "",
                "accepted": ["script", "asset", "storyboard", "feed"],
                "current": "feed",
                "action": "finish",
                "passed": True,
            },
        }
        write_project(project)
        folder = project_dir(project_id)
        (folder / "spec.json").write_text(json.dumps(RENDER_SPEC, ensure_ascii=False), encoding="utf-8")
        write_locks(folder, {"aspect": "16:9", "model": "h3"})
        if rendered:
            self._seed_outputs(folder)
        from tests.test_rerun_from import RerunProjectTest

        RerunProjectTest._allow(None, folder, project.get("brief") or "")
        return folder

    def _seed_outputs(self, folder):
        for name in ("A1", "A2", "A3"):
            self._file(folder, "images/%s.png" % name)
        for name in ("S01", "S02", "S03"):
            self._file(folder, "clips/%s.mp4" % name)
        self._file(folder, "joined.mp4")
        steps = {}
        for name in ("A1", "A2", "A3"):
            steps[name] = _done("image")
        for name in ("S01", "S02", "S03"):
            steps[name] = _done("video")
        steps["join"] = _done("join")
        steps["audio"] = _done("audio")
        steps["subtitles"] = _done("subtitles")
        state = {
            "phase": "done",
            "paused": False,
            "pause_reason": "",
            "paused_by": "",
            "current_step": "subtitles",
            "steps": steps,
        }
        (folder / "run_state.json").write_text(json.dumps(state), encoding="utf-8")

    def _file(self, folder, relative):
        path = folder / "outputs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path


if __name__ == "__main__":
    unittest.main()
