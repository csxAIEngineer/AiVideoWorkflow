"""Locks, stage gates, machine checks, and the public delivery package."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.delivery import list_deliverables, publish
from end.gates import check_feed, check_prompt, feed_uses_h3
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
from end.server import answer_gate, answer_lock, run_lock_turn, run_project
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
    render_feed_text,
    previs_check_text,
    _INSTRUCTION,
    mount_header,
    next_pending,
    patch_asset_phrases,
    stages_for,
)

ROOT = Path(__file__).resolve().parents[1]


def _voice_block():
    return "\n".join((
        "#### 图3 · 黑鳞怪物设定板",
        "16:9",
        "全图共 4 格",
        "五官 10 项锁定",
        "≤8 词",
        "fifth panel",
        "different face",
    ))


def _passing_voice():
    return _voice_block() + "\n声纹"


def _mode_block():
    return "\n".join((
        "#### 图5 · 客厅九宫格机位调度图",
        "16:9",
        "【场景定位】客厅",
        "【九宫格内容分配】逐格",
        "【参考场景锁定模式】模式B，按子区域",
        "全图共 9 格",
        "格内编号 1~9，图外对照表",
    ))


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
        self.assertEqual(locks["model"], "h3")
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

    def test_empty_video_model_is_wan_and_the_next_question_is_aspect(self):
        chosen = blank_locks()
        chosen["track"] = "1"
        chosen["source"] = "1"
        locks = prepare_locks(chosen, "雨夜")
        self.assertEqual(locks["model"], "h3")
        self.assertEqual(locks["reference_mode"], "")
        question = pending_question(locks)
        self.assertEqual(question["id"], "aspect")
        self.assertNotIn("Seedance 2.5", question["options"])
        brief = lock_brief(locks)
        self.assertIn("出镜：万相 3.0", brief)
        self.assertNotIn("模型：h3", brief)
        self.assertIn("模型：h3", mount_header("script", locks, "", ""))
        kept = prepare_locks(_base_locks(model="2.0"), "12秒")
        self.assertEqual(kept["model"], "2.0")
        self.assertIn("模型：2.0", lock_brief(kept))
        self.assertIn("Seedance 2.0 不写首尾帧。", lock_brief(kept))

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

    def test_a_shown_model_choice_stays_on_wan(self):
        make_project(
            "p-wan",
            brief="雨夜",
            status="drilling",
            progress={
                "percent": 0,
                "phase": "lock_wait",
                "message": "立项锁定",
                "lock_machine": True,
                "question": {
                    "id": "model",
                    "kind": "single",
                    "reply": "三选一。",
                    "options": ["Seedance 2.5", "Seedance 2.0", "MiniMax H3"],
                },
            },
        )
        folder = project_dir("p-wan")
        locks = blank_locks()
        locks["track"] = "1"
        locks["source"] = "1"
        write_locks(folder, locks)
        answer_lock(read_project("p-wan"), "Seedance 2.5")
        saved = read_locks(folder)
        self.assertEqual(saved["model"], "h3")
        self.assertEqual(saved["reference_mode"], "")
        shown = read_project("p-wan")
        self.assertEqual(shown["progress"]["question"]["id"], "aspect")
        self.assertEqual(shown["messages"][-1]["options"], ["16:9", "9:16", "21:9"])

    def test_a_shown_reference_mode_choice_returns_to_wan(self):
        make_project(
            "p-ref",
            brief="雨夜",
            status="drilling",
            progress={
                "percent": 0,
                "phase": "lock_wait",
                "message": "立项锁定",
                "lock_machine": True,
                "question": {
                    "id": "reference_mode",
                    "kind": "single",
                    "reply": "参考模式。",
                    "options": ["① 全能参考", "② 首尾帧"],
                },
            },
        )
        folder = project_dir("p-ref")
        locks = _base_locks(model="2.5", reference_mode="")
        locks["aspect"] = ""
        write_locks(folder, locks)
        answer_lock(read_project("p-ref"), "① 全能参考")
        saved = read_locks(folder)
        self.assertEqual(saved["model"], "h3")
        self.assertEqual(saved["reference_mode"], "")
        self.assertEqual(read_project("p-ref")["progress"]["question"]["id"], "aspect")

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
            if "任务：赛道分流" in brief:
                return {"text": "路线"}
            if "任务：剧本" in brief:
                return {"title": "雨夜", "script": "她在雨里等车。"}
            if "只写资产" in brief or "机检没过" in brief:
                return {"image_prompts": "#### 图1 · 女（角色）\n同上"}
            raise AssertionError("跳到了后面的阶段")

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-gate")
            self.assertEqual(len(calls), 2)
            self.assertIn("任务：赛道分流", calls[0])
            self.assertIn("任务：剧本", calls[1])
            saved = read_project("p-gate")
            self.assertEqual(saved["status"], "gate")
            self.assertEqual(saved["progress"]["phase"], "gate_script")
            self.assertIn("继续", saved["messages"][-1]["options"])
            with patch("end.server.submit_agent_task"):
                answer_gate(read_project("p-gate"), "继续")
            run_project("p-gate")
            self.assertEqual(len(calls), 3)
            self.assertIn("任务：资产锁定", calls[2])
            self.assertIn("只写资产", calls[2])
            saved = read_project("p-gate")
            self.assertEqual(saved["progress"]["phase"], "gate_asset")
            self.assertFalse(saved["progress"]["pipeline"]["passed"])
            self.assertIn("根据建议修改会按下面这些项改", saved["messages"][-1]["content"])
            self.assertNotIn("已按检查结果自动改了", saved["messages"][-1]["content"])
            self.assertEqual(saved["messages"][-1]["options"], ["根据建议修改"])
            run_project("p-gate")
            self.assertEqual(len(calls), 3)

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
                "pipeline": {
                    "current": "asset",
                    "accepted": ["script"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "\n".join((
                        "[+] 图1   角色    PASS  林浅",
                        "[x] 图8   场景母版 FAIL  预演（场景母版）",
                        "[-] FAIL: A1 图8 出现跨条引用「同上」",
                    )),
                },
            },
            status="gate",
        )
        folder = project_dir("p-rewrite")
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text(
            "\n\n".join((
                "#### 图1 · 林浅\n通过块不要动\n16:9\n全图共 4 格\n五官 10 项锁定\n声纹\n≤8 词\nfifth panel\ndifferent face",
                "#### 图8 · 预演（场景母版）\n旧失败块\n同上\n16:9",
            )) + "\n",
            encoding="utf-8",
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图8 · 预演\n改过的块\n"}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"), patch("end.server.submit_agent_task"):
            answer_gate(read_project("p-rewrite"), "继续")
            run_project("p-rewrite")
        self.assertEqual(len(calls), 1)
        self.assertIn("任务：建议修补", calls[0])
        self.assertIn("旧失败块", calls[0])
        self.assertNotIn("通过块不要动", calls[0])
        self.assertNotIn("任务：分镜", calls[0])
        self.assertNotIn("任务：资产锁定", calls[0])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("通过块不要动", stored)
        self.assertIn("改过的块", stored)
        saved = read_project("p-rewrite")
        self.assertEqual(saved["progress"]["pipeline"]["current"], "asset")
        self.assertFalse(saved["progress"]["pipeline"]["passed"])

    def test_failed_asset_gate_revises_from_the_check(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光"
        locks["style"] = "自然光"
        locks["seams"] = []
        make_project(
            "p-asset-revise",
            status="gate",
            messages=[{"role": "assistant", "content": "机检没过。", "options": ["继续"]}],
            progress={
                "percent": 40,
                "phase": "gate_asset",
                "message": "资产门",
                "lock_machine": True,
                "pipeline": {
                    "current": "asset",
                    "accepted": ["script"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "FAIL: A4 图1 角色缺五官 10 项锁定",
                },
                "question": {"id": "asset", "options": ["继续"], "reply": "机检没过。"},
            },
        )
        folder = project_dir("p-asset-revise")
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text("#### 图1 · 女主\n旧稿\n", encoding="utf-8")
        shown = read_project("p-asset-revise")
        from end.server import public_view

        self.assertEqual(public_view(shown)["messages"][-1]["options"], ["根据建议修改"])
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图1 · 女主\n不该叫模型\n"}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"), patch("end.server.submit_agent_task") as submit:
            view = answer_gate(read_project("p-asset-revise"), "根据建议修改")
        submit.assert_not_called()
        self.assertEqual(calls, [])
        self.assertIn("交给模型改这一块", view["messages"][-1]["options"])
        self.assertIn("补上「声纹」", view["messages"][-1]["options"])
        self.assertNotIn("仅允许摄影机移动", view["messages"][-1]["options"])
        self.assertIn("图1", view["messages"][-1]["content"])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("旧稿", stored)
        self.assertNotIn("五官 10 项锁定", stored)
        self.assertNotIn("仅允许摄影机移动", stored)
        saved = read_project("p-asset-revise")
        self.assertEqual(saved["status"], "gate")
        self.assertFalse(saved["progress"]["pipeline"]["passed"])

    def _open_asset(self, project_id, draft):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光"
        locks["style"] = "自然光"
        locks["seams"] = []
        make_project(
            project_id,
            status="gate",
            messages=[{"role": "assistant", "content": "机检没过。", "options": ["继续"]}],
            progress={
                "percent": 40,
                "phase": "gate_asset",
                "message": "资产门",
                "lock_machine": True,
                "pipeline": {
                    "current": "asset",
                    "accepted": ["script"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "",
                },
                "question": {"id": "asset", "options": ["继续"], "reply": "机检没过。"},
            },
        )
        folder = project_dir(project_id)
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        text = draft if str(draft).endswith("\n") else draft + "\n"
        (internal / "assets.md").write_text(text, encoding="utf-8")
        return internal

    def test_mode_choice_is_two_sentences_and_writes_neither(self):
        internal = self._open_asset("p-mode-choice", _mode_block())
        with patch("end.server.submit_agent_task") as submit, patch("end.server.call_deepseek") as writer:
            view = answer_gate(read_project("p-mode-choice"), "根据建议修改")
        submit.assert_not_called()
        writer.assert_not_called()
        self.assertEqual(
            view["messages"][-1]["options"],
            ["仅允许摄影机移动", "允许按子区域切换机位", "交给模型改这一块"],
        )
        self.assertIn("图5", view["messages"][-1]["content"])
        self.assertIn("缺【参考场景锁定模式】正文", view["messages"][-1]["content"])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("模式B", stored)
        self.assertIn("子区域", stored)
        self.assertNotIn("仅允许摄影机移动", stored)
        self.assertNotIn("允许按子区域切换机位", stored)

    def test_voice_phrase_fills_without_calling_the_model(self):
        internal = self._open_asset("p-voice-choice", _voice_block())
        with patch("end.server.submit_agent_task"):
            view = answer_gate(read_project("p-voice-choice"), "根据建议修改")
            self.assertEqual(view["messages"][-1]["options"], ["补上「声纹」", "交给模型改这一块"])
            self.assertIn("图3", view["messages"][-1]["content"])
            self.assertIn("缺图底声纹锚", view["messages"][-1]["content"])
            answer_gate(read_project("p-voice-choice"), "补上「声纹」")
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图3 · 黑鳞怪物设定板\n不该叫模型\n"}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-voice-choice")
        self.assertEqual(calls, [])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("声纹", stored)
        saved = read_project("p-voice-choice")
        self.assertTrue(saved["progress"]["pipeline"]["passed"])

    def test_model_brief_contains_only_the_chosen_block(self):
        internal = self._open_asset("p-model-brief", _voice_block() + "\n\n" + _mode_block())
        with patch("end.server.submit_agent_task"):
            first = answer_gate(read_project("p-model-brief"), "根据建议修改")
            self.assertIn("缺图底声纹锚", first["messages"][-1]["content"])
            self.assertNotIn("参考场景锁定模式", first["messages"][-1]["content"])
            second = answer_gate(read_project("p-model-brief"), "交给模型改这一块")
            self.assertIn("缺【参考场景锁定模式】正文", second["messages"][-1]["content"])
            self.assertNotIn("缺图底声纹锚", second["messages"][-1]["content"])
            answer_gate(read_project("p-model-brief"), "仅允许摄影机移动")
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"partials": [{"skill": "图-0", "text": _passing_voice()}]}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-model-brief")
        self.assertEqual(len(calls), 1)
        self.assertIn("缺图底声纹锚", calls[0])
        self.assertNotIn("参考场景锁定模式", calls[0])
        self.assertNotIn("客厅九宫格", calls[0])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("仅允许摄影机移动", stored)
        self.assertNotIn("允许按子区域切换机位", stored)
        self.assertIn("声纹", stored)
        saved = read_project("p-model-brief")
        self.assertTrue(saved["progress"]["pipeline"]["passed"])

    def test_model_text_that_still_fails_is_asked_again(self):
        internal = self._open_asset("p-model-again", _voice_block())
        with patch("end.server.submit_agent_task"):
            answer_gate(read_project("p-model-again"), "根据建议修改")
            answer_gate(read_project("p-model-again"), "交给模型改这一块")
        calls = []
        rewritten = "\n".join((
            "#### 图3 · 黑鳞怪物设定板",
            "改过的正文",
            "同上",
            "16:9",
            "全图共 4 格",
            "五官 10 项锁定",
            "≤8 词",
            "fifth panel",
            "different face",
        ))

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"partials": [{"skill": "图-0", "text": rewritten}]}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
            run_project("p-model-again")
        self.assertEqual(len(calls), 1)
        self.assertIn("缺图底声纹锚", calls[0])
        stored = (internal / "assets.md").read_text(encoding="utf-8")
        self.assertIn("改过的正文", stored)
        self.assertIn("同上", stored)
        self.assertNotIn("声纹", stored)
        saved = read_project("p-model-again")
        self.assertFalse(saved["progress"]["pipeline"]["passed"])
        question = saved["progress"]["question"]
        self.assertEqual(question["id"], "asset_repair_choice")
        self.assertIn("缺图底声纹锚", question["reply"])
        self.assertIn("同上", question["reply"])
        self.assertEqual(question["options"], ["补上「声纹」", "交给模型改这一块"])
        self.assertIn("缺图底声纹锚", saved["messages"][-1]["content"])
        self.assertIn("同上", saved["messages"][-1]["content"])

    def test_failed_storyboard_gate_revises_from_the_check(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光"
        locks["style"] = "自然光"
        locks["seams"] = []
        make_project(
            "p-board-revise",
            status="gate",
            messages=[{"role": "assistant", "content": "机检没过。", "options": ["继续"]}],
            progress={
                "percent": 62,
                "phase": "gate_storyboard",
                "message": "分镜门",
                "lock_machine": True,
                "pipeline": {
                    "current": "storyboard",
                    "accepted": ["script", "asset"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "FAIL: 段1 缺六问",
                },
                "question": {"id": "storyboard", "options": ["继续"], "reply": "机检没过。"},
            },
        )
        folder = project_dir("p-board-revise")
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "分镜.md").write_text("第 1 格：她站在门内。\n", encoding="utf-8")
        (internal / "previs.md").write_text(
            "## 段 1\n第 1 格：她站在门内。\n\n## 段 2\n通过的段不要动\n",
            encoding="utf-8",
        )
        shown = read_project("p-board-revise")
        from end.server import public_view

        self.assertEqual(
            public_view(shown)["messages"][-1]["options"],
            ["根据建议修改", "先用这一稿继续"],
        )
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"storyboard": "第 1 格：她站在门内。", "previs": "## 段 1\n第 1 格：她站在门内，身体朝门。"}

        with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"), patch("end.server.submit_agent_task"):
            answer_gate(read_project("p-board-revise"), "根据建议修改")
            run_project("p-board-revise")
        self.assertEqual(len(calls), 1)
        self.assertIn("任务：建议修补", calls[0])
        self.assertIn("FAIL: 段1 缺六问", calls[0])
        self.assertIn("第 1 格：她站在门内。", calls[0])
        self.assertNotIn("通过的段不要动", calls[0])
        self.assertNotIn("这次是返修", calls[0])
        self.assertNotIn("只写分镜", calls[0])
        stored = (internal / "previs.md").read_text(encoding="utf-8")
        self.assertIn("通过的段不要动", stored)
        self.assertEqual((internal / "分镜.md").read_text(encoding="utf-8"), "第 1 格：她站在门内。\n")
        saved = read_project("p-board-revise")
        self.assertEqual(saved["progress"]["pipeline"]["current"], "storyboard")

    def test_failed_storyboard_can_keep_the_draft_and_write_the_feed(self):
        locks = prepare_locks(_base_locks(), "时长：12秒")
        locks["style_look"] = "自然光"
        locks["style"] = "自然光"
        locks["seams"] = []
        make_project(
            "p-board-keep",
            status="gate",
            messages=[{"role": "assistant", "content": "机检没过。", "options": ["继续"]}],
            progress={
                "percent": 62,
                "phase": "gate_storyboard",
                "message": "分镜门",
                "lock_machine": True,
                "pipeline": {
                    "current": "storyboard",
                    "accepted": ["script", "asset"],
                    "action": "wait",
                    "passed": False,
                    "only": "",
                    "report": "FAIL: 段1 缺六问",
                    "repair": "FAIL: 段1 缺六问",
                },
                "question": {"id": "storyboard", "options": ["继续"], "reply": "机检没过。"},
            },
        )
        folder = project_dir("p-board-keep")
        write_locks(folder, locks)
        internal = folder / "_内部_"
        internal.mkdir()
        from end.server import public_view

        self.assertEqual(public_view(read_project("p-board-keep"))["messages"][-1]["options"], ["根据建议修改"])
        board = "第 1 格：她站在门内。\n"
        previs = "## 段 1\n第 1 格：她站在门内。\n"
        (internal / "分镜.md").write_text(board, encoding="utf-8")
        (internal / "previs.md").write_text(previs, encoding="utf-8")
        self.assertEqual(
            public_view(read_project("p-board-keep"))["messages"][-1]["options"],
            ["根据建议修改", "先用这一稿继续"],
        )
        calls = []

        def writer(*_args, **_kwargs):
            calls.append(True)
            return {}

        with patch("end.server.call_deepseek", writer), patch("end.server.submit_agent_task"):
            answer_gate(read_project("p-board-keep"), "先用这一稿继续")
        self.assertEqual(calls, [])
        self.assertEqual((internal / "分镜.md").read_text(encoding="utf-8"), board)
        self.assertEqual((internal / "previs.md").read_text(encoding="utf-8"), previs)
        saved = read_project("p-board-keep")
        pipeline = saved["progress"]["pipeline"]
        self.assertEqual(pipeline["current"], "feed")
        self.assertEqual(pipeline["action"], "write")
        self.assertIn("storyboard", pipeline["accepted"])
        self.assertEqual(pipeline.get("repair") or "", "")
        self.assertNotIn("缺六问", saved["messages"][-1]["content"])

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
        captured = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            captured.append(brief)
            return {"script": "新稿", "title": "题", "internal": "圆桌"}

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
        self.assertEqual(len(captured), 1)
        self.assertIn("任务：剧本", captured[0])
        self.assertIn("产品信息写清楚", captured[0])
        self.assertIn("旧稿里有女主和胶囊", captured[0])
        self.assertIn("精修原文：1", captured[0])

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
        self.assertEqual(len(calls), 1)
        self.assertIn("根据建议修改会按下面这些项改", outcome["message"]["content"])
        again = execute(
            {"template": "short-drama-director", "progress": outcome["progress"]},
            folder,
            locks,
            writer,
            lambda *args, **kwargs: "pack",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(again["progress"]["pipeline"]["action"], "wait")

    def test_failed_rewrite_keeps_the_previous_draft_and_returns_to_the_gate(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("上一稿还在\n", encoding="utf-8")
        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "brief": "雨夜",
            "progress": {
                "pipeline": {
                    "action": "rewrite",
                    "current": "script",
                    "accepted": ["recon"],
                    "only": "",
                    "passed": True,
                    "report": "",
                    "revise_note": "产品信息写清楚",
                }
            },
        }
        calls = []

        def writer(*_args, **_kwargs):
            calls.append(1)
            raise RuntimeError("模型没有返回 JSON")

        outcome = execute(project, folder, _base_locks(), writer, lambda *_args, **_kwargs: "pack")
        self.assertEqual(len(calls), 1)
        self.assertEqual((internal / "剧本.md").read_text(encoding="utf-8").strip(), "上一稿还在")
        self.assertEqual(outcome["status"], "gate")
        self.assertEqual(outcome["progress"]["phase"], "gate_script")
        self.assertEqual(outcome["progress"]["pipeline"]["action"], "wait")
        self.assertTrue(outcome["progress"]["pipeline"]["passed"])
        self.assertIn("这一版没写完", outcome["message"]["content"])
        self.assertIn("精修", outcome["message"]["options"])
        self.assertIn("继续", outcome["message"]["options"])

    def test_retry_failure_after_a_bad_draft_stays_on_the_failed_gate(self):
        folder = Path(tempfile.mkdtemp())
        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "brief": "药盒",
            "progress": {
                "pipeline": {
                    "action": "write",
                    "current": "asset",
                    "accepted": ["script"],
                    "only": "",
                    "passed": True,
                    "report": "",
                }
            },
        }
        calls = []

        def writer(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"image_prompts": "#### 图1 · 女（角色）\n同上"}
            raise RuntimeError("模型没有返回 JSON")

        outcome = execute(project, folder, _base_locks(), writer, lambda *_args, **_kwargs: "pack")
        stored = (folder / "_内部_" / "assets.md").read_text(encoding="utf-8")
        self.assertEqual(len(calls), 1)
        self.assertIn("同上", stored)
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        self.assertEqual(outcome["progress"]["pipeline"]["action"], "wait")
        self.assertIn("根据建议修改会按下面这些项改", outcome["message"]["content"])
        self.assertEqual(outcome["message"]["options"], ["根据建议修改"])

    def test_write_failure_without_a_draft_stays_on_the_gate(self):
        folder = Path(tempfile.mkdtemp())
        project = {
            "template": "short-drama-director",
            "model": "deepseek-v4-pro",
            "brief": "雨夜",
            "progress": {"pipeline": {"action": "write", "current": "feed", "accepted": ["script", "asset", "storyboard"], "only": ""}},
        }

        def writer(*_args, **_kwargs):
            raise RuntimeError("模型没有返回 JSON")

        outcome = execute(project, folder, _base_locks(), writer, lambda *_args, **_kwargs: "pack")
        self.assertEqual(outcome["status"], "gate")
        self.assertEqual(outcome["progress"]["phase"], "gate_feed")
        self.assertTrue(outcome["progress"]["pipeline"]["retry_write"])
        self.assertFalse(outcome["progress"]["pipeline"]["passed"])
        self.assertEqual(outcome["message"]["options"], ["继续"])
        self.assertIn("没写完", outcome["message"]["content"])

    def test_asset_repair_keeps_blocks_the_model_left_out(self):
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text(
            "#### 图1 · 胶囊（道具）\n旧的药盒\n\n#### 图2 · 客厅（场景母版）\n16:9\n旧的客厅\n",
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
        self.assertIn("旧的客厅", stored)
        self.assertIn("禁止手持", stored)
        self.assertIn("道具设定图", stored)
        self.assertTrue(outcome["progress"]["pipeline"]["passed"])
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
        self.assertEqual(len(calls), 1)
        self.assertIn("根据建议修改会按下面这些项改", outcome["message"]["content"])
        self.assertNotIn("已按检查结果自动改了", outcome["message"]["content"])

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
        self.assertEqual(len(calls), 2)

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
        self.assertEqual(len(calls), 1)
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

    def test_h3_segment_object_is_written_as_six_section_text(self):
        payload = {
            "model": "MiniMax H3",
            "segments": [{
                "time_range": "0:00-0:15",
                "format": "Ref2VA (Six-section)",
                "content": {
                    "subject_definitions": "<Subject 1> is a woman standing indoors.",
                    "summary": "She waits by the door.",
                    "retention_analysis": "<Subject 1>: fully_preserved for the woman.",
                    "detailed_description": "[Shot 1] She stands inside.",
                    "overall_soundscape": "Room tone.",
                    "non_diegetic_music": "none.",
                },
            }],
        }
        text = render_feed_text(payload)
        self.assertEqual(render_feed_text(str(payload)), text)
        self.assertTrue(text.startswith("## 【投喂单元 1/1 · 段1】"))
        self.assertIn("\nsubject_definitions:\n", text)
        self.assertIn("duration: 15", text)
        self.assertTrue(feed_uses_h3(text, "h3"))
        self.assertEqual(render_feed_text(text), text)
        later = render_feed_text({"segments": [{
            "time_range": "0:15-0:30",
            "content": {"detailed_description": "[Shot 6] At 00:18.000 she turns."},
        }]})
        self.assertIn("[Shot 6] At 00:03.000", later)
        self.assertNotIn("00:18.000", later)
        bare = "\n".join((
            "subject_definitions",
            "<Subject 1> is a woman standing indoors.",
            "summary",
            "She waits.",
            "retention_analysis",
            "<Subject 1>: fully_preserved for the woman.",
            "non_diegetic_music",
            "none.",
            "overall_soundscape",
            "Room tone.",
            "detailed_description",
            "[Shot 1] At 00:00.000, she stands inside.",
            "[Shot 2] At 00:04.000, the camera cuts closer.",
            "[Shot 3] At 00:16.000, the camera cuts to the door.",
        ))
        self.assertTrue(feed_uses_h3(bare, "h3"))
        loose = render_feed_text(bare)
        self.assertIn("\nsubject_definitions:\n", loose)
        self.assertNotIn("[Shot 1] At", loose)
        self.assertEqual(loose.count("## 【投喂单元"), 2)
        loose_path = Path(tempfile.mkdtemp()) / "feed.md"
        loose_path.write_text(loose, encoding="utf-8")
        _loose_code, loose_report = check_feed(loose_path, "h3", "M2", "1")
        self.assertNotIn("未找到投喂单元", loose_report)
        path = Path(tempfile.mkdtemp()) / "feed.md"
        path.write_text(text, encoding="utf-8")
        _code, report = check_feed(path, "h3", "M2", "1")
        self.assertNotIn("未找到投喂单元", report)

    def test_h3_sections_are_reordered_before_the_check(self):
        swapped = "\n".join((
            "## 【投喂单元 1/1】",
            "subject_definitions:",
            "<Subject 1> is a woman.",
            "summary:",
            "She waits.",
            "retention_analysis:",
            "<Subject 1>: fully_preserved.",
            "overall_soundscape:",
            "Room tone.",
            "non_diegetic_music:",
            "none.",
            "detailed_description:",
            "[Shot 1] She stands inside.",
            "",
            "强制禁止项：禁字幕。",
        ))
        text = render_feed_text(swapped)
        detailed = text.index("detailed_description:")
        sound = text.index("overall_soundscape:")
        music = text.index("non_diegetic_music:")
        self.assertLess(detailed, sound)
        self.assertLess(sound, music)
        self.assertIn("[Shot 1] She stands inside.", text)
        self.assertTrue(text.rstrip().endswith("强制禁止项：禁字幕。"))
        self.assertEqual(render_feed_text(text), text)


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
        self.assertIn("function chatHome(project)", app)
        self.assertIn("function canvasHome(project)", app)
        self.assertIn('status === "drilling" || status === "gate" || status === "writing"', app)
        self.assertIn('status === "done" && phase === "prompts_ready"', app)
        self.assertIn('status === "rendering" || status === "building"', app)
        self.assertIn('from === "rendering" || from === "building"', app)
        self.assertIn('(progress.paused_from || "") === "writing"', app)
        self.assertIn("surfaceLock", app)
        self.assertIn("if (chatHome(current))", app)
        self.assertIn("seam-option", css)
        self.assertIn("style-card", css)


if __name__ == "__main__":
    unittest.main()
