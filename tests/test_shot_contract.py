"""Read-only video contract on the project view. Submit behavior stays put."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.render_loop import save_run_state
from end.server import public_view, rpc_get_project
from end.shot_contract import annotate_shots
from end.workflow_from_spec import collect_steps


PUBLIC_BASE = "http://203.0.113.10:8787"

MATCH_UNIT = "\n".join([
    "## 【封装单元 3/5】",
    "subject_definitions:",
    "<Subject 1>: female protagonist under a street lamp",
    "detailed_description:",
    "She looks up.",
    "overall_soundscape:",
    "rain on glass",
    "non_diegetic_music:",
    "N/A",
])

FALLBACK_UNIT = "\n".join([
    "## 【封装单元 4/5】",
    "subject_definitions:",
    "<Subject 1>: a red umbrella on a bench",
    "detailed_description:",
    "The umbrella stays still.",
    "overall_soundscape:",
    "wind",
    "non_diegetic_music:",
    "none",
])


def _forbid(*_args, **_kwargs):
    raise AssertionError("video contract read must not extract a frame or upload")


class ShotContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()
        self._env = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = PUBLIC_BASE
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        self._view_patches = (
            patch("end.server.normalize_model", side_effect=lambda model: model or ""),
            patch("end.server.model_label", return_value=""),
            patch("end.server.normalize_template", side_effect=lambda template: template or ""),
            patch("end.server.template_label", return_value=""),
        )
        for item in self._view_patches:
            item.start()

    def tearDown(self):
        for item in self._view_patches:
            item.stop()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _save(self, project_id, spec, state=None, reviews=None):
        created = "2026-01-01T00:00:00+08:00"
        write_project({
            "id": project_id,
            "brief": "雨夜",
            "messages": [],
            "title": "雨夜",
            "status": "ready",
            "stage": "分镜",
            "script": spec.get("script") or "",
            "storyboard": spec.get("storyboard") or "",
            "image_prompts": "",
            "error": None,
            "events": [],
            "tasks": [],
            "progress": {},
            "cloud_log": "",
            "prompt_id": "",
            "video_file": "",
            "model": "deepseek-v4-pro",
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        })
        folder = project_dir(project_id)
        (folder / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if state is not None:
            save_run_state(folder, state)
        if reviews is not None:
            (folder / "reviews.json").write_text(
                json.dumps(reviews, ensure_ascii=False),
                encoding="utf-8",
            )
        return folder

    def _files(self, folder):
        return sorted(
            str(path.relative_to(folder))
            for path in folder.rglob("*")
            if path.is_file()
        )

    def _view(self, project_id):
        return public_view(read_project(project_id))

    def test_inferred_and_written_modes_and_empty_end_image(self):
        spec = {
            "assets": [
                {"id": "图1", "title": "女主", "prompt": "woman"},
                {"id": "图2", "title": "雨", "prompt": "rain"},
            ],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "她抬头", "images": ["图1"]},
                {
                    "id": "S02",
                    "duration": 5,
                    "prompt": "文字镜",
                    "images": ["图1"],
                    "video_mode": "text",
                },
                {
                    "id": "S03",
                    "duration": 5,
                    "prompt": "首尾",
                    "images": ["图1"],
                    "end_image": "图2",
                },
                {"id": "S04", "duration": 5, "prompt": "用户首帧", "images": ["U1"]},
            ],
        }
        folder = self._save("aabbcc000001", spec, {
            "steps": {
                "图1": {
                    "kind": "image",
                    "status": "done",
                    "public_url": "https://cdn.example/fig1.png",
                },
                "U1": {
                    "kind": "image",
                    "status": "done",
                    "local_override": True,
                    "public_url": "https://cdn.example/u1.png",
                },
                "S01": {
                    "kind": "video",
                    "status": "done",
                    "video_mode": "first_frame",
                },
            },
        })
        _assets, raw = collect_steps(spec)
        self.assertNotIn("end_image", raw[0])
        self.assertNotIn("video_mode_resolved", raw[0])
        self.assertNotIn("reference", raw[0])
        self.assertNotIn("images_source", raw[0])
        self.assertNotIn("feed", raw[0])
        self.assertEqual(raw[1]["video_mode"], "text")
        self.assertNotIn("end_image", raw[1])

        view = self._view("aabbcc000001")
        by_id = {shot["id"]: shot for shot in view["shots"]}

        self.assertEqual(by_id["S01"]["video_mode_resolved"], {
            "value": "first_frame",
            "inferred": True,
            "written": "",
        })
        self.assertEqual(by_id["S01"]["end_image"], "")
        self.assertEqual(by_id["S01"]["reference"], [])
        self.assertEqual(by_id["S01"]["images"], ["图1"])

        self.assertEqual(by_id["S02"]["video_mode_resolved"], {
            "value": "text",
            "inferred": False,
            "written": "text",
        })
        self.assertEqual(by_id["S02"]["end_image"], "")
        self.assertEqual(by_id["S02"]["reference"], [])

        self.assertEqual(by_id["S03"]["end_image"], "图2")
        self.assertEqual(by_id["S03"]["video_mode_resolved"], {
            "value": "first_last_frame",
            "inferred": True,
            "written": "",
        })
        self.assertEqual(by_id["S03"]["reference"], [])

        self.assertEqual(by_id["S04"]["video_mode_resolved"]["value"], "first_frame")
        self.assertTrue(by_id["S04"]["video_mode_resolved"]["inferred"])
        self.assertEqual(by_id["S04"]["reference"], [])
        self.assertNotIn("video_mode", by_id["S04"])

        stored = json.loads((folder / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["steps"]["S01"]["video_mode"], "first_frame")
        self.assertNotIn("S02", stored["steps"])

    def test_submitted_mode_wins_over_a_different_written_mode(self):
        spec = {
            "assets": [{"id": "图1", "title": "女主", "prompt": "woman"}],
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "她回头",
                "images": ["图1"],
                "video_mode": "text",
            }],
        }
        self._save("aabbcc000002", spec, {
            "steps": {
                "图1": {
                    "kind": "image",
                    "status": "done",
                    "public_url": "https://cdn.example/fig1.png",
                },
                "S01": {
                    "kind": "video",
                    "status": "done",
                    "video_mode": "reference",
                },
            },
        })
        shot = self._view("aabbcc000002")["shots"][0]
        self.assertEqual(shot["video_mode_resolved"], {
            "value": "reference",
            "inferred": False,
            "written": "text",
        })
        self.assertEqual(shot["reference"], [{
            "id": "图1",
            "url": "https://cdn.example/fig1.png",
            "source": "bound_image",
            "pending": False,
            "available": True,
        }])

    def test_image_source_and_feed_fields_come_from_the_packed_unit(self):
        spec = {
            "assets": [
                {"id": "图1", "title": "女主", "prompt": "woman"},
                {"id": "图9", "title": "雨", "prompt": "rain"},
            ],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "开场", "images": ["图9"]},
                {
                    "id": "S02",
                    "duration": 6,
                    "prompt": "跟拍",
                    "images": ["图9"],
                    "video_mode": "text",
                },
            ],
            "feed": MATCH_UNIT + "\n\n" + FALLBACK_UNIT,
        }
        self._save("aabbcc000003", spec)
        _assets, raw = collect_steps(spec)
        view = self._view("aabbcc000003")
        self.assertNotIn("feed", view)
        first, second = view["shots"]
        self.assertEqual(first["images"], raw[0]["images"])
        self.assertEqual(first["images"], ["图1"])
        self.assertEqual(first["prompt"], raw[0]["prompt"])
        self.assertTrue(first["prompt"].startswith("## 【封装单元 3/5】"))
        self.assertEqual(first["images_source"], {"value": "unit_match", "index": None})
        self.assertEqual(first["feed"]["heading"], "## 【封装单元 3/5】")
        self.assertEqual(first["feed"]["title"], "【封装单元 3/5】")
        self.assertEqual(first["feed"]["unit"], "3/5")
        self.assertEqual(first["feed"]["index"], 3)
        self.assertEqual(first["feed"]["total"], 5)
        self.assertEqual(
            first["feed"]["subject_definitions"],
            "<Subject 1>: female protagonist under a street lamp",
        )
        self.assertEqual(first["feed"]["detailed_description"], "She looks up.")
        self.assertEqual(first["feed"]["overall_soundscape"], "rain on glass")
        self.assertEqual(first["feed"]["non_diegetic_music"], "N/A")
        self.assertEqual(first["video_mode_resolved"]["value"], "reference")
        self.assertEqual(first["reference"][0]["id"], "图1")
        self.assertEqual(first["reference"][0]["source"], "bound_image")

        self.assertEqual(second["images"], ["图9"])
        self.assertEqual(second["images_source"], {
            "value": "storyboard_index_fallback",
            "index": 1,
        })
        self.assertEqual(second["feed"]["unit"], "4/5")
        self.assertEqual(second["feed"]["index"], 4)
        self.assertEqual(second["video_mode_resolved"], {
            "value": "text",
            "inferred": False,
            "written": "text",
        })
        self.assertEqual(second["reference"], [])
        self.assertEqual(second["end_image"], "")
        self.assertNotIn("video_mode_resolved", raw[1])

    def test_pending_tail_is_listed_without_extracting_or_uploading(self):
        spec = {
            "assets": [{"id": "图1", "title": "女主", "prompt": "woman"}],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "开场", "images": ["图1"]},
                {"id": "S02", "duration": 5, "prompt": "空镜", "images": []},
                {"id": "S03", "duration": 5, "prompt": "再来", "images": ["图1"]},
            ],
        }
        state = {
            "steps": {
                "图1": {
                    "kind": "image",
                    "status": "done",
                    "public_url": "https://cdn.example/fig1.png",
                },
                "S01": {
                    "kind": "video",
                    "status": "done",
                    "output_file": "clips/S01.mp4",
                    "video_mode": "reference",
                },
            },
        }
        folder = self._save("aabbcc000004", spec, state)
        clip = folder / "outputs" / "clips" / "S01.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fake-mp4")
        before = self._files(folder)
        state_bytes = (folder / "run_state.json").read_bytes()
        clip_bytes = clip.read_bytes()
        spec_bytes = (folder / "spec.json").read_bytes()
        guards = (
            patch("end.render_loop._tail_frame", _forbid),
            patch("end.render_loop._continuation_tail_url", _forbid),
            patch("end.image_store.store._put_cos", _forbid),
            patch("end.image_store.store.LocalImageStore.publish", _forbid),
            patch("end.image_store.store.CosImageStore.publish", _forbid),
            patch("end.image_store.store.ImageStore.ensure_remote", _forbid),
        )
        for item in guards:
            item.start()
        try:
            view = self._view("aabbcc000004")
            again = rpc_get_project({"id": "aabbcc000004"})
        finally:
            for item in reversed(guards):
                item.stop()
        self.assertEqual(self._files(folder), before)
        self.assertEqual((folder / "run_state.json").read_bytes(), state_bytes)
        self.assertEqual(clip.read_bytes(), clip_bytes)
        self.assertEqual((folder / "spec.json").read_bytes(), spec_bytes)
        self.assertFalse((folder / "outputs" / "images").exists())
        bare = view["shots"][1]
        self.assertEqual(bare["video_mode_resolved"], {
            "value": "text",
            "inferred": True,
            "written": "",
        })
        self.assertEqual(bare["reference"], [])
        self.assertIsNone(bare["first_frame"])
        shot = view["shots"][2]
        self.assertEqual(shot["video_mode_resolved"]["value"], "reference")
        self.assertEqual(shot["reference"], [{
            "id": "图1",
            "url": "https://cdn.example/fig1.png",
            "source": "bound_image",
            "pending": False,
            "available": True,
        }])
        self.assertNotIn("previous_tail", [item["source"] for item in shot["reference"]])
        self.assertEqual(again["shots"][1]["reference"], bare["reference"])
        self.assertEqual(again["shots"][2]["reference"], shot["reference"])

    def test_existing_tail_url_is_reported_and_a_missing_previous_clip_is_not(self):
        spec = {
            "assets": [{"id": "图1", "title": "女主", "prompt": "woman"}],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "开场", "images": ["图1"]},
                {
                    "id": "S02",
                    "duration": 5,
                    "prompt": "接上",
                    "images": ["图1"],
                    "video_mode": "first_frame",
                    "first_frame_source": "previous_tail",
                },
            ],
        }
        folder = self._save("aabbcc000005", spec, {
            "steps": {
                "S01": {
                    "kind": "video",
                    "status": "done",
                    "output_file": "clips/S01.mp4",
                    "video_mode": "reference",
                },
            },
        })
        clip = folder / "outputs" / "clips" / "S01.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fake-mp4")
        tail = folder / "outputs" / "images" / "S02_tail.jpg"
        tail.parent.mkdir(parents=True)
        tail.write_bytes(b"already-there")
        with patch("end.render_loop._tail_frame", _forbid), \
                patch("end.render_loop._continuation_tail_url", _forbid), \
                patch("end.image_store.store._put_cos", _forbid), \
                patch("end.image_store.store.LocalImageStore.publish", _forbid), \
                patch("end.image_store.store.CosImageStore.publish", _forbid):
            shot = self._view("aabbcc000005")["shots"][1]
        self.assertEqual(tail.read_bytes(), b"already-there")
        self.assertEqual(shot["reference"], [])
        self.assertEqual(shot["first_frame"], {
            "id": "S02_tail",
            "url": PUBLIC_BASE + "/media/projects/aabbcc000005/images/S02_tail.jpg",
            "source": "previous_tail",
            "pending": False,
            "available": True,
        })
        self.assertEqual(shot["video_mode_resolved"], {
            "value": "first_frame",
            "inferred": False,
            "written": "first_frame",
        })

        os.environ["AIVIDEO_IMAGE_STORE"] = "cos"
        with patch("end.image_store.store._put_cos", _forbid), \
                patch("end.image_store.store.CosImageStore.publish", _forbid), \
                patch("end.image_store.store.LocalImageStore.publish", _forbid):
            cos_shot = self._view("aabbcc000005")["shots"][1]
        self.assertTrue(cos_shot["first_frame"]["url"].startswith(
            "/media/projects/aabbcc000005/images/S02_tail.jpg?v="
        ))
        self.assertTrue(cos_shot["first_frame"]["available"])
        self.assertFalse(cos_shot["first_frame"]["pending"])

        state = json.loads((folder / "run_state.json").read_text(encoding="utf-8"))
        state["steps"]["S01"]["status"] = "running"
        save_run_state(folder, state)
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        running = self._view("aabbcc000005")["shots"][1]
        self.assertEqual(running["reference"], [])
        self.assertTrue(running["first_frame"]["pending"])
        self.assertFalse(running["first_frame"]["available"])

    def test_a_recorded_submit_beats_a_new_prediction(self):
        spec = {
            "assets": [{"id": "图1", "title": "女主", "prompt": "woman"}],
            "shots": [
                {"id": "S01", "duration": 5, "prompt": "开场", "images": ["图1"]},
                {"id": "S02", "duration": 5, "prompt": "接上", "images": ["图1"]},
            ],
        }
        folder = self._save(
            "aabbcc000006",
            spec,
            {
                "steps": {
                    "S01": {
                        "kind": "video",
                        "status": "done",
                        "output_file": "clips/S01.mp4",
                        "video_mode": "reference",
                    },
                    "S02": {
                        "kind": "video",
                        "status": "done",
                        "video_mode": "reference",
                        "submitted_media": [{
                            "id": "图9",
                            "url": "https://cdn.example/recorded.png",
                            "source": "bound_image",
                        }],
                    },
                },
            },
            reviews={
                "submit_leases": {
                    "video:S02": {"kind": "video", "step_id": "S02", "has_tail": True},
                },
            },
        )
        clip = folder / "outputs" / "clips" / "S01.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fake-mp4")
        tail = folder / "outputs" / "images" / "S02_tail.jpg"
        tail.parent.mkdir(parents=True)
        tail.write_bytes(b"leftover")
        shot = self._view("aabbcc000006")["shots"][1]
        self.assertEqual(shot["reference"], [{
            "id": "图9",
            "url": "https://cdn.example/recorded.png",
            "source": "bound_image",
            "pending": False,
            "available": True,
        }])

        state = json.loads((folder / "run_state.json").read_text(encoding="utf-8"))
        state["steps"]["S02"].pop("submitted_media")
        save_run_state(folder, state)
        (folder / "reviews.json").write_text(json.dumps({
            "submit_leases": {
                "video:S02": {"kind": "video", "step_id": "S02", "has_tail": False},
            },
        }), encoding="utf-8")
        without_tail = self._view("aabbcc000006")["shots"][1]
        self.assertEqual([item["source"] for item in without_tail["reference"]], ["bound_image"])

        state["steps"]["S02"]["status"] = "pending"
        state["steps"]["S02"].pop("video_mode", None)
        save_run_state(folder, state)
        predicted = self._view("aabbcc000006")["shots"][1]
        self.assertEqual([item["source"] for item in predicted["reference"]], ["bound_image"])
        self.assertNotIn("previous_tail", [item["source"] for item in predicted["reference"]])

    def test_nine_bound_images_leave_no_room_for_a_tail(self):
        folder = self.root / "nine"
        folder.mkdir()
        clip = folder / "outputs" / "clips" / "S01.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fake-mp4")
        save_run_state(folder, {
            "steps": {
                "S01": {
                    "kind": "video",
                    "status": "done",
                    "output_file": "clips/S01.mp4",
                    "video_mode": "reference",
                },
            },
        })
        images = ["A%s" % index for index in range(1, 10)]
        rows = annotate_shots(
            [
                {"id": "S01", "prompt": "开场", "images": ["A1"]},
                {"id": "S02", "prompt": "接上", "images": images, "video_mode": "reference"},
            ],
            [
                {"value": "storyboard", "index": None},
                {"value": "storyboard", "index": None},
            ],
            folder,
            "contractnine1",
        )
        self.assertEqual([item["id"] for item in rows[1]["reference"]], images)
        self.assertNotIn("previous_tail", [item["source"] for item in rows[1]["reference"]])
        self.assertEqual(rows[1]["reference"][0]["source"], "bound_image")
