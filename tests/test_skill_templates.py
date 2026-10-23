"""The vendored short-drama suite: one template, staged packs, and a render gate."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project
from end.server import public_view, rpc_create_project, rpc_render, rpc_send_message, run_project
from AI.skills.registry import (
    _slice_module,
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
    locks_ready,
    normalize_template,
    template_choices,
    template_policy,
)
from tests.test_project_api import make_project

ROOT = Path(__file__).resolve().parents[1]
LOCKED = "\n".join(
    [
        "赛道：1",
        "素材来源：1",
        "模型：MiniMax H3",
        "画幅：16:9",
        "交付：③",
        "段缝：①",
        "画风：冷色写实",
        "动作强度：R1",
    ]
)
H3_FEED = "\n".join(
    [
        "integrated_multimodal_description: A woman in a blue coat stands on a wet platform. [Shot 1] She turns and walks toward the train.",
        "overall_soundscape: Rain and a distant train brake.",
        "non_diegetic_music: Low strings, slow.",
        "duration_seconds: 5",
    ]
)


def _spec():
    return {
        "title": "雨夜",
        "script": "她在站台上等车。",
        "storyboard": "1. 她转身。",
        "image_prompts": "女人，蓝外套。",
        "assets": [
            {
                "id": "A1",
                "title": "女人",
                "prompt": "蓝外套的女人站在站台",
                "subject": "女人",
                "subject_kind": "character",
                "view": "single",
            }
        ],
        "shots": [{"id": "S01", "duration": 5, "prompt": H3_FEED, "images": ["A1"]}],
    }


class ShortDramaSkillTest(unittest.TestCase):
    def test_only_one_template_is_listed(self):
        choices = template_choices()
        self.assertEqual([item["id"] for item in choices], ["short-drama-director"])
        self.assertEqual(choices[0]["policy"], "drama_still")
        self.assertEqual(normalize_template(None), "short-drama-director")
        self.assertEqual(normalize_template("luxury-suv"), "short-drama-director")
        self.assertEqual(template_policy(), "drama_still")
        suite = ROOT / "AI" / "skills" / "manju-laoli-skill"
        names = {path.name for path in suite.iterdir() if path.is_dir() and not path.name.startswith(".")}
        self.assertIn("short-drama-director", names)
        self.assertTrue(names.issuperset({
            "sd-asset-engine",
            "sd-combat-engine",
            "sd-decision-engine",
            "sd-feed-engine",
            "sd-jieshuo-engine",
            "sd-overseas-engine",
            "sd-qc-engine",
            "sd-real-engine",
            "sd-recon-engine",
            "sd-script-engine",
            "sd-storyboard-engine",
            "sd-xuanhuan-engine",
        }))
        root_names = {path.name for path in (ROOT / "AI" / "skills").iterdir() if path.is_dir()}
        for old in ("luxury-suv", "apple-product", "drama-product-skill", "drama-product-skill2"):
            self.assertNotIn(old, names)
            self.assertNotIn(old, root_names)

    def test_drill_asks_each_lock_with_its_options_and_explanation(self):
        from AI.script import choice_options

        drill = drill_system_prompt()
        self.assertIn("MiniMax H3", drill)
        self.assertIn("Seedance 2.5", drill)
        self.assertIn("Seedance 2.0", drill)
        self.assertIn("16:9", drill)
        self.assertIn("9:16", drill)
        self.assertIn("21:9", drill)
        self.assertIn("① 末帧定格承接", drill)
        self.assertIn("② 换机位硬切", drill)
        self.assertIn("③ 空镜过渡", drill)
        self.assertIn("④ 技巧型转场", drill)
        self.assertIn("先于后面所有问题", drill)
        self.assertIn("先出对标情报卡", drill)
        self.assertIn("提示词语法按这个模型彻底分开", drill)
        self.assertIn("必须由用户选定", drill)
        self.assertIn("四项各自独立", drill)
        self.assertIn("导演风格卡锁定，选定即冻结", drill)
        self.assertIn("按你的推荐走", drill)
        self.assertIn("不得 `phase=ready`", drill)
        self.assertEqual(
            choice_options(["① 末帧定格承接", "② 换机位硬切", "③ 空镜过渡", "④ 技巧型转场", "其他"]),
            ["① 末帧定格承接", "② 换机位硬切", "③ 空镜过渡", "④ 技巧型转场", "其他"],
        )
        self.assertFalse(locks_ready("拍一支雨夜短片"))
        self.assertFalse(locks_ready("赛道：1\n素材来源：1\n画幅：16:9\n画风：冷色写实\n动作强度：R1"))
        self.assertTrue(locks_ready(LOCKED))
        shape = json_shape(brief=LOCKED)
        self.assertIn("本片画幅以简报为准：16:9", shape)
        self.assertNotIn("画幅 9:16", shape)
        bare = json_shape()
        self.assertIn("不要默认竖屏", bare)

    def test_p4_pack_includes_the_h3_adapter_and_not_the_whole_skill(self):
        pack = load_skill_pack(context=LOCKED, stage="P4")
        self.assertIn("short-drama-director/references/h3-adapter.md", pack)
        self.assertNotIn("# short-drama-director/SKILL.md", pack)
        prompt = build_system_prompt(pack, brief=LOCKED)
        self.assertIn("本片画幅以简报为准：16:9", prompt)

    def test_a_missing_subskill_is_an_error(self):
        module = _slice_module()
        with patch.object(module, "resolve_files", return_value=["short-drama-director/missing-stage.md"]):
            with self.assertRaises(RuntimeError) as caught:
                load_skill_pack(context=LOCKED, stage="P1")
        self.assertIn("缺少子技能", str(caught.exception))


class PromptGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_writing_stops_at_the_board(self):
        make_project("stagewrite01", status="writing", brief=LOCKED, template="short-drama-director")
        stages = []

        def capture(template, context="", stage="P4"):
            stages.append(stage)
            return "pack"

        with patch("end.server.load_skill_pack", side_effect=capture), patch(
            "end.server.call_deepseek", return_value=_spec()
        ), patch("end.server.template_policy", return_value="drama_still"), patch(
            "end.server.start_render_thread"
        ) as start:
            run_project("stagewrite01")
        start.assert_not_called()
        self.assertEqual(stages, ["P1", "P2", "P3", "P4"])
        saved = read_project("stagewrite01")
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"]["phase"], "prompts_ready")
        folder = project_dir("stagewrite01")
        self.assertIn("integrated_multimodal_description", (folder / "feed.md").read_text(encoding="utf-8"))
        self.assertTrue((folder / "project.md").is_file())
        self.assertTrue((folder / "board.html").is_file())
        spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["aspect_ratio"], "16:9")
        self.assertEqual(spec["assets"][0]["view"], "single")

    def test_render_words_start_video_and_plain_text_does_not(self):
        project_id = "a6e1700000a1"
        make_project(
            project_id,
            status="done",
            brief=LOCKED,
            template="short-drama-director",
            stage="提示词已就绪",
            progress={"percent": 100, "phase": "prompts_ready", "message": "提示词已就绪"},
        )
        folder = project_dir(project_id)
        (folder / "board.html").write_text("<p>board</p>", encoding="utf-8")
        with patch("end.server.start_render_thread") as start, patch(
            "end.server.template_label", return_value="漫剧"
        ), patch("end.server.normalize_template", side_effect=lambda value: value or "short-drama-director"), patch(
            "end.server.normalize_model", side_effect=lambda value: value or "deepseek-v4-pro"
        ), patch("end.server.model_label", return_value="DeepSeek"):
            stayed = rpc_send_message({"id": project_id, "text": "旁白再短一点"})
            self.assertEqual(stayed["progress"]["phase"], "prompts_ready")
            start.assert_not_called()
            started = rpc_send_message({"id": project_id, "text": "出图"})
        start.assert_called_once_with(project_id, replace=True)
        self.assertEqual(started["status"], "building")

    def test_render_button_uses_the_same_gate(self):
        project_id = "a6e1700000a2"
        make_project(
            project_id,
            status="done",
            brief="",
            template="short-drama-director",
            progress={"percent": 100, "phase": "prompts_ready", "message": "提示词已就绪"},
        )
        with patch("end.server.start_render_thread") as start, patch(
            "end.server.template_label", return_value="漫剧"
        ), patch("end.server.normalize_template", side_effect=lambda value: value or "short-drama-director"), patch(
            "end.server.normalize_model", side_effect=lambda value: value or "deepseek-v4-pro"
        ), patch("end.server.model_label", return_value="DeepSeek"):
            asked = rpc_render({"id": project_id})
        start.assert_not_called()
        self.assertEqual(asked["progress"]["phase"], "aspect_wait")
        self.assertNotIn("9:16", asked["status"])

    def test_page_hides_the_old_templates(self):
        html = (ROOT / "front" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("luxury-suv", html)
        self.assertNotIn("apple-product", html)
        self.assertNotIn("drama-product-skill", html)
        self.assertIn("出图出片", html)
        self.assertIn("立项锁定", script)
        self.assertIn("short-drama-director", script)
        self.assertIn('templates.length < 2', script)


class TemplateProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "api.db"
        db._CONN = None
        db.DATA.mkdir()

    def tearDown(self):
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def test_a_blank_template_displays_the_drama(self):
        make_project("oldtemplate01")
        conn = db.connect()
        conn.execute("UPDATE projects SET template = '' WHERE id = ?", ("oldtemplate01",))
        conn.commit()
        self.assertEqual(read_project("oldtemplate01")["template"], "short-drama-director")
        with patch("end.server.normalize_template", side_effect=lambda value: value or "short-drama-director"), patch(
            "end.server.template_label", return_value="漫剧"
        ), patch("end.server.normalize_model", side_effect=lambda value: value or "deepseek-v4-pro"), patch(
            "end.server.model_label", return_value="DeepSeek"
        ):
            view = public_view(read_project("oldtemplate01"))
        self.assertEqual(view["template"], "short-drama-director")
        self.assertEqual(view["template_label"], "漫剧")

    def test_create_stores_the_drama_template(self):
        def keep(project):
            from end.db import write_project

            write_project(project)

        with patch("end.server.start_drill", side_effect=keep), patch(
            "end.server.normalize_template", side_effect=lambda value: "short-drama-director"
        ), patch("end.server.template_label", return_value="漫剧"), patch(
            "end.server.normalize_model", side_effect=lambda value: value or "deepseek-v4-pro"
        ), patch("end.server.model_label", return_value="DeepSeek"):
            created = rpc_create_project({"brief": "雨夜站台"})
        self.assertEqual(created["template"], "short-drama-director")
        self.assertEqual(created["template_label"], "漫剧")


if __name__ == "__main__":
    unittest.main()
