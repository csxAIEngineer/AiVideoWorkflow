"""Conversation chat pins the composer at the bottom; workflow keeps the previous column layout."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "front" / "styles.css"
APP = ROOT / "front" / "app.js"
INDEX = ROOT / "front" / "index.html"


def css_rule(css, selector):
    marker = "\n" + selector + " {"
    index = css.find(marker)
    if index < 0:
        raise AssertionError("missing rule %s" % selector)
    return css[index + len(marker):].split("}", 1)[0]


def extract_function(source, name):
    marker = "function " + name + "("
    start = source.index(marker)
    candidates = [
        item for item in (
            source.find("\nfunction ", start + 1),
            source.find("\nasync function ", start + 1),
        )
        if item >= 0
    ]
    if not candidates:
        raise AssertionError("missing function after %s" % name)
    return source[start:min(candidates)].strip() + "\n"


def run_js(body):
    source = APP.read_text(encoding="utf-8")
    helpers = "\n".join(
        extract_function(source, name)
        for name in ("chatUsesWorkflowLayout", "chatScroller", "threadFollowsLatest", "scrollThreadToLatest", "settleThread")
    )
    result = subprocess.run(
        ["node", "-e", helpers + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class ChatColumnScrollTest(unittest.TestCase):
    def setUp(self):
        self.css = CSS.read_text(encoding="utf-8")

    def test_thread_scrolls_inside_a_pinned_column(self):
        column = css_rule(self.css, ".chat-column")
        self.assertIn("overflow: hidden", column)
        self.assertNotIn("overflow-y: auto", column)
        self.assertIn("flex-direction: column", column)

        thread = css_rule(self.css, ".chat-column .thread")
        self.assertIn("flex: 1 1 auto", thread)
        self.assertIn("min-height: 0", thread)
        self.assertIn("overflow: auto", thread)
        self.assertNotIn("overflow: visible", thread)
        self.assertNotIn("flex: 1 0 auto", thread)

        actions = css_rule(self.css, ".chat-actions")
        self.assertNotIn("position: absolute", actions)
        self.assertNotIn("position: fixed", actions)
        self.assertNotIn("position: sticky", actions)

    def test_composer_is_pinned_at_the_bottom_once_there_is_chat(self):
        composer = css_rule(self.css, ".chat-column .composer")
        self.assertIn("flex: none", composer)
        self.assertIn("auto auto 16px", composer)
        self.assertNotIn("position: sticky", composer)
        self.assertNotIn("position: fixed", composer)

        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/app.js?v=94", index)
        self.assertIn("/styles.css?v=59", index)

    def test_workflow_dialog_keeps_the_previous_column_layout(self):
        column = css_rule(self.css, ".main.workflow .chat-column")
        self.assertIn("overflow-y: auto", column)

        thread = css_rule(self.css, ".main.workflow .chat-column .thread")
        self.assertIn("flex: 1 0 auto", thread)
        self.assertIn("overflow: visible", thread)
        self.assertNotIn("overflow: auto", thread)
        self.assertNotIn("flex: 1 1 auto", thread)

        workflow = css_rule(self.css, ".main.workflow .chat-column .composer")
        self.assertIn("position: sticky", workflow)
        self.assertIn("bottom: 0", workflow)
        self.assertIn("width: 100%", workflow)
        self.assertIn("margin: 0", workflow)
        self.assertIn("border-radius: 0", workflow)
        self.assertNotIn("border-radius: 24px", workflow)

    def test_empty_home_centers_a_large_composer_card(self):
        fresh = css_rule(self.css, ".main.fresh .chat-column")
        self.assertIn("justify-content: center", fresh)
        self.assertIn("align-items: center", fresh)

        home = css_rule(self.css, ".main.fresh .chat-column .composer")
        self.assertIn("margin: 0 auto", home)
        self.assertNotIn("auto auto", home)
        self.assertIn("min(680px, calc(100% - 48px))", home)

        field = css_rule(self.css, ".main.fresh .brief-input")
        self.assertIn("min-height: 8.5em", field)

    def test_feed_unit_inspector_still_scrolls_by_column(self):
        field = self.css.split(".contract-field p {", 1)[1].split("}", 1)[0]
        self.assertNotIn("max-height", field)
        self.assertNotIn("line-clamp", field)
        self.assertNotIn("overflow: hidden", field)
        self.assertIn("pre-wrap", field)
        column = self.css.split(".contract-col {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow: auto", column)

    def test_transcript_paints_oldest_messages_first(self):
        app = APP.read_text(encoding="utf-8")
        paint = extract_function(app, "paintTranscript")
        self.assertIn("for (let index = 0; index < messages.length; index += 1)", paint)
        self.assertIn("wrap.append(transcriptBubble(messages[index], index))", paint)
        self.assertIn("scrollThreadToLatest()", paint)
        self.assertIn("threadFollowsLatest()", paint)
        self.assertIn("if (showProgress) wrap.append(progressPanel(current))", paint)

    def test_workflow_transcript_keeps_latest_first_and_the_progress_pin(self):
        app = APP.read_text(encoding="utf-8")
        paint = extract_function(app, "paintTranscript")
        self.assertIn("if (!full) {", paint)
        self.assertIn("wrap.append(transcriptBubble(messages[messages.length - 1], messages.length - 1))", paint)
        self.assertIn("for (let index = messages.length - 2; index >= 0; index -= 1)", paint)
        self.assertIn("fillChatPin(showProgress ? progressPanel(current) : null, pinActions)", paint)

    def test_scroll_helpers_follow_the_bottom(self):
        report = run_js(
            r"""
            global.thread = { scrollTop: 0, scrollHeight: 800, clientHeight: 200 };
            const atTop = threadFollowsLatest();
            thread.scrollTop = 600;
            const atBottom = threadFollowsLatest();
            thread.scrollTop = 0;
            scrollThreadToLatest();
            const afterScroll = thread.scrollTop;
            thread.scrollTop = 10;
            settleThread({ scroll: "bottom" }, false, null);
            const afterSettle = thread.scrollTop;
            console.log(JSON.stringify({ atTop, atBottom, afterScroll, afterSettle }));
            """
        )
        self.assertFalse(report["atTop"])
        self.assertTrue(report["atBottom"])
        self.assertEqual(report["afterScroll"], 600)
        self.assertEqual(report["afterSettle"], 600)

    def test_workflow_scroll_helpers_follow_the_top(self):
        report = run_js(
            r"""
            global.current = { status: "building", script: "x" };
            global.workflowMode = function (project) { return !!(project && project.status === "building"); };
            global.document = { querySelector: function () { return null; } };
            global.thread = { scrollTop: 0, scrollHeight: 800, clientHeight: 200 };
            const atTop = threadFollowsLatest();
            thread.scrollTop = 600;
            const atBottom = threadFollowsLatest();
            scrollThreadToLatest();
            const afterScroll = thread.scrollTop;
            thread.scrollTop = 40;
            settleThread({ scroll: "top" }, false, null);
            const afterSettle = thread.scrollTop;
            console.log(JSON.stringify({ atTop, atBottom, afterScroll, afterSettle }));
            """
        )
        self.assertTrue(report["atTop"])
        self.assertFalse(report["atBottom"])
        self.assertEqual(report["afterScroll"], 0)
        self.assertEqual(report["afterSettle"], 0)

    def test_helpers_are_named_functions(self):
        source = APP.read_text(encoding="utf-8")
        for name in ("chatUsesWorkflowLayout", "chatScroller", "threadFollowsLatest", "scrollThreadToLatest", "settleThread", "paintTranscript"):
            self.assertIsNotNone(re.search(r"^function %s\(" % name, source, re.M), name)


if __name__ == "__main__":
    unittest.main()
