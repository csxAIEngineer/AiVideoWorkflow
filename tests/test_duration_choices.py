"""Finished-film duration ask: preset seconds, and a custom numeric send."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"

DURATION_REPLY = "先写这支片子的成片时长，单位用秒。没写时长，不能判断要不要段缝。"
DELIVERY_OPTIONS = ["① 只要离线看板", "② 只要 markdown", "③ 两者都要"]


def helpers():
    source = APP.read_text(encoding="utf-8")
    start = source.index("const DURATION_PRESETS")
    end = source.index("function durationChoices")
    return source[start:end]


def run_js(body):
    script = helpers() + "\n" + body
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class DurationChoiceTest(unittest.TestCase):
    def test_presets_submit_plain_seconds_and_custom_rejects_bad_input(self):
        report = run_js(
            r"""
            const cases = [];
            function check(name, actual, expected) {
              cases.push({
                name: name,
                ok: JSON.stringify(actual) === JSON.stringify(expected),
                actual: actual,
                expected: expected,
              });
            }
            check("preset-10", durationPresetValue("10秒"), "10");
            check("preset-30", durationPresetValue("30秒"), "30");
            check("preset-60", durationPresetValue("60秒"), "60");
            check("preset-other", durationPresetValue("其他（自己输入）"), "");
            check("preset-delivery", durationPresetValue("① 只要离线看板"), "");
            check("answer-10", durationAnswerText("10"), "10");
            check("answer-trim", durationAnswerText(" 30 "), "30");
            check("answer-60", durationAnswerText("60"), "60");
            check("answer-decimal", durationAnswerText("45.5"), "45.5");
            check("answer-empty", durationAnswerText(""), "");
            check("answer-blank", durationAnswerText("   "), "");
            check("answer-words", durationAnswerText("abc"), "");
            check("answer-unit", durationAnswerText("10秒"), "");
            check("answer-zero", durationAnswerText("0"), "");
            check("answer-negative", durationAnswerText("-5"), "");
            check("answer-exponent", durationAnswerText("1e2"), "");
            const duration = {
              can_reply: true,
              status: "drilling",
              progress: {
                phase: "lock_wait",
                question: { id: "duration", kind: "number", reply: %s, options: [] },
              },
              messages: [
                { role: "assistant", content: "必须由用户选定。markdown 是正文，看板由它编译。", options: %s, choice_kind: "single" },
                { role: "user", content: "① 只要离线看板" },
                { role: "assistant", content: %s, options: [], choice_kind: "number" },
              ],
            };
            const delivery = {
              can_reply: true,
              progress: { question: { id: "delivery", kind: "single", options: %s } },
              messages: [{ role: "assistant", content: "必须由用户选定。markdown 是正文，看板由它编译。", options: %s, choice_kind: "single" }],
            };
            const seam = {
              can_reply: true,
              progress: { question: { id: "seam", kind: "multi", options: ["① 末帧定格承接"] } },
              messages: [{ role: "assistant", options: ["① 末帧定格承接"], choice_kind: "multi" }],
            };
            const kindOnly = {
              messages: [{ role: "assistant", content: %s, options: [], choice_kind: "number" }],
            };
            check("ask-duration", isDurationAsk(duration), true);
            check("ask-delivery", isDurationAsk(delivery), false);
            check("ask-seam", isDurationAsk(seam), false);
            check("ask-empty", isDurationAsk(null), false);
            check("ask-kind", isDurationAsk(kindOnly), true);
            process.stdout.write(JSON.stringify(cases));
            """
            % (
                json.dumps(DURATION_REPLY, ensure_ascii=False),
                json.dumps(DELIVERY_OPTIONS, ensure_ascii=False),
                json.dumps(DURATION_REPLY, ensure_ascii=False),
                json.dumps(DELIVERY_OPTIONS, ensure_ascii=False),
                json.dumps(DELIVERY_OPTIONS, ensure_ascii=False),
                json.dumps(DURATION_REPLY, ensure_ascii=False),
            )
        )
        failed = [row for row in report if not row["ok"]]
        self.assertEqual(failed, [])

    def test_duration_ui_uses_the_existing_send_path_and_leaves_other_choices(self):
        source = APP.read_text(encoding="utf-8")
        choices = source.split("function durationChoices", 1)[1].split("function choiceList", 1)[0]
        self.assertIn("其他（自己输入）", choices)
        self.assertIn("单位：秒", choices)
        self.assertIn("durationPresetValue(label)", choices)
        self.assertIn("durationAnswerText(input.value)", choices)
        self.assertIn("sendText(value)", choices)
        self.assertNotIn("sendText(label)", choices)
        listed = source.split("function choiceList", 1)[1].split("function messageBubble", 1)[0]
        self.assertIn("sendText(label)", listed)
        paint = source.split("const durationBox = ", 1)[1].split('if (current.status === "drilling"', 1)[0]
        self.assertIn("isDurationAsk(current)", paint)
        self.assertIn("!isDurationAsk(current)", paint)
        self.assertIn("choiceList(choice)", paint)
        self.assertIn("if (durationBox) wrap.append(durationBox);", paint)
        self.assertLess(paint.index("progressPanel(current)"), paint.index("wrap.append(durationBox)"))
        send = source.split("async function sendText", 1)[1].split("const materialFile", 1)[0]
        self.assertIn('rpc("projects.send_message"', send)
        self.assertIn("text: body", send)


if __name__ == "__main__":
    unittest.main()
