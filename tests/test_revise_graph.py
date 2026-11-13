"""One-block revise: a feeling edits this asset or this shot, and nothing else."""

import json
import unittest

from tests.offline_guard import install, uninstall


def setUpModule():
    install()


def tearDownModule():
    uninstall()

from AI.qwen_api.revise_graph import block_system, build_block_graph, describe_block_graph, run_block


def _brief(kind, current, suggestion):
    """Same labels the revise user message uses: current block, then 修改建议."""
    title = "当前图块：" if kind == "asset" else "当前封装单元："
    return "\n".join((
        title,
        str(current or "").strip(),
        "修改建议：",
        str(suggestion or "").strip(),
        "只按修改建议改这一块。没提到的句子保持。不要写其他图或其他镜头。",
    ))


_ASSET = "#### 图1 · 女主林浅\n雨夜车站，林浅穿黑色大衣，引用图1。"
_COLD = "#### 图1 · 女主林浅\n雨夜车站更冷，林浅穿黑色大衣，引用图1。"
_UNIT = "\n".join((
    "## 【投喂单元 1/2 · 段1】",
    "[Shot 1] A woman waits, 图1, S01.",
    "She says 等我。",
))
_CLOSER = "\n".join((
    "## 【投喂单元 1/2 · 段1】",
    "[Shot 1] A woman waits closer, 图1, S01.",
    "She says 等我。",
))


class _Reply(object):
    def __init__(self, content):
        self.content = content


class _Model(object):
    def __init__(self, text="", empty_first=False, always_empty=False):
        self.text = text
        self.empty_first = empty_first
        self.always_empty = always_empty
        self.calls = []
        self.systems = []
        self.users = []

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        self.calls.append((max_tokens, timeout))
        self.systems.append(messages[0]["content"])
        self.users.append(messages[-1]["content"])
        if self.always_empty or (self.empty_first and len(self.calls) == 1):
            return _Reply("")
        return _Reply(json.dumps({"text": self.text}, ensure_ascii=False))


class ReviseGraphSystemTest(unittest.TestCase):
    def test_systems_keep_the_block_and_forbid_new_ids(self):
        asset = block_system("asset")
        shot = block_system("shot")
        for text in (asset, shot):
            self.assertIn("感觉", text)
            self.assertIn("只改当前这一块", text)
            self.assertIn("没提到的句子、人名、顺序和未点名的内容保持。", text)
            self.assertIn("禁止新增或改写当前块里未出现的资产 ID 和镜头引用。", text)
            self.assertIn("改完的这一块放在 text。", text)
        self.assertIn("这一张图", asset)
        self.assertIn("不要写其他图", asset)
        self.assertNotIn("封装单元标题", asset)
        self.assertIn("这一个镜头对应的封装单元", shot)
        self.assertIn("不要整份改写 feed", shot)
        self.assertIn("不要写其他镜头", shot)
        self.assertIn("保住已有的封装单元标题行", shot)
        self.assertIn("不要改镜号，不要再写第二个单元。", shot)

    def test_script_and_storyboard_stay_closed(self):
        for kind in ("script", "storyboard"):
            with self.assertRaises(ValueError):
                block_system(kind)

    def test_describe_is_still_one_block_then_merge(self):
        model = _Model()
        asset = describe_block_graph(build_block_graph("asset", model), "asset")
        shot = describe_block_graph(build_block_graph("shot", model), "shot")
        self.assertIn("revise  图块", asset)
        self.assertIn("revise  封装单元", shot)
        for text in (asset, shot):
            self.assertIn("merge  汇总", text)
            self.assertIn("START -> revise", text)
            self.assertIn("revise -> merge", text)
            self.assertIn("merge -> END", text)
        self.assertEqual(model.calls, [])


class ReviseGraphRunTest(unittest.TestCase):
    def test_feeling_rewrites_the_asset_block_and_returns_that_text(self):
        brief = _brief("asset", _ASSET, "更冷一点")
        model = _Model(_COLD)
        result = run_block("asset", brief, model, model_name="qwen3.8-flash")
        self.assertEqual(result, _COLD)
        self.assertFalse(result.startswith("{"))
        self.assertNotIn("@@", result)
        self.assertEqual(model.systems[0], block_system("asset"))
        self.assertIn("更冷一点", model.users[0])
        self.assertIn("林浅", model.users[0])
        self.assertEqual(model.calls, [(8000, 300)])

    def test_invented_asset_id_is_rejected_even_if_the_feeling_names_it(self):
        brief = _brief("asset", _ASSET, "改成 A3 那种冷色")
        kept = _Model(_COLD)
        self.assertEqual(run_block("asset", brief, kept), _COLD)
        invented = _Model(_COLD.replace("图1。", "图1，再加上 A3。"))
        with self.assertRaises(RuntimeError) as raised:
            run_block("asset", brief, invented)
        self.assertIn("超出了当前这一块", str(raised.exception))
        self.assertEqual(len(invented.calls), 1)

    def test_a_color_code_is_not_an_asset_id(self):
        brief = _brief("asset", "雨夜，图1。", "更冷一点")
        colder = "更冷的雨夜，色值 #A3B2C1，图1。"
        self.assertEqual(run_block("asset", brief, _Model(colder)), colder)

    def test_a_second_figure_is_rejected(self):
        brief = _brief("asset", _ASSET, "更冷一点")
        spread = _COLD + "\n\n#### 图2 · 雨\n旧雨"
        with self.assertRaises(RuntimeError) as raised:
            run_block("asset", brief, _Model(spread))
        self.assertIn("超出了当前这一块", str(raised.exception))

    def test_feeling_rewrites_one_feed_unit_and_keeps_its_head(self):
        brief = _brief("shot", _UNIT, "镜头再近")
        model = _Model(_CLOSER)
        result = run_block("shot", brief, model, model_name="qwen3.8-flash")
        self.assertEqual(result, _CLOSER)
        self.assertTrue(result.startswith("## 【投喂单元 1/2 · 段1】"))
        same_head = _CLOSER.replace("## 【投喂单元 1/2 · 段1】", "### 【投喂单元 1/2 · 段1】", 1)
        self.assertEqual(run_block("shot", brief, _Model(same_head)), same_head)
        self.assertEqual(model.systems[0], block_system("shot"))
        self.assertIn("镜头再近", model.users[0])
        self.assertNotIn("投喂单元 2/2", result)

    def test_shot_rewrite_that_leaves_the_unit_is_rejected(self):
        brief = _brief("shot", _UNIT, "镜头再近")
        cases = (
            _CLOSER.replace("1/2", "2/2"),
            _CLOSER + "\n\n## 【投喂单元 2/2】\n另一镜",
            _CLOSER.replace("[Shot 1]", "[Shot 2]"),
            _CLOSER.replace("S01", "S02"),
            "镜头更近，图1，S01。",
            "先写一句。\n" + _CLOSER,
            "## 【投喂单元 1/2 · 段1】\n" + _CLOSER,
        )
        for text in cases:
            with self.assertRaises(RuntimeError) as raised:
                run_block("shot", brief, _Model(text))
            self.assertIn("超出了当前这一块", str(raised.exception))

    def test_a_headerless_shot_does_not_grow_a_feed(self):
        current = "第一镜原文，林浅回头，图1。"
        brief = _brief("shot", current, "她不要回头")
        rewritten = "第一镜原文，林浅不回头，图1。"
        self.assertEqual(run_block("shot", brief, _Model(rewritten)), rewritten)
        invented = "## 【投喂单元 1/2】\n" + rewritten
        with self.assertRaises(RuntimeError):
            run_block("shot", brief, _Model(invented))

    def test_unlabeled_brief_still_rejects_a_new_id(self):
        brief = "雨夜，图1。更冷一点"
        self.assertEqual(run_block("asset", brief, _Model("雨夜更冷，图1。")), "雨夜更冷，图1。")
        with self.assertRaises(RuntimeError):
            run_block("asset", brief, _Model("雨夜更冷，图1，A3。"))

    def test_empty_output_retries_once_then_fails(self):
        model = _Model(always_empty=True)
        with self.assertRaises(RuntimeError) as raised:
            run_block("asset", _brief("asset", _ASSET, "更冷一点"), model)
        self.assertIn("没有正文", str(raised.exception))
        self.assertEqual(model.calls, [(8000, 300), (8000, 300)])
        self.assertIn("没有正文", model.users[1])

    def test_empty_output_then_a_block_is_kept(self):
        model = _Model(_COLD, empty_first=True)
        result = run_block("asset", _brief("asset", _ASSET, "更冷一点"), model)
        self.assertEqual(result, _COLD)
        self.assertEqual(len(model.calls), 2)

    def test_other_kinds_never_call_the_model(self):
        model = _Model("新剧本")
        with self.assertRaises(ValueError):
            run_block("script", "旧剧本", model)
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
