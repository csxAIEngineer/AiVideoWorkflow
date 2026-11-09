"""Complete storyboard is the only new manuscript. Previs stays on disk and out of the gate."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end.db import project_dir, read_project
from end.locks import prepare_locks, write_locks
from end.script_review import set_reviewer
from end.server import agent_queue, answer_gate, public_view, resume_render, start_agent_workers
from end.stage_runner import check_stage, execute
from end.storyboard_contract import project_shots, rendering_conflict, shots_conflict
from tests.test_director_pipeline import _base_locks, make_project

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests" / "fixtures" / "562f48a5ed07-previs.md").read_text(encoding="utf-8")
BOARD = "\n".join((
    "## S01",
    "动作：她推门进来",
    "景别机位：中景，门口平视",
    "时长：6",
    "资产：A1",
    "站位：人在门内",
))
ASSETS = [{"id": "A1", "title": "门", "subject": "门", "prompt": "一扇门", "subject_kind": "location", "view": "single"}]
PLACEHOLDER = {
    "id": "S01",
    "duration": 4,
    "prompt": "按这张资产图演这一镜",
    "images": ["A1"],
}


def setUpModule():
    from tests.offline_guard import install

    install()
    set_reviewer(lambda _messages: '{"issues":[]}')
    start_agent_workers()


def tearDownModule():
    from tests.offline_guard import uninstall

    set_reviewer(None)
    uninstall()


def _locks():
    locks = prepare_locks(_base_locks(), "时长：12秒")
    locks["style_look"] = "自然光，写实肤色"
    locks["style"] = locks["style_look"]
    locks["seams"] = []
    locks["delivery"] = "2"
    return locks


class ProjectionTest(unittest.TestCase):
    def test_duration_and_asset_are_not_invented(self):
        missing, report = project_shots(
            "\n".join(("## S01", "动作：她进门", "景别机位：中景", "资产：林浅")),
            ["图2"],
        )
        self.assertEqual(missing, [])
        self.assertIn("S01 缺时长、资产", report)
        self.assertNotIn("4", report)

        named, named_report = project_shots(
            "\n".join(("## S01", "动作：她进门", "景别机位：中景", "时长：3", "资产：林浅")),
            ["图2"],
        )
        self.assertEqual(named, [])
        self.assertIn("缺资产", named_report)

        shots, ok = project_shots(BOARD, ["A1"])
        self.assertEqual(ok, "")
        self.assertEqual(shots[0]["duration"], 6)
        self.assertEqual(shots[0]["images"], ["A1"])
        self.assertIn("站位：人在门内", shots[0]["prompt"])

    def test_supplied_shots_must_match_the_same_id(self):
        shots, report = project_shots(BOARD, ["A1"])
        self.assertEqual(report, "")
        self.assertEqual(
            shots_conflict(shots, [{"id": "S01", "duration": 4, "prompt": "另一份", "images": ["A1"]}]),
            "镜头不一致：S01 时长正文 6，shots 为 4",
        )
        self.assertEqual(shots_conflict(shots, [PLACEHOLDER]), "")


class GateTest(unittest.TestCase):
    def test_legacy_prose_is_not_locked_for_missing_grids(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "分镜.md").write_text("她进门，中景。没有九宫格。\n", encoding="utf-8")
        (internal / "previs.md").write_text(FIXTURE, encoding="utf-8")
        ok, report = check_stage(folder, "storyboard", {"material_mode": "M1"})
        self.assertTrue(ok, report)
        self.assertEqual((internal / "previs.md").read_text(encoding="utf-8"), FIXTURE)

    def test_previs_only_is_not_a_storyboard(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "previs.md").write_text(FIXTURE, encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps({"assets": ASSETS, "shots": [PLACEHOLDER], "previs": "x"}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls = []

        def writer(brief, *_args, **_kwargs):
            calls.append(brief)
            return {"previs": FIXTURE}

        outcome = execute(
            {
                "template": "short-drama-director",
                "model": "deepseek-v4-flash-0731",
                "brief": "发丝救援",
                "progress": {
                    "pipeline": {
                        "action": "write",
                        "current": "storyboard",
                        "accepted": ["script", "asset"],
                        "passed": True,
                        "only": "",
                    }
                },
            },
            folder,
            _locks(),
            writer,
            lambda *_a, **_k: "",
        )
        self.assertEqual(len(calls), 1)
        self.assertNotIn("预演：1", calls[0])
        self.assertIn("景别机位", calls[0])
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        self.assertEqual(outcome["progress"]["pipeline"]["report"], "分镜是空的")
        self.assertEqual(outcome["progress"]["pipeline"]["repairs"], 0)
        self.assertEqual(outcome["message"]["options"], ["补写分镜"])
        self.assertFalse((internal / "分镜.md").is_file())
        self.assertEqual((internal / "previs.md").read_text(encoding="utf-8"), FIXTURE)
        self.assertEqual(outcome["spec"]["shots"], [])
        self.assertNotEqual(outcome["spec"].get("previs"), FIXTURE)


class QueueTest(unittest.TestCase):
    def _project(self, project_id):
        folder = project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        internal = folder / "_内部_"
        internal.mkdir(exist_ok=True)
        (internal / "previs.md").write_text(FIXTURE, encoding="utf-8")
        for name in ("分镜.md", "分镜.contract", "分镜.hold", "feed.md"):
            path = internal / name
            if path.is_file():
                path.unlink()
        (folder / "spec.json").write_text(
            json.dumps(
                {"title": "发丝救援", "assets": ASSETS, "shots": [PLACEHOLDER]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        write_locks(folder, _locks())
        make_project(
            project_id,
            status="gate",
            brief="发丝救援",
            progress={
                "percent": 62,
                "phase": "gate_storyboard",
                "message": "分镜没写出来。",
                "lock_machine": True,
                "pipeline": {
                    "current": "storyboard",
                    "accepted": ["script", "asset"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "分镜是空的",
                    "repairs": 3,
                },
                "question": {
                    "id": "storyboard",
                    "kind": "single",
                    "reply": "分镜是空的",
                    "options": ["根据建议修改"],
                    "cards": [],
                },
            },
            messages=[{"role": "assistant", "content": "分镜是空的", "options": ["根据建议修改"]}],
        )
        return folder

    def test_history_get_offers_one_rewrite_and_the_queue_runs_the_fake_model(self):
        project_id = "sb562f48a5ed"
        folder = self._project(project_id)
        calls = []

        def writer(brief, skill_pack="", debug_dir=None, model=None, template=None, **_kwargs):
            calls.append(brief)
            if "任务：提示词封装" in brief:
                return {
                    "feed": "\n".join((
                        "## 【投喂单元 1/1】",
                        "subject_definitions:",
                        "<Subject 1> is a woman at a door.",
                        "summary:",
                        "She steps inside.",
                        "retention_analysis:",
                        "<Subject 1>: fully_preserved.",
                        "detailed_description:",
                        "[Shot 1] <Subject 1> opens the door.",
                        "overall_soundscape:",
                        "Room tone.",
                        "non_diegetic_music:",
                        "Soft pulse.",
                    ))
                }
            if len(calls) == 1:
                return {"previs": FIXTURE}
            return {"storyboard": BOARD}

        with patch("end.server.call_deepseek", side_effect=writer), patch(
            "end.stage_runner.check_feed", return_value=(0, "")
        ), patch("end.stage_runner.check_prompt", return_value=(0, "")), patch(
            "end.server.resume_paused"
        ) as resume:
            view = public_view(read_project(project_id))
            self.assertEqual(calls, [])
            resume.assert_not_called()
            self.assertEqual(view["question"]["options"], ["补写分镜"])
            self.assertEqual(view["shots"][0]["prompt"], "按这张资产图演这一镜")
            self.assertEqual(len(view["shots"]), 1)
            before = (folder / "_内部_" / "previs.md").read_bytes()
            answer_gate(read_project(project_id), "补写分镜")
            self.assertTrue(agent_queue().wait_until_idle(8))
            self.assertEqual(len(calls), 1, "\n---\n".join(call.splitlines()[0] for call in calls))
            saved = read_project(project_id)
            self.assertEqual(saved["status"], "gate")
            self.assertEqual(saved["progress"]["pipeline"]["repairs"], 0)
            self.assertIn("分镜是空的", saved["progress"]["pipeline"]["report"])
            self.assertFalse((folder / "_内部_" / "分镜.md").is_file())
            self.assertEqual((folder / "_内部_" / "previs.md").read_bytes(), before)
            spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec.get("shots"), [])
            answer_gate(read_project(project_id), "补写分镜")
            self.assertTrue(agent_queue().wait_until_idle(8))
        self.assertEqual(len(calls), 3)
        self.assertIn("任务：分镜", calls[1])
        self.assertIn("任务：提示词封装", calls[2])
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["shots"][0]["id"], "S01")
        self.assertEqual(spec["shots"][0]["duration"], 6)
        self.assertEqual(spec["shots"][0]["images"], ["A1"])
        self.assertIn("她推门进来", (folder / "_内部_" / "分镜.md").read_text(encoding="utf-8"))
        self.assertTrue((folder / "_内部_" / "feed.md").is_file())
        self.assertEqual((folder / "_内部_" / "previs.md").read_bytes(), FIXTURE.encode("utf-8"))
        saved = read_project(project_id)
        self.assertEqual(saved["status"], "done")
        self.assertNotEqual(saved["status"], "rendering")

    def test_conflict_stops_once_and_resume_keeps_a_matching_checkpoint(self):
        project_id = "sbconflict0001"
        folder = project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        internal = folder / "_内部_"
        internal.mkdir(exist_ok=True)
        (internal / "previs.md").write_text(FIXTURE, encoding="utf-8")
        before = (internal / "previs.md").read_bytes()
        (folder / "spec.json").write_text(
            json.dumps({"assets": ASSETS, "shots": [PLACEHOLDER]}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls = []

        def writer(brief, *_args, **_kwargs):
            calls.append(brief)
            return {
                "storyboard": BOARD,
                "shots": [{"id": "S01", "duration": 4, "prompt": "另一份", "images": ["A1"]}],
            }

        outcome = execute(
            {
                "template": "short-drama-director",
                "model": "deepseek-v4-flash-0731",
                "brief": "冲突",
                "progress": {
                    "pipeline": {
                        "action": "write",
                        "current": "storyboard",
                        "accepted": ["script", "asset"],
                        "passed": True,
                        "only": "",
                    }
                },
            },
            folder,
            _locks(),
            writer,
            lambda *_a, **_k: "",
        )
        self.assertEqual(len(calls), 1)
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        self.assertIn("时长正文 6", outcome["progress"]["pipeline"]["report"])
        self.assertEqual(outcome["progress"]["pipeline"]["repairs"], 0)
        self.assertEqual(outcome["message"]["options"], ["补全"])
        self.assertEqual(outcome["spec"].get("shots"), [])
        self.assertEqual((internal / "previs.md").read_bytes(), before)

        spec = {
            "storyboard_contract": "shot-prose-v1",
            "shots": [{"id": "S01", "duration": 6, "prompt": "她推门进来", "images": ["A1"]}],
        }
        (folder / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        (folder / "run_state.json").write_text(
            json.dumps({"steps": {"S01": {"kind": "video", "status": "pending"}, "A1": {"kind": "image", "status": "done"}}}),
            encoding="utf-8",
        )
        self.assertEqual(rendering_conflict(folder), "")
        mismatched = dict(spec)
        mismatched["shots"] = [{"id": "S02", "duration": 6, "prompt": "另一镜", "images": ["A1"]}]
        (folder / "spec.json").write_text(json.dumps(mismatched), encoding="utf-8")
        self.assertIn("S01", rendering_conflict(folder))
        self.assertIn("不会从预演稿重建", rendering_conflict(folder))
        make_project(
            project_id,
            status="paused",
            progress={"paused_from": "rendering", "phase": "paused", "pipeline": {}},
        )
        with patch("end.server.resume_paused") as paused:
            with self.assertRaises(RuntimeError) as caught:
                resume_render(read_project(project_id))
        paused.assert_not_called()
        self.assertIn("不会从预演稿重建", str(caught.exception))
        (folder / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        with patch("end.server.resume_paused") as paused, patch(
            "end.server.thread_alive", return_value=False
        ), patch("end.server.start_render_thread"):
            resume_render(read_project(project_id))
        paused.assert_called_once()


if __name__ == "__main__":
    unittest.main()
