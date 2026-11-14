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
    drop_packing,
    invalid_scene_relation_error,
    map_submit_params,
    normalize_entry_ids,
    normalize_scene_relation,
    packed_units,
    seam_blocks_render,
    strip_seam,
    unit_id_from_text,
    write_unit_fields,
)
from end.stage_runner import (
    _spec_patch,
    accepted_done,
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
        "end_size": "" if lock == "none" else "中景",
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
        self.assertEqual(by_id["S04"]["video_mode"], "text")
        self.assertEqual(by_id["S04"]["images"], [])

    def test_mapping_choices(self):
        shot = _shot("S02", ["图1"])
        continued = map_submit_params(_entry("S02", "S01", "1", anchor_end=True, end_image="图9"), shot)
        self.assertEqual(continued["video_mode"], "first_last_frame")
        self.assertEqual(continued["first_frame_source"], "previous_tail")
        self.assertEqual(continued["end_image"], "图9")
        self.assertEqual(continued["images"], [])
        redrawn = map_submit_params(
            _entry("S02", "S01", "1", redraw_still=True, designed_still="图缝", still_prompt="可截的中景"),
            shot,
        )
        self.assertEqual(redrawn["first_frame_source"], "图缝")
        self.assertEqual(redrawn["images"], [])
        hard = map_submit_params(_entry("S03", "S02", "2"), _shot("S03", []))
        self.assertEqual(hard["video_mode"], "text")
        self.assertEqual(hard["first_frame_source"], "none")
        opening = map_submit_params(
            _entry("S01", "", "none", lock_t0=True, designed_still="图0", still_prompt="锁住第一帧"),
            _shot("S01", ["图1"]),
        )
        self.assertEqual(opening["video_mode"], "first_frame")
        self.assertEqual(opening["first_frame_source"], "图0")
        self.assertEqual(opening["images"], [])

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
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", end_size="全景", start_size="中景"),
        ], shots)
        report = check_seam_ledger({"entries": loose_size}, shots)
        self.assertIn("同档", report["report"])

        bare = apply_submit_params([
            _entry("S01", "", "none"),
            _entry("S02", "S01", "1", tail_croppable=False, crop_plan=""),
        ], shots)
        report = check_seam_ledger({"entries": bare}, shots)
        self.assertIn("可截", report["report"])

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
        self.assertEqual(group_shot_durations([5, 5, 6]), [[5, 5], [6]])
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
        self.assertEqual([item["video_mode"] for item in written], ["text"] * 4)
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


if __name__ == "__main__":
    unittest.main()
