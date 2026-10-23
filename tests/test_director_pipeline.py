"""Locks, stage gates, machine checks, and the public delivery package."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.delivery import list_deliverables, publish
from end.gates import check_feed, check_prompt
from end.locks import (
    SEAM_OPTIONS,
    TRACK_OPTIONS,
    absorb_model_output,
    apply_selection,
    blank_locks,
    lock_brief,
    pending_question,
    prepare_locks,
    read_locks,
    style_question,
    write_locks,
)
from end.server import answer_gate, run_lock_turn, run_project
from end.stage_runner import (
    _produce,
    apply_structured_assets,
    asset_markdown,
    assets_from_markdown,
    detect_only,
    execute,
    keep_missing_asset_blocks,
    keep_richer_board,
    patch_h3_voiceover,
    previs_check_text,
    _INSTRUCTION,
    mount_header,
    next_pending,
    patch_asset_phrases,
    stages_for,
)

ROOT = Path(__file__).resolve().parents[1]


def _base_locks(**extra):
    locks = blank_locks()
    locks.update(
        {
            "track": "1",
            "source": "1",
            "model": "h3",
            "aspect": "16:9",
            "delivery": "3",
            "combat": "R1",
            "material_mode": "M1",
        }
    )
    locks.update(extra)
    return locks


def make_project(project_id, **extra):
    created = "2026-01-01T00:00:00+08:00"
    project = {
        "id": project_id,
        "brief": extra.pop("brief", "雨夜"),
        "messages": [{"role": "user", "content": "雨夜的方向"}],
        "title": "雨夜",
        "status": extra.pop("status", "writing"),
        "stage": "立项锁定",
        "script": "",
        "storyboard": "",
        "image_prompts": "",
        "error": None,
        "events": [],
        "tasks": [],
        "progress": extra.pop(
            "progress",
            {"percent": 0, "phase": "writing", "message": "立项锁定", "lock_machine": True},
        ),
        "cloud_log": "",
        "prompt_id": "",
        "video_file": "",
        "model": "deepseek-v4-pro",
        "template": "short-drama-director",
        "created_at": created,
        "started_at": created,
        "updated_at": created,
        "finished_at": None,
    }
    project.update(extra)
    write_project(project)
    return project


class LockMachineTest(unittest.TestCase):
    def test_genre_words_do_not_choose_a_lock(self):
        locks = prepare_locks(blank_locks(), "海外剧 玄幻打斗 王家卫")
        self.assertEqual(locks["track"], "")
        self.assertEqual(locks["source"], "")
        self.assertEqual(locks["model"], "")
        self.assertEqual(locks["combat"], "")
        self.assertEqual(locks["style_look"], "")
        question = pending_question(locks)
        self.assertEqual(question["id"], "track")
        self.assertEqual(tuple(question["options"]), TRACK_OPTIONS)

    def test_duration_decides_whether_seam_is_asked(self):
        short = prepare_locks(_base_locks(), "时长：10秒")
        self.assertEqual(short["duration_seconds"], 10)
        self.assertEqual(short["seams"], [])
        self.assertEqual(pending_question(short)["id"], "style")

        edge = prepare_locks(_base_locks(), "15秒")
        self.assertEqual(pending_question(edge)["id"], "style")

        long = prepare_locks(_base_locks(), "16秒")
        self.assertIsNone(long["seams"])
        seam = pending_question(long)
        self.assertEqual(seam["id"], "seam")
        self.assertEqual(seam["kind"], "multi")
        self.assertEqual(tuple(seam["options"]), SEAM_OPTIONS)

        seedance = prepare_locks(_base_locks(model="2.5", reference_mode="① 全能参考"), "30秒")
        self.assertEqual(pending_question(seedance)["id"], "style")
        over = prepare_locks(_base_locks(model="2.5", reference_mode="① 全能参考"), "31秒")
        self.assertEqual(pending_question(over)["id"], "seam")

        missing = prepare_locks(_base_locks(), "雨夜")
        self.assertEqual(pending_question(missing)["id"], "duration")

    def test_style_card_includes_the_recommendation_and_hides_the_name(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        question = style_question([{"title": "王家卫", "look": "王家卫，霓虹暖黄，狭窄空间"}])
        self.assertIn("按你的推荐走", question["options"])
        updated, error = apply_selection(locks, question, "按你的推荐走", cards=question["cards"])
        self.assertEqual(error, "")
        self.assertEqual(updated["style_look"], "霓虹暖黄 狭窄空间")
        self.assertNotIn("王家卫", updated["style"])
        self.assertNotIn("王家卫", updated["style_look"])
        self.assertEqual(updated["style_source"], "王家卫")
        self.assertNotIn("王家卫", lock_brief(updated))
        self.assertIn("霓虹暖黄", lock_brief(updated))

    def test_model_payload_cannot_rewrite_locks(self):
        folder = Path(tempfile.mkdtemp())
        locks = _base_locks()
        write_locks(folder, locks)
        absorb_model_output(folder, {"locks": {"track": "2", "model": "2.0"}})
        saved = read_locks(folder)
        self.assertEqual(saved["track"], "1")
        self.assertEqual(saved["model"], "h3")


class DirectorDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_first_question_is_the_track_options(self):
        make_project("p-lock", brief="海外剧 玄幻打斗", status="drilling")
        run_lock_turn("p-lock")
        saved = read_project("p-lock")
        self.assertEqual(read_locks(project_dir("p-lock"))["track"], "")
        message = saved["messages"][-1]
        self.assertEqual(message["options"], list(TRACK_OPTIONS))
        self.assertEqual(message["choice_kind"], "single")
        self.assertEqual(saved["progress"]["phase"], "lock_wait")

    def test_stages_stop_at_each_gate_and_cannot_skip(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光，写实肤色"
        locks["style"] = locks["style_look"]
        locks["seams"] = []
        self.assertEqual(stages_for(locks, ""), ["script", "asset", "storyboard", "feed"])
        self.assertEqual(next_pending(stages_for(locks, ""), []), "script")
        self.assertEqual(stages_for(locks, detect_only("只要打戏")), ["script", "asset", "storyboard"])
        self.assertNotIn("feed", stages_for(locks, detect_only("只要打戏")))

        make_project(
            "p-gate",
            progress={"percent": 8, "phase": "writing", "message": "剧本", "lock_machine": True, "pipeline": {"current": "feed", "accepted": [], "action": "write"}},
        )
        write_locks(project_dir("p-gate"), locks)
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            if "只写剧本" in brief:
                return {"title": "雨夜", "script": "她在雨里等车。"}
            if "只写资产" in brief:
                return {"image_prompts": "#### 图1 · 女（角色）\n同上"}
            raise AssertionError("跳到了后面的阶段")

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-gate")
            self.assertEqual(len(calls), 1)
            self.assertIn("只写剧本", calls[0])
            saved = read_project("p-gate")
            self.assertEqual(saved["status"], "gate")
            self.assertEqual(saved["progress"]["phase"], "gate_script")
            self.assertIn("继续", saved["messages"][-1]["options"])
            with patch("end.server.submit_agent_task"):
                answer_gate(read_project("p-gate"), "继续")
            run_project("p-gate")
            self.assertEqual(len(calls), 4)
            self.assertIn("只写资产", calls[1])
            self.assertIn("机检没过", calls[2])
            saved = read_project("p-gate")
            self.assertEqual(saved["progress"]["phase"], "gate_asset")
            self.assertFalse(saved["progress"]["pipeline"]["passed"])
            self.assertIn("已按检查结果自动改了3次", saved["messages"][-1]["content"])
            self.assertNotIn("不会自动改稿", saved["messages"][-1]["content"])
            run_project("p-gate")
            self.assertEqual(len(calls), 4)

    def test_failed_continue_rewrites_only_the_current_stage(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光"
        locks["style"] = "自然光"
        locks["seams"] = []
        make_project(
            "p-rewrite",
            progress={
                "percent": 40,
                "phase": "gate_asset",
                "message": "资产门",
                "lock_machine": True,
                "pipeline": {"current": "asset", "accepted": ["script"], "action": "wait", "passed": False, "only": ""},
            },
            status="gate",
        )
        write_locks(project_dir("p-rewrite"), locks)
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图1 · 女（角色）\n同上"}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"), patch("end.server.submit_agent_task"):
            answer_gate(read_project("p-rewrite"), "继续")
            run_project("p-rewrite")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("只写资产" in brief for brief in calls))
        self.assertNotIn("只写分镜", calls[0])
        self.assertIn("机检没过", calls[1])
        saved = read_project("p-rewrite")
        self.assertEqual(saved["progress"]["pipeline"]["current"], "asset")
        self.assertFalse(saved["progress"]["pipeline"]["passed"])

    def test_script_polish_asks_how_to_change_before_rewriting(self):
        make_project(
            "p-polish",
            status="gate",
            script="她在雨里等车。",
            progress={
                "percent": 20,
                "phase": "gate_script",
                "message": "剧本门",
                "lock_machine": True,
                "pipeline": {
                    "current": "script",
                    "accepted": [],
                    "action": "wait",
                    "passed": True,
                    "report": "",
                },
                "question": {"id": "script", "reply": "剧本门", "options": ["继续", "精修"]},
            },
        )
        with patch("end.server.submit_agent_task") as submit:
            view = answer_gate(read_project("p-polish"), "精修")
        submit.assert_not_called()
        self.assertEqual(view["status"], "gate")
        self.assertIn("更贴我写的故事", view["messages"][-1]["options"])
        self.assertIn("其他", view["messages"][-1]["options"])
        with patch("end.server.submit_agent_task") as submit:
            answer_gate(read_project("p-polish"), "产品信息写清楚")
        submit.assert_called_once()
        saved = read_project("p-polish")
        self.assertEqual(saved["progress"]["pipeline"]["action"], "rewrite")
        self.assertEqual(saved["progress"]["pipeline"]["revise_note"], "产品信息写清楚")
        self.assertEqual(saved["status"], "writing")


class MachineAndMountTest(unittest.TestCase):
    def test_revise_note_is_given_with_the_current_draft(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("旧稿里有女主和胶囊。\n", encoding="utf-8")
        captured = {}

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            captured["brief"] = brief
            return {"script": "新稿"}

        _produce(
            {
                "brief": "雨夜",
                "template": "short-drama-director",
                "model": "deepseek-v4-pro",
                "progress": {"pipeline": {"revise_note": "产品信息写清楚"}},
            },
            folder,
            "script",
            _base_locks(),
            "",
            writer,
            lambda *args, **kwargs: "pack",
        )
        self.assertIn("产品信息写清楚", captured["brief"])
        self.assertIn("旧稿里有女主和胶囊", captured["brief"])

    def test_multi_segment_validation_uses_by_segment(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "feed.md"
        path.write_text("段1\n段2\n", encoding="utf-8")
        with patch("end.gates.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="PASSED")
            check_prompt(path, "h3", "1")
        args = run.call_args.args[0]
        self.assertIn("--by-segment", args)
        self.assertIn("--model", args)
        self.assertIn("h3", args)
        path.write_text("段1\n", encoding="utf-8")
        with patch("end.gates.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="PASSED")
            check_prompt(path, "2.5", "2")
        self.assertNotIn("--by-segment", run.call_args.args[0])

    def test_named_combat_loads_only_that_engine(self):
        from AI.skills.registry import load_skill_pack

        locks = _base_locks(combat="R1")
        header = mount_header("storyboard", locks, "两人打斗", "combat")
        self.assertIn("只加载：sd-real-engine/", header)
        self.assertNotIn("sd-storyboard-engine/", header)
        pack = load_skill_pack(context=header, stage="P3")
        self.assertIn("# sd-real-engine/", pack)
        self.assertNotIn("# sd-storyboard-engine/", pack)
        self.assertNotIn("# sd-combat-engine/", pack)
        self.assertNotIn("# sd-xuanhuan-engine/", pack)

        feed = mount_header("feed", _base_locks(model="h3"), "", "feed")
        feed_pack = load_skill_pack(context=feed, stage="P4")
        self.assertIn("# sd-feed-engine/", feed_pack)
        self.assertIn("# short-drama-director/references/h3-adapter.md", feed_pack)
        self.assertNotIn("# sd-storyboard-engine/", feed_pack)
        self.assertNotIn("# short-drama-director/SKILL.md", feed_pack)

    def test_asset_failure_rewrites_until_the_limit(self):
        folder = Path(tempfile.mkdtemp())
        locks = _base_locks()
        project = {"template": "short-drama-director", "model": "deepseek-v4-pro", "progress": {"pipeline": {"action": "write", "current": "asset", "accepted": ["script"], "only": ""}}}
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图1 · 女（角色）\n同上"}

        outcome = execute(project, folder, locks, writer, lambda *args, **kwargs: "pack")
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        self.assertEqual(outcome["progress"]["pipeline"]["action"], "wait")
        self.assertEqual(len(calls), 3)
        self.assertIn("机检没过", calls[1])
        again = execute(
            {"template": "short-drama-director", "progress": outcome["progress"]},
            folder,
            locks,
            writer,
            lambda *args, **kwargs: "pack",
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(again["progress"]["pipeline"]["action"], "wait")

    def test_asset_repair_keeps_blocks_the_model_left_out(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text(
            "#### 图1 · 胶囊（道具）\n旧的药盒\n\n#### 图2 · 客厅（场景母版）\n旧的客厅\n",
            encoding="utf-8",
        )
        locks = _base_locks()
        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "brief": "药盒广告",
            "progress": {
                "pipeline": {
                    "action": "rewrite",
                    "current": "asset",
                    "accepted": ["script"],
                    "only": "",
                    "repair": "[-] FAIL: A6 图1 道具未禁手持",
                    "passed": False,
                }
            },
        }

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            return {"image_prompts": "#### 图1 · 胶囊（道具）\n禁止手持，16:9，道具设定图"}

        outcome = execute(project, folder, locks, writer, lambda *args, **kwargs: "pack")
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("图2 · 客厅", stored)
        self.assertIn("禁止手持", stored)
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        merged = keep_missing_asset_blocks(
            "#### 图1 · 甲\nA\n\n#### 图2 · 乙\nB\n",
            "#### 图1 · 甲\n新\n",
        )
        self.assertIn("图2 · 乙", merged)
        self.assertIn("新", merged)
        moved = asset_markdown({"image_prompts": "", "script": "#### 图1 · 甲\n新\n\n#### 图2 · 乙\nB\n"})
        self.assertIn("图2 · 乙", moved)
        folded = apply_structured_assets(
            "#### 图1 · 胶囊道具\n旧药盒\n\n#### 图2 · 客厅场景母版\n旧客厅\n",
            [{"title": "胶囊道具", "prompt": "禁止手持。16:9。道具设定图。"}],
        )
        self.assertIn("禁止手持", folded)
        self.assertIn("旧客厅", folded)
        kept = apply_structured_assets(
            "#### 图1 · 胶囊道具设定图\n道具设定图。16:9。正面视图。45° 侧视图。顶部俯视图。关键细节特写。禁止手持。\n",
            [{"title": "图1 · 胶囊道具设定图", "prompt": "短提示，没有禁手持。"}],
        )
        self.assertIn("禁止手持", kept)
        self.assertNotIn("短提示", kept)
        voiced = patch_asset_phrases("#### 图4 · 女主角色四格纯图板\n全图共 4 格。音色速览：soft\n")
        self.assertIn("≤8 词音色速览", voiced)
        rebuilt = assets_from_markdown(
            "#### 图1 · 胶囊道具设定图\n甲\n\n#### 图2 · 客厅场景母版\n乙\n",
            [{"id": "图1", "references": ["U1"], "title": "旧"}],
        )
        self.assertEqual([item["id"] for item in rebuilt], ["图1", "图2"])
        self.assertEqual(rebuilt[0]["references"], ["U1"])

    def test_feed_and_previs_failures_stop_without_a_rewrite(self):
        folder = Path(tempfile.mkdtemp())
        locks = _base_locks()
        calls = []

        def feed_writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"feed": "只有一段文字"}

        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "progress": {"pipeline": {"action": "write", "current": "feed", "accepted": ["script", "asset", "storyboard"], "only": ""}},
        }
        outcome = execute(project, folder, locks, feed_writer, lambda *args, **kwargs: "pack")
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        execute({"template": "short-drama-director", "progress": outcome["progress"]}, folder, locks, feed_writer, lambda *args, **kwargs: "pack")
        self.assertEqual(len(calls), 3)
        self.assertIn("已按检查结果自动改了", outcome["message"]["content"])
        self.assertNotIn("不会自动改稿", outcome["message"]["content"])

        def board_writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"storyboard": "她进门。", "previs": "没有段"}

        board = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "progress": {"pipeline": {"action": "write", "current": "storyboard", "accepted": ["script", "asset"], "only": ""}},
        }
        stopped = execute(board, folder, locks, board_writer, lambda *args, **kwargs: "pack")
        self.assertFalse(stopped["progress"]["pipeline"]["passed"])
        self.assertEqual(stopped["progress"]["phase"], "gate_storyboard")
        self.assertFalse(any(path.name.endswith("-预演故事板.md") for path in folder.iterdir()))
        self.assertEqual(len(calls), 6)

    def test_storyboard_repair_keeps_panels_and_asks_for_real_grids(self):
        rich = "\n".join((
            "## 段 1",
            "**本段要验什么**：她站在门内",
            "**风险提示**：左右别换",
            "### 故事板提示词（1 格）",
            "第 1 格：她站在门内，身体朝门，手抬在门把前，镜头在侧面中景。",
            "看图先看这三点：门、人、手",
            "预演故事板：段1~段1 ✅",
        ))
        summary = "段1验证客厅。段2验证门口。"
        self.assertEqual(keep_richer_board(rich, summary), rich)
        self.assertIn("第 1 格", keep_richer_board(summary, rich))
        checked = previs_check_text(summary, "段 1\n" + rich, "M2")
        self.assertIn("第 1 格", checked)
        self.assertNotIn("验证客厅", checked)
        self.assertIn("第 1 格：", _INSTRUCTION["storyboard"])
        self.assertIn("（1 格）", _INSTRUCTION["storyboard"])
        self.assertIn("六问", _INSTRUCTION["storyboard"])

        folder = Path(tempfile.mkdtemp())
        locks = _base_locks()
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            if len(calls) == 1:
                return {"storyboard": "段1她进门。", "previs": rich}
            return {"storyboard": summary, "previs": summary}

        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "progress": {"pipeline": {"action": "write", "current": "storyboard", "accepted": ["script", "asset"], "only": ""}},
        }
        stopped = execute(project, folder, locks, writer, lambda *args, **kwargs: "pack")
        self.assertFalse(stopped["progress"]["pipeline"]["passed"])
        self.assertEqual(len(calls), 3)
        self.assertIn("第 1 格", calls[1])
        self.assertNotIn("已有图块必须全部保留", calls[1])
        stored = (folder / "_内部_" / "previs.md").read_text(encoding="utf-8")
        self.assertIn("第 1 格", stored)
        self.assertNotIn("验证客厅", stored)

    def test_h3_feed_is_not_rejected_for_missing_seven_section_units(self):
        folder = Path(tempfile.mkdtemp())
        body = "\n".join((
            "## 【投喂单元 1/1】",
            "duration 15s",
            "subject_definitions:",
            "<Subject 1> is a woman standing indoors.",
            "summary:",
            "She waits by the door.",
            "retention_analysis:",
            "<Subject 1>: fully_preserved for the woman.",
            "detailed_description:",
            "[Shot 1] She stands inside. A narrator (S1) says in an off-screen voiceover: <d>[Chinese] 留下。</d>",
            "overall_soundscape:",
            "Room tone.",
            "non_diegetic_music:",
            "none.",
        ))
        path = folder / "feed.md"
        path.write_text(body, encoding="utf-8")
        code, report = check_feed(path, "h3", "M2", "1")
        self.assertNotIn("未找到投喂单元", report)
        self.assertIn("H9", report)
        self.assertNotEqual(code, 0)
        patched = patch_h3_voiceover(body)
        self.assertIn("lips remain completely closed", patched)
        path.write_text(patched, encoding="utf-8")
        again, again_report = check_feed(path, "h3", "M2", "1")
        self.assertNotIn("未找到投喂单元", again_report)
        self.assertNotIn("H9 画外声缺闭口证据", again_report)
        self.assertEqual(again, 0)


class DeliveryTest(unittest.TestCase):
    def test_public_package_is_at_most_three_and_skips_internal(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("内部剧本不能进交付包", encoding="utf-8")
        locks = _base_locks(delivery="3", material_mode="M1")
        names = publish(folder, locks, "雨夜", "资产提示", "分镜提示", "素材形态：M1\n\n## 段1\n故事板提示词（1 格）\n第 1 格：人在门口\n")
        self.assertLessEqual(len(names), 3)
        self.assertEqual(names, ["project.md", "board.html", "雨夜-预演故事板.md"])
        self.assertEqual(list_deliverables(folder), names)
        self.assertTrue(all("_内部_" not in name for name in names))
        text = (folder / "project.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("素材形态：M1"))
        self.assertIn("资产提示", text)
        self.assertNotIn("内部剧本不能进交付包", text)

        board = folder / "board.html"
        locks["delivery"] = "2"
        markdown_only = publish(folder, locks, "雨夜", "资产提示", "分镜提示", "")
        self.assertEqual(markdown_only, ["project.md"])
        self.assertFalse(board.is_file())
        self.assertNotIn("_内部_", "".join(list_deliverables(folder)))


class PageTest(unittest.TestCase):
    def test_page_stops_on_gates_and_renders_seam_and_style(self):
        app = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "front" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('["立项锁定", "赛道分流", "剧本", "资产锁定", "分镜", "提示词封装", "独立质检", "离线看板"]', app)
        self.assertIn('kind === "multi"', app)
        self.assertIn('type = "checkbox"', app)
        self.assertIn('kind === "cards"', app)
        self.assertIn("按你的推荐走", app)
        self.assertIn('current.status === "gate"', app)
        self.assertIn('sendText("继续")', app)
        self.assertIn("seam-option", css)
        self.assertIn("style-card", css)


if __name__ == "__main__":
    unittest.main()
