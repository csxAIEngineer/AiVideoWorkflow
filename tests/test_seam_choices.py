"""Seam lock choices: ① and ② are exclusive, ③ and ④ stay multi-select."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"

SOFT = "① 末帧定格承接"
HARD = "② 换机位硬切"
EMPTY = "③ 空镜过渡"
FX = "④ 技巧型转场"


def helpers():
    source = APP.read_text(encoding="utf-8")
    start = source.index("function isSeamSoftCut")
    end = source.index("function multiChoices")
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


class SeamChoiceTest(unittest.TestCase):
    def test_soft_and_hard_cuts_are_exclusive_and_the_others_stay_multi(self):
        report = run_js(
            r"""
            const SOFT = "① 末帧定格承接";
            const HARD = "② 换机位硬切";
            const EMPTY = "③ 空镜过渡";
            const FX = "④ 技巧型转场";
            const cases = [];
            function check(name, actual, expected) {
              cases.push({
                name: name,
                ok: JSON.stringify(actual) === JSON.stringify(expected),
                actual: actual,
                expected: expected,
              });
            }
            check("pick-soft", nextSeamChecks([], SOFT, true), [SOFT]);
            check("pick-hard-drops-soft", nextSeamChecks([SOFT, EMPTY, FX], HARD, true), [EMPTY, FX, HARD]);
            check("pick-soft-drops-hard", nextSeamChecks([HARD, EMPTY], SOFT, true), [EMPTY, SOFT]);
            check("add-empty-keeps-soft", nextSeamChecks([SOFT], EMPTY, true), [SOFT, EMPTY]);
            check("add-fx-keeps-hard", nextSeamChecks([HARD, EMPTY], FX, true), [HARD, EMPTY, FX]);
            check("clear-soft-keeps-others", nextSeamChecks([SOFT, EMPTY, FX], SOFT, false), [EMPTY, FX]);
            check("clear-empty-keeps-hard", nextSeamChecks([HARD, EMPTY], EMPTY, false), [HARD]);
            check("neither-cut-allowed", nextSeamChecks([EMPTY], EMPTY, true), [EMPTY]);
            check(
              "sanitize-keeps-later-cut",
              sanitizedSeamKept([SOFT, EMPTY, HARD, FX]),
              [EMPTY, HARD, FX]
            );
            check("sanitize-leaves-single-cut", sanitizedSeamKept([SOFT, FX]), [SOFT, FX]);
            process.stdout.write(JSON.stringify(cases));
            """
        )
        failed = [row for row in report if not row["ok"]]
        self.assertEqual(failed, [])
