"""The 过程 list hides 读图 / 向前重放 rows and an empty heading."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
INDEX = ROOT / "front" / "index.html"


def extract_function(source, name):
    marker = "function " + name + "("
    start = source.index(marker)
    nxt = source.find("\nfunction ", start + 1)
    if nxt < 0:
        raise AssertionError("missing function after %s" % name)
    return source[start:nxt].strip() + "\n"


def js_dom_shim():
    return r"""
function classes(el) { return String(el.className || "").split(/\s+/).filter(Boolean); }
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.id = "";
    this.disabled = false;
    this.type = "";
    this._text = "";
    this._listeners = {};
    const self = this;
    this.classList = {
      contains(name) { return classes(self).includes(name); },
    };
  }
  get childNodes() { return this.children; }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }
  append(...nodes) {
    nodes.forEach((node) => {
      node.parentNode = this;
      this.children.push(node);
    });
    return nodes[0];
  }
  addEventListener(name, fn) {
    (this._listeners[name] || (this._listeners[name] = [])).push(fn);
  }
  querySelectorAll(sel) {
    const out = [];
    function walk(node) {
      (node.children || []).forEach((child) => {
        if (sel === "li" && child.tagName === "LI") out.push(child);
        if (sel === "button" && child.tagName === "BUTTON") out.push(child);
        if (sel === "h4" && child.tagName === "H4") out.push(child);
        walk(child);
      });
    }
    walk(this);
    return out;
  }
}
const document = {
  createElement(tag) { return new El(tag); },
};
"""


def run_js(body):
    source = APP.read_text(encoding="utf-8")
    helpers = "\n".join(
        [
            js_dom_shim(),
            "let sending = false;",
            extract_function(source, "isVisionTrace"),
            extract_function(source, "visibleTraces"),
            extract_function(source, "traceList"),
        ]
    )
    result = subprocess.run(
        ["node", "-e", helpers + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class TraceListDisplayTest(unittest.TestCase):
    def test_source_hides_vision_rows_in_trace_list(self):
        app = APP.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/app.js?v=96", index)
        self.assertIn("function isVisionTrace(", app)
        self.assertIn("function visibleTraces(", app)
        body = extract_function(app, "traceList")
        self.assertIn("visibleTraces(project)", body)
        self.assertIn("过程", body)
        self.assertIn("向前重放", body)
        self.assertIn('["writing", "building", "rendering"].includes(project.status)', body)

    def test_vision_rows_are_hidden_and_empty_section_is_dropped(self):
        report = run_js(
            r"""
            const visionOnly = {
              debug_trace: true,
              status: "drilling",
              traces: [
                { id: "1", stage: "vision", label: "读图" },
                { id: "2", stage: "vision", label: "读图 2" },
              ],
            };
            const mixed = {
              debug_trace: true,
              status: "writing",
              traces: [
                { id: "1", stage: "vision", label: "读图" },
                { id: "2", stage: "vision", label: "读图 2" },
                { id: "3", stage: "script", label: "剧本" },
              ],
            };
            const hidden = traceList(visionOnly);
            const shown = traceList(mixed);
            const labels = shown ? shown.querySelectorAll("li").map((item) => item.textContent) : [];
            const buttons = shown ? shown.querySelectorAll("button").map((item) => item.textContent) : [];
            const heading = shown ? shown.querySelectorAll("h4").map((item) => item.textContent) : [];
            console.log(JSON.stringify({
              vision: {
                isVision: [isVisionTrace(visionOnly.traces[0]), isVisionTrace(visionOnly.traces[1])],
                visible: visibleTraces(visionOnly),
                hidden: hidden === null,
              },
              mixed: {
                visible: visibleTraces(mixed).map((row) => row.label),
                labels: labels,
                buttons: buttons,
                heading: heading,
                hasVisionLabel: labels.some((text) => text.indexOf("读图") >= 0),
              },
            }));
            """
        )
        self.assertEqual(report["vision"]["isVision"], [True, True])
        self.assertEqual(report["vision"]["visible"], [])
        self.assertTrue(report["vision"]["hidden"])
        self.assertEqual(report["mixed"]["visible"], ["剧本"])
        self.assertEqual(report["mixed"]["heading"], ["过程"])
        self.assertEqual(len(report["mixed"]["labels"]), 1)
        self.assertIn("剧本", report["mixed"]["labels"][0])
        self.assertEqual(report["mixed"]["buttons"], ["向前重放"])
        self.assertFalse(report["mixed"]["hasVisionLabel"])

    def test_helpers_are_named_functions(self):
        source = APP.read_text(encoding="utf-8")
        for name in ("isVisionTrace", "visibleTraces", "traceList"):
            self.assertIsNotNone(re.search(r"^function %s\(" % name, source, re.M), name)


if __name__ == "__main__":
    unittest.main()
