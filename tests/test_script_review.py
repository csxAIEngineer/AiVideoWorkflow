"""Semantic gates. The fake model proves routing and hashing, not real accuracy."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir
from end.render_loop import H3_NO_ONSCREEN_CAPTION, run_local_pipeline
from end.rerun import _rerun_writing
from end.script_review import (
    GenerationBlocked,
    assert_generation_prompt,
    confirm_script,
    content_hash,
    delivery_decision,
    downstream_allowed,
    generation_bundle,
    planned_video_text,
    review_delivery,
    review_script,
    set_reviewer,
    story_context,
)
from end.server import _run_project, _start_explicit_render
from end.stage_runner import execute
from tests.offline_guard import install, uninstall
from tests.test_director_pipeline import make_project
from tests.test_render_loop import FakeCR, FakeICreat


def setUpModule():
    install()


def tearDownModule():
    uninstall()


def _issue(category, severity, owner, evidence, location="第1场"):
    return {
        "location": location,
        "evidence": evidence,
        "category": category,
        "severity": severity,
        "reason": category + "不一致",
        "owner": owner,
        "revision": "只改这一处，不要重写全文",
    }


def _writer(script):
    def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
        if "任务：赛道分流" in str(brief):
            return {"text": "路线说明"}
        if "任务：资产" in str(brief):
            raise AssertionError("资产阶段不应在剧本确认前启动")
        return {"title": "题", "script": script, "internal": "圆桌"}

    return writer


class ScriptReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.calls = []
        set_reviewer(self._review)

    def tearDown(self):
        set_reviewer(None)
        self.tmp.cleanup()

    def _review(self, messages):
        self.calls.append(messages)
        return self.response

    def _run(self, script, response='{"issues":[]}'):
        self.response = response
        outcome = execute(
            {
                "template": "short-drama-director",
                "brief": "用户原文：雨夜等车",
                "model": "fake",
                "progress": {"pipeline": {"action": "write", "current": "script", "accepted": [], "only": ""}},
            },
            self.folder,
            {"duration_seconds": 60, "aspect": "16:9", "track": "1"},
            _writer(script),
            lambda *args, **kwargs: "pack",
        )
        return outcome

    def test_a_clean_script_can_be_confirmed_and_does_not_change_the_text(self):
        script = "甲在车站等车。她想离开这座城。"
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = self._run(script)
        saved = (self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8")
        self.assertEqual(saved.strip(), script)
        self.assertIn("继续", outcome["message"]["options"])
        self.assertEqual(outcome["progress"]["pipeline"]["semantic_status"], "passed")
        ok, reason = confirm_script(self.folder, brief="用户原文：雨夜等车")
        self.assertTrue(ok, reason)
        self.assertEqual((self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), script)
        allowed, why = downstream_allowed(self.folder, brief="用户原文：雨夜等车")
        self.assertTrue(allowed, why)

    def test_causal_motivation_and_continuity_blocks_do_not_open_downstream(self):
        base = "甲把信交给乙。甲并不知道信的内容。"
        for index, category in enumerate(("causal", "motivation", "continuity")):
            script = base + ("。" * index)
            payload = json.dumps({"issues": [_issue(category, "block", "feed", "甲把信交给乙")]}, ensure_ascii=False)
            with patch("end.stage_runner.check_stage", return_value=(True, "")):
                outcome = self._run(script, payload)
            self.assertNotIn("继续", outcome["message"]["options"])
            self.assertEqual(outcome["progress"]["pipeline"]["semantic_owner"], "script")
            self.assertFalse(downstream_allowed(self.folder, brief="用户原文：雨夜等车")[0])
            self.assertEqual((self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8").strip(), script)
            self.assertEqual(self.calls[-1][0]["content"].count("不改写故事"), 1)

    def test_suspect_waits_for_a_person_and_preference_does_not_block(self):
        script = "甲看着雨。"
        suspect = json.dumps({"issues": [_issue("continuity", "suspect", "script", "甲看着雨")]}, ensure_ascii=False)
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = self._run(script, suspect)
        self.assertIn("接受可疑项并继续", outcome["message"]["options"])
        self.assertNotIn("继续", outcome["message"]["options"])
        self.assertFalse(confirm_script(self.folder, brief="用户原文：雨夜等车")[0])
        ok, reason = confirm_script(self.folder, accept_suspects=True, brief="用户原文：雨夜等车")
        self.assertTrue(ok, reason)
        preference = json.dumps(
            {"issues": [_issue("preference", "preference", "storyboard", "甲看着雨")]},
            ensure_ascii=False,
        )
        self.response = preference
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = self._run(script + "她没有说话。", preference)
        self.assertIn("继续", outcome["message"]["options"])

    def test_missing_context_is_not_filled_in(self):
        internal = self.folder / "_内部_"
        chain = internal / "剧本链"
        chain.mkdir(parents=True)
        (chain / "01-来源登记.md").write_text("只知道第一集在车站。\n", encoding="utf-8")
        script = "上一集他已经赢了。这一集甲在车站等车。"
        (internal / "剧本.md").write_text(script + "\n", encoding="utf-8")
        self.response = json.dumps(
            {"issues": [_issue("continuity", "suspect", "script", "上一集他已经赢了")]},
            ensure_ascii=False,
        )
        review_script(self.folder, {"script": script}, brief="用户原文：雨夜等车")
        context = story_context(self.folder, "用户原文：雨夜等车")
        self.assertIn("只知道第一集在车站", context)
        self.assertNotIn("他后来当了城主", context)
        sent = self.calls[-1][1]["content"]
        self.assertIn("不要编造", sent)
        before, _after = sent.split("最终剧本：", 1)
        self.assertNotIn("上一集他已经赢了", before)
        self.assertEqual((internal / "剧本.md").read_text(encoding="utf-8").strip(), script)
        self.assertEqual((chain / "01-来源登记.md").read_text(encoding="utf-8").strip(), "只知道第一集在车站。")

    def test_bad_json_and_model_errors_are_not_a_pass(self):
        script = "甲在车站等车。"
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            outcome = self._run(script, "不是json")
        self.assertEqual(outcome["progress"]["pipeline"]["semantic_status"], "failed")
        self.assertNotIn("继续", outcome["message"]["options"])
        set_reviewer(None)
        with patch("end.ai_client.ask_text", side_effect=RuntimeError("down")):
            row = review_script(self.folder, {"script": script}, brief="用户原文：雨夜等车", force=True)
        self.assertEqual(row["status"], "failed")
        self.assertNotEqual(row["status"], "passed")
        set_reviewer(self._review)

    def test_a_changed_script_invalidates_the_old_pass_without_deleting_it(self):
        script = "甲在车站等车。"
        with patch("end.stage_runner.check_stage", return_value=(True, "")):
            self._run(script)
        confirm_script(self.folder, brief="用户原文：雨夜等车")
        path = self.folder / "_内部_" / "剧本.md"
        path.write_text("甲决定留下。\n", encoding="utf-8")
        self.assertFalse(downstream_allowed(self.folder, brief="用户原文：雨夜等车")[0])
        self.assertTrue(path.is_file())

    def test_rereview_of_the_same_text_is_bounded(self):
        script = "甲在车站等车。"
        (self.folder / "_内部_").mkdir()
        (self.folder / "_内部_" / "剧本.md").write_text(script + "\n", encoding="utf-8")
        self.response = json.dumps({"issues": [_issue("causal", "block", "script", "甲在车站等车")]}, ensure_ascii=False)
        for _index in range(5):
            review_script(self.folder, {"script": script}, brief="", force=True)
        self.assertLessEqual(len(self.calls), 3)
        row = review_script(self.folder, {"script": script}, brief="", force=True)
        self.assertIn("复审", row["reason"])

    def test_disagreement_between_copies_does_not_delete_either(self):
        internal = self.folder / "_内部_"
        internal.mkdir()
        (internal / "剧本.md").write_text("文件里的剧本。\n", encoding="utf-8")
        (self.folder / "spec.json").write_text(json.dumps({"script": "spec里的剧本。"}, ensure_ascii=False), encoding="utf-8")
        row = review_script(self.folder, brief="")
        self.assertEqual(row["status"], "failed")
        self.assertIn("不一致", row["reason"])
        self.assertIn("文件里的剧本", (internal / "剧本.md").read_text(encoding="utf-8"))
        self.assertIn("spec里的剧本", (self.folder / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(self.calls, [])

    def test_format_failure_does_not_call_the_semantic_model(self):
        self.response = '{"issues":[]}'
        with patch("end.stage_runner.check_stage", return_value=(False, "剧本是空的")):
            execute(
                {
                    "template": "short-drama-director",
                    "brief": "用户原文",
                    "model": "fake",
                    "progress": {"pipeline": {"action": "write", "current": "script", "accepted": [], "only": ""}},
                },
                self.folder,
                {"track": "1"},
                _writer("甲在车站等车。"),
                lambda *args, **kwargs: "pack",
            )
        self.assertEqual(self.calls, [])


class DeliveryReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.calls = []
        set_reviewer(self._review)
        self.response = '{"issues":[]}'
        self.script = "甲在车站等车。她说：我要走。"
        (self.folder / "_内部_").mkdir()
        (self.folder / "_内部_" / "剧本.md").write_text(self.script + "\n", encoding="utf-8")
        self.spec = {
            "script": self.script,
            "aspect_ratio": "9:16",
            "image_prompts": "#### 图1 · 甲\n车站",
            "storyboard": "甲转身。",
            "feed": "subject_definitions:\n甲\ndetailed_description:\n甲转身离开车站。\n",
            "assets": [{"id": "A1", "title": "甲", "prompt": "车站里的甲"}],
            "shots": [{
                "id": "S01",
                "duration": 5,
                "prompt": "subject_definitions:\n甲\ndetailed_description:\n甲转身离开车站。\n",
                "images": ["A1"],
            }],
        }
        (self.folder / "spec.json").write_text(json.dumps(self.spec, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        set_reviewer(None)
        self.tmp.cleanup()

    def _review(self, messages):
        self.calls.append(messages)
        return self.response

    def _confirm(self):
        review_script(self.folder, self.spec, brief="")
        self.assertTrue(confirm_script(self.folder, brief="")[0])

    def test_h3_bundle_contains_the_caption_lock_and_a_clean_pass_submits(self):
        self._confirm()
        _items, manuscript = generation_bundle(self.spec)
        self.assertIn(H3_NO_ONSCREEN_CAPTION, manuscript)
        self.assertIn("甲转身离开车站", manuscript)
        row = review_delivery(self.folder, self.spec, brief="")
        self.assertEqual(row["status"], "passed")
        sent = self.calls[-1][1]["content"]
        self.assertIn(H3_NO_ONSCREEN_CAPTION, sent)

        videos = FakeCR()
        try:
            run_local_pipeline(
                self.spec,
                self.folder,
                icreat=FakeICreat(),
                cr=videos,
                sleeper=lambda _seconds: None,
                reviewer=self._review,
            )
        except Exception:
            pass
        self.assertTrue(videos.submits)
        self.assertIn(H3_NO_ONSCREEN_CAPTION, videos.submits[0]["text"])

    def test_downstream_omissions_block_and_keep_the_script(self):
        self._confirm()
        before = (self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8")
        cases = (
            ("setup", "feed", "甲要走"),
            ("dialogue", "storyboard", "我要走"),
            ("order", "asset", "甲在车站等车"),
        )
        for category, owner, evidence in cases:
            self.response = json.dumps({"issues": [_issue(category, "block", owner, evidence)]}, ensure_ascii=False)
            row = review_delivery(self.folder, self.spec, brief="", force=True)
            decision = delivery_decision(row)
            self.assertFalse(decision["ok"])
            self.assertEqual(decision["owner"], owner)
            self.assertEqual((self.folder / "_内部_" / "剧本.md").read_text(encoding="utf-8"), before)

    def test_expression_only_preference_does_not_block_generation(self):
        self._confirm()
        self.response = json.dumps(
            {"issues": [_issue("preference", "preference", "feed", "甲转身离开车站")]},
            ensure_ascii=False,
        )
        row = review_delivery(self.folder, self.spec, brief="", force=True)
        self.assertTrue(delivery_decision(row)["ok"])

    def test_old_project_and_direct_render_do_not_submit(self):
        bare = self.folder / "bare"
        spec = {
            "aspect_ratio": "9:16",
            "assets": [{"id": "A1", "title": "甲", "prompt": "车站"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": "她离开", "images": ["A1"]}],
        }

        class Images(object):
            submits = []

            def submit_image(self, *args, **kwargs):
                self.submits.append(args)
                raise AssertionError("没有审查不能生图")

        with self.assertRaises(GenerationBlocked):
            run_local_pipeline(spec, bare, icreat=Images(), cr=object(), sleeper=lambda _s: None, reviewer=self._review)
        self.assertEqual(Images.submits, [])

    def test_prompt_changed_after_review_is_rejected(self):
        from end.script_review import authorize_video_submit

        self._confirm()
        self.response = '{"issues":[]}'
        review_delivery(self.folder, self.spec, brief="", force=True)
        allowed = authorize_video_submit(self.folder, "S01", False, brief="")
        self.assertIn("甲转身离开车站", allowed)
        spec_path = self.folder / "spec.json"
        changed = json.loads(spec_path.read_text(encoding="utf-8"))
        changed["shots"][0]["prompt"] = "subject_definitions:\n乙\ndetailed_description:\n乙留下。\n"
        spec_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(GenerationBlocked):
            authorize_video_submit(self.folder, "S01", False, brief="")

    def test_render_entry_and_script_rerun_stop_without_a_pass(self):
        self._db = tempfile.TemporaryDirectory()
        root = Path(self._db.name)
        old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "t.db"
        db._CONN = None
        db.DATA.mkdir()
        try:
            make_project(
                "p-render",
                status="done",
                brief="雨夜",
                progress={"phase": "prompts_ready", "lock_machine": True},
            )
            folder = project_dir("p-render")
            spec = {
                "script": "甲在车站等车。",
                "aspect_ratio": "9:16",
                "assets": [{"id": "A1", "title": "甲", "prompt": "车站"}],
                "shots": [{"id": "S01", "duration": 4, "prompt": "她离开", "images": ["A1"]}],
            }
            (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            (folder / "locks.json").write_text(json.dumps({"aspect": "9:16", "model": "h3"}), encoding="utf-8")
            view = _start_explicit_render({"id": "p-render", "brief": "雨夜", "template": "short-drama-director", "progress": {"lock_machine": True}})
            self.assertEqual(view["status"], "gate")
            self.assertFalse(downstream_allowed(folder, spec, "雨夜")[0])
            media = folder / "outputs" / "clips" / "S01.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"old")
            project = {"id": "p-render", "brief": "雨夜"}

            class Site(object):
                def read_project(self, _project_id):
                    return project

                def _present_script_gate(self, _project):
                    self.presented = True

            site = Site()
            result = _rerun_writing(site, project, spec, folder, "script", [], ({}, "asset"))
            self.assertIsNone(result)
            self.assertTrue(site.presented)
            self.assertEqual(media.read_bytes(), b"old")
        finally:
            conn = db._CONN
            db.DATA, db.DB_PATH, db._CONN = old
            if conn is not None and conn is not old[2]:
                conn.close()
            self._db.cleanup()

    def test_legacy_writer_stops_before_assets(self):
        self._db = tempfile.TemporaryDirectory()
        root = Path(self._db.name)
        old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = root / "projects"
        db.DB_PATH = root / "legacy.db"
        db._CONN = None
        db.DATA.mkdir()
        stages = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None, trace_prompts=None):
            if "资产" in str(brief) or "分镜" in str(brief) or "投喂" in str(brief):
                stages.append("downstream")
                return {"image_prompts": "不应写到"}
            stages.append("script")
            return {"title": "题", "script": "甲在车站等车。"}

        try:
            make_project("p-legacy", status="writing", progress={"phase": "writing", "lock_machine": False})
            with patch("end.server.call_deepseek", writer), patch("end.server.load_skill_pack", return_value="pack"):
                _run_project("p-legacy")
            self.assertNotIn("downstream", stages)
            saved = (project_dir("p-legacy") / "_内部_" / "剧本.md").read_text(encoding="utf-8")
            self.assertIn("甲在车站等车", saved)
        finally:
            conn = db._CONN
            db.DATA, db.DB_PATH, db._CONN = old
            if conn is not None and conn is not old[2]:
                conn.close()
            self._db.cleanup()


class GateContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.calls = []
        set_reviewer(lambda messages: self._review(messages))
        self.response = '{"issues":[]}'

    def tearDown(self):
        set_reviewer(None)
        self.tmp.cleanup()

    def _review(self, messages):
        self.calls.append(messages)
        return self.response

    def _script(self, text):
        internal = self.folder / "_内部_"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "剧本.md").write_text(text + "\n", encoding="utf-8")
        spec = {"script": text, "aspect_ratio": "9:16", "assets": [{"id": "A1", "title": "甲", "prompt": "车站里的甲"}], "shots": [{"id": "S01", "duration": 4, "prompt": "她离开", "images": ["A1"]}]}
        (self.folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return spec

    def test_bad_review_shapes_cannot_pass(self):
        spec = self._script("甲在车站等车。")
        samples = (
            '{"issues":[{"location":"场","evidence":"","category":"causal","severity":"block","reason":"断","owner":"script","revision":"补"}]}',
            '{"issues":[{"location":"场","evidence":"甲","category":"nope","severity":"block","reason":"断","owner":"script","revision":"补"}]}',
            '{"issues":[{"location":"场","evidence":"甲","category":"causal","severity":"huge","reason":"断","owner":"script","revision":"补"}]}',
            '{"issues":[{"location":"场","evidence":"甲","category":"causal","severity":"block","reason":"断","owner":"camera","revision":"补"}]}',
            '{"status":"passed","issues":[{"location":"场","evidence":"甲","category":"causal","severity":"block","reason":"断","owner":"feed","revision":"补"}]}',
            '{"issues":[],"script":"改写成另一部"}',
        )
        for raw in samples:
            self.response = raw
            row = review_script(self.folder, spec, brief="", force=True)
            self.assertNotEqual(row["status"], "passed", raw)
            self.assertNotIn("改写成另一部", json.dumps(row, ensure_ascii=False))
        self.assertFalse(downstream_allowed(self.folder, spec, "")[0])

    def test_prior_episode_without_facts_stays_uncertain(self):
        spec = self._script("上一集他已经赢了。这一集甲在车站等车。")
        self.response = '{"issues":[]}'
        row = review_script(self.folder, spec, brief="只有这一集的事", force=True)
        self.assertEqual(row["status"], "suspect")
        self.assertTrue(any(item.get("uncertain") for item in row["issues"]))
        self.assertFalse(confirm_script(self.folder, brief="只有这一集的事")[0])
        chain = self.folder / "_内部_" / "剧本链"
        chain.mkdir()
        (chain / "00-前集.md").write_text("前集事实：他上次输了。\n", encoding="utf-8")
        self.response = '{"issues":[]}'
        later = review_script(self.folder, spec, brief="只有这一集的事", force=True)
        self.assertEqual(later["status"], "passed")
        self.assertFalse(any(item.get("uncertain") for item in later["issues"]))

    def test_old_suspect_acceptance_does_not_carry(self):
        spec = self._script("甲看着雨。")
        self.response = json.dumps({"issues": [_issue("continuity", "suspect", "script", "甲看着雨")]}, ensure_ascii=False)
        review_script(self.folder, spec, brief="雨", force=True)
        self.assertTrue(confirm_script(self.folder, accept_suspects=True, brief="雨")[0])
        spec["script"] = "甲决定留下。"
        (self.folder / "_内部_" / "剧本.md").write_text("甲决定留下。\n", encoding="utf-8")
        (self.folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        self.assertFalse(downstream_allowed(self.folder, spec, "雨")[0])

    def test_story_issue_cannot_be_routed_to_feed(self):
        spec = self._script("甲在车站等车。她说：我要走。")
        spec["feed"] = "subject_definitions:\n甲\ndetailed_description:\n甲离开。\n"
        spec["shots"][0]["prompt"] = spec["feed"]
        (self.folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        review_script(self.folder, spec, brief="")
        confirm_script(self.folder, brief="")
        self.response = json.dumps({"issues": [_issue("causal", "block", "feed", "我要走")]}, ensure_ascii=False)
        row = review_delivery(self.folder, spec, brief="", force=True)
        self.assertEqual(row["owner"], "script")
        self.assertEqual(row["issues"][0]["owner"], "script")
        decision = delivery_decision(row)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["owner"], "script")

    def test_tail_binding_uses_the_persisted_shot_and_rejects_a_mid_check_edit(self):
        from end.script_review import authorize_video_submit, lease_still_current, planned_video_text

        spec = self._script("甲在车站等车。")
        spec["shots"] = [
            {"id": "S01", "duration": 4, "prompt": "第一镜离开", "images": ["A1"]},
            {"id": "S02", "duration": 4, "prompt": "第二镜继续走", "images": ["A1"]},
        ]
        (self.folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        review_script(self.folder, spec, brief="")
        confirm_script(self.folder, brief="")
        self.response = '{"issues":[]}'
        rendered = {
            "image": "",
            "image_from": "",
            "video_lock": "",
            "video_next": "",
            "video_tail": "TAIL_MARK",
            "video_from": "",
        }
        with patch("end.ai_client.template_render", return_value=rendered):
            review_delivery(self.folder, spec, brief="", force=True)
            bare = authorize_video_submit(self.folder, "S02", False, brief="")
            tailed = authorize_video_submit(self.folder, "S02", True, brief="")
            self.assertNotIn("TAIL_MARK", bare)
            self.assertIn("TAIL_MARK", tailed)
            self.assertEqual(tailed, planned_video_text(spec, spec["shots"][1], 1, has_tail=True))
            self.assertTrue(lease_still_current(self.folder, "video", "S02"))
            self.assertFalse(lease_still_current(self.folder, "image", "A1"))
            disk = json.loads((self.folder / "spec.json").read_text(encoding="utf-8"))
            disk["script"] = "另一稿"
            (self.folder / "spec.json").write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
            (self.folder / "_内部_" / "剧本.md").write_text("另一稿\n", encoding="utf-8")
            self.assertFalse(lease_still_current(self.folder, "video", "S02"))
            disk["script"] = spec["script"]
            (self.folder / "spec.json").write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
            (self.folder / "_内部_" / "剧本.md").write_text(spec["script"] + "\n", encoding="utf-8")
            original = planned_video_text

            def tamper(*args, **kwargs):
                if not getattr(tamper, "done", False):
                    tamper.done = True
                    disk = json.loads((self.folder / "spec.json").read_text(encoding="utf-8"))
                    disk["shots"][1]["prompt"] = "第二镜改口"
                    (self.folder / "spec.json").write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
                return original(*args, **kwargs)

            with patch("end.script_review.planned_video_text", side_effect=tamper):
                with self.assertRaises(GenerationBlocked):
                    authorize_video_submit(self.folder, "S02", True, brief="")

    def test_confirmed_rerun_archives_old_media_and_does_not_reuse_it(self):
        from end.render_loop import load_run_state, save_run_state
        from end.rerun import _rerun_writing

        blocked = json.dumps({"issues": [_issue("causal", "block", "feed", "甲在车站等车")]}, ensure_ascii=False)
        self.response = blocked
        spec = self._script("甲在车站等车。")
        row = review_script(self.folder, spec, brief="雨", force=True)
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["issues"][0]["owner"], "script")
        old_feed = self.folder / "_内部_" / "feed.md"
        old_feed.write_text("旧投喂正文\n", encoding="utf-8")
        old_clip = self.folder / "outputs" / "clips" / "S01.mp4"
        old_clip.parent.mkdir(parents=True)
        old_clip.write_bytes(b"old-film-bytes")
        save_run_state(self.folder, {"steps": {"S01": {"kind": "video", "status": "done", "output_file": "clips/S01.mp4"}}})
        self.assertTrue(old_feed.is_file())
        self.response = '{"issues":[]}'
        spec["script"] = "甲在车站等车。她决定留下。"
        (self.folder / "_内部_" / "剧本.md").write_text(spec["script"] + "\n", encoding="utf-8")
        (self.folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        passed = review_script(self.folder, spec, brief="雨", force=True)
        self.assertEqual(passed["status"], "passed")
        self.assertTrue(confirm_script(self.folder, brief="雨")[0])

        class Site(object):
            def read_project(self, _project_id):
                return {"id": "p", "brief": "雨", "video_file": "", "script": spec["script"]}

            def load_spec(self, _project_id):
                return json.loads((self.folder / "spec.json").read_text(encoding="utf-8"))

            def write_project(self, _project):
                return None

            def _enqueue_stage(self, *_args):
                self.enqueued = True

        site = Site()
        site.folder = self.folder
        _rerun_writing(site, site.read_project("p"), spec, self.folder, "script", [("S01", "video")], ({"accepted": ["script"], "current": "asset", "action": "write"}, "asset"))
        self.assertTrue(site.enqueued)
        self.assertFalse(old_feed.is_file())
        self.assertFalse(old_clip.is_file())
        copies = list((self.folder / "history").glob("*/_内部_/feed.md"))
        films = list((self.folder / "history").glob("*/outputs/clips/S01.mp4"))
        self.assertTrue(copies)
        self.assertTrue(films)
        self.assertEqual(copies[0].read_text(encoding="utf-8"), "旧投喂正文\n")
        self.assertEqual(films[0].read_bytes(), b"old-film-bytes")
        self.assertNotEqual(load_run_state(self.folder)["steps"]["S01"]["status"], "done")

    def test_offline_guard_blocks_provider_text(self):
        from end.ai_client import ask_text

        with self.assertRaises(AssertionError) as caught:
            ask_text([{"role": "user", "content": "不要发出去"}])
        self.assertIn("offline guard", str(caught.exception))

    def test_chat_gate_sends_the_new_labels(self):
        source = Path("front/app.js").read_text(encoding="utf-8")
        choice = source.split("function choiceList", 1)[1].split("function messageBubble", 1)[0]
        self.assertIn("sendText(label)", choice)
        send = source.split("async function sendText", 1)[1].split("const materialFile", 1)[0]
        self.assertIn('rpc("projects.send_message"', send)
        resume = source.split("function onResumeClick", 1)[1].split("function syncChoiceDisabled", 1)[0]
        self.assertIn('sendText("继续")', resume)
        self.assertNotIn("接受可疑项并继续", resume)


if __name__ == "__main__":
    unittest.main()
