"""Chat column should show a writing cue while the script is being generated."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
CSS = ROOT / "front" / "styles.css"
HTML = ROOT / "front" / "index.html"


def helpers():
    source = APP.read_text(encoding="utf-8")
    start = source.index("function isPromptDebug")
    end = source.index("function writeStep")
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


class ScriptLoadingTest(unittest.TestCase):
    def test_script_writing_has_a_loading_cue(self):
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
            const writing = {
              status: "writing",
              stage: "正在写剧本",
              progress: {
                phase: "writing",
                message: "正在写剧本",
                pipeline: { current: "script", action: "write" },
              },
            };
            const asset = {
              status: "writing",
              progress: {
                phase: "writing",
                pipeline: { current: "asset", action: "write" },
              },
            };
            const drilling = {
              status: "drilling",
              progress: { phase: "drill_think", pipeline: { current: "script" } },
            };
            const scriptCue = autoRun(writing);
            check("script-key", scriptCue && scriptCue.key, "script");
            check("script-text", scriptCue && scriptCue.text, "正在写剧本…");
            check("asset-still-auto", autoRun(asset) && autoRun(asset).text, "正在自动写资产…");
            check("drilling-silent", autoRun(drilling), null);
            process.stdout.write(JSON.stringify(cases));
            """
        )
        failed = [row for row in report if not row["ok"]]
        self.assertEqual(failed, [])

    def test_waiting_worker_is_not_shown_as_already_writing(self):
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
            const waiting = {
              status: "writing",
              stage: "正在写剧本",
              worker_state: "waiting",
              worker_kind: "write",
              progress: {
                phase: "writing",
                message: "正在写剧本",
                pipeline: { current: "script", action: "write" },
              },
            };
            const running = {
              status: "writing",
              worker_state: "running",
              worker_kind: "write",
              progress: {
                phase: "writing",
                message: "正在写剧本",
                pipeline: { current: "script", action: "write" },
              },
            };
            const idle = {
              status: "gate",
              worker_state: "",
              stage: "剧本候选",
            };
            check("waiting-flag", workerWaiting(waiting), true);
            check("running-not-waiting", workerWaiting(running), false);
            check("waiting-text", workerWaitText(waiting), "等待写剧本");
            check("waiting-status", statusText(waiting), "等待写剧本");
            check("running-status", statusText(running), "正在写剧本…");
            check("idle-status", statusText(idle), "剧本候选");
            check("waiting-auto-run-still-present", !!(autoRun(waiting) && autoRun(waiting).text), true);
            process.stdout.write(JSON.stringify(cases));
            """
        )
        failed = [row for row in report if not row["ok"]]
        self.assertEqual(failed, [])

    def test_chat_header_has_a_spinner_while_writing(self):
        app = APP.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("chat-toggle-spinner", html)
        self.assertIn("chat-toggle-spinner", app)
        self.assertIn("job-spinner", app)
        self.assertIn("@keyframes chat-spin", css)
        self.assertIn("chat-toggle-spinner", css)
        self.assertIn("prefers-reduced-motion", css)
