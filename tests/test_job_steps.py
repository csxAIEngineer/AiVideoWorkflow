"""Task progress steps no longer include the retired offline board."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
INDEX = ROOT / "front" / "index.html"
FIXTURE = ROOT / "front" / "fixtures" / "chat-column-scroll.html"

EXPECTED_STEPS = ["立项锁定", "赛道分流", "剧本", "资产锁定", "分镜", "提示词封装", "投喂机检", "分镜衔接"]


def helpers():
    source = APP.read_text(encoding="utf-8")
    start = source.index("function writeStep(project)")
    end = source.index("function promptsReady(project)")
    job_start = source.index("function jobIndex(project)")
    job_end = source.index("function formatSpan(ms)")
    pieces = [
        "const JOB_STEPS = " + json.dumps(EXPECTED_STEPS, ensure_ascii=False) + ";",
        "function isIntentDebug(project) { return false; }",
        "function isPromptDebug(project) { return false; }",
        "function isDebugProject(project) { return false; }",
        "function autoStageKey(project) { return \"\"; }",
        source[start:end],
        source[job_start:job_end],
    ]
    return "\n".join(pieces)


def run_js(body):
    result = subprocess.run(
        ["node", "-e", helpers() + "\n" + body],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class JobStepsTest(unittest.TestCase):
    def test_progress_labels_stop_at_seam_and_ignore_board_payload(self):
        app = APP.read_text(encoding="utf-8")
        self.assertIn(
            '["立项锁定", "赛道分流", "剧本", "资产锁定", "分镜", "提示词封装", "投喂机检", "分镜衔接"]',
            app,
        )
        self.assertNotIn("离线看板", app)
        write_step = app.split("function writeStep(project)", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("看板", write_step)
        job_index = app.split("function jobIndex(project)", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("has_board", job_index)
        self.assertNotIn("看板", job_index)
        self.assertIn("/app.js?v=96", INDEX.read_text(encoding="utf-8"))
        self.assertNotIn("离线看板", FIXTURE.read_text(encoding="utf-8"))

        report = run_js(
            """
            const cases = [];
            function check(name, actual, expected) {
              cases.push({
                name: name,
                ok: JSON.stringify(actual) === JSON.stringify(expected),
                actual: actual,
                expected: expected,
              });
            }
            check("labels", JOB_STEPS.slice(), %s);
            check("no-board-label", JOB_STEPS.indexOf("离线看板"), -1);
            check("script-stage", writeStep({ stage: "正在写剧本" }), 2);
            check("board-stage", writeStep({ stage: "离线看板" }), 1);
            check("board-message", writeStep({ progress: { message: "看板" } }), 1);
            check("seam-stage", writeStep({ stage: "分镜衔接" }), 7);
            const writing = { status: "writing", stage: "正在写剧本", progress: { phase: "writing", message: "正在写剧本" }, has_board: true };
            check("writing-ignores-has-board", jobIndex(writing), 2);
            const ready = { status: "done", progress: { phase: "prompts_ready" }, has_board: false };
            check("prompts-ready", jobIndex(ready), JOB_STEPS.length);
            console.log(JSON.stringify({ cases: cases }));
            """ % json.dumps(EXPECTED_STEPS, ensure_ascii=False)
        )
        failed = [item for item in report["cases"] if not item["ok"]]
        self.assertEqual(failed, [], msg=json.dumps(failed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
