"""Canvas prompt saves stay authoritative, and rerun starts at the edited node."""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.locks import write_locks
from end.render_loop import load_run_state
from end.image_node import prepare_image_node
from end.rerun import rerun_from, steps_to_invalidate
from end.server import _PIPELINE_HANDOFF, save_project_content
from end.workflow_from_spec import collect_steps
from tests.test_project_api import make_project


LONG = "旧的很长的提示词" * 20
PIPELINE = {
    "only": "",
    "accepted": ["script", "asset", "storyboard", "feed"],
    "current": "feed",
    "action": "finish",
    "passed": True,
    "report": "",
    "check": "",
}
RENDER_SPEC = {
    "title": "雨夜",
    "script": "旧剧本",
    "storyboard": "旧分镜",
    "image_prompts": "",
    "assets": [
        {"id": "A1", "title": "主角", "prompt": "主角提示"},
        {"id": "A2", "title": "侧面", "prompt": "侧面提示", "depends_on": "A1"},
        {"id": "A3", "title": "雨", "prompt": "雨提示"},
    ],
    "shots": [
        {"id": "S01", "duration": 5, "prompt": "第一镜", "images": ["A1"]},
        {"id": "S02", "duration": 5, "prompt": "第二镜", "images": ["A3"]},
        {"id": "S03", "duration": 5, "prompt": "第三镜", "images": ["A3"]},
    ],
}


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


class EditedPromptTest(unittest.TestCase):
    def test_edited_prompt_beats_a_longer_block(self):
        spec = {
            "image_prompts": "#### 图1 · 主角\n%s\n" % LONG,
            "assets": [{"id": "图1", "title": "主角", "prompt": "短", "prompt_edited": True}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "走", "images": ["图1"]}],
        }
        assets, _shots = collect_steps(spec)
        self.assertEqual(assets[0]["prompt"], "短")

    def test_later_write_keeps_the_edited_prompt(self):
        spec = {
            "image_prompts": "#### 图1 · 主角\n%s\n" % LONG,
            "assets": [{"id": "图1", "title": "主角", "prompt": "短", "prompt_edited": True}],
            "shots": [{"id": "S01", "duration": 5, "prompt": "走", "images": ["图1"]}],
        }
        prepared = prepare_image_node(spec, policy="drama_still")
        self.assertTrue(prepared["assets"][0]["prompt_edited"])
        assets, _shots = collect_steps(prepared)
        self.assertEqual(assets[0]["prompt"], "短")

    def test_asset_rerun_follows_depends_on_and_later_shots(self):
        steps = steps_to_invalidate(RENDER_SPEC, "asset", "A1")
        ids = [step_id for step_id, _kind in steps]
        self.assertEqual(ids[:2], ["A1", "A2"])
        self.assertNotIn("A3", ids)
        self.assertEqual(ids[2:], ["S01", "S02", "S03", "join", "audio", "subtitles"])

    def test_unrelated_asset_keeps_earlier_shots(self):
        steps = steps_to_invalidate(RENDER_SPEC, "asset", "A3")
        ids = [step_id for step_id, _kind in steps]
        self.assertEqual(ids, ["A3", "S02", "S03", "join", "audio", "subtitles"])

    def test_shot_rerun_starts_at_that_shot(self):
        steps = steps_to_invalidate(RENDER_SPEC, "shot", "S02")
        ids = [step_id for step_id, _kind in steps]
        self.assertEqual(ids, ["S02", "S03", "join", "audio", "subtitles"])


class RerunProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        self._catalog = ExitStack()
        self._catalog.enter_context(
            patch("end.server.normalize_model", side_effect=lambda value: value or "qwen3.8-flash")
        )
        self._catalog.enter_context(
            patch("end.server.model_label", side_effect=lambda value: value or "Qwen")
        )
        self._catalog.enter_context(
            patch(
                "end.server.normalize_template",
                side_effect=lambda value: value or "short-drama-director",
            )
        )
        self._catalog.enter_context(
            patch("end.server.template_label", side_effect=lambda value: value or "短剧")
        )

    def tearDown(self):
        self._catalog.close()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_save_replaces_the_figure_block_and_feed_unit(self):
        project_id = "a11ed1700001"
        make_project(project_id, status="done")
        folder = project_dir(project_id)
        spec = {
            "title": "雨夜",
            "script": "旧剧本",
            "storyboard": "旧分镜",
            "image_prompts": "#### 图1 · 主角\n%s\n\n#### 图2 · 雨\n旧雨\n" % LONG,
            "assets": [
                {"id": "图1", "title": "主角", "prompt": LONG},
                {"id": "图2", "title": "雨", "prompt": "旧雨"},
            ],
            "feed": "## 【投喂单元 1/2】\n第一镜原文\n\n## 【投喂单元 2/2】\n第二镜原文\n",
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "第一镜原文", "images": ["图1"]},
                {"id": "S02", "duration": 5, "prompt": "第二镜原文", "images": ["图2"]},
            ],
        }
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        save_project_content(project_id, {"script": "新剧本", "assets": [{"id": "图1", "prompt": "短提示"}]})
        save_project_content(project_id, {"shots": [{"id": "S01", "prompt": "新的第一镜"}]})
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        assets, shots = collect_steps(saved)
        self.assertEqual(assets[0]["prompt"], "短提示")
        self.assertIn("旧雨", assets[1]["prompt"])
        self.assertNotIn(LONG, saved["image_prompts"])
        self.assertIn("短提示", saved["image_prompts"])
        self.assertIn("新的第一镜", shots[0]["prompt"])
        self.assertNotIn("第一镜原文", shots[0]["prompt"])
        self.assertIn("第二镜原文", shots[1]["prompt"])
        self.assertIn("新剧本", (folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"))
        self.assertIn("短提示", (folder / "_内部_" / "assets.md").read_text(encoding="utf-8"))
        self.assertIn("新的第一镜", (folder / "_内部_" / "feed.md").read_text(encoding="utf-8"))
        self.assertEqual(read_project(project_id)["script"], "新剧本")
        self.assertEqual(read_project(project_id)["image_prompts"], saved["image_prompts"])
        with self.assertRaises(ValueError):
            save_project_content(project_id, {"shots": [{"id": "NOPE", "prompt": "x"}]})
        save_project_content(project_id, {"shots": [{"id": "S01", "prompt": "【画幅风格】\n新画幅"}]})
        saved = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        _assets, shots = collect_steps(saved)
        self.assertIn("新画幅", shots[0]["prompt"])
        self.assertIn("投喂单元", shots[0]["prompt"])
        self.assertIn("第二镜原文", shots[1]["prompt"])
        self.assertNotIn("新画幅", shots[1]["prompt"])

    def test_script_rerun_clears_downstream_and_keeps_the_gates(self):
        project_id = "5c11b7000002"
        folder = self._ready(project_id, {"script": "新剧本", "storyboard": "旧分镜"})
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("旧剧本\n", encoding="utf-8")
        (internal / "assets.md").write_text("#### 图1\n旧\n", encoding="utf-8")
        (internal / "分镜.md").write_text("旧分镜\n", encoding="utf-8")
        (internal / "previs.md").write_text("旧预览\n", encoding="utf-8")
        (internal / "feed.md").write_text("旧投喂\n", encoding="utf-8")
        self._file(folder, "images/A1.png")
        self._file(folder, "joined.mp4")
        with patch("end.server.submit_agent_task") as submit, patch("end.server.start_render_thread") as render:
            rerun_from(project_id, "script")
        submit.assert_called_once()
        render.assert_not_called()
        self.assertIn("新剧本", (internal / "剧本.md").read_text(encoding="utf-8"))
        self.assertFalse((internal / "assets.md").is_file())
        self.assertFalse((internal / "分镜.md").is_file())
        self.assertFalse((internal / "previs.md").is_file())
        self.assertFalse((internal / "feed.md").is_file())
        self.assertFalse((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertFalse((folder / "outputs" / "joined.mp4").is_file())
        project = read_project(project_id)
        self.assertEqual(project["status"], "writing")
        self.assertEqual(project["script"], "新剧本")
        self.assertEqual(project["storyboard"], "")
        self.assertEqual(project["image_prompts"], "")
        self.assertEqual(project["video_file"], "")
        pipeline = project["progress"]["pipeline"]
        self.assertEqual(pipeline["action"], "write")
        self.assertEqual(pipeline["current"], "asset")
        self.assertEqual(pipeline["accepted"], ["script"])

    def test_storyboard_rerun_keeps_stills_and_rewrites_feed(self):
        project_id = "5704b0a7d003"
        folder = self._ready(project_id, {"storyboard": "新分镜"})
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text("资产留下\n", encoding="utf-8")
        (internal / "分镜.md").write_text("旧分镜\n", encoding="utf-8")
        (internal / "previs.md").write_text("预览留下\n", encoding="utf-8")
        (internal / "feed.md").write_text("旧投喂\n", encoding="utf-8")
        (folder / "feed.md").write_text("旧投喂\n", encoding="utf-8")
        self._file(folder, "images/A1.png")
        self._file(folder, "clips/S01.mp4")
        with patch("end.server.submit_agent_task") as submit, patch("end.server.start_render_thread") as render:
            rerun_from(project_id, "storyboard")
        submit.assert_called_once()
        render.assert_not_called()
        self.assertIn("新分镜", (internal / "分镜.md").read_text(encoding="utf-8"))
        self.assertTrue((internal / "assets.md").is_file())
        self.assertTrue((internal / "previs.md").is_file())
        self.assertFalse((internal / "feed.md").is_file())
        self.assertFalse((folder / "feed.md").is_file())
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S01.mp4").is_file())
        pipeline = read_project(project_id)["progress"]["pipeline"]
        self.assertEqual(pipeline["current"], "feed")
        self.assertEqual(pipeline["accepted"], ["script", "asset", "storyboard"])
        self.assertEqual(read_project(project_id)["storyboard"], "新分镜")
        self.assertEqual(read_project(project_id)["image_prompts"], "主角提示")

    def test_asset_rerun_invalidates_only_the_dependent_chain(self):
        project_id = "a55e70000004"
        folder = self._ready(project_id, spec=RENDER_SPEC, outputs=True)
        with patch("end.server.submit_agent_task") as submit, patch("end.server.start_render_thread") as render:
            rerun_from(project_id, "asset", "A3")
        submit.assert_not_called()
        render.assert_called_once()
        self.assertEqual(render.call_args.kwargs.get("video_submit_mode", "generate"), "generate")
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertTrue((folder / "outputs" / "images" / "A2.png").is_file())
        self.assertFalse((folder / "outputs" / "images" / "A3.png").is_file())
        self.assertTrue((folder / "outputs" / "clips" / "S01.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S02.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S03.mp4").is_file())
        self.assertFalse((folder / "outputs" / "joined.mp4").is_file())
        state = load_run_state(folder)
        self.assertEqual(state["steps"]["A1"]["status"], "done")
        self.assertEqual(state["steps"]["A3"]["status"], "pending")
        self.assertEqual(state["steps"]["S01"]["status"], "done")
        self.assertEqual(state["steps"]["S02"]["status"], "pending")
        tasks = {task["id"]: task["state"] for task in read_project(project_id)["tasks"]}
        self.assertEqual(tasks["A1"], "done")
        self.assertEqual(tasks["A3"], "pending")
        self.assertEqual(tasks["S01"], "done")
        self.assertEqual(tasks["S02"], "pending")
        self.assertEqual(read_project(project_id)["status"], "building")

    def test_shot_rerun_keeps_earlier_media(self):
        project_id = "510700000005"
        folder = self._ready(project_id, spec=RENDER_SPEC, outputs=True)
        with patch("end.server.start_render_thread") as render:
            rerun_from(project_id, "shot", "S02")
        render.assert_called_once()
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertTrue((folder / "outputs" / "clips" / "S01.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S02.mp4").is_file())
        self.assertFalse((folder / "outputs" / "clips" / "S03.mp4").is_file())
        state = load_run_state(folder)
        self.assertEqual(state["steps"]["S01"]["status"], "done")
        self.assertEqual(state["steps"]["S02"]["status"], "pending")
        self.assertEqual(state["steps"]["A1"]["status"], "done")

    def test_rerun_rejected_while_a_thread_is_alive(self):
        project_id = "b17570000006"
        folder = self._ready(project_id, {"script": "新剧本"})
        (folder / "_内部_").mkdir()
        (folder / "_内部_" / "剧本.md").write_text("保持\n", encoding="utf-8")
        _PIPELINE_HANDOFF[project_id] = "writer"
        try:
            with patch("end.server.thread_alive", return_value=True), patch("end.server.submit_agent_task") as submit:
                with self.assertRaises(RuntimeError) as raised:
                    rerun_from(project_id, "script")
            self.assertIn("正在跑", str(raised.exception))
            submit.assert_not_called()
        finally:
            _PIPELINE_HANDOFF.pop(project_id, None)
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"), "保持\n")

    def test_idle_writer_can_rerun(self):
        project_id = "1d1e07000008"
        folder = self._ready(project_id, {"script": "新剧本"})
        _PIPELINE_HANDOFF[project_id] = "yielded"
        try:
            with patch("end.server.thread_alive", return_value=True), patch("end.server.submit_agent_task") as submit:
                rerun_from(project_id, "script")
            submit.assert_called_once()
        finally:
            _PIPELINE_HANDOFF.pop(project_id, None)
        self.assertIn("新剧本", (folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"))

    def test_script_only_pipeline_does_not_delete_before_refusing(self):
        project_id = "0a1700000009"
        folder = self._ready(project_id, {"script": "新剧本"})
        project = read_project(project_id)
        progress = dict(project["progress"])
        pipeline = dict(progress["pipeline"])
        pipeline["only"] = "script"
        progress["pipeline"] = pipeline
        project["progress"] = progress
        write_project(project)
        (folder / "_内部_").mkdir()
        (folder / "_内部_" / "剧本.md").write_text("保持\n", encoding="utf-8")
        self._file(folder, "images/A1.png")
        with patch("end.server.submit_agent_task") as submit:
            with self.assertRaises(ValueError) as raised:
                rerun_from(project_id, "script")
        self.assertIn("后面没有", str(raised.exception))
        submit.assert_not_called()
        self.assertEqual((folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"), "保持\n")
        self.assertTrue((folder / "outputs" / "images" / "A1.png").is_file())
        self.assertEqual(read_project(project_id)["status"], "done")

    def test_rerun_rejected_before_prompts_are_ready(self):
        project_id = "0ea470000007"
        make_project(project_id, status="writing")
        with patch("end.server.submit_agent_task") as submit:
            with self.assertRaises(RuntimeError) as raised:
                rerun_from(project_id, "script")
        self.assertIn("还没写完", str(raised.exception))
        submit.assert_not_called()

    def _ready(self, project_id, spec_patch=None, spec=None, outputs=False):
        make_project(project_id, status="done")
        project = read_project(project_id)
        project["status"] = "done"
        project["stage"] = "提示词已就绪"
        project["script"] = (spec or RENDER_SPEC).get("script") or "旧剧本"
        project["storyboard"] = (spec or RENDER_SPEC).get("storyboard") or "旧分镜"
        project["image_prompts"] = (spec or RENDER_SPEC).get("image_prompts") or "主角提示"
        project["video_file"] = "joined.mp4"
        project["progress"] = {
            "percent": 100,
            "phase": "prompts_ready",
            "message": "提示词已就绪",
            "lock_machine": True,
            "pipeline": dict(PIPELINE),
        }
        write_project(project)
        folder = project_dir(project_id)
        stored = dict(spec or RENDER_SPEC)
        if spec_patch:
            stored.update(spec_patch)
        (folder / "spec.json").write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
        write_locks(folder, {"aspect": "16:9", "model": "h3"})
        if outputs:
            self._seed_outputs(folder)
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
