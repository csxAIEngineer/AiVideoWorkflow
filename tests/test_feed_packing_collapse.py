"""Packing must not collapse a written draft onto Shot 1 or onto S01.

Golden records are the unedited debug files in
``tests/fixtures/feed-packing-3db05329ecfe/`` from project 3db05329ecfe.

    python3 -m unittest tests.test_feed_packing_collapse
"""

import importlib.util
import json
import unittest
from pathlib import Path

from Agents.workers.feed import (
    inject_shot_skeleton,
    last_complete_draft,
    merge_targeted_repair,
    packing_shot_skeleton,
    run_feed_loop,
    shot_skeleton_failures,
)
from end.board_shots import apply_feed_binding, packing_membership
from end.product_names import UNIT_HEAD_LINE
from end.prompt_gate import delivery_units
from end.storyboard_contract import project_shots
from end.workflow_from_spec import collect_steps

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "feed-packing-3db05329ecfe"
VALIDATOR = ROOT / "AI" / "skills" / "manju-laoli-skill" / "short-drama-director" / "scripts" / "validate_prompt.py"
_SPEC = importlib.util.spec_from_file_location("collapse_validate_prompt", VALIDATOR)
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)
validate_h3 = _VALIDATOR.validate_h3

KNOWN = ["图1", "图2", "图3", "图4", "图6", "图8", "图9", "A1"]
STYLE = "手绘赛璐璐 · 粗描边 · 平涂色块 · 经典动画质感"
H7 = "[段4] H7 (Sx) 未按首现顺序编号：实际首现序 ['S2']（应从 (S1) 起递增；未发声角色不给 ID）"
ACCEPT = '{"verdict":"accept","issues":[]}'
PLACEHOLDER = "按这张资产图演这一镜"


def _read(name):
    return (FIX / name).read_text(encoding="utf-8")


def _units(text):
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    found = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        found.append(raw[match.start():end])
    return found


def _state():
    board = _read("分镜.md")
    shots, report = project_shots(board, KNOWN)
    if report:
        raise AssertionError(report)
    return {
        "storyboard": board,
        "source": board,
        "script": "画风：%s\n" % STYLE,
        "shots": shots,
        "locks": {"model": "h3", "material_mode": "M1", "track": "1"},
    }


def _dp_state():
    # The ``dp-*`` fixtures encode the DP packer (c4f50d8/b8e8cb1) at the 15s
    # Seedance 2.0 clip limit: groups [4,5,4],[3,3,4],[4,4,4],[4,4,4,2]. h3 now
    # packs toward 30s (cc9f410), so these collapse/repair cases use the 15s lock
    # to keep the four-unit golden scenario the fixtures record.
    state = _state()
    state["locks"] = {"model": "2.0", "material_mode": "M1", "track": "1"}
    return state


def _bind_spec(feed_name):
    state = _state()
    assets = [{"id": "图99", "title": "女主", "subject_kind": "character", "prompt": "女主"}]
    for asset_id in KNOWN:
        assets.append({"id": asset_id, "title": asset_id, "prompt": asset_id})
    return {
        "feed": _read(feed_name),
        "storyboard": state["storyboard"],
        "assets": assets,
        "shots": [dict(shot) for shot in state["shots"]],
    }


class StoryboardBindingTest(unittest.TestCase):
    def test_packed_units_do_not_replace_the_thirteen_storyboard_prompts(self):
        state = _state()
        prompts = [shot["prompt"] for shot in state["shots"]]
        images = [list(shot["images"]) for shot in state["shots"]]
        self.assertEqual(len(prompts), 13)
        self.assertIn("夜晚卧室，女主坐在床边", prompts[0])
        # h3 now packs toward 30s with the DP packer (cc9f410, c4f50d8), so the
        # 13-shot board fills two units instead of the old greedy four.
        self.assertEqual(
            packing_membership(state["shots"]),
            [
                ["S01", "S02", "S03", "S04", "S05", "S06"],
                ["S07", "S08", "S09", "S10", "S11", "S12", "S13"],
            ],
        )
        for name in ("feed-attempt-3-plan.txt", "feed.md"):
            spec = _bind_spec(name)
            bound = apply_feed_binding(spec)
            units = delivery_units(bound["feed"])
            self.assertEqual(len(bound["shots"]), 13, name)
            self.assertEqual(len(units), 4, name)
            self.assertIn("封装单元", bound["feed"])
            for index, shot in enumerate(bound["shots"]):
                self.assertIn(prompts[index].split("\n", 1)[0][:12], shot["prompt"], name)
                self.assertFalse(shot["prompt"].lstrip().startswith("## 【封装单元"), name)
                self.assertNotIn(shot["prompt"].strip(), [unit.strip() for unit in units], name)
                self.assertEqual(shot["images"], images[index], name)
            _assets, submitted = collect_steps(bound)
            self.assertEqual(len(submitted), 4, name)
            self.assertIn("封装单元", submitted[0]["prompt"])
            self.assertIn("subject_definitions", submitted[0]["prompt"])
            self.assertNotEqual(submitted[0]["prompt"].strip(), bound["shots"][0]["prompt"].strip())

    def test_a_placeholder_canvas_still_receives_the_packed_unit(self):
        unit = "\n".join((
            "## 【封装单元 1/1】",
            "subject_definitions:",
            "<Subject 1> (Female) a woman in the room.",
            "detailed_description:",
            "[Shot 1] She waits.",
            "overall_soundscape:",
            "room",
            "non_diegetic_music:",
            "N/A",
        ))
        spec = {
            "feed": unit,
            "assets": [{"id": "图1", "title": "女主", "prompt": "女主"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": PLACEHOLDER, "images": ["图9"]}],
        }
        bound = apply_feed_binding(spec)
        self.assertIn("封装单元", bound["shots"][0]["prompt"])
        self.assertNotEqual(bound["shots"][0]["prompt"], PLACEHOLDER)


class SkeletonCollapseTest(unittest.TestCase):
    def test_a_headingless_blob_is_not_locked_under_shot_1(self):
        state = _state()
        raw = _read("feed-attempt-1-extract-3.txt")
        self.assertNotIn("[Shot", raw)
        injected = inject_shot_skeleton(raw, packing_shot_skeleton(state))
        self.assertNotIn("[Shot 1]", injected)
        self.assertNotRegex(injected, r"\[Shot 1\][^\n]*A tense ambient drone")
        self.assertIn("A tense ambient drone", injected)
        errors = shot_skeleton_failures(injected, state)
        self.assertTrue(errors, injected[:200])
        joined = "\n".join(errors)
        self.assertIn("镜号锁", joined)
        self.assertTrue(
            any(mark in joined for mark in ("没有 [Shot", "没有正文", "subject_definitions", "overall_soundscape")),
            joined,
        )

    def test_collapsed_records_fail_body_subject_and_soundscape(self):
        # dp-* fixtures match the DP packer (c4f50d8/b8e8cb1) at the 15s lock.
        state = _dp_state()
        for name in ("dp-collapsed-plan.txt", "dp-collapsed-late.txt", "dp-collapsed-early.txt"):
            errors = shot_skeleton_failures(_read(name), state)
            joined = "\n".join(errors)
            self.assertIn("没有正文", joined, name)
            self.assertIn("subject_definitions", joined, name)
            self.assertIn("<Subject", joined, name)
            self.assertIn("overall_soundscape", joined, name)
            self.assertIn("镜号锁", joined, name)

    def test_the_complete_draft_passes_body_and_subject_gates_and_unit_4_fails_h7(self):
        # dp-complete-* encode the DP packer (c4f50d8/b8e8cb1) at the 15s lock;
        # the slogan unit (4) still carries the real narrator (S2) H7 error.
        state = _dp_state()
        for name in ("dp-complete-plan.txt", "dp-complete-extract.txt"):
            errors = shot_skeleton_failures(_read(name), state)
            self.assertEqual(errors, [], name)
        units = _units(_read("dp-complete-plan.txt"))
        self.assertEqual(len(units), 4)
        for index, block in enumerate(units, 1):
            issues, _warns, _stats = validate_h3(block)
            h7 = [item for item in issues if "H7" in item]
            if index == 4:
                self.assertEqual(len(h7), 1, issues)
                self.assertIn("S2", h7[0])
                self.assertEqual([item for item in issues if "H7" not in item], [])
            else:
                self.assertEqual(h7, [], issues)
                self.assertEqual(issues, [])


class TargetedRepairTest(unittest.TestCase):
    def test_a_named_h7_repair_keeps_the_complete_units(self):
        state = _state()
        previous = _read("feed-attempt-3-plan.txt")
        report = {"errors": [H7], "warnings": []}
        before = _units(previous)
        self.assertEqual(len(before), 4)
        for incoming_name in ("feed-attempt-1-extract-3.txt", "feed-attempt-1-plan-3.txt"):
            merged = merge_targeted_repair(previous, _read(incoming_name), report, state)
            after = _units(merged)
            self.assertEqual(len(after), 4, incoming_name)
            for index in range(3):
                self.assertEqual(after[index], before[index], incoming_name)
            self.assertEqual(after[3], before[3], incoming_name)
            self.assertNotIn("A shimmering chime plays as the camera pans", merged)
            self.assertNotIn("[Shot 1] A tense ambient drone", merged)
            self.assertIn("Soft plastic cap-twisting friction", merged)
            self.assertIn("narrator (S2)", merged)

    def test_a_noncollapsed_patch_replaces_only_the_named_unit(self):
        state = _state()
        previous = _read("feed-attempt-3-plan.txt")
        incoming = previous.replace("narrator (S2)", "narrator (S1)", 1)
        self.assertNotEqual(incoming, previous)
        report = {"errors": [H7], "warnings": []}
        merged = merge_targeted_repair(previous, incoming, report, state)
        before = _units(previous)
        after = _units(merged)
        patched = _units(incoming)
        for index in range(3):
            self.assertEqual(after[index], before[index])
        self.assertEqual(after[3], patched[3])
        self.assertIn("narrator (S1)", after[3])
        self.assertNotIn("narrator (S2)", after[3])

    def test_history_skips_a_later_collapsed_draft(self):
        # dp-* fixtures match the DP packer (c4f50d8/b8e8cb1) at the 15s lock.
        state = _dp_state()
        complete = _read("dp-complete-plan.txt")
        collapsed = _read("dp-collapsed-plan.txt")
        state.update({
            "draft": collapsed,
            "history": [
                {"attempt": 1, "draft": complete},
                {"attempt": 2, "draft": collapsed},
            ],
        })
        self.assertEqual(last_complete_draft(state), complete)

    def test_generate_folds_the_repair_onto_the_last_complete_draft(self):
        # complete uses the DP-packed dp fixture (c4f50d8/b8e8cb1, 15s lock); the
        # collapsed extract has no cuts/durations, so the shared original is reused
        # read-only as the grouping-independent collapsed incoming draft.
        state = _dp_state()
        complete = _read("dp-complete-plan.txt")
        collapsed = _read("feed-attempt-1-extract-3.txt")
        calls = {"n": 0}

        def complete_fn(role, messages):
            if role == "critic":
                return ACCEPT
            if role == "director":
                calls["n"] += 1
                body = complete if calls["n"] == 1 else collapsed
                return json.dumps({"feed": body}, ensure_ascii=False)
            return json.dumps({"text": "节点"}, ensure_ascii=False)

        def checker(text, _state):
            errors = []
            if "narrator (S2)" in text:
                errors.append(H7)
            if "A shimmering chime plays as the camera pans" in text:
                errors.append("镜号锁：封装单元 2/4 的 [Shot 2] 没有正文。")
            return {
                "ok": not errors,
                "errors": errors,
                "warnings": [],
                "fatal": "",
                "needs_human": False,
            }

        result = run_feed_loop(
            state["storyboard"],
            shots=state["shots"],
            script=state["script"],
            locks=state["locks"],
            max_attempts=2,
            complete=complete_fn,
            checker=checker,
        )
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("A shimmering chime plays as the camera pans", result["feed"])
        self.assertIn("Soft plastic cap-twisting friction", result["feed"])
        self.assertIn("narrator (S2)", result["feed"])
        stored = _units(_read("dp-complete-plan.txt"))
        produced = _units(result["feed"])
        self.assertEqual(len(produced), 4)
        for index in range(3):
            self.assertEqual(produced[index], stored[index])


class WanCompletenessTest(unittest.TestCase):
    def test_ws4_wan_draft_is_complete_without_subject_tags(self):
        from Agents.workers.feed.repair import draft_is_complete, repair_unit_collapsed

        unit = "\n".join((
            "## 【封装单元 1/1】",
            "duration: 10",
            "总体描述:",
            "城市夜景。",
            "分镜:",
            "分镜1（00:00.000-00:10.000）：",
            "他推门走进房间。",
            "台词:",
            '甲说："好。"',
            "风格 / 情绪:",
            "画面风格：冷白",
            "负向清单:",
            "不要字幕、不要水印、不要复杂文字",
        )) + "\n"
        state = {
            "storyboard": "## S01\n时长：10",
            "shots": [{"duration": 10}],
            "locks": {"model": "h3"},
        }
        self.assertTrue(draft_is_complete(unit, state))
        self.assertFalse(repair_unit_collapsed(unit))
        collapsed = unit.replace("他推门走进房间。", "")
        self.assertTrue(repair_unit_collapsed(collapsed))
        self.assertFalse(draft_is_complete(collapsed, state))


if __name__ == "__main__":
    unittest.main()
