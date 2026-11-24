"""The 'already left the script stop' banner is not shown in the chat column."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "front" / "script-edit.js"
INDEX = ROOT / "front" / "index.html"


def run_js(body):
    shim = r"""
    function El(tag) {
      this.tagName = String(tag).toUpperCase();
      this.className = "";
      this.dataset = {};
      this.children = [];
      this.parentNode = null;
      this.hidden = false;
      this._text = "";
      this.attributes = {};
    }
    El.prototype.setAttribute = function (key, value) {
      this.attributes[key] = String(value);
      if (key === "data-project-id") this.dataset.projectId = String(value);
    };
    El.prototype.append = function () {
      for (let i = 0; i < arguments.length; i += 1) {
        arguments[i].parentNode = this;
        this.children.push(arguments[i]);
      }
    };
    El.prototype.insertBefore = function (node, anchor) {
      node.parentNode = this;
      const index = anchor ? this.children.indexOf(anchor) : -1;
      if (index < 0) this.children.push(node);
      else this.children.splice(index, 0, node);
      return node;
    };
    El.prototype.removeChild = function (node) {
      const index = this.children.indexOf(node);
      if (index >= 0) this.children.splice(index, 1);
      node.parentNode = null;
      return node;
    };
    Object.defineProperty(El.prototype, "textContent", {
      get() {
        if (this.children.length) return this.children.map((child) => child.textContent).join("");
        return this._text;
      },
      set(value) { this._text = String(value); },
    });
    El.prototype.querySelector = function (selector) {
      const all = [];
      (function walk(node) {
        node.children.forEach((child) => { all.push(child); walk(child); });
      })(this);
      return all.find((node) => matchSel(node, selector)) || null;
    };
    function matchSel(node, selector) {
      if (selector.charAt(0) === ".") {
        const name = selector.slice(1).split("[")[0];
        return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
      }
      return false;
    }
    function collect(node, acc) {
      acc.push(node);
      node.children.forEach((child) => collect(child, acc));
      return acc;
    }
    const body = new El("body");
    global.document = {
      body: body,
      createElement: function (tag) { return new El(tag); },
      querySelector: function (selector) {
        const all = collect(body, []);
        if (selector.indexOf("script-revision-outside") >= 0) {
          const matched = /data-project-id="([^"]+)"/.exec(selector);
          return all.find((node) => {
            if ((" " + (node.className || "") + " ").indexOf(" script-revision-outside ") < 0) return false;
            return !matched || node.dataset.projectId === matched[1];
          }) || null;
        }
        return null;
      },
    };
    """
    result = subprocess.run(
        ["node", "-e", shim + "\n" + SCRIPT.read_text(encoding="utf-8") + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class ScriptStopLeftNoticeTest(unittest.TestCase):
    def test_done_continue_state_does_not_render_the_leave_banner(self):
        report = run_js(
            r"""
            const id = "p1";
            async function go() {
              const view = {
                id: id,
                continue_state: "queued",
                opinion_state: "none",
                review_state: "none",
                base_token: "tok",
              };
              const options = {
                rpc: async function () { return view; },
                getProjectId: function () { return id; },
              };
              await ScriptRevisionEdit.prepareRevision(options, id);
              const queued = ScriptRevisionEdit.createOutside({ id: id });
              const queuedText = queued ? queued.textContent : "";
              view.continue_state = "done";
              await ScriptRevisionEdit.prepareRevision(options, id);
              const after = ScriptRevisionEdit.createOutside({ id: id });
              const leftover = document.querySelector(".script-revision-outside");
              console.log(JSON.stringify({
                queuedText: queuedText,
                after: after,
                leftover: leftover ? leftover.textContent : "",
              }));
            }
            go().catch((error) => { console.error(error); process.exit(1); });
            """
        )
        self.assertIn("下一段已在队列里", report["queuedText"])
        self.assertIsNone(report["after"])
        self.assertNotIn("已经离开剧本停点", report["leftover"])
        self.assertEqual(report["leftover"], "")

    def test_index_uses_updated_script_edit_cache(self):
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/script-edit.js?v=3", index)
        self.assertNotIn("/script-edit.js?v=2", index)


if __name__ == "__main__":
    unittest.main()
