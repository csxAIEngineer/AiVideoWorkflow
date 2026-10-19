"""Team templates: how prompts are assembled, and how projects remember the choice."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project
from end.server import public_view, rpc_config, rpc_create_project, run_project
from AI.skills.registry import (
    build_system_prompt,
    drill_system_prompt,
    json_shape,
    load_skill_pack,
    normalize_template,
    template_files,
    template_policy,
    template_render,
)
from AI.agent.layout import AgentLayout
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
        self.assertIn("luxury-suv/写提示词.md", pack)
        self.assertIn("读 `案例.md`", pack)
        self.assertIn("luxury-suv/案例.md", pack)
        self.assertIn("luxury-suv/生图.md", pack)
        self.assertNotIn("apple-product/", pack)
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
        self.assertIn("luxury-suv/导演.md", files)
        self.assertIn("luxury-suv/意图分析.md", files)
        self.assertIn("luxury-suv/意图补充.md", files)
        self.assertIn("luxury-suv/案例.md", files)
        self.assertNotIn("apple-product/写提示词.md", files)
        render = template_render("luxury-suv")
        self.assertEqual(render["image_from"], "luxury-suv/生图.md")
        self.assertIn("轮胎接地", render["image"])
        self.assertEqual(render["video_from"], "luxury-suv/写提示词.md")
        self.assertIn("轮子在转", render["video_lock"])
        self.assertNotIn("render-video-lock", render["video_lock"])

    def test_vehicle_skill_obeys_the_delivery_contract(self):
        text = (ROOT / "AI" / "skills" / "luxury-suv" / "写提示词.md").read_text(encoding="utf-8")
        for phrase in (
            "只返回一个 JSON",
            "画幅 9:16",
            "帧率 24",
            "4 到 15",
            "E4",
            "没有司机就写",
            "references",
            "不要写进 script",
            "这些句子作废",
            "从远景起幅",
            "已经在行驶",
            "全片轨迹",
            "不得调头",
            "机位侧",
        ):
            self.assertIn(phrase, text)
        for retired in (
            "Karakoram",
            "ready-to-use Seedance",
            "编号分镜表，一秒一条",
            "ask if unclear",
        ):
            self.assertNotIn(retired, text)
        prompt = build_system_prompt(load_skill_pack("luxury-suv"), "luxury-suv")
        self.assertIn("帧率 24", prompt)
        self.assertNotIn("技能里的一秒一镜", prompt)
        self.assertNotIn("Karakoram", prompt)

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
        self.assertNotIn("案例.md", pack)
        self.assertNotIn("轮胎接地", pack)
        self.assertIn("apple-product/生图.md", pack)
        self.assertNotIn("luxury-suv/", pack)
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

    def test_drama_drill_and_write_use_the_insert_team(self):
        drill = drill_system_prompt("drama-product-skill")
        self.assertIn("人物", drill)
        self.assertIn("植入", drill)
        self.assertIn("不要问司机", drill)
        self.assertIn("不要问路面", drill)
        self.assertNotIn("3. 司机", drill)
        self.assertIn("就按这个做", drill)
        pack = load_skill_pack("drama-product-skill")
        self.assertIn("drama-product-skill/写提示词.md", pack)
        self.assertIn("drama-product-skill/案例.md", pack)
        self.assertIn("drama-product-skill/生图.md", pack)
        self.assertIn("D1", pack)
        self.assertIn("钩子", pack)
        self.assertIn("没有口播", pack)
        self.assertNotIn("luxury-suv/", pack)
        self.assertNotIn("apple-product/", pack)
        self.assertNotIn("不得调头", pack)
        prompt = build_system_prompt(pack, "drama-product-skill")
        self.assertIn("hero", prompt)
        self.assertIn("three_quarter", prompt)
        self.assertIn("macro", prompt)
        self.assertIn("定妆", prompt)
        self.assertIn("人物锁定", prompt)
        self.assertNotIn("车辆锁定", prompt)
        self.assertNotIn("view 只能是 front", prompt)
        self.assertEqual(template_policy("drama-product-skill"), "product_hero")
        files = template_files("drama-product-skill")
        self.assertIn("drama-product-skill/导演.md", files)
        self.assertIn("drama-product-skill/意图分析.md", files)
        self.assertIn("drama-product-skill/意图补充.md", files)
        self.assertIn("drama-product-skill/案例.md", files)
        render = template_render("drama-product-skill")
        self.assertEqual(render["image_from"], "drama-product-skill/生图.md")
        self.assertIn("不要商标文字", render["image"])
        self.assertNotIn("同一张脸", render["image"])
        self.assertEqual(render["video_from"], "drama-product-skill/写提示词.md")
        self.assertIn("不要换脸", render["video_lock"])
        self.assertIn("不要换房间", render["video_next"])
        self.assertNotIn("同一件商品", render["video_lock"])
        self.assertNotIn("render-video-lock", render["video_lock"])
        shape = json_shape("drama-product-skill")
        self.assertIn("植入", shape)
        self.assertIn("==人名：原句==", shape)

    def test_missing_template_stays_on_luxury(self):
        self.assertEqual(normalize_template(None), "luxury-suv")
        self.assertEqual(normalize_template("nope"), "luxury-suv")
        self.assertEqual(normalize_template(""), "luxury-suv")
        pack = load_skill_pack()
        self.assertIn("luxury-suv/写提示词.md", pack)
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

    def test_each_template_owns_its_prompt_set(self):
        skills = ROOT / "AI" / "skills"
        names = {path.name for path in skills.iterdir() if path.is_dir()}
        self.assertEqual(names, {"apple-product", "drama-product-skill", "luxury-suv"})
        stages = ("导演.md", "意图分析.md", "意图补充.md", "成稿.md", "写提示词.md", "生图.md")
        for name in names:
            folder = skills / name
            for stage in stages:
                self.assertTrue((folder / stage).is_file(), "%s/%s" % (name, stage))
            manifest = json.loads((folder / "template.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], name)
            self.assertEqual(manifest["intent_analyze"], "意图分析.md")
            self.assertEqual(manifest["intent_fill"], "意图补充.md")
        self.assertTrue((skills / "luxury-suv" / "案例.md").is_file())
        self.assertFalse((skills / "apple-product" / "案例.md").exists())
        registry = (skills / "registry.py").read_text(encoding="utf-8")
        self.assertIn("intent_fill", registry)
        self.assertNotIn("就按这个做", registry)
        studio = (ROOT / "front" / "studio" / "chat.py").read_text(encoding="utf-8")
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
        self.assertIn("drama-product-skill", html)
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
