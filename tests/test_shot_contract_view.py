"""Front labels for the read-only shot contract inspector."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "front" / "shot-contract-view.js"


def view_eval(expression, shot=None, shots=None):
    script = "\n".join([
        "const view = require(%s);" % json.dumps(str(VIEW)),
        "const shot = %s;" % json.dumps(shot, ensure_ascii=False),
        "const shots = %s;" % json.dumps(shots or [], ensure_ascii=False),
        "process.stdout.write(JSON.stringify(%s));" % expression,
    ])
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ShotContractViewTest(unittest.TestCase):
    def test_mode_badges_and_inferred_written_value(self):
        shot = {
            "id": "S03",
            "video_mode": "text",
            "video_mode_resolved": {"value": "reference", "inferred": True, "written": ""},
            "end_image": "",
        }
        self.assertEqual(view_eval("view.modeBadge(shot)", shot), {"text": "参考·推断", "empty": False})
        self.assertEqual(
            view_eval("view.writtenBadge(shot)", shot)["text"],
            "写入 · 空（提交时按规则推断）",
        )
        self.assertEqual(view_eval("view.submitBadge(shot)", shot)["text"], "实际提交 · reference")
        self.assertEqual(view_eval("view.tailBadge(shot)", shot), {"text": "尾帧空", "empty": True})

        written = {
            "video_mode": "reference",
            "video_mode_resolved": {"value": "first_last_frame", "inferred": False, "written": "first_last_frame"},
            "end_image": "图3",
        }
        self.assertEqual(view_eval("view.modeBadge(shot)", written)["text"], "首尾帧")
        self.assertEqual(view_eval("view.writtenBadge(shot)", written)["text"], "写入 · first_last_frame")
        self.assertEqual(view_eval("view.tailBadge(shot)", written), {"text": "尾帧有", "empty": False})
        labels = {
            "text": "文本",
            "first_frame": "首帧",
            "first_last_frame": "首尾帧",
            "reference": "参考",
        }
        for value, label in labels.items():
            item = {"video_mode_resolved": {"value": value, "inferred": False, "written": value}}
            self.assertEqual(view_eval("view.modeBadge(shot)", item)["text"], label)

    def test_empty_contract_stays_visible(self):
        self.assertEqual(view_eval("view.feedHeading(shot)", None), "投喂稿")
        rows = view_eval("view.feedRows(shot)", None)
        self.assertEqual([row["key"] for row in rows], [
            "title",
            "subject_definitions",
            "detailed_description",
            "overall_soundscape",
            "non_diegetic_music",
        ])
        self.assertTrue(all(row["text"] == "空" and row["empty"] for row in rows))
        self.assertEqual(view_eval("view.imagesSourceText(shot)", None), "空")
        self.assertEqual(view_eval("view.durationText(shot)", {}), "空")
        self.assertEqual(view_eval("view.submitBadge(shot)", {})["text"], "实际提交 · 空")
        self.assertEqual(view_eval("view.writtenBadge(shot)", {})["text"], "写入 · 空")
        note = view_eval("view.contractNote()")
        self.assertIn("不是分镜模板", note)
        self.assertIn("空", note)

    def test_feed_and_image_source_use_structured_fields(self):
        shot = {
            "prompt": "do not reparse this",
            "images_source": {"value": "storyboard_index_fallback", "index": 0},
            "feed": {
                "heading": "## 【封装单元 3/5】",
                "title": "夜间案头 · 对屏工作",
                "unit": "3/5",
                "subject_definitions": "<Subject 1> 黑发",
                "detailed_description": "夜晚室内",
                "overall_soundscape": "键盘轻敲",
                "non_diegetic_music": "",
            },
        }
        self.assertEqual(view_eval("view.feedHeading(shot)", shot), "投喂稿 · 封装单元 3/5")
        rows = {row["key"]: row for row in view_eval("view.feedRows(shot)", shot)}
        self.assertEqual(rows["title"]["text"], "夜间案头 · 对屏工作")
        self.assertEqual(rows["non_diegetic_music"]["text"], "空")
        self.assertFalse(rows["subject_definitions"]["empty"])
        self.assertNotIn("do not reparse", view_eval("view.feedHeading(shot)", shot))
        self.assertEqual(
            view_eval("view.imagesSourceText(shot.images_source)", shot),
            "分镜序号兜底 · 0",
        )
        self.assertEqual(
            view_eval("view.imagesSourceText(shot)", {"value": "unit_match", "index": None}),
            "投喂单元匹配",
        )
        self.assertEqual(
            view_eval("view.imagesSourceText(shot)", {"value": "storyboard", "index": None}),
            "分镜模板",
        )

    def test_reference_note_is_a_prediction(self):
        shots = [{"id": "S02"}, {"id": "S03"}]
        pending = {
            "id": "S03",
            "video_mode_resolved": {"value": "reference", "inferred": True, "written": ""},
            "reference": [
                {"id": "图1", "url": "https://cdn.example/fig1.png", "source": "bound_image", "pending": False, "available": True},
                {"id": "S02_tail", "url": None, "source": "previous_tail", "pending": True, "available": False},
            ],
        }
        note = view_eval("view.referenceNote(shot, shots)", pending, shots)
        self.assertIn("按提交规则预测", note)
        self.assertIn("S02", note)
        self.assertIn("上镜尾帧还没生成", note)
        self.assertNotIn("已经实际提交的存档列表", note)
        ready = {
            "id": "S03",
            "video_mode_resolved": {"value": "reference", "inferred": False, "written": "reference"},
            "reference": [
                {"id": "S02_tail", "url": "/media/projects/p/images/S02_tail.jpg?v=1", "source": "previous_tail", "pending": False, "available": True},
            ],
        }
        ready_note = view_eval("view.referenceNote(shot, shots)", ready, shots)
        self.assertIn("可以和绑定资产图不同", ready_note)
        self.assertNotIn("还没生成", ready_note)
        text_shot = {
            "id": "S05",
            "video_mode_resolved": {"value": "text", "inferred": False, "written": "text"},
            "reference": [],
        }
        text_note = view_eval("view.referenceNote(shot, shots)", text_shot, shots)
        self.assertIn("文本模式不会附带", text_note)
        self.assertNotIn("已经实际提交的存档列表", text_note)

    def test_front_wires_the_shared_inspector(self):
        app = (ROOT / "front" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "front" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "front" / "styles.css").read_text(encoding="utf-8")
        view = VIEW.read_text(encoding="utf-8")
        self.assertLess(html.index("shot-contract-view.js"), html.index("/app.js"))
        self.assertIn('aria-label="分镜详情"', html)
        self.assertIn("ShotContractView.writtenBadge", app)
        self.assertIn("ShotContractView.referenceNote", app)
        self.assertIn("实际参考图", app)
        self.assertIn("上镜尾帧", app)
        self.assertIn("待生成", app)
        self.assertNotRegex(app, r"shot\.video_mode(?!_resolved)")
        self.assertNotRegex(view, r"shot\.video_mode(?!_resolved)")
        self.assertIn("video_mode_resolved", view)
        self.assertNotIn("已经实际提交的存档列表", app)
        self.assertNotIn("已经实际提交的存档列表", view)
        self.assertIn(".shot-contract", css)
        self.assertIn("尾帧空", view)
        self.assertIn("尾帧有", view)


if __name__ == "__main__":
    unittest.main()
