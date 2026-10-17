"""Two team templates: how prompts are assembled, and how projects remember the choice."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project
from end.server import public_view, rpc_config, rpc_create_project, run_project
from skills.registry import (
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
    normalize_template,
    template_files,
    template_policy,
    template_render,
)
from agent.layout import AgentLayout
from tests.test_agent_layout import ASK, SPEC, ScriptedModel
from tests.test_project_api import make_project

ROOT = Path(__file__).resolve().parents[1]


class TemplateAssemblyTest(unittest.TestCase):
    def test_luxury_drill_and_write_use_the_suv_team(self):
        drill = drill_system_prompt("luxury-suv")
        self.assertIn("高奢", drill)
        self.assertIn("就按这个做", drill)
        self.assertIn("其他", drill)
        self.assertIn("phase", drill)
        self.assertIn("3. 司机", drill)
        self.assertIn("只问一个问题", drill)
        pack = load_skill_pack("luxury-suv")
        self.assertIn("seedance-car-vehicle/SKILL.md", pack)
        self.assertIn("references/cases.md", pack)
        self.assertIn("image-node/luxury-suv/SKILL.md", pack)
        self.assertNotIn("image-node/apple-product", pack)
        self.assertNotIn("apple-product-closeup", pack)
        prompt = build_system_prompt(pack, "luxury-suv")
        self.assertIn("front", prompt)
        self.assertIn("side", prompt)
        self.assertIn("back", prompt)
        self.assertIn("高奢", prompt)
        self.assertNotIn("你是汽车视频导演", prompt)
        self.assertNotIn("apple-product-closeup", prompt)
        self.assertEqual(template_policy("luxury-suv"), "vehicle_turnaround")
        files = template_files("luxury-suv")
        self.assertIn("directors/luxury-suv/SKILL.md", files)
        self.assertIn("drill-me/SKILL.md", files)
        self.assertIn("seedance-car-vehicle/references/cases.md", files)
        self.assertNotIn("apple-product-closeup/SKILL.md", files)
        render = template_render("luxury-suv")
        self.assertEqual(render["image_from"], "image-node/luxury-suv/SKILL.md")
        self.assertIn("轮胎接地", render["image"])
        self.assertEqual(render["video_from"], "seedance-car-vehicle/SKILL.md")
        self.assertIn("轮子在转", render["video_lock"])

    def test_apple_drill_and_write_use_the_product_team(self):
        drill = drill_system_prompt("apple-product")
        self.assertIn("材质", drill)
        self.assertIn("光线", drill)
        self.assertIn("要看清的细节", drill)
        self.assertIn("不要问司机", drill)
        self.assertIn("不要问路面", drill)
        self.assertNotIn("3. 司机", drill)
        self.assertNotIn("车辆锁定", drill)
        self.assertIn("就按这个做", drill)
        pack = load_skill_pack("apple-product")
        self.assertIn("开场", pack)
        self.assertIn("产品锁定", pack)
        self.assertIn("光线和背景", pack)
        self.assertIn("镜头与产品的配比", pack)
        self.assertIn("编号分镜", pack)
        self.assertIn("风格和负面清单", pack)
        self.assertNotIn("seedance-car-vehicle", pack)
        self.assertNotIn("cases.md", pack)
        self.assertNotIn("轮胎接地", pack)
        self.assertIn("image-node/apple-product/SKILL.md", pack)
        self.assertNotIn("image-node/luxury-suv", pack)
        self.assertIn("hero", pack)
        self.assertNotIn("主角车辆", pack)
        apple_render = template_render("apple-product")
        self.assertEqual(apple_render["image"], "")
        self.assertEqual(apple_render["image_from"], "")
        self.assertEqual(apple_render["video_lock"], "")
        self.assertEqual(apple_render["video_from"], "")
        prompt = build_system_prompt(pack, "apple-product")
        self.assertIn("hero", prompt)
        self.assertIn("three_quarter", prompt)
        self.assertIn("macro", prompt)
        self.assertIn("不写车辆", prompt)
        self.assertNotIn("你是汽车视频导演", prompt)
        self.assertNotIn("references/cases.md", prompt)
        self.assertEqual(template_policy("apple-product"), "product_hero")
        shape = json_shape("apple-product")
        self.assertIn("hero", shape)
        self.assertIn("three_quarter", shape)
        self.assertIn("macro", shape)
        self.assertNotIn("view 只能是 front", shape)

    def test_missing_template_stays_on_luxury(self):
        self.assertEqual(normalize_template(None), "luxury-suv")
        self.assertEqual(normalize_template("nope"), "luxury-suv")
        self.assertEqual(normalize_template(""), "luxury-suv")
        pack = load_skill_pack()
        self.assertIn("seedance-car-vehicle/SKILL.md", pack)
        self.assertIn("front", json_shape())
        self.assertIn("side", json_shape())
        self.assertIn("back", json_shape())

    def test_layout_passes_the_template_into_both_prompts(self):
        drill_model = ScriptedModel([ASK])
        AgentLayout(model=drill_model).drill(
            [{"role": "user", "content": "银色耳机"}],
            template="apple-product",
        )
        drill_system = drill_model.calls[0]["messages"][0]["content"]
        self.assertIn("不要问司机", drill_system)
        self.assertNotIn("3. 司机", drill_system)
        write_model = ScriptedModel([SPEC])
        AgentLayout(model=write_model).write(
            "银色耳机",
            load_skill_pack("apple-product"),
            template="apple-product",
        )
        write_system = write_model.calls[0]["messages"][0]["content"]
        self.assertIn("hero", write_system)
        self.assertIn("three_quarter", write_system)
        self.assertNotIn("seedance-car-vehicle", write_system)

    def test_work_skill_points_at_the_registry(self):
        text = (ROOT / "skills" / "aivideo-work" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/drill-me/SKILL.md", text)
        self.assertIn("skills/registry.py", text)
        self.assertIn("luxury-suv", text)
        self.assertIn("apple-product", text)
        self.assertNotIn("就按这个做", text)
        studio = (ROOT / "studio" / "chat.py").read_text(encoding="utf-8")
        self.assertNotIn("不再切换短剧技能", studio)
        self.assertIn("/template", studio)
        self.assertIn("template_files", studio)


class TemplateProjectTest(unittest.TestCase):
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
        os.environ["AIVIDEO_PUBLIC_BASE"] = "http://203.0.113.10:8787"
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"

    def tearDown(self):
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

    def test_config_lists_luxury_first(self):
        config = rpc_config(None)
        ids = [item["id"] for item in config["templates"]]
        self.assertEqual(ids[0], "luxury-suv")
        self.assertIn("apple-product", ids)
        self.assertEqual(config["template"], "luxury-suv")

    def test_create_stores_the_chosen_template(self):
        def keep(project):
            from end.db import write_project

            write_project(project)

        with patch("end.server.start_drill", side_effect=keep):
            created = rpc_create_project({"brief": "银色耳机", "template": "apple-product"})
            defaulted = rpc_create_project({"brief": "山路上的 SUV"})
        self.assertEqual(created["template"], "apple-product")
        self.assertEqual(created["template_label"], "Apple 风产品特写")
        self.assertEqual(read_project(created["id"])["template"], "apple-product")
        self.assertEqual(defaulted["template"], "luxury-suv")
        self.assertEqual(defaulted["template_label"], "高奢感 SUV")

    def test_a_project_without_a_template_displays_luxury(self):
        make_project("oldtemplate01")
        conn = db.connect()
        conn.execute("UPDATE projects SET template = '' WHERE id = ?", ("oldtemplate01",))
        conn.commit()
        self.assertEqual(read_project("oldtemplate01")["template"], "luxury-suv")
        view = public_view(read_project("oldtemplate01"))
        self.assertEqual(view["template"], "luxury-suv")
        self.assertEqual(view["template_label"], "高奢感 SUV")

    def test_page_sends_the_template_with_a_new_project(self):
        html = (ROOT / "front" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="template"', html)
        self.assertIn('aria-label="模版"', html)
        self.assertIn("luxury-suv", html)
        self.assertIn("apple-product", html)
        self.assertIn("template: selectedTemplate()", script)
        self.assertIn('project.template || "luxury-suv"', script)

    def test_written_spec_uses_the_project_policy(self):
        make_project("applewrite01", status="writing", template="apple-product")
        make_project("suvwrite0001", status="writing", template="luxury-suv")
        apple = {
            "title": "耳机",
            "script": "特写",
            "assets": [
                {"id": "E1", "title": "耳机", "prompt": "银色耳机", "subject": "耳机", "subject_kind": "prop"},
                {"id": "Note", "title": "说明书", "prompt": "一张纸", "subject": "说明书", "subject_kind": "prop"},
            ],
            "shots": [{"id": "S01", "duration": 5, "prompt": "停在微距", "images": ["E1"]}],
        }
        suv = {
            "title": "山路",
            "script": "出发",
            "assets": [
                {"id": "Car", "title": "SUV", "prompt": "银色 SUV", "subject": "SUV", "subject_kind": "prop"},
            ],
            "shots": [{"id": "S01", "duration": 5, "prompt": "驶出", "images": ["Car"]}],
        }
        with patch("end.server.call_deepseek", side_effect=[apple, suv]), patch(
            "end.server.start_render_thread"
        ):
            run_project("applewrite01")
            run_project("suvwrite0001")
        apple_spec = json.loads((project_dir("applewrite01") / "spec.json").read_text(encoding="utf-8"))
        product = {asset["view"] for asset in apple_spec["assets"] if asset["subject"] == "耳机"}
        self.assertEqual(product, {"hero", "three_quarter", "macro"})
        note = [asset for asset in apple_spec["assets"] if asset["subject"] == "说明书"]
        self.assertEqual(len(note), 1)
        self.assertNotIn(note[0]["view"], ("front", "side", "back"))
        suv_spec = json.loads((project_dir("suvwrite0001") / "spec.json").read_text(encoding="utf-8"))
        views = {asset["view"] for asset in suv_spec["assets"] if asset["subject"] == "SUV"}
        self.assertEqual(views, {"front", "side", "back"})


if __name__ == "__main__":
    unittest.main()
