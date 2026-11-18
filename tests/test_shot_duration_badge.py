"""Shot card duration badges follow the official feed-unit duration line."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"


def helpers():
    source = APP.read_text(encoding="utf-8")
    start = source.index("function officialShotDuration")
    end = source.index("function durationText(task)")
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


class ShotDurationBadgeTest(unittest.TestCase):
    def test_badge_prefers_feed_unit_duration_line(self):
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
            const unit = {
              id: "S01",
              duration: 15,
              prompt: "## 【封装单元 1/2】\nduration: 30\n资产：图1\n",
            };
            check("unit-over-clamp", officialShotDuration(unit), 30);
            check("unit-label", shotDurationLabel(unit), "30 秒");
            check(
              "crlf",
              officialShotDuration({ duration: 15, prompt: "title\r\nduration: 30\r\n资产：图1\r\n" }),
              30
            );
            check("fallback-payload", officialShotDuration({ id: "S01", duration: 10, prompt: "中景，雨巷。" }), 10);
            check("fallback-label", shotDurationLabel({ id: "S01", duration: 10 }), "10 秒");
            check("empty", officialShotDuration({ id: "S01", prompt: "" }), null);
            check("empty-label", shotDurationLabel({ id: "S01" }, "空"), "空");
            check("ignore-inline", officialShotDuration({ duration: 8, prompt: "keep duration: 30 in a sentence" }), 8);
            check("zero-line-fallback", officialShotDuration({ duration: 12, prompt: "duration: 0\n" }), 12);
            const current = {
              shots: [
                { id: "S01", duration: 15, prompt: "duration: 30\n" },
                { id: "S02", duration: 15, prompt: "duration: 30\n" },
              ],
            };
            check("task-video", officialTaskSeconds({ kind: "video", id: "S01", duration: 15 }), 30);
            check("task-join", officialJoinSeconds({ kind: "join", duration: 30 }), 60);
            console.log(JSON.stringify({ cases: cases }));
            """
        )
        failed = [item for item in report["cases"] if not item["ok"]]
        self.assertEqual(failed, [], msg=json.dumps(failed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
