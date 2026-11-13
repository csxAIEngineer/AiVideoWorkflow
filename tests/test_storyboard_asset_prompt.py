"""Storyboard prompts must ask for bare locked ids. The old parenthetical line still fails."""

import json
import tempfile
import unittest
from pathlib import Path

from AI.skills.registry import json_shape, merge_prompt
from end.stage_runner import _produce


def setUpModule():
    from tests.worker_model import install_worker_bridge

    install_worker_bridge()


def tearDownModule():
    from tests.worker_model import uninstall_worker_bridge

    uninstall_worker_bridge()
from end.storyboard_contract import (
    ASSET_FIELD_EXAMPLE,
    ASSET_FIELD_RULES,
    _assets,
    locked_asset_catalog,
    project_shots,
)

# Ids and titles taken from e88b583287a5. The parenthetical asset line is that task's S01.
COLLISION = [
    {"id": "图1", "title": "古塔顶层内景（场景母版）"},
    {"id": "图1_2", "title": "场景九宫格（古塔）"},
    {"id": "图1_3", "title": "阿青丝（角色立绘 · 2D）"},
    {"id": "图2", "title": "古塔顶层内景（场景九宫格）"},
    {"id": "图2_2", "title": "场景九宫格（水墨留白空间·品牌落点）"},
    {"id": "图2_3", "title": "柳公子（角色立绘 · 2D）"},
    {"id": "图3", "title": "阿青丝（人物立绘 · 四格纯图板）"},
    {"id": "图3_2", "title": "100年润发冷萃国风洗发露（道具图 · 2D）"},
    {"id": "图5", "title": "100年润发冷萃国风洗发露（道具设定图）"},
]
OLD_S01 = "\n".join((
    "## S01",
    "动作：阿青丝背对镜头立于古塔顶层木窗前。",
    "景别机位：全景，固定机位",
    "时长：5",
    "资产：图1（古塔顶层内景），图1（阿青丝），图2（柳公子）",
))
KNOWN = {item["id"] for item in COLLISION}


def _brief(folder, draft=""):
    seen = []

    def writer(brief, *_args, **_kwargs):
        seen.append(brief)
        return {"storyboard": "## S01\n动作：停\n景别机位：中景\n时长：5\n资产：图1"}

    if draft:
        internal = folder / "_内部_"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "分镜.md").write_text(draft, encoding="utf-8")
    _produce(
        {
            "brief": "阿青丝发丝救援",
            "model": "deepseek-v4-flash-0731",
            "template": "short-drama-director",
            "progress": {"pipeline": {"action": "write", "repair": ""}},
        },
        folder,
        "storyboard",
        {},
        "",
        writer,
        lambda *_args, **_kwargs: "",
    )
    return seen[0]


class StoryboardAssetPromptTest(unittest.TestCase):
    def test_example_parses_and_the_old_line_still_does_not(self):
        body = ASSET_FIELD_EXAMPLE.split("：", 1)[1]
        self.assertEqual(_assets(body, KNOWN), ["图1", "图1_3", "图2_3"])
        _shots, report = project_shots(OLD_S01, KNOWN)
        self.assertIn("S01 缺资产", report)
        self.assertEqual(_shots, [])

    def test_catalog_keeps_figure_one_distinct_from_its_suffix(self):
        text = locked_asset_catalog(COLLISION + [{"id": "图1", "title": "重复"}])
        self.assertEqual(text.count("\n图1 = "), 1)
        self.assertIn("图1 = 古塔顶层内景（场景母版）", text)
        self.assertIn("图1_3 = 阿青丝（角色立绘 · 2D）", text)
        self.assertNotIn("图1_3 = 古塔", text)

    def test_first_write_and_explicit_fill_send_the_same_constraint(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "spec.json").write_text(
            json.dumps({"assets": COLLISION}, ensure_ascii=False),
            encoding="utf-8",
        )
        first = _brief(folder)
        again = _brief(folder, OLD_S01)
        for brief in (first, again):
            self.assertIn("任务：分镜", brief)
            self.assertIn(ASSET_FIELD_EXAMPLE, brief)
            self.assertIn(ASSET_FIELD_RULES, brief)
            self.assertIn("图1 = 古塔顶层内景（场景母版）", brief)
            self.assertIn("图1_3 = 阿青丝（角色立绘 · 2D）", brief)
            self.assertIn("图2_3 = 柳公子（角色立绘 · 2D）", brief)
            shape = json_shape(None, brief)
            merged = merge_prompt("storyboard", brief)
            for prompt in (shape, merged):
                self.assertIn(ASSET_FIELD_EXAMPLE, prompt)
                self.assertIn("不要把图1_3写成图1", prompt)
                self.assertNotIn("多个用顿号分开", prompt)
                self.assertNotIn("资产：已经锁定的资产 id", prompt)
            self.assertNotIn("图1（阿青丝）", brief)


if __name__ == "__main__":
    unittest.main()
