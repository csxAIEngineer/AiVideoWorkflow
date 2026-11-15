"""The right-hand column scrolls as one piece past a long task list."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "front" / "styles.css"


def css_rule(css, selector):
    marker = "\n" + selector + " {"
    index = css.find(marker)
    if index < 0:
        raise AssertionError("missing rule %s" % selector)
    return css[index + len(marker):].split("}", 1)[0]


class ChatColumnScrollTest(unittest.TestCase):
    def setUp(self):
        self.css = CSS.read_text(encoding="utf-8")

    def test_column_scrolls_past_the_task_list_to_the_actions(self):
        column = css_rule(self.css, ".chat-column")
        self.assertIn("overflow-y: auto", column)
        self.assertNotIn("overflow: hidden", column)

        pin = css_rule(self.css, ".chat-pin")
        self.assertIn("flex: 0 0 auto", pin)
        self.assertNotIn("overflow: hidden", pin)
        self.assertNotIn("overflow: auto", pin)
        self.assertNotIn("max-height", pin)

        progress = css_rule(self.css, ".chat-progress")
        self.assertIn("overflow: visible", progress)
        self.assertNotIn("overflow: auto", progress)
        self.assertNotIn("overflow-y", progress)
        self.assertNotIn("max-height", progress)
        self.assertNotIn(".chat-progress .task-list", self.css)

        actions = css_rule(self.css, ".chat-actions")
        self.assertNotIn("position: absolute", actions)
        self.assertNotIn("position: fixed", actions)
        self.assertNotIn("position: sticky", actions)

        thread = css_rule(self.css, ".chat-column .thread")
        self.assertIn("flex: 1 0 auto", thread)
        self.assertIn("overflow: visible", thread)
        self.assertNotIn("overflow: auto", thread)

    def test_composer_stays_at_the_bottom_of_the_scrolling_column(self):
        composer = css_rule(self.css, ".main.workflow .chat-column .composer")
        self.assertIn("position: sticky", composer)
        self.assertIn("bottom: 0", composer)
        self.assertIn("flex: none", composer)

    def test_feed_unit_inspector_still_scrolls_by_column(self):
        field = self.css.split(".contract-field p {", 1)[1].split("}", 1)[0]
        self.assertNotIn("max-height", field)
        self.assertNotIn("line-clamp", field)
        self.assertNotIn("overflow: hidden", field)
        self.assertIn("pre-wrap", field)
        column = self.css.split(".contract-col {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow: auto", column)


if __name__ == "__main__":
    unittest.main()
