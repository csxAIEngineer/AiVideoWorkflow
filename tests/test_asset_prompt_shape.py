"""Asset blocks survive when the model returns a list instead of one string."""

import tempfile
import unittest
from pathlib import Path

from end.repair_units import direction_labels, fill_block_gaps, units_for
from end.stage_runner import _asset_pieces, _coerce_asset_prompts, _produce


def _pass_role(number, title, marker):
    return "\n".join((
        "#### 图%d · %s" % (number, title),
        marker,
        "16:9",
        "全图共 4 格",
        "五官 10 项锁定",
        "声纹",
        "≤8 词",
        "fifth panel",
        "different face",
    ))


class AssetPromptShapeTest(unittest.TestCase):
    def test_list_of_prompts_becomes_the_markdown_file_body(self):
        result = _coerce_asset_prompts(
            {
                "image_prompts": [
                    "#### 图1 · 角色\n16:9 四格",
                    "#### 图2 · 客厅\n16:9 九宫格",
                ],
                "partials": [],
            }
        )
        self.assertEqual(len(_asset_pieces(result["image_prompts"])), 2)

    def test_specialist_notes_fill_in_when_the_field_has_no_blocks(self):
        result = _coerce_asset_prompts(
            {
                "image_prompts": {"status": "completed"},
                "partials": [
                    {"skill": "a", "text": "#### 图1 · 胶囊\n四格道具"},
                ],
            }
        )
        self.assertIn("图1", result["image_prompts"])
        self.assertEqual(len(_asset_pieces(result["image_prompts"])), 1)

    def test_repair_rewrites_only_the_failed_figure(self):
        report = "\n".join(
            (
                "[+] 图1   角色    PASS  林浅",
                "[+] 图8   角色    PASS  林浅（觉醒态）",
                "[x] 图8   场景母版 FAIL  预演（场景母版）",
                "[-] FAIL: A1 图8 出现跨条引用「同上」",
            )
        )
        draft = "\n\n".join(
            (
                _pass_role(1, "林浅", "通过块不要动"),
                _pass_role(8, "林浅（觉醒态）", "觉醒态不要动"),
                "#### 图8 · 预演（场景母版）\n旧失败块\n同上\n16:9",
            )
        )
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text(draft + "\n", encoding="utf-8")
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图8 · 预演（场景母版）\n改过的块\n"}

        project = {
            "template": "short-drama-director",
            "model": "qwen3.8-flash",
            "brief": "",
            "progress": {"pipeline": {"repair": report}},
        }
        result = _produce(project, folder, "asset", {}, "", writer, lambda *_args, **_kwargs: "pack")
        self.assertEqual(len(calls), 1)
        self.assertIn("旧失败块", calls[0])
        self.assertNotIn("通过块不要动", calls[0])
        self.assertNotIn("觉醒态不要动", calls[0])
        self.assertNotIn("任务：资产锁定", calls[0])
        pieces = _asset_pieces(result["image_prompts"])
        self.assertEqual([number for number, _body in pieces], [1, 8, 8])
        self.assertIn("通过块不要动", pieces[0][1])
        self.assertIn("觉醒态不要动", pieces[1][1])
        self.assertIn("改过的块", pieces[2][1])
        self.assertNotIn("被整份改掉", result["image_prompts"])
        self.assertNotIn("也被改掉", result["image_prompts"])
        self.assertIn("同上", calls[0])
        self.assertNotIn("觉醒态不要动", calls[0])

    def test_local_phrase_fill_skips_the_model(self):
        report = "\n".join(
            (
                "[x] 图1   角色    FAIL  女主林浅",
                "[x] 图2   道具    FAIL  胶囊（道具）",
            )
        )
        draft = "\n\n".join(
            (
                "#### 图1 · 女主林浅\n通过块不要动",
                "#### 图2 · 胶囊（道具）\n旧药盒",
            )
        )
        folder = Path(tempfile.mkdtemp())
        internal = folder / "_内部_"
        internal.mkdir()
        (internal / "assets.md").write_text(draft + "\n", encoding="utf-8")
        calls = []

        def writer(brief, skill_pack, debug_dir=None, model=None, template=None):
            calls.append(brief)
            return {"image_prompts": "#### 图1 · 女主林浅\n不该叫模型\n"}

        project = {
            "template": "short-drama-director",
            "model": "qwen3.8-flash",
            "brief": "",
            "progress": {"pipeline": {"repair": report}},
        }
        result = _produce(project, folder, "asset", {}, "", writer, lambda *_args, **_kwargs: "pack")
        self.assertEqual(calls, [])
        text = result["image_prompts"]
        self.assertIn("通过块不要动", text)
        self.assertIn("旧药盒", text)
        self.assertIn("五官 10 项锁定", text)
        self.assertIn("道具设定图", text)
        self.assertIn("different face", text)
        self.assertNotIn("不该叫模型", text)

    def test_same_figure_number_keeps_its_own_fail_lines(self):
        report = "\n".join(
            (
                "[x] 图5   场景母版 FAIL  客厅甲",
                "[x] 图5   场景母版 FAIL  客厅乙",
            )
        )
        draft = "\n\n".join(
            (
                "#### 图5 · 客厅甲（场景母版）\n甲块正文\n同上\n16:9",
                "#### 图5 · 客厅乙（场景母版）\n乙块正文\n同规格\n16:9",
            )
        )
        units = units_for("asset", report, asset=draft)
        self.assertEqual([unit["id"] for unit in units], ["图-0", "图-1"])
        self.assertIn("同上", units[0]["suggestion"])
        self.assertNotIn("同规格", units[0]["suggestion"])
        self.assertNotIn("乙块正文", units[0]["suggestion"])
        self.assertIn("同规格", units[1]["suggestion"])
        self.assertNotIn("同上", units[1]["suggestion"])
        self.assertNotIn("甲块正文", units[1]["suggestion"])

    def test_mode_sentences_are_not_chosen_for_the_user(self):
        body = "\n".join((
            "#### 图5 · 客厅九宫格机位调度图",
            "16:9",
            "【场景定位】客厅",
            "【九宫格内容分配】逐格",
            "【参考场景锁定模式】模式B，按子区域",
            "全图共 9 格",
            "格内编号 1~9，图外对照表",
        ))
        filled = fill_block_gaps(body)
        self.assertNotIn("仅允许摄影机移动", filled)
        self.assertNotIn("允许按子区域切换机位", filled)
        self.assertEqual(
            direction_labels(body),
            ["仅允许摄影机移动", "允许按子区域切换机位", "交给模型改这一块"],
        )
        cross = "#### 图8 · 预演（场景母版）\n旧失败块\n同上\n16:9"
        self.assertEqual(direction_labels(cross), ["交给模型改这一块"])
        main_figure = _pass_role(1, "林浅", "主图在正面格旁边")
        self.assertEqual(direction_labels(main_figure), ["交给模型改这一块"])
        unknown = "#### 图1 · 某物\n16:9\n四格画面"
        self.assertEqual(direction_labels(unknown), ["交给模型改这一块"])


if __name__ == "__main__":
    unittest.main()
