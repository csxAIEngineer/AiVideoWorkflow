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
    _piece,
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
        self.assertIn("what is different at the end", drill)
        self.assertIn("photos, clips, or sound", drill)
        self.assertIn("手怎么碰到这件商品", drill)
        pack = load_skill_pack("drama-product-skill")
        self.assertIn("drama-product-skill/写提示词.md", pack)
        self.assertIn("drama-product-skill/案例.md", pack)
        self.assertIn("drama-product-skill/生图.md", pack)
        self.assertIn("D1", pack)
        self.assertIn("钩子", pack)
        self.assertIn("没有口播", pack)
        self.assertIn("drama-product-skill/seedance-prompt.md", pack)
        self.assertNotIn("seedance-prompt-short", pack)
        self.assertNotIn("seedance-prompt-short", drill)
        self.assertFalse(
            (ROOT / "AI" / "skills" / "drama-product-skill" / "seedance-prompt-short.md").exists()
        )
        self.assertIn("drama-product-skill/seedance-camera.md", pack)
        self.assertIn("drama-product-skill/seedance-direct-for-the-model.md", pack)
        self.assertIn("缓慢推镜", pack)
        self.assertIn("不要输出 `@Image1`", pack)
        self.assertNotIn("drama-product-skill/seedance-interview.md", pack)
        self.assertNotIn("drama-product-skill/seedance-interview-short.md", pack)
        self.assertNotIn("luxury-suv/", pack)
        self.assertNotIn("apple-product/", pack)
        self.assertNotIn("不得调头", pack)
        prompt = build_system_prompt(pack, "drama-product-skill")
        self.assertIn("hero", prompt)
        self.assertIn("three_quarter", prompt)
        self.assertIn("macro", prompt)
        self.assertIn("定妆", prompt)
        self.assertIn("seedance-prompt", prompt)
        self.assertIn("尽可能详细", prompt)
        self.assertIn("不缩成一句", prompt)
        self.assertNotIn("seedance-prompt-short", prompt)
        self.assertNotIn("30-100", prompt)
        self.assertNotIn("30 到 100", prompt)
        self.assertNotIn("Compression Rules", prompt)
        self.assertNotIn("short shooting brief", prompt)
        self.assertIn("seedance-vocab-zh", prompt)
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

    def test_director_template_loads_one_style_from_the_brief(self):
        drill = drill_system_prompt("drama-product-skill2")
        self.assertIn("Drill me", drill)
        self.assertIn("一次一问", drill)
        self.assertIn("导演风格", drill)
        self.assertIn("画里是谁", drill)
        self.assertIn("地点和年代", drill)
        self.assertIn("商品在这场戏里怎么被用到", drill)
        self.assertIn("必须留下的外观", drill)
        self.assertIn("不要问司机", drill)
        self.assertIn("不要问路面", drill)
        self.assertIn("就按这个做", drill)
        self.assertIn("其他", drill)
        self.assertIn("phase", drill)
        self.assertIn("身份跟图", drill)
        self.assertNotIn("seedance-interview", drill)
        self.assertNotIn("3. 司机", drill)
        self.assertNotIn("09_wong_kar_wai.md", drill)
        self.assertNotIn("Failure Modes: Diagnosis and Repair", drill)
        pack = load_skill_pack("drama-product-skill2")
        self.assertIn("Story function first", pack)
        self.assertIn("product-and-macro", pack)
        self.assertIn("即梦/Seedance", pack)
        self.assertIn("hero", pack)
        self.assertIn("three_quarter", pack)
        self.assertIn("macro", pack)
        self.assertIn("drama-product-skill2/director/SKILL.md", pack)
        self.assertIn("drama-product-skill2/director/assets/director-book-template.md", pack)
        self.assertIn("drama-product-skill2/director/references/image-model-adapters.md", pack)
        self.assertIn("drama-product-skill2/写提示词.md", pack)
        self.assertIn("drama-product-skill2/生图.md", pack)
        self.assertNotIn("drama-product-skill2/案例.md", pack)
        self.assertTrue((ROOT / "AI" / "skills" / "drama-product-skill2" / "案例.md").is_file())
        styles = (
            "01_spielberg.md",
            "02_hitchcock.md",
            "03_kubrick.md",
            "04_kurosawa.md",
            "05_scorsese.md",
            "06_fellini.md",
            "07_bergman.md",
            "08_tarkovsky.md",
            "09_wong_kar_wai.md",
            "10_nolan.md",
            "11_villeneuve.md",
            "12_fincher.md",
            "13_refn.md",
            "14_bi_gan.md",
            "15_zhang_yimou.md",
            "16_hou_hsiao_hsien.md",
            "17_park_chan_wook.md",
            "18_malick.md",
            "19_michael_mann.md",
            "20_coen_brothers.md",
        )
        for name in styles:
            self.assertNotIn(
                "# drama-product-skill2/director/references/director_styles/%s" % name,
                pack,
            )
        self.assertNotIn(
            "drama-product-skill2/director/references/failure-modes.md",
            pack,
        )
        routed = load_skill_pack("drama-product-skill2", context="王家卫")
        self.assertTrue(routed.startswith(pack))
        extra = routed[len(pack) :]
        self.assertIn("09_wong_kar_wai.md", extra)
        self.assertEqual(extra.count("director_styles/"), 1)
        for name in styles:
            if name != "09_wong_kar_wai.md":
                self.assertNotIn(name, extra)
        self.assertNotIn("failure-modes.md", extra)
        english = load_skill_pack("drama-product-skill2", context="Wong Kar-wai")
        self.assertIn("09_wong_kar_wai.md", english[len(pack) :])
        self.assertNotIn("10_nolan.md", english[len(pack) :])
        glued = load_skill_pack("drama-product-skill2", context="Wong Kar-wai风格")
        self.assertIn("09_wong_kar_wai.md", glued[len(pack) :])
        nolan = load_skill_pack("drama-product-skill2", context="导演Nolan")
        self.assertIn("10_nolan.md", nolan[len(pack) :])
        self.assertNotIn("09_wong_kar_wai.md", nolan[len(pack) :])
        slideshow = load_skill_pack("drama-product-skill2", context="上一版PPT")
        self.assertIn("drama-product-skill2/director/references/failure-modes.md", slideshow)
        self.assertNotIn("10_nolan.md", load_skill_pack("drama-product-skill2", context="Nolanese"))
        self.assertNotIn(
            "drama-product-skill2/director/references/failure-modes.md",
            load_skill_pack("drama-product-skill2", context="APPT"),
        )
        earliest = load_skill_pack("drama-product-skill2", context="诺兰，再参考王家卫")
        self.assertIn("10_nolan.md", earliest)
        self.assertNotIn("09_wong_kar_wai.md", earliest)
        failed = load_skill_pack("drama-product-skill2", context="上一版换脸了")
        self.assertIn("drama-product-skill2/director/references/failure-modes.md", failed)
        self.assertNotIn("director_styles/", failed[len(pack) :])
        self.assertEqual(template_policy("drama-product-skill2"), "product_hero")
        render = template_render("drama-product-skill2")
        self.assertEqual(render["image_from"], "drama-product-skill2/生图.md")
        self.assertIn("不要商标文字", render["image"])
        self.assertEqual(render["video_from"], "drama-product-skill2/写提示词.md")
        self.assertIn("不要换脸", render["video_lock"])
        self.assertIn("不要换房间", render["video_next"])
        self.assertNotIn("render-video-lock", render["video_lock"])
        shape = json_shape("drama-product-skill2")
        self.assertIn("hero", shape)
        self.assertIn("three_quarter", shape)
        self.assertIn("macro", shape)
        self.assertIn("==人名：原句==", shape)
        prompt = build_system_prompt(pack, "drama-product-skill2")
        self.assertIn("Story function first", prompt)
        self.assertIn("即梦/Seedance", prompt)
        self.assertIn(
            "景别和焦段、身份、能看出动作的姿势、衣服和道具、地点、光源和光比、构图、质感、约束",
            prompt,
        )
        self.assertIn(
            "画面里必须在的物件、起动止、景别和机位高度、焦段、光的方向和光比、这一镜不一样的光、要接着的物件",
            prompt,
        )
        self.assertIn("不缩成四项", prompt)
        self.assertIn("一个主动作，一个机位运动，一个终点", prompt)
        self.assertIn("出片用它当首帧", prompt)
        self.assertNotIn("静帧只锁身份", prompt)

    def test_drill_style_follows_the_latest_user_line(self):
        styles = (
            "01_spielberg.md",
            "02_hitchcock.md",
            "03_kubrick.md",
            "04_kurosawa.md",
            "05_scorsese.md",
            "06_fellini.md",
            "07_bergman.md",
            "08_tarkovsky.md",
            "09_wong_kar_wai.md",
            "10_nolan.md",
            "11_villeneuve.md",
            "12_fincher.md",
            "13_refn.md",
            "14_bi_gan.md",
            "15_zhang_yimou.md",
            "16_hou_hsiao_hsien.md",
            "17_park_chan_wook.md",
            "18_malick.md",
            "19_michael_mann.md",
            "20_coen_brothers.md",
        )
        names = (
            "斯皮尔伯格",
            "希区柯克",
            "库布里克",
            "黑泽明",
            "斯科塞斯",
            "费里尼",
            "伯格曼",
            "塔可夫斯基",
            "王家卫",
            "诺兰",
            "维伦纽瓦",
            "芬奇",
            "雷弗恩",
            "毕赣",
            "张艺谋",
            "侯孝贤",
            "朴赞郁",
            "泰伦斯·马力克",
            "迈克尔·曼",
            "科恩兄弟",
        )
        bodies = {
            name: _piece("drama-product-skill2", "director/references/director_styles/" + name)
            for name in styles
        }
        base = drill_system_prompt("drama-product-skill2")
        self.assertIn("一次一问", base)
        self.assertIn("导演风格", base)
        self.assertIn("风格还没确认", base)
        self.assertNotIn("09_wong_kar_wai.md", base)
        for name in names:
            self.assertIn(name, base)
        for body in bodies.values():
            self.assertNotIn(body, base)
        self.assertNotIn("Failure Modes: Diagnosis and Repair", base)

        wong = bodies["09_wong_kar_wai.md"]
        nolan = bodies["10_nolan.md"]
        self.assertEqual(drill_system_prompt("drama-product-skill2", "王家卫"), base + "\n\n" + wong)
        self.assertEqual(
            drill_system_prompt("drama-product-skill2", "不要王家卫，改诺兰"),
            base + "\n\n" + nolan,
        )
        self.assertEqual(
            drill_system_prompt("drama-product-skill2", "不要诺兰，改王家卫"),
            base + "\n\n" + wong,
        )
        self.assertEqual(drill_system_prompt("drama-product-skill2", "Nolanese"), base)
        self.assertEqual(
            drill_system_prompt("drama-product-skill2", "王家卫，上一版换脸了"),
            base + "\n\n" + wong,
        )
        self.assertEqual(
            drill_system_prompt(
                "drama-product-skill2",
                [
                    {"role": "assistant", "content": "王家卫：雨夜楼梯口，耳机还在手里"},
                    {"role": "user", "content": "先讲谁在这场戏里"},
                ],
            ),
            base,
        )
        self.assertEqual(
            drill_system_prompt(
                "drama-product-skill2",
                [
                    {"role": "user", "content": "王家卫"},
                    {"role": "assistant", "content": "要不要改成诺兰"},
                ],
            ),
            base + "\n\n" + wong,
        )
        self.assertEqual(
            drill_system_prompt(
                "drama-product-skill2",
                [
                    {"role": "user", "content": "王家卫"},
                    {"role": "assistant", "content": "好"},
                    {"role": "user", "content": "不要王家卫，改诺兰"},
                ],
            ),
            base + "\n\n" + nolan,
        )
        for template in ("luxury-suv", "apple-product", "drama-product-skill"):
            plain = drill_system_prompt(template)
            self.assertEqual(drill_system_prompt(template, "王家卫"), plain)
            self.assertEqual(
                drill_system_prompt(
                    template,
                    [{"role": "user", "content": "不要王家卫，改诺兰"}],
                ),
                plain,
            )
            self.assertNotIn("导演风格 Skill", plain)

    def test_agent_and_studio_drill_pass_user_lines(self):
        from AI.agent.agent import Agent
        from AI.api import studio_drill

        prepared = Agent()._prepare_drill(
            {
                "model": None,
                "template": "drama-product-skill2",
                "messages": [
                    {"role": "assistant", "content": "王家卫：雨夜楼梯口"},
                    {"role": "user", "content": "不要王家卫，改诺兰"},
                ],
            }
        )
        system = prepared["request"]["messages"][0]["content"]
        self.assertIn("克里斯托弗·诺兰导演风格 Skill", system)
        self.assertNotIn("王家卫导演风格 Skill", system)
        self.assertEqual(prepared["system"], system)

        class Reply(object):
            def __init__(self, content):
                self.content = content
                self.reasoning_content = ""

        repaired = Agent()._read_drill(
            {
                "template": "drama-product-skill2",
                "messages": [{"role": "user", "content": "王家卫"}],
                "model_name": "deepseek-v4-pro",
                "drill_tokens": 4000,
                "system": "",
                "debug_dir": None,
                "result": Reply("不是 json"),
            }
        )
        repair_system = repaired["request"]["messages"][0]["content"]
        self.assertIn("王家卫导演风格 Skill", repair_system)
        self.assertNotIn("克里斯托弗·诺兰导演风格 Skill", repair_system)

        with patch("AI.api._provider_chat", return_value="{}") as chat:
            studio_drill(
                {
                    "template": "drama-product-skill2",
                    "history": [{"role": "assistant", "content": "建议王家卫"}],
                    "line": "改诺兰",
                    "provider": {},
                }
            )
        sent = chat.call_args[0][1]
        studio_system = sent[0]["content"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("克里斯托弗·诺兰导演风格 Skill", studio_system)
        self.assertNotIn("王家卫导演风格 Skill", studio_system)
        self.assertEqual(sent[-1], {"role": "user", "content": "改诺兰"})

    def test_template_paths_reject_parent_segments(self):
        from AI.skills.registry import _rel

        self.assertEqual(_rel("luxury-suv", "写提示词.md"), "luxury-suv/写提示词.md")
        self.assertEqual(
            _rel("drama-product-skill2", "director/SKILL.md"),
            "drama-product-skill2/director/SKILL.md",
        )
        for bad in (
            "../secret.md",
            "director/../../secret.md",
            "/etc/passwd",
            "C:/secret.md",
            "..\\secret.md",
        ):
            with self.assertRaises(RuntimeError):
                _rel("drama-product-skill2", bad)

    def test_skill_pack_cache_key_uses_the_routed_filenames(self):
        from end import ai_client

        calls = []

        def fake_call(method, params, timeout=60):
            calls.append(method)
            context = params.get("context") or ""
            if method == "skills.route":
                if "诺兰" in context:
                    extra = [
                        "drama-product-skill2/director/references/director_styles/10_nolan.md"
                    ]
                elif "王家卫" in context:
                    extra = [
                        "drama-product-skill2/director/references/director_styles/09_wong_kar_wai.md"
                    ]
                else:
                    extra = []
                return {"extra": extra}
            return {"pack": "pack:" + context, "extra": []}

        saved = dict(ai_client._MEMO)
        ai_client._MEMO.clear()
        try:
            with patch.object(ai_client, "call", side_effect=fake_call):
                first = ai_client.load_skill_pack("drama-product-skill2", "王家卫的雨")
                second = ai_client.load_skill_pack("drama-product-skill2", "另一条王家卫")
                third = ai_client.load_skill_pack("drama-product-skill2", "诺兰")
            self.assertEqual(first, second)
            self.assertEqual(first, "pack:王家卫的雨")
            self.assertEqual(third, "pack:诺兰")
            self.assertEqual(
                calls,
                ["skills.route", "skills.load", "skills.route", "skills.route", "skills.load"],
            )
        finally:
            ai_client._MEMO.clear()
            ai_client._MEMO.update(saved)

    def test_api_passes_the_brief_into_the_skill_pack(self):
        from AI import api

        with patch("AI.api.load_skill_pack", return_value="PACK") as loader, patch(
            "AI.api.call_deepseek", return_value={"title": "耳机"}
        ):
            api.write({"template": "drama-product-skill2", "brief": "王家卫"})
        loader.assert_called_once_with("drama-product-skill2", context="王家卫")
        with patch("AI.api.load_skill_pack", return_value="PACK") as loader, patch(
            "AI.api._provider_chat", return_value="{}"
        ):
            api.studio_write(
                {
                    "template": "drama-product-skill2",
                    "locked_brief": "王家卫",
                    "line": "写",
                    "history": [],
                    "provider": {},
                }
            )
        loader.assert_called_once_with("drama-product-skill2", context="王家卫")
        loaded = api.skills_load({"template": "drama-product-skill2", "context": "王家卫"})
        self.assertIn("09_wong_kar_wai.md", loaded["extra"][0])
        self.assertIn("09_wong_kar_wai.md", loaded["pack"])
        routed = api.skills_route({"template": "drama-product-skill2", "context": "王家卫"})
        self.assertEqual(routed["extra"], loaded["extra"])

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
        self.assertEqual(
            names,
            {"apple-product", "drama-product-skill", "drama-product-skill2", "luxury-suv"},
        )
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
        self.assertIn("drama-product-skill2", html)
        self.assertIn("短剧植入 · 导演", html)
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

    def test_writer_routes_the_pack_from_the_brief(self):
        make_project(
            "dirwrite0001",
            status="writing",
            template="drama-product-skill2",
            brief="王家卫，她把耳机递出去",
        )
        seen = {}

        def capture(template, context=""):
            seen["template"] = template
            seen["context"] = context
            return "pack"

        spec = {
            "title": "耳机",
            "script": "递出",
            "assets": [
                {
                    "id": "E1",
                    "title": "耳机",
                    "prompt": "黑色耳机",
                    "subject": "耳机",
                    "subject_kind": "prop",
                }
            ],
            "shots": [{"id": "S01", "duration": 5, "prompt": "递出", "images": ["E1"]}],
        }
        with patch("end.server.load_skill_pack", side_effect=capture), patch(
            "end.server.call_deepseek", return_value=spec
        ), patch("end.server.start_render_thread"):
            run_project("dirwrite0001")
        self.assertEqual(seen["template"], "drama-product-skill2")
        self.assertIn("王家卫", seen["context"])


if __name__ == "__main__":
    unittest.main()
