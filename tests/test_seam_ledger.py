"""Seam ledger: D-lock rules, official H3 fields, packing, and the render gate."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agents.workers.seam import GENERATOR_SYSTEM, compile_seam_graph, generator_messages, run_seam_loop
from end.render_loop import assemble_shot_media, choose_video_mode, run_local_pipeline
from end.seam_ledger import (
    DEFAULT_SCENE_RELATION,
    FIRST_UNIT_SCENE_RELATION,
    SCENE_RELATIONS,
    apply_submit_params,
    check_seam_ledger,
    continues_from_previous,
    drop_packing,
    fold_seam_entries,
    invalid_scene_relation_error,
    map_submit_params,
    normalize_entry_ids,
    normalize_scene_relation,
    packed_units,
    seam_blocks_render,
    strip_seam,
    unit_id_from_text,
    units_for_submit,
    write_unit_fields,
)
from end.stage_runner import (
    _spec_patch,
    accepted_done,
    carried_text,
    group_shot_durations,
    next_pending,
    stages_for,
)
from end.storyboard_contract import project_into_spec
from end.workflow_from_spec import collect_steps


def _shot(shot_id, images, duration=5):
    return {
        "id": shot_id,
        "duration": duration,
        "prompt": shot_id,
        "images": list(images),
    }


def _entry(shot_id, prev, lock, **extra):
    row = {
        "shot_id": shot_id,
        "prev_id": prev,
        "d_lock": lock,
        "reason": "这一刀怎么接",
        "scene_relation": {
            "none": "opening",
            "1": "same_scene",
            "2": "plot_group",
            "3": "emotion_landing",
            "4": "same_scene",
        }[lock],
        "action_continuous": lock == "1",
        "end_size": "中景",
        "start_size": "中景",
        "camera_change": lock in ("1", "2", "4"),
        "camera_from": "" if lock == "none" else "平视左",
        "camera_to": "" if lock == "none" else "平视右",
        "tail_croppable": lock == "1",
        "crop_plan": "人在场，朝向可用，背景锚点可见，无压屏" if lock == "1" else "",
        "transition": "闪白" if lock == "4" else "",
        "empty_use": "情绪空镜" if lock == "3" else "",
        "enter_exit": "",
    }
    row.update(extra)
    return row


def _board():
    shots = [
        _shot("S01", ["图1"]),
        _shot("S02", ["图1"]),
        _shot("S03", ["图2"]),
        _shot("S04", [], duration=2),
    ]
    entries = apply_submit_params([
        _entry("S01", "", "none"),
        _entry("S02", "S01", "1"),
        _entry("S03", "S02", "2"),
        _entry("S04", "S03", "3"),
    ], shots)
    return shots, {"entries": entries}


class MachineCheckTest(unittest.TestCase):
    def test_a_valid_ledger_passes_and_maps_official_fields(self):
        shots, ledger = _board()
        report = check_seam_ledger(ledger, shots)
        self.assertEqual(report["errors"], [], report["report"])
        by_id = {item["shot_id"]: item for item in ledger["entries"]}
        self.assertEqual(by_id["S01"]["video_mode"], "reference")
        self.assertEqual(by_id["S01"]["images"], ["图1"])
        self.assertEqual(by_id["S01"]["first_frame_source"], "none")
        self.assertEqual(by_id["S01"]["end_image"], "")
        self.assertEqual(by_id["S02"]["video_mode"], "first_frame")
        self.assertEqual(by_id["S02"]["first_frame_source"], "previous_tail")
        self.assertEqual(by_id["S02"]["images"], [])
        self.assertNotIn("writing_mode", by_id["S02"])
        self.assertEqual(by_id["S03"]["video_mode"], "reference")
        self.assertEqual(by_id["S03"]["images"], ["图2"])
        self.assertEqual(by_id["S03"]["end_image"], "")
        self.assertEqual(by_id["S03"]["first_frame_source"], "none")
        self.assertEqual(by_id["S04"]["video_mode"], "reference")
        self.assertEqual(by_id["S04"]["images"], [])
        self.assertEqual(by_id["S04"]["end_image"], "")
        for item in ledger["entries"]:
            self.assertIn(item["video_mode"], ("first_frame", "reference"))
            self.assertNotEqual(item["video_mode"], "text")
            self.assertNotEqual(item["video_mode"], "first_last_frame")

    def test_mapping_choices(self):
        shot = _shot("S02", ["图1"])
        continued = map_submit_params(_entry("S02", "S01", "1", anchor_end=True, end_image="图9"), shot)
        self.assertEqual(continued["video_mode"], "first_frame")
        self.assertEqual(continued["first_frame_source"], "previous_tail")
        self.assertEqual(continued["end_image"], "")
        self.assertEqual(continued["images"], [])
        redrawn = map_submit_params(
            _entry("S02", "S01", "1", redraw_still=True, designed_still="图缝", still_prompt="可截的中景"),
            shot,
        )
        self.assertEqual(redrawn["video_mode"], "first_frame")
        self.assertEqual(redrawn["first_frame_source"], "previous_tail")
        self.assertEqual(redrawn["images"], [])
        self.assertEqual(redrawn["end_image"], "")
        hard = map_submit_params(_entry("S03", "S02", "2"), _shot("S03", []))
        self.assertEqual(hard["video_mode"], "reference")
        self.assertEqual(hard["first_frame_source"], "none")
        self.assertEqual(hard["images"], [])
        self.assertEqual(hard["end_image"], "")
        opening = map_submit_params(
            _entry("S01", "", "none", lock_t0=True, designed_still="图0", still_prompt="锁住第一帧"),
            _shot("S01", ["图1"]),
            first=True,
        )
        self.assertEqual(opening["video_mode"], "reference")
        self.assertEqual(opening["first_frame_source"], "none")
        self.assertEqual(opening["images"], ["图1"])
        self.assertEqual(opening["end_image"], "")

    def test_false_continue_missing_camera_and_crop_fail(self):
        shots = [_shot("S01", ["图1"]), _shot("S02", ["图1"])]
        false_continue = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", scene_relation="plot_group", action_continuous=False),
        ], shots)
        report = check_seam_ledger({"entries": false_continue}, shots)
        self.assertFalse(report["ok"])
        self.assertIn("并没有接上", report["report"])

        same_camera = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", camera_from="平视", camera_to="平视", camera_change=False),
        ], shots)
        report = check_seam_ledger({"entries": same_camera}, shots)
        self.assertIn("换机位", report["report"])

        loose_size = apply_submit_params([
            _entry("S01", "", "none", end_size="全景"),
            _entry("S02", "S01", "1", start_size="中景", end_size="中景"),
        ], shots)
        report = check_seam_ledger({"entries": loose_size}, shots)
        self.assertIn("同档", report["report"])

        bare = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", tail_croppable=False, crop_plan=""),
        ], shots)
        report = check_seam_ledger({"entries": bare}, shots)
        self.assertIn("可截", report["report"])


    def test_first_frame_size_matches_previous_end_not_this_units_end(self):
        shots = [_shot("S01", ["图1"]), _shot("S02", ["图1"])]
        aligned = apply_submit_params([
            _entry("S01", "", "none", start_size="全景", end_size="中近景"),
            _entry("S02", "S01", "1", start_size="中近景", end_size="近景"),
        ], shots)
        report = check_seam_ledger({"entries": aligned}, shots)
        self.assertNotIn("同档", report["report"], report["report"])
        self.assertTrue(report["ok"], report["report"])

        mismatched = apply_submit_params([
            _entry("S01", "", "none", start_size="全景", end_size="中近景"),
            _entry("S02", "S01", "1", start_size="特写", end_size="特写"),
        ], shots)
        report = check_seam_ledger({"entries": mismatched}, shots)
        self.assertFalse(report["ok"])
        self.assertIn("同档", report["report"])
        self.assertIn("S02", report["report"])

    def test_hard_cut_freeze_words_and_reference_mix_fail(self):
        shots = [_shot("S01", ["图1"]), _shot("S02", ["图1"])]
        frozen = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "2", reason="末镜定格接上"),
        ], shots)
        report = check_seam_ledger({"entries": frozen}, shots)
        self.assertIn("不能写定格", report["report"])

        mixed = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1"),
        ], shots)
        mixed[1]["images"] = ["图1"]
        report = check_seam_ledger({"entries": mixed}, shots)
        self.assertIn("不能再带参考图", report["report"])

        stretched = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "2", writing_mode="视频延长强连续"),
        ], shots)
        report = check_seam_ledger({"entries": stretched}, shots)
        self.assertIn("视频延长只能配 ①", report["report"])

    def test_empty_and_stylized_limits(self):
        shots = [_shot("S%02d" % index, [], duration=2) for index in range(1, 6)]
        shots[0]["images"] = ["图1"]
        shots[0]["duration"] = 5
        entries = [_entry("S01", "", "none")]
        for index in range(2, 6):
            entries.append(_entry("S%02d" % index, "S%02d" % (index - 1), "3"))
        report = check_seam_ledger({"entries": apply_submit_params(entries, shots)}, shots)
        self.assertIn("不能超过 3 处", report["report"])

        long = [_shot("S01", ["图1"]), _shot("S02", [], duration=4)]
        report = check_seam_ledger(
            {"entries": apply_submit_params([
                _entry("S01", "", "none"),
                _entry("S02", "S01", "3"),
            ], long)},
            long,
        )
        self.assertIn("1 到 2 秒", report["report"])

        styled = [_shot("S%02d" % index, ["图1"]) for index in range(1, 6)]
        entries = [_entry("S01", "", "none")]
        for index in range(2, 6):
            entries.append(_entry("S%02d" % index, "S%02d" % (index - 1), "4"))
        report = check_seam_ledger({"entries": apply_submit_params(entries, styled)}, styled)
        self.assertIn("技巧型转场", report["report"])
        nameless = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "4", transition=""),
        ], styled[:2])
        report = check_seam_ledger({"entries": nameless}, styled[:2])
        self.assertIn("转场手法", report["report"])


class SubmitAndPackingTest(unittest.TestCase):
    def test_reference_submit_does_not_attach_the_previous_tail(self):
        def url_for(asset_id):
            return "https://cdn.example/%s.png" % asset_id, None

        _aspect, media = assemble_shot_media(
            {"id": "S02", "images": ["图1"], "video_mode": "reference", "first_frame_source": "none"},
            "reference",
            url_for,
            "https://cdn.example/tail.jpg",
        )
        self.assertEqual([item["role"] for item in media], ["reference_image"])
        self.assertEqual(media[0]["url"], "https://cdn.example/图1.png")
        self.assertEqual(
            choose_video_mode(
                {"id": "S02", "images": ["图1"], "end_image": "图2", "video_mode": "reference"},
                continues=True,
            ),
            "reference",
        )
        _aspect, framed = assemble_shot_media(
            {
                "id": "S02",
                "images": ["图1"],
                "video_mode": "first_frame",
                "first_frame_source": "previous_tail",
            },
            "first_frame",
            url_for,
            "https://cdn.example/tail.jpg",
        )
        self.assertEqual(framed, [{"role": "first_frame", "url": "https://cdn.example/tail.jpg"}])

    def test_collect_steps_reads_fields_from_packed_units(self):
        self.assertEqual(group_shot_durations([5, 5, 6]), [[5, 5, 6]])
        self.assertEqual(group_shot_durations([5, 5], locks=["2", "2"]), [[5, 5]])
        feed = "\n\n".join((
            "## 【封装单元 1/2】\nduration: 10\nsubject_definitions:\n<Subject 1>: a woman\ndetailed_description:\nShe stays.\noverall_soundscape:\nroom\nnon_diegetic_music:\nN/A",
            "## 【封装单元 2/2】\nduration: 5\nsubject_definitions:\n<Subject 1>: a door\ndetailed_description:\nThe door opens.\noverall_soundscape:\nroom\nnon_diegetic_music:\nN/A",
        ))
        units = [
            {"id": "封装单元 1/2", "duration": 10, "images": ["图1"], "prompt": "one"},
            {"id": "封装单元 2/2", "duration": 5, "images": ["图2"], "prompt": "two"},
        ]
        entries = apply_submit_params([
            _entry("封装单元 1/2", "", "none"),
            _entry("封装单元 2/2", "封装单元 1/2", "2"),
        ], units)
        stamped = write_unit_fields(units, entries)
        spec = {
            "assets": [
                {"id": "图1", "title": "人", "prompt": "一个人"},
                {"id": "图2", "title": "门", "prompt": "一扇门"},
            ],
            "shots": [
                _shot("S01", ["图1"]),
                _shot("S02", ["图1"]),
                _shot("S03", ["图2"]),
            ],
            "seam_ledger": {"entries": entries},
            "feed_units": stamped,
            "feed": feed,
        }
        _assets, built = collect_steps(spec)
        self.assertEqual(built[0]["video_mode"], "reference")
        self.assertEqual(built[0]["images"], ["图1"])
        self.assertEqual(built[0]["first_frame_source"], "none")
        self.assertNotIn("end_image", built[0])
        self.assertEqual(built[1]["video_mode"], "reference")
        self.assertEqual(built[1]["images"], ["图2"])
        self.assertEqual(built[1]["first_frame_source"], "none")
        self.assertEqual(built[1]["seam_d_lock"], "2")
        self.assertNotIn("video_mode", spec["shots"][0])
        self.assertNotIn("video_mode", spec["shots"][1])

    def test_locked_storyboard_blocks_image_and_video_until_the_ledger_passes(self):
        spec = {
            "storyboard_contract": "shot-prose-v1",
            "storyboard": "## S01\n动作：停\n景别机位：中景\n时长：5\n资产：图1",
            "assets": [{"id": "图1", "title": "人", "prompt": "一个人"}],
            "shots": [_shot("S01", ["图1"])],
        }
        self.assertIn("不能出图或出片", seam_blocks_render(spec))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as caught:
                run_local_pipeline(spec, Path(tmp), images_only=True)
            self.assertIn("不能出图或出片", str(caught.exception))
        feed = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 5",
            "subject_definitions:",
            "<Subject 1>: a woman",
            "detailed_description:",
            "She stays.",
            "overall_soundscape:",
            "room",
            "non_diegetic_music:",
            "N/A",
        ))
        units = packed_units({"feed": feed, "assets": spec["assets"], "shots": spec["shots"]})
        entries = apply_submit_params([_entry(units[0]["id"], "", "none")], units)
        ready = dict(spec)
        ready["feed"] = feed
        ready["feed_units"] = write_unit_fields(units, entries)
        ready["seam_ledger"] = {"entries": entries}
        self.assertEqual(seam_blocks_render(ready, collect_steps(ready)[1]), "")

    def test_a_new_storyboard_drops_the_ledger_and_written_video_fields(self):
        shots, ledger = _board()
        spec = {
            "storyboard": "旧分镜",
            "storyboard_contract": "shot-prose-v1",
            "seam_ledger": ledger,
            "shots": write_unit_fields(shots, ledger["entries"]),
        }
        self.assertEqual(spec["shots"][1]["video_mode"], "first_frame")
        spec["feed"] = "## 【封装单元 1/1】\nduration: 5\n"
        spec["feed_units"] = [{"id": "封装单元 1/1", "video_mode": "reference", "seam_d_lock": "none"}]
        cleared = project_into_spec(spec, {"storyboard": "## S01\n动作：停\n景别机位：中景\n时长：5\n资产：图1", "_storyboard_contract": True})
        self.assertNotIn("seam_ledger", cleared)
        self.assertNotIn("feed", cleared)
        self.assertNotIn("feed_units", cleared)
        self.assertNotIn("video_mode", cleared["shots"][0])
        self.assertNotIn("first_frame_source", cleared["shots"][0])
        again = strip_seam({
            "feed": spec["feed"],
            "feed_units": write_unit_fields(
                [{"id": "S02", "images": ["图1"]}],
                ledger["entries"],
            ),
            "shots": write_unit_fields(shots, ledger["entries"]),
            "seam_ledger": ledger,
        })
        self.assertNotIn("seam_ledger", again)
        self.assertNotIn("video_mode", again["shots"][1])
        self.assertNotIn("video_mode", again["feed_units"][0])
        self.assertEqual(again["feed"], spec["feed"])
        dropped = drop_packing(spec)
        self.assertNotIn("feed", dropped)
        self.assertNotIn("seam_ledger", dropped)


class SeamWorkerTest(unittest.TestCase):
    def test_graph_is_generate_critique_check_and_the_critic_does_not_edit(self):
        drawn = compile_seam_graph(lambda role, messages: "").get_graph()
        self.assertEqual(
            set(drawn.nodes),
            {"__start__", "generate", "critique", "machine_check", "__end__"},
        )
        shots, ledger = _board()
        calls = []

        def complete(role, messages):
            calls.append(role)
            if role == "critic":
                return json.dumps({
                    "verdict": "accept",
                    "issues": [],
                    "seam_ledger": {"entries": [{"shot_id": "NOPE", "d_lock": "4"}]},
                }, ensure_ascii=False)
            return json.dumps({"seam_ledger": ledger}, ensure_ascii=False)

        result = run_seam_loop(
            "她把药盒放下。",
            "## S01\n时长：5",
            units=shots,
            complete=complete,
        )
        self.assertTrue(result["passed"], result["machine_report"])
        self.assertEqual([unit["id"] for unit in result["feed_units"]], ["S01", "S02", "S03", "S04"])
        self.assertEqual(result["feed_units"][1]["video_mode"], "first_frame")
        self.assertEqual(result["feed_units"][1]["first_frame_source"], "previous_tail")
        self.assertEqual(result["feed_units"][1]["seam_d_lock"], "1")
        self.assertTrue(result["critic_report"]["stripped_rewrite"])
        self.assertNotIn("NOPE", json.dumps(result["seam_ledger"], ensure_ascii=False))
        self.assertEqual(calls, ["specialist", "director", "critic"])


class UnitOrderTest(unittest.TestCase):
    def _feed(self, count):
        blocks = []
        for index in range(1, count + 1):
            blocks.append("\n".join((
                "## 【封装单元 %s/%s】" % (index, count),
                "duration: 5",
                "subject_definitions:",
                "<Subject 1>: a woman",
                "detailed_description:",
                "She stays.",
                "overall_soundscape:",
                "room",
                "non_diegetic_music:",
                "N/A",
            )))
        return "\n\n".join(blocks)

    def _spec(self, count, entries):
        feed = self._feed(count)
        spec = {
            "storyboard_contract": "shot-prose-v1",
            "storyboard": "## S01\n动作：停\n景别机位：中景\n时长：5\n资产：图1",
            "assets": [{"id": "图1", "title": "人", "prompt": "一个人"}],
            "shots": [_shot("S%02d" % index, ["图1"]) for index in range(1, count + 1)],
            "feed": feed,
            "seam_ledger": {"entries": entries},
        }
        spec["feed_units"] = packed_units(spec)
        return spec

    def test_packing_can_run_without_a_ledger(self):
        stages = stages_for({}, "")
        self.assertEqual(stages, ["script", "asset", "storyboard", "feed", "seam"])
        self.assertEqual(next_pending(stages, ["script", "asset", "storyboard"]), "feed")
        self.assertEqual(
            next_pending(stages, ["script", "asset", "storyboard", "seam", "feed"]),
            "seam",
        )
        self.assertEqual(
            next_pending(stages, ["script", "asset", "storyboard", "feed"]),
            "seam",
        )
        self.assertEqual(
            next_pending(stages, ["script", "asset", "storyboard", "feed", "seam"]),
            "",
        )
        self.assertNotIn("seam", accepted_done(["script", "asset", "storyboard", "seam", "feed"]))
        self.assertIn("seam", accepted_done(["script", "asset", "storyboard", "feed", "seam"]))

    def test_seam_matches_packed_unit_ids_and_count(self):
        units = packed_units({"feed": self._feed(2)})
        self.assertEqual([unit["id"] for unit in units], ["封装单元 1/2", "封装单元 2/2"])
        entries = apply_submit_params([
            _entry("封装单元 1/2", "", "none"),
            _entry("封装单元 2/2", "封装单元 1/2", "2"),
        ], units)
        report = check_seam_ledger({"entries": entries}, units)
        self.assertEqual(report["errors"], [], report["report"])
        numbered = normalize_entry_ids([
            _entry("1", "", "none"),
            _entry("2", "1", "2"),
        ], units)
        numbered = apply_submit_params(numbered, units)
        for entry, bare in zip(numbered, ("1", "2")):
            entry["shot_id"] = bare
        numbered_report = check_seam_ledger({"entries": numbered}, units)
        self.assertEqual(numbered_report["errors"], [], numbered_report["report"])
        self.assertEqual(
            [item["shot_id"] for item in normalize_entry_ids(numbered, units)],
            ["封装单元 1/2", "封装单元 2/2"],
        )

    def test_shot_level_and_short_ledgers_fail_the_gate(self):
        shot_level = self._spec(2, apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "2"),
        ], [_shot("S01", ["图1"]), _shot("S02", ["图1"])]))
        blocked = seam_blocks_render(shot_level)
        self.assertIn("不能出图或出片", blocked)
        self.assertIn("对不上封装单元", blocked)
        self.assertIn("S01", blocked)

        short = self._spec(5, [
            _entry(str(index), "", "none" if index == 1 else "2")
            for index in range(1, 5)
        ])
        blocked = seam_blocks_render(short)
        self.assertIn("不能出图或出片", blocked)
        self.assertIn("不能把多个单元收成更少的段", blocked)
        self.assertIn("5", blocked)

    def test_packing_rewrite_drops_the_ledger_and_unit_video_fields(self):
        units = packed_units({"feed": self._feed(1), "assets": [{"id": "图1", "title": "人", "prompt": "一个人"}]})
        entries = apply_submit_params([_entry(units[0]["id"], "", "none")], units)
        spec = {
            "feed": self._feed(1),
            "feed_units": write_unit_fields(units, entries),
            "seam_ledger": {"entries": entries},
            "shots": [_shot("S01", ["图1"], duration=5)],
        }
        spec["shots"][0]["video_mode"] = "reference"
        rewritten = _spec_patch("feed", {"feed": self._feed(1).replace("duration: 5", "duration: 6")}, spec)
        self.assertNotIn("seam_ledger", rewritten)
        self.assertEqual(rewritten["feed_units"][0]["id"], "封装单元 1/1")
        self.assertNotIn("video_mode", rewritten["feed_units"][0])
        self.assertNotIn("seam_d_lock", rewritten["feed_units"][0])
        self.assertNotIn("video_mode", rewritten["shots"][0])
        self.assertIn("duration: 6", rewritten["feed"])

    def _timed_feed(self, durations, token="封装单元", first_suffix=""):
        total = len(durations)
        blocks = []
        for index, seconds in enumerate(durations, 1):
            suffix = first_suffix if index == 1 else ""
            blocks.append("\n".join((
                "## 【%s %s/%s%s】" % (token, index, total, suffix),
                "duration: %s" % seconds,
                "subject_definitions:",
                "<Subject 1>: a woman",
                "detailed_description:",
                "She stays.",
                "overall_soundscape:",
                "room",
                "non_diegetic_music:",
                "N/A",
            )))
        return "\n\n".join(blocks)

    def test_packing_emits_fraction_ids_and_a_matching_ledger_passes(self):
        feed = self._timed_feed((11, 14, 9), first_suffix=" · 段1")
        self.assertEqual(unit_id_from_text(feed.split("\n\n", 1)[0], 1), "封装单元 1/3")
        self.assertEqual(
            unit_id_from_text("## 【封装单元 2／3】\nduration: 14\n", 2),
            "封装单元 2/3",
        )
        self.assertEqual(unit_id_from_text("## 【封装单元 1】\n", 1), "封装单元 1")
        units = packed_units({"feed": feed})
        self.assertEqual(
            [unit["id"] for unit in units],
            ["封装单元 1/3", "封装单元 2/3", "封装单元 3/3"],
        )
        self.assertEqual([unit["duration"] for unit in units], [11.0, 14.0, 9.0])
        entries = apply_submit_params([
            _entry("封装单元 1/3", "", "none"),
            _entry("封装单元 2/3", "封装单元 1/3", "2"),
            _entry("封装单元 3/3", "封装单元 2/3", "2"),
        ], units)
        report = check_seam_ledger({"entries": entries}, units)
        self.assertEqual(report["errors"], [], report["report"])
        state = {
            "attempt": 0,
            "units": units,
            "source": "剧本",
            "storyboard": "## S01",
            "feed": feed,
        }
        brief = generator_messages(state)[-1]["content"]
        self.assertIn(
            "要原样照抄这些 id：封装单元 1/3、封装单元 2/3、封装单元 3/3。",
            brief,
        )
        self.assertIn("封装单元 N/M", GENERATOR_SYSTEM)
        opened = generator_messages(dict(state, opening="任务：分镜衔接\n已有简报"))[-1]["content"]
        self.assertIn(
            "要原样照抄这些 id：封装单元 1/3、封装单元 2/3、封装单元 3/3。",
            opened,
        )

    def test_short_stored_id_normalizes_without_rewriting_submit_fields(self):
        feed = self._timed_feed((11, 14, 9))
        stored = [
            {
                "id": "封装单元 1",
                "video_mode": "reference",
                "seam_d_lock": "none",
                "first_frame_source": "none",
                "images": ["图1"],
                "video_images": ["图1"],
            },
            {
                "id": "封装单元 2",
                "video_mode": "first_frame",
                "seam_d_lock": "2",
                "first_frame_source": "previous_tail",
                "end_image": "图尾",
                "writing_mode": "普通衔接",
            },
            {
                "id": "封装单元 3",
                "video_mode": "reference",
                "seam_d_lock": "2",
                "first_frame_source": "none",
            },
        ]
        units = packed_units({"feed": feed, "feed_units": stored})
        self.assertEqual(
            [unit["id"] for unit in units],
            ["封装单元 1/3", "封装单元 2/3", "封装单元 3/3"],
        )
        self.assertEqual(units[0]["video_mode"], "reference")
        self.assertEqual(units[0]["seam_d_lock"], "none")
        self.assertEqual(units[0]["first_frame_source"], "none")
        self.assertEqual(units[0]["images"], ["图1"])
        self.assertEqual(units[0]["video_images"], ["图1"])
        self.assertEqual(units[1]["video_mode"], "first_frame")
        self.assertEqual(units[1]["seam_d_lock"], "2")
        self.assertEqual(units[1]["first_frame_source"], "previous_tail")
        self.assertEqual(units[1]["end_image"], "图尾")
        self.assertEqual(units[1]["writing_mode"], "普通衔接")
        self.assertEqual(units[2]["video_mode"], "reference")
        self.assertEqual(units[2]["seam_d_lock"], "2")
        entries = apply_submit_params([
            _entry("封装单元 1/3", "", "none"),
            _entry("封装单元 2/3", "封装单元 1/3", "2"),
            _entry("封装单元 3/3", "封装单元 2/3", "2"),
        ], units)
        passed = check_seam_ledger({"entries": entries}, units)
        self.assertEqual(passed["errors"], [], passed["report"])
        short = apply_submit_params([
            _entry("封装单元 1", "", "none"),
            _entry("封装单元 2", "封装单元 1", "2"),
            _entry("封装单元 3", "封装单元 2", "2"),
        ], units)
        blocked = check_seam_ledger({"entries": short}, units)
        self.assertFalse(blocked["ok"])
        self.assertIn("封装单元 1/3 没有段缝", blocked["errors"])
        self.assertIn("封装单元 2/3 没有段缝", blocked["errors"])
        self.assertIn("封装单元 3/3 没有段缝", blocked["errors"])
        self.assertIn(
            "段缝 封装单元 1 对不上封装单元。要原样照抄这些 id：封装单元 1/3、封装单元 2/3、封装单元 3/3。",
            blocked["errors"],
        )
        self.assertIn(
            "段缝 封装单元 2 对不上封装单元。要原样照抄这些 id：封装单元 1/3、封装单元 2/3、封装单元 3/3。",
            blocked["errors"],
        )

    def test_legacy_feed_title_keeps_its_token_and_the_fraction(self):
        feed = self._timed_feed((6, 7), token="投喂单元")
        units = packed_units({
            "feed": feed,
            "feed_units": [
                {
                    "id": "投喂单元 1",
                    "video_mode": "text",
                    "seam_d_lock": "none",
                    "first_frame_source": "none",
                },
                {
                    "id": "投喂单元 2",
                    "video_mode": "reference",
                    "seam_d_lock": "2",
                    "first_frame_source": "none",
                },
            ],
        })
        self.assertEqual([unit["id"] for unit in units], ["投喂单元 1/2", "投喂单元 2/2"])
        self.assertEqual(units[0]["video_mode"], "text")
        self.assertEqual(units[0]["seam_d_lock"], "none")
        self.assertEqual(units[1]["video_mode"], "reference")
        self.assertEqual(units[1]["seam_d_lock"], "2")
        entries = apply_submit_params([
            _entry("投喂单元 1/2", "", "none"),
            _entry("投喂单元 2/2", "投喂单元 1/2", "2"),
        ], units)
        report = check_seam_ledger({"entries": entries}, units)
        self.assertEqual(report["errors"], [], report["report"])
        wrong_token = apply_submit_params([
            _entry("封装单元 1/2", "", "none"),
            _entry("封装单元 2/2", "封装单元 1/2", "2"),
        ], units)
        mismatched = check_seam_ledger({"entries": wrong_token}, units)
        self.assertFalse(mismatched["ok"])
        self.assertIn("投喂单元 1/2 没有段缝", mismatched["errors"])
        self.assertIn(
            "要原样照抄这些 id：投喂单元 1/2、投喂单元 2/2。",
            mismatched["report"],
        )


def _packed_units(count):
    blocks = []
    for index in range(1, count + 1):
        blocks.append("\n".join((
            "## 【封装单元 %s/%s】" % (index, count),
            "duration: 5",
            "subject_definitions:",
            "<Subject 1>: a woman",
            "detailed_description:",
            "She stays.",
            "overall_soundscape:",
            "room",
            "non_diegetic_music:",
            "N/A",
        )))
    return packed_units({"feed": "\n\n".join(blocks)})


class SceneRelationTest(unittest.TestCase):
    def test_aliases_keep_an_explicit_relation_and_default_the_rest(self):
        self.assertEqual(FIRST_UNIT_SCENE_RELATION, "opening")
        self.assertEqual(DEFAULT_SCENE_RELATION, "same_scene")
        self.assertIn(FIRST_UNIT_SCENE_RELATION, SCENE_RELATIONS)
        self.assertIn(DEFAULT_SCENE_RELATION, SCENE_RELATIONS)

        continuous = "同场景" + "\uff0c" + "连续"
        disconnected = "同场景" + "\uff0c" + "事件断开"
        self.assertEqual(normalize_scene_relation(continuous, first=True), "same_scene")
        self.assertEqual(normalize_scene_relation("同场景,连续", first=True), "same_scene")
        self.assertEqual(normalize_scene_relation("  " + continuous + "  ", first=True), "same_scene")
        self.assertEqual(normalize_scene_relation(disconnected, first=False), "same_scene")
        self.assertEqual(normalize_scene_relation("同场景", first=True), "same_scene")
        self.assertEqual(normalize_scene_relation("同场", first=False), "same_scene")
        self.assertEqual(normalize_scene_relation("SAME_SCENE", first=True), "same_scene")
        self.assertEqual(normalize_scene_relation("同场景" + "\u3001" + "连续", first=True), "same_scene")

        self.assertEqual(normalize_scene_relation("", first=True), "opening")
        self.assertEqual(normalize_scene_relation(None, first=True), "opening")
        self.assertEqual(normalize_scene_relation("随便写的", first=True), "opening")
        self.assertEqual(normalize_scene_relation("不同场景", first=True), "opening")
        self.assertEqual(normalize_scene_relation("", first=False), "same_scene")
        self.assertEqual(normalize_scene_relation("???", first=False), "same_scene")
        self.assertEqual(normalize_scene_relation("不同场景", first=False), "same_scene")

        self.assertEqual(normalize_scene_relation("time_jump", first=True), "time_jump")
        self.assertEqual(normalize_scene_relation(" Time_Jump ", first=False), "time_jump")
        self.assertEqual(normalize_scene_relation("opening", first=False), "opening")

        pairs = (
            ("开场", "opening"),
            ("首段", "opening"),
            ("无入缝", "opening"),
            ("新场景", "new_scene"),
            ("换场", "new_scene"),
            ("时间跳", "time_jump"),
            ("时间跳跃", "time_jump"),
            ("换时间", "time_jump"),
            ("地点跳", "place_jump"),
            ("换地点", "place_jump"),
            ("剧情段", "plot_group"),
            ("剧情组", "plot_group"),
            ("对白拆分", "dialogue_split"),
            ("一句话被拆开", "dialogue_split"),
            ("一镜到底", "oner"),
            ("情绪落地", "emotion_landing"),
            ("情绪收尾", "emotion_landing"),
        )
        for phrase, token in pairs:
            self.assertEqual(normalize_scene_relation(phrase, first=False), token, phrase)
            self.assertEqual(normalize_scene_relation(phrase, first=True), token, phrase)

    def test_chinese_same_scene_ledger_passes_and_writes_enum_tokens(self):
        units = _packed_units(4)
        self.assertEqual(
            [unit["id"] for unit in units],
            ["封装单元 1/4", "封装单元 2/4", "封装单元 3/4", "封装单元 4/4"],
        )
        phrases = (
            "同场景" + "\uff0c" + "连续",
            "同场景" + "\uff0c" + "事件断开",
            "同场景" + "\uff0c" + "连续",
            "同场景" + "\uff0c" + "事件断开",
        )
        raw = []
        for index, phrase in enumerate(phrases, 1):
            shot_id = "封装单元 %s/4" % index
            prev = "" if index == 1 else "封装单元 %s/4" % (index - 1)
            lock = "none" if index == 1 else "2"
            raw.append(_entry(shot_id, prev, lock, scene_relation=phrase))
        written = apply_submit_params(raw, units)
        self.assertEqual([item["scene_relation"] for item in written], ["same_scene"] * 4)
        self.assertEqual(written[0]["d_lock"], "none")
        self.assertEqual([item["d_lock"] for item in written[1:]], ["2", "2", "2"])
        self.assertEqual([item["video_mode"] for item in written], ["reference"] * 4)
        self.assertTrue(all(item["end_image"] == "" for item in written))
        self.assertNotIn("text", [item["video_mode"] for item in written])
        self.assertNotIn("first_last_frame", [item["video_mode"] for item in written])
        for item in written:
            self.assertIn(item["scene_relation"], SCENE_RELATIONS)

        stored = []
        for item, phrase in zip(written, phrases):
            row = dict(item)
            row["scene_relation"] = phrase
            stored.append(row)
        report = check_seam_ledger({"entries": stored}, units)
        self.assertTrue(report["ok"], report["report"])
        self.assertEqual([item["scene_relation"] for item in stored], list(phrases))

        mixed = apply_submit_params([
            _entry("封装单元 1/4", "", "none", scene_relation=""),
            _entry("封装单元 2/4", "封装单元 1/4", "2", scene_relation="没写清"),
            _entry("封装单元 3/4", "封装单元 2/4", "2", scene_relation="time_jump"),
            _entry("封装单元 4/4", "封装单元 3/4", "2", scene_relation=phrases[1]),
        ], units)
        self.assertEqual(
            [item["scene_relation"] for item in mixed],
            ["opening", "same_scene", "time_jump", "same_scene"],
        )
        mixed_report = check_seam_ledger({"entries": mixed}, units)
        self.assertTrue(mixed_report["ok"], mixed_report["report"])

        flipped = apply_submit_params([
            _entry("封装单元 2/4", "封装单元 1/4", "2", scene_relation=""),
            _entry("封装单元 1/4", "", "none", scene_relation="看不懂"),
        ], units[:2])
        by_id = {item["shot_id"]: item["scene_relation"] for item in flipped}
        self.assertEqual(by_id["封装单元 1/4"], "opening")
        self.assertEqual(by_id["封装单元 2/4"], "same_scene")

        shots = [_shot("S01", ["图1"]), _shot("S02", ["图1"])]
        false_continue = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", scene_relation="剧情段", action_continuous=False),
        ], shots)
        self.assertEqual(false_continue[1]["scene_relation"], "plot_group")
        blocked = check_seam_ledger({"entries": false_continue}, shots)
        self.assertFalse(blocked["ok"])
        self.assertIn("并没有接上", blocked["report"])

    def test_prompts_and_invalid_relation_error_list_the_closed_set(self):
        root = Path(__file__).resolve().parents[1]
        generator = (root / "Agents/skills/segment-splicing/generator.md").read_text(encoding="utf-8")
        critic = (root / "Agents/skills/segment-splicing/critic.md").read_text(encoding="utf-8")
        units = _packed_units(4)
        state = {
            "attempt": 0,
            "units": units,
            "source": "剧本",
            "storyboard": "分镜",
            "feed": "",
        }
        brief = generator_messages(state)[-1]["content"]
        repair = generator_messages({
            "attempt": 1,
            "units": units,
            "draft": "{}",
            "critic_report": {"verdict": "revise", "issues": []},
            "machine_report": {"errors": ["封装单元 1/4 的场景关系无效"]},
        })[-1]["content"]
        opened = generator_messages(dict(state, opening="任务：分镜衔接\n已有简报"))[-1]["content"]
        message = invalid_scene_relation_error("封装单元 1/4")
        for token in SCENE_RELATIONS:
            self.assertIn(token, GENERATOR_SYSTEM)
            self.assertIn(token, brief)
            self.assertIn(token, repair)
            self.assertIn(token, opened)
            self.assertIn(token, generator)
            self.assertIn(token, critic)
            self.assertIn(token, message)
        self.assertIn("只能是", message)

        entries = apply_submit_params([
            _entry("封装单元 1/4", "", "none"),
            _entry("封装单元 2/4", "封装单元 1/4", "2"),
            _entry("封装单元 3/4", "封装单元 2/4", "2"),
            _entry("封装单元 4/4", "封装单元 3/4", "2"),
        ], units)
        with patch("end.seam_ledger.normalize_scene_relation", return_value="not-a-relation"):
            blocked = check_seam_ledger({"entries": entries}, units)
        self.assertFalse(blocked["ok"])
        for unit in units:
            self.assertIn(invalid_scene_relation_error(unit["id"]), blocked["errors"])


def _h3_unit(index, total, seconds, body="She stays."):
    return "\n".join((
        "## 【封装单元 %s/%s】" % (index, total),
        "duration: %s" % seconds,
        "subject_definitions:",
        "<Subject 1>: a woman",
        "detailed_description:",
        body,
        "overall_soundscape:",
        "room",
        "non_diegetic_music:",
        "N/A",
    ))


class OfficialModeTest(unittest.TestCase):
    def test_only_first_frame_and_reference_are_ever_chosen(self):
        self.assertFalse(continues_from_previous(_entry("S01", "", "none", action_continuous=True), first=True))
        self.assertTrue(continues_from_previous(_entry("S02", "S01", "1")))
        self.assertTrue(continues_from_previous(
            _entry("S02", "S01", "2", scene_relation="dialogue_split", action_continuous=False),
        ))
        self.assertTrue(continues_from_previous(
            _entry("S02", "S01", "2", scene_relation="oner", action_continuous=False),
        ))
        self.assertTrue(continues_from_previous(
            _entry("S02", "S01", "2", plot_continuous=True, action_continuous=False),
        ))
        self.assertFalse(continues_from_previous(
            _entry("S02", "S01", "2", scene_relation="same_scene", action_continuous=False, plot_continuous=False),
        ))
        self.assertFalse(continues_from_previous(_entry("S02", "S01", "2")))
        self.assertFalse(continues_from_previous(_entry("S02", "S01", "3")))

        for lock in ("none", "1", "2", "3", "4"):
            chosen = map_submit_params(
                _entry(
                    "S02",
                    "S01",
                    lock,
                    anchor_end=True,
                    end_image="图9",
                    video_mode="first_last_frame",
                ),
                _shot("S02", []),
                first=(lock == "none"),
            )
            self.assertIn(chosen["video_mode"], ("first_frame", "reference"))
            self.assertEqual(chosen["end_image"], "")
            self.assertNotIn(chosen["video_mode"], ("text", "first_last_frame"))
            if lock == "1":
                self.assertEqual(chosen["video_mode"], "first_frame")
                self.assertEqual(chosen["first_frame_source"], "previous_tail")
                self.assertEqual(chosen["images"], [])
            else:
                self.assertEqual(chosen["video_mode"], "reference")
                self.assertEqual(chosen["first_frame_source"], "none")

        shots = [_shot("S01", ["图1"]), _shot("S02", ["图1"])]
        dialogue = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "2", scene_relation="dialogue_split", action_continuous=False),
        ], shots)
        self.assertEqual(dialogue[1]["video_mode"], "first_frame")
        self.assertEqual(dialogue[1]["images"], [])
        self.assertEqual(dialogue[1]["first_frame_source"], "previous_tail")
        report = check_seam_ledger({"entries": dialogue}, shots)
        self.assertEqual(report["errors"], [], report["report"])

        rejected = [dict(item) for item in dialogue]
        rejected[1]["video_mode"] = "text"
        text_report = check_seam_ledger({"entries": rejected}, shots)
        self.assertIn("first_frame 或 reference", text_report["report"])
        rejected[1]["video_mode"] = "first_last_frame"
        rejected[1]["end_image"] = "图9"
        frame_report = check_seam_ledger({"entries": rejected}, shots)
        self.assertIn("first_frame 或 reference", frame_report["report"])

        opening = apply_submit_params([
            _entry("S01", "", "none", action_continuous=True, plot_continuous=True),
        ], [_shot("S01", ["图1"])])
        self.assertEqual(opening[0]["video_mode"], "reference")
        self.assertEqual(opening[0]["images"], ["图1"])
        self.assertEqual(opening[0]["first_frame_source"], "none")

    def test_empty_unit_images_still_reference_and_bind_storyboard_stills(self):
        feed = "\n\n".join((_h3_unit(1, 2, 20), _h3_unit(2, 2, 11)))
        assets = [
            {"id": "图1", "title": "小君", "subject": "小君", "subject_kind": "character"},
            {"id": "图2", "title": "药盒", "subject": "药盒", "subject_kind": "prop"},
            {"id": "图3", "title": "小君房间", "subject": "房间", "subject_kind": "location"},
        ]
        board = [
            _shot("S01", ["图1"], duration=10),
            _shot("S02", ["图2"], duration=10),
            _shot("S03", ["图3"], duration=11),
        ]
        spec = {"feed": feed, "assets": assets, "shots": board}
        units = packed_units(spec)
        self.assertEqual([unit["id"] for unit in units], ["封装单元 1/2", "封装单元 2/2"])
        self.assertEqual(units[0]["images"], ["图1", "图2"])
        self.assertEqual(units[1]["images"], ["图3"])
        entries = apply_submit_params([
            _entry(units[0]["id"], "", "none"),
            _entry(units[1]["id"], units[0]["id"], "2"),
        ], units)
        self.assertEqual(entries[0]["video_mode"], "reference")
        self.assertEqual(entries[0]["images"], ["图1", "图2"])
        self.assertEqual(entries[0]["end_image"], "")
        self.assertEqual(entries[1]["video_mode"], "reference")
        self.assertEqual(entries[1]["images"], ["图3"])
        self.assertNotIn(entries[0]["video_mode"], ("text", "first_last_frame"))
        self.assertNotIn(entries[1]["video_mode"], ("text", "first_last_frame"))
        spec["seam_ledger"] = {"entries": entries}
        submitted = units_for_submit(spec)
        self.assertEqual(submitted[0]["video_mode"], "reference")
        self.assertEqual(submitted[0]["video_images"], ["图1", "图2"])
        self.assertEqual(submitted[0]["images"], ["图1", "图2"])
        self.assertEqual(submitted[1]["video_images"], ["图3"])
        self.assertNotIn("end_image", submitted[0])
        self.assertNotIn("end_image", submitted[1])

        named = packed_units({
            "feed": _h3_unit(1, 1, 5, body="小君 stays by the window."),
            "assets": [{"id": "图2", "title": "小君", "subject": "小君", "subject_kind": "character"}],
            "shots": [_shot("S01", [], duration=5)],
        })
        self.assertEqual(named[0]["images"], [])
        named_entry = apply_submit_params([
            _entry(named[0]["id"], "", "none"),
        ], named)[0]
        self.assertEqual(named_entry["video_mode"], "reference")
        self.assertEqual(named_entry["images"], [])
        self.assertNotEqual(named_entry["video_mode"], "text")

        continuous = apply_submit_params([
            _entry(units[0]["id"], "", "none", action_continuous=True),
            _entry(units[1]["id"], units[0]["id"], "1"),
        ], units)
        self.assertEqual(continuous[0]["video_mode"], "reference")
        self.assertEqual(continuous[0]["images"], ["图1", "图2"])
        self.assertEqual(continuous[1]["video_mode"], "first_frame")
        self.assertEqual(continuous[1]["first_frame_source"], "previous_tail")
        self.assertEqual(continuous[1]["images"], [])
        self.assertEqual(continuous[1]["end_image"], "")
        spec["seam_ledger"] = {"entries": continuous}
        submitted = units_for_submit(spec)
        self.assertEqual(submitted[1]["video_mode"], "first_frame")
        self.assertEqual(submitted[1]["video_images"], [])
        self.assertEqual(submitted[1]["images"], [])
        self.assertEqual(submitted[1]["first_frame_source"], "previous_tail")
        self.assertEqual(submitted[1]["seam_d_lock"], "1")

    def test_extracted_tail_stays_on_the_unit_and_is_not_a_designed_end_frame(self):
        shots, ledger = _board()
        stamped = {"entries": [dict(item) for item in ledger["entries"]]}
        stamped["entries"][2]["end_image"] = "S03_tail"
        report = check_seam_ledger(stamped, shots)
        self.assertEqual(report["errors"], [], report["report"])
        self.assertEqual(stamped["entries"][2]["video_mode"], "reference")
        self.assertNotEqual(stamped["entries"][2]["video_mode"], "first_last_frame")
        self.assertNotEqual(stamped["entries"][2]["video_mode"], "text")

        designed = {"entries": [dict(item) for item in ledger["entries"]]}
        designed["entries"][2]["end_image"] = "图9"
        designed_report = check_seam_ledger(designed, shots)
        self.assertIn("不能带结束帧", designed_report["report"])

        units = [dict(item) for item in shots]
        units[0]["end_image"] = "S01_tail"
        units[2]["end_image"] = "图9"
        written = write_unit_fields(units, ledger["entries"])
        self.assertEqual(written[0]["end_image"], "S01_tail")
        self.assertEqual(written[0]["video_mode"], "reference")
        self.assertNotIn("end_image", written[2])
        self.assertNotIn(written[0]["video_mode"], ("text", "first_last_frame"))
        self.assertNotIn(written[1]["video_mode"], ("text", "first_last_frame"))


# Storyboard 资产栏 for the five packed units of 「眼睛先下班」.
_EYE_COLUMNS = (
    ("S01", ["图1", "图4"]),
    ("S02", ["图1", "图4"]),
    ("S03", ["图1", "图4"]),
    ("S04", ["图2", "图5", "图6"]),
    ("S05", ["图1", "图4", "图5"]),
    ("S06", ["图1", "图4", "图5", "图6"]),
    ("S07", ["图3", "图9", "图4", "图5", "图6"]),
    ("S08", ["图3", "图9", "图4", "图5"]),
    ("S09", ["图3", "图9", "图4", "图5", "图6"]),
    ("S10", ["图3", "图9"]),
)
_EYE_UNITS = (
    ["图1", "图4"],
    ["图1", "图4", "图2", "图5", "图6"],
    ["图1", "图4", "图5", "图6"],
    ["图3", "图9", "图4", "图5", "图6"],
    ["图3", "图9", "图4", "图5", "图6"],
)


def _eye_feed():
    blocks = []
    for index, ids in enumerate(_EYE_UNITS, 1):
        body = "She stays."
        if index == 2:
            body = "The man waits in the living room.\n资产：图5"
        block = _h3_unit(index, 5, 10, body=body)
        line = "资产：" + "，".join(ids)
        block = block.replace("duration: 10\n", "duration: 10\n%s\n" % line, 1)
        blocks.append(block)
    return "\n\n".join(blocks)


def _eye_spec():
    assets = []
    for number, title in (
        (1, "女主"),
        (2, "女主近景"),
        (3, "女主侧脸"),
        (4, "药盒"),
        (5, "男主"),
        (6, "男主手部"),
        (7, "客厅母版"),
        (8, "卧室母版"),
        (9, "客厅九宫格"),
    ):
        assets.append({
            "id": "图%s" % number,
            "title": title,
            "subject": title,
            "prompt": title,
        })
    return {
        "assets": assets,
        "shots": [_shot(shot_id, images, duration=5) for shot_id, images in _EYE_COLUMNS],
        "feed": _eye_feed(),
    }


class StoryboardAssetBindingTest(unittest.TestCase):
    def test_man_alias_does_not_replace_the_storyboard_union(self):
        spec = _eye_spec()
        units = packed_units(spec)
        self.assertEqual([unit["images"] for unit in units], [list(ids) for ids in _EYE_UNITS])
        for unit in units:
            self.assertNotIn("图7", unit["images"])
            self.assertNotIn("图8", unit["images"])
        self.assertIn("图9", units[3]["images"])
        self.assertIn("图9", units[4]["images"])
        self.assertIn("The man", units[1]["prompt"])
        self.assertNotEqual(units[1]["images"], ["图5"])

    def test_asset_line_binds_when_shots_are_not_placed(self):
        feed = _h3_unit(1, 1, 5, body="The man waits in the living room.")
        feed = feed.replace("duration: 5\n", "duration: 5\n资产：图1，图4\n", 1)
        units = packed_units({
            "feed": feed,
            "assets": [
                {"id": "图1", "title": "女主", "prompt": "女主"},
                {"id": "图4", "title": "药盒", "prompt": "药盒"},
                {"id": "图5", "title": "男主", "prompt": "男主"},
                {"id": "图7", "title": "客厅母版", "prompt": "客厅"},
                {"id": "图8", "title": "卧室母版", "prompt": "客厅"},
            ],
        })
        self.assertEqual(units[0]["images"], ["图1", "图4"])

    def test_overflow_keeps_storyboard_order_and_logs_the_drop(self):
        ids = ["图%s" % number for number in range(1, 11)]
        feed = _h3_unit(1, 1, 5)
        with self.assertLogs("end.seam_ledger", level="WARNING") as caught:
            units = packed_units({
                "feed": feed,
                "shots": [_shot("S01", ids, duration=5)],
            })
        self.assertEqual(units[0]["images"], ids[:8])
        logged = "\n".join(caught.output)
        self.assertIn("图9", logged)
        self.assertIn("图10", logged)
        self.assertIn("按分镜顺序", logged)

    def test_first_frame_submit_omits_images_and_keeps_the_asset_line(self):
        spec = _eye_spec()
        units = packed_units(spec)
        entries = apply_submit_params([
            _entry(units[0]["id"], "", "none"),
            _entry(units[1]["id"], units[0]["id"], "2"),
            _entry(units[2]["id"], units[1]["id"], "2"),
            _entry(units[3]["id"], units[2]["id"], "1"),
            _entry(units[4]["id"], units[3]["id"], "2"),
        ], units)
        self.assertEqual(entries[3]["video_mode"], "first_frame")
        self.assertEqual(entries[3]["first_frame_source"], "previous_tail")
        self.assertEqual(entries[3]["images"], [])
        self.assertEqual(entries[1]["images"], list(_EYE_UNITS[1]))
        spec["seam_ledger"] = {"entries": entries}
        submitted = units_for_submit(spec)
        self.assertEqual(submitted[3]["video_mode"], "first_frame")
        self.assertEqual(submitted[3]["first_frame_source"], "previous_tail")
        self.assertEqual(submitted[3]["images"], [])
        self.assertEqual(submitted[3]["video_images"], [])
        self.assertNotIn("end_image", submitted[3])
        self.assertIn("资产：图3，图9，图4，图5，图6", submitted[3]["prompt"])
        self.assertEqual(submitted[1]["video_mode"], "reference")
        self.assertEqual(submitted[1]["images"], list(_EYE_UNITS[1]))
        self.assertEqual(submitted[1]["video_images"], list(_EYE_UNITS[1]))
        _assets, built = collect_steps(spec)
        self.assertEqual(built[1]["video_mode"], "reference")
        self.assertEqual(built[1]["images"], list(_EYE_UNITS[1]))
        self.assertEqual(built[3]["video_mode"], "first_frame")
        self.assertEqual(built[3]["first_frame_source"], "previous_tail")
        self.assertEqual(built[3]["images"], [])
        self.assertNotIn("end_image", built[3])
        self.assertIn("资产：图3，图9，图4，图5，图6", built[3]["prompt"])


class SeamBriefScopeTest(unittest.TestCase):
    def test_seam_carried_text_is_only_the_packed_feed(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("剧本正文不应该进衔接简报\n", encoding="utf-8")
        (internal / "assets.md").write_text("#### 图1 · 女主（角色）\n资产正文不应该进衔接简报\n", encoding="utf-8")
        (internal / "分镜.md").write_text("## S01\n分镜正文不应该进衔接简报\n", encoding="utf-8")
        feed = "## 【封装单元 1/1】\nduration: 60\n只要这份封装稿\n"
        (internal / "feed.md").write_text(feed, encoding="utf-8")
        text = carried_text(folder, "seam")
        self.assertIn("只要这份封装稿", text)
        self.assertNotIn("不应该进衔接简报", text)
        packed = carried_text(folder, "feed")
        self.assertIn("剧本正文不应该进衔接简报", packed)
        self.assertIn("资产正文不应该进衔接简报", packed)
        self.assertIn("分镜正文不应该进衔接简报", packed)
        self.assertNotIn("只要这份封装稿", packed)

    def test_seam_first_write_brief_uses_the_packed_feed_not_script_or_board(self):
        feed = self._feed()
        state = {
            "attempt": 0,
            "units": packed_units({"feed": feed}),
            "source": "这是剧本全文，不应该出现在衔接简报里",
            "storyboard": "这是分镜全文，不应该出现在衔接简报里",
            "feed": feed,
        }
        brief = generator_messages(state)[-1]["content"]
        self.assertIn("封装单元 1/3", brief)
        self.assertIn(feed.splitlines()[0], brief)
        self.assertNotIn("这是剧本全文，不应该出现在衔接简报里", brief)
        self.assertNotIn("这是分镜全文，不应该出现在衔接简报里", brief)

    def _feed(self):
        return UnitOrderTest()._timed_feed((11, 14, 9))




class FoldSubmitParamsTest(unittest.TestCase):
    def test_fold_stamps_packed_stills_so_reference_params_match(self):
        units = [
            {"id": "封装单元 1/2", "duration": 24, "images": ["图1", "图2", "图3"]},
            {"id": "封装单元 2/2", "duration": 26, "images": ["图7", "图8", "图9"]},
        ]
        folded = fold_seam_entries([
            _entry(units[0]["id"], "", "none", images=[]),
            _entry(units[1]["id"], units[0]["id"], "2", images=[]),
        ], units)
        self.assertEqual(folded[0]["video_mode"], "reference")
        self.assertEqual(folded[0]["first_frame_source"], "none")
        self.assertEqual(folded[0]["images"], ["图1", "图2", "图3"])
        self.assertEqual(folded[1]["video_mode"], "reference")
        self.assertEqual(folded[1]["first_frame_source"], "none")
        self.assertEqual(folded[1]["images"], ["图7", "图8", "图9"])
        report = check_seam_ledger({"entries": folded}, units)
        self.assertNotIn("提交参数和连续关系不一致", report["report"])
        self.assertEqual(report["errors"], [], report["report"])

    def test_empty_official_stills_stay_empty(self):
        units = [
            {"id": "封装单元 1/1", "duration": 12, "images": []},
        ]
        folded = fold_seam_entries([
            _entry(units[0]["id"], "", "none", images=["图9"]),
        ], units)
        self.assertEqual(folded[0]["video_mode"], "reference")
        self.assertEqual(folded[0]["images"], [])

    def test_dropping_a_covered_character_still_fails_the_gate(self):
        units = [
            {"id": "封装单元 1/2", "duration": 24, "images": ["图1", "图2"]},
            {"id": "封装单元 2/2", "duration": 26, "images": ["图1", "图7", "图8", "图9"]},
        ]
        folded = fold_seam_entries([
            _entry(units[0]["id"], "", "none"),
            _entry(units[1]["id"], units[0]["id"], "2"),
        ], units)
        dropped = [dict(item) for item in folded]
        dropped[1]["images"] = ["图7", "图8", "图9"]
        report = check_seam_ledger({"entries": dropped}, units)
        self.assertFalse(report["ok"])
        self.assertIn("图1", report["report"])
        self.assertIn(units[1]["id"], report["report"])


class PackedUnitStillListsTest(unittest.TestCase):
    def test_mislabeled_packed_shots_do_not_one_to_one_cover_units(self):
        columns = [
            ("S01", 8, ["图1", "图2", "图3"]),
            ("S02", 7, ["图1", "图2", "图3"]),
            ("S03", 9, ["图1", "图2", "图16"]),
            ("S04", 10, ["图7", "图8", "图9"]),
            ("S05", 10, ["图7", "图8", "图9"]),
            ("S06", 6, ["图7", "图8", "图23"]),
        ]
        board = "\n\n".join(
            "\n".join((
                "## %s" % shot_id,
                "时长：%s" % duration,
                "资产：%s" % "，".join(images),
                "对白：无",
                "旁白：无",
            ))
            for shot_id, duration, images in columns
        )
        unit1_refs = ["图1", "图2", "图3", "图16"]
        unit2_refs = ["图7", "图8", "图9", "图23"]
        feed = "\n\n".join((
            _wan_packed_unit(1, 2, 24, unit1_refs + ["图99"], "红毯与交易"),
            _wan_packed_unit(2, 2, 26, unit2_refs + ["图99"], "宴会厅主桌"),
        ))
        spec = {
            "storyboard": board,
            "feed": feed,
            "shots": [
                {"id": "S01", "prompt": "误标成封装单元 1 的镜头"},
                {"id": "S02", "prompt": "误标成封装单元 2 的镜头"},
            ],
        }
        units = packed_units(spec)
        self.assertEqual([unit["id"] for unit in units], ["封装单元 1/2", "封装单元 2/2"])
        self.assertEqual(units[0]["images"], unit1_refs)
        self.assertEqual(units[1]["images"], unit2_refs)
        self.assertNotEqual(units[1]["images"], ["图1", "图2", "图3"])
        self.assertNotIn("图99", units[0]["images"])
        self.assertNotIn("图99", units[1]["images"])
        folded = fold_seam_entries([
            _entry(units[0]["id"], "", "none", images=[]),
            _entry(units[1]["id"], units[0]["id"], "2", images=[]),
        ], units)
        self.assertEqual(folded[1]["images"], unit2_refs)
        report = check_seam_ledger({"entries": folded}, units)
        self.assertNotIn("提交参数和连续关系不一致", report["report"])
        self.assertEqual(report["errors"], [], report["report"])

    def test_feed_reference_line_binds_when_the_board_cannot_be_grouped(self):
        board = "\n\n".join(
            "\n".join((
                "## %s" % shot_id,
                "资产：%s" % "，".join(images),
                "对白：无",
                "旁白：无",
            ))
            for shot_id, images in (
                ("S01", ["图1", "图2", "图3"]),
                ("S02", ["图1", "图2", "图3"]),
                ("S03", ["图1", "图2", "图16"]),
                ("S04", ["图7", "图8", "图9"]),
                ("S05", ["图7", "图8", "图9"]),
                ("S06", ["图7", "图8", "图23"]),
            )
        )
        unit1_refs = ["图1", "图2", "图3", "图16"]
        unit2_refs = ["图7", "图8", "图9", "图23"]
        feed = "\n\n".join((
            _wan_packed_unit(1, 2, 24, unit1_refs, "红毯与交易"),
            _wan_packed_unit(2, 2, 26, unit2_refs, "宴会厅主桌"),
        ))
        units = packed_units({
            "storyboard": board,
            "feed": feed,
            "shots": [
                {"id": "S01", "prompt": "误标成封装单元 1 的镜头"},
                {"id": "S02", "prompt": "误标成封装单元 2 的镜头"},
            ],
        })
        self.assertEqual(units[0]["images"], unit1_refs)
        self.assertEqual(units[1]["images"], unit2_refs)
        self.assertNotEqual(units[1]["images"], ["图1", "图2", "图3"])


def _wan_packed_unit(index, total, seconds, refs, body):
    return "\n".join((
        "## 【封装单元 %s/%s】" % (index, total),
        "duration: %s" % seconds,
        "总体描述:",
        body,
        "参考素材引用:",
        ", ".join(refs),
        "分镜:",
        "分镜1（00:00.000-00:08.000）： 场面。",
        "台词:",
        "无",
        "音效 / BGM:",
        "现场",
        "风格 / 情绪:",
        "平静",
        "负向清单:",
        "不要字幕、不要水印、不要复杂文字",
    ))


class SeamEmptyShotSkillTest(unittest.TestCase):
    def test_skills_require_canonical_empty_use_and_a_one_to_two_second_unit(self):
        from Agents.workers.seam import SKILL_FILE

        root = Path(__file__).resolve().parents[1]
        self.assertEqual(SKILL_FILE, "segment-splicing/generator.md")
        generator = (root / "Agents/skills" / SKILL_FILE).read_text(encoding="utf-8")
        critic = (root / "Agents/skills/segment-splicing/critic.md").read_text(encoding="utf-8")

        self.assertNotIn("情绪段落收尾 → ③", generator)
        self.assertNotIn("情绪段落收尾 -> ③", generator)

        for token in ("情绪空镜", "音效桥", "时空转场"):
            self.assertIn(token, generator)
            self.assertIn(token, critic)

        self.assertIn("empty_use", generator)
        self.assertIn("empty_use", critic)
        self.assertIn("恰好", generator)
        self.assertIn("原词", critic)
        self.assertIn("duration", generator)
        self.assertIn("duration", critic)
        self.assertIn("程序会改回 ②", generator)
        self.assertTrue(
            any(token in generator for token in ("1 或 2", "1–2", "1-2", "1 到 2")),
            generator,
        )
        self.assertTrue(
            any(token in critic for token in ("1 或 2", "1–2", "1-2", "1 到 2")),
            critic,
        )




if __name__ == "__main__":
    unittest.main()
