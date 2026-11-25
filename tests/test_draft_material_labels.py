"""New-conversation uploads insert draggable labels into the composer text."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
CSS = ROOT / "front" / "styles.css"
INDEX = ROOT / "front" / "index.html"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def extract_helpers(source):
    start = source.index("function isRichBrief")
    end = source.index("function materialStrip")
    return source[start:end]


def js_dom_shim():
    return r"""
class TextEl {
  constructor(value) {
    this.nodeType = 3;
    this.nodeValue = String(value);
    this.parentNode = null;
    this.tagName = "";
    this.childNodes = [];
    this.children = [];
  }
  get textContent() { return this.nodeValue; }
  set textContent(value) { this.nodeValue = String(value); }
  get nextSibling() { return sibling(this, 1); }
  get previousSibling() { return sibling(this, -1); }
}
class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this.id = "";
    this.style = {};
    this.draggable = false;
    this.contentEditable = "inherit";
    this._text = "";
    this._listeners = {};
    const self = this;
    this.classList = {
      add(...names) {
        const set = new Set(classes(self));
        names.filter(Boolean).forEach((name) => set.add(name));
        self.className = Array.from(set).join(" ");
      },
      remove(...names) {
        const set = new Set(classes(self));
        names.forEach((name) => set.delete(name));
        self.className = Array.from(set).join(" ");
      },
      toggle(name, force) {
        const has = classes(self).includes(name);
        const on = force == null ? !has : !!force;
        if (on) this.add(name);
        else this.remove(name);
        return on;
      },
      contains(name) { return classes(self).includes(name); },
    };
  }
  get childNodes() { return this.children; }
  get nextSibling() { return sibling(this, 1); }
  get previousSibling() { return sibling(this, -1); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "contenteditable") this.contentEditable = String(value);
  }
  getAttribute(name) {
    if (name === "id" && this.id) return this.id;
    if (name === "contenteditable") {
      if (Object.prototype.hasOwnProperty.call(this.attributes, "contenteditable")) return this.attributes.contenteditable;
      return this.contentEditable !== "inherit" ? String(this.contentEditable) : null;
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }
  closest(sel) {
    let node = this;
    while (node && node.tagName) {
      if (match(node, sel)) return node;
      node = node.parentNode;
    }
    return null;
  }
  append(...nodes) {
    nodes.forEach((node) => {
      if (node == null) return;
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.children.push(node);
    });
  }
  appendChild(node) { this.append(node); return node; }
  insertBefore(node, ref) {
    if (!ref) return this.appendChild(node);
    if (node.parentNode) node.parentNode.removeChild(node);
    const index = this.children.indexOf(ref);
    node.parentNode = this;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    return node;
  }
  removeChild(node) {
    this.children = this.children.filter((child) => child !== node);
    if (node) node.parentNode = null;
    return node;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) { return walk(this, false).filter((el) => match(el, sel)); }
  addEventListener(type, fn) {
    this._listeners[type] = this._listeners[type] || [];
    this._listeners[type].push(fn);
  }
  dispatchEvent(event) {
    event.currentTarget = this;
    if (!event.target) event.target = this;
    (this._listeners[event.type] || []).slice().forEach((fn) => fn(event));
    return !event.defaultPrevented;
  }
  getBoundingClientRect() { return { left: 0, width: 40, top: 0, height: 20 }; }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    this._text = "";
    if (value) this.appendChild(new TextEl(value));
  }
  get innerHTML() { return this.textContent; }
  set innerHTML(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    this._text = "";
    if (value) this.appendChild(new TextEl(String(value)));
  }
}
function sibling(node, dir) {
  const parent = node.parentNode;
  if (!parent) return null;
  const list = parent.childNodes || parent.children || [];
  const index = list.indexOf(node);
  if (index < 0) return null;
  return list[index + dir] || null;
}
function classes(el) {
  return String(el.className || "").trim().split(/\s+/).filter(Boolean);
}
function walk(root, includeSelf) {
  const out = [];
  if (includeSelf && root.nodeType === 1) out.push(root);
  (root.children || []).forEach((child) => {
    if (child.nodeType === 1) {
      out.push(child);
      out.push(...walk(child, false));
    }
  });
  return out;
}
function matchSimple(el, part) {
  if (part.charAt(0) === "#") return el.id === part.slice(1);
  const attrEq = part.match(/^([a-zA-Z][\w-]*)?\[([^\]]+)\]$/);
  const parsed = part.match(/^([a-zA-Z][\w-]*)?(?:\.([\w-]+))*$/);
  if (part.charAt(0) === "." || (parsed && !attrEq)) {
    if (!parsed) return false;
    const tag = parsed[1];
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    const classNames = part.match(/\.([\w-]+)/g) || [];
    return classNames.every((item) => classes(el).includes(item.slice(1)));
  }
  if (attrEq) {
    const tag = attrEq[1];
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    const raw = attrEq[2];
    const eq = raw.match(/^([^\]=]+)="([^"]*)"$/) || raw.match(/^([^\]=]+)='([^']*)'$/);
    if (eq) {
      const attr = eq[1];
      const want = eq[2];
      if (attr === "data-id") return el.dataset && el.dataset.id === want;
      return el.getAttribute(attr) === want;
    }
    return el.getAttribute(raw) != null;
  }
  return false;
}
function match(el, selector) {
  const parts = String(selector).trim().split(/\s+/);
  if (parts.length === 1) return matchSimple(el, parts[0]);
  if (!matchSimple(el, parts[parts.length - 1])) return false;
  let current = el.parentNode;
  for (let i = parts.length - 2; i >= 0; i -= 1) {
    let found = false;
    while (current) {
      if (current.tagName && matchSimple(current, parts[i])) {
        found = true;
        current = current.parentNode;
        break;
      }
      current = current.parentNode;
    }
    if (!found) return false;
  }
  return true;
}
const document = {
  body: new El("body"),
  createElement(tag) { return new El(tag); },
  createTextNode(value) { return new TextEl(value); },
  querySelector(sel) {
    if (matchSimple(this.body, sel)) return this.body;
    return this.body.querySelector(sel);
  },
  querySelectorAll(sel) {
    const out = [];
    if (matchSimple(this.body, sel)) out.push(this.body);
    return out.concat(this.body.querySelectorAll(sel));
  },
  createRange() {
    return {
      startContainer: null,
      startOffset: 0,
      collapsed: true,
      commonAncestorContainer: null,
      setStart(node, offset) {
        this.startContainer = node;
        this.startOffset = offset;
        this.commonAncestorContainer = node;
      },
      setStartBefore(node) {
        this.startContainer = node.parentNode;
        this.startOffset = (node.parentNode.childNodes || []).indexOf(node);
        this.commonAncestorContainer = node.parentNode;
      },
      setStartAfter(node) {
        this.startContainer = node.parentNode;
        this.startOffset = (node.parentNode.childNodes || []).indexOf(node) + 1;
        this.commonAncestorContainer = node.parentNode;
      },
      collapse() { this.collapsed = true; },
      deleteContents() {},
      insertNode(node) {
        const container = this.startContainer;
        const offset = this.startOffset;
        if (!container) return;
        if (container.nodeType === 3) {
          const parent = container.parentNode;
          const before = String(container.nodeValue || "").slice(0, offset);
          const after = String(container.nodeValue || "").slice(offset);
          if (before && after) {
            container.nodeValue = before;
            parent.insertBefore(node, container.nextSibling);
            parent.insertBefore(new TextEl(after), node.nextSibling);
          } else if (!before) {
            parent.insertBefore(node, container);
          } else {
            parent.insertBefore(node, container.nextSibling);
          }
        } else {
          const ref = (container.childNodes || [])[offset] || null;
          if (ref) container.insertBefore(node, ref);
          else container.appendChild(node);
        }
      },
    };
  },
};
let currentRange = null;
const window = {
  getSelection() {
    if (!currentRange) return { rangeCount: 0, getRangeAt() { return null; }, removeAllRanges() {}, addRange() {} };
    return {
      rangeCount: 1,
      isCollapsed: true,
      getRangeAt() { return currentRange; },
      removeAllRanges() { currentRange = null; },
      addRange(range) { currentRange = range; },
    };
  },
};
"""


def helpers():
    source = APP.read_text(encoding="utf-8")
    return "\n".join(
        [
            js_dom_shim(),
            "const MAX_MATERIALS = 8;",
            "let draftMaterials = [];",
            "let draftMaterialSeq = 0;",
            "let draggingMaterialId = \"\";",
            "let briefEl = null;",
            "const URL = { createObjectURL(file) { return \"blob:\" + ((file && file.name) || \"file\"); }, revokeObjectURL() {} };",
            extract_helpers(source),
        ]
    )


def run_js(body):
    result = subprocess.run(
        ["node", "-e", helpers() + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class DraftMaterialLabelTest(unittest.TestCase):
    def test_source_keeps_labels_inline_in_the_composer(self):
        app = APP.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/app.js?v=96", index)
        self.assertIn("/styles.css?v=59", index)
        self.assertIn('contenteditable="true"', index)
        self.assertIn('id="brief"', index)
        self.assertNotIn('id="material-list"', index)
        self.assertIn("insertMaterialLabelAtCaret", app)
        self.assertIn("serializeComposer", app)
        self.assertIn("captureComposerSend", app)
        self.assertIn("moveMaterialLabelToRange", app)
        self.assertIn("function materialTitle", app)
        self.assertIn("refreshMaterialLabels", app)
        self.assertIn("【", app)
        self.assertNotIn("function renderDraftMaterials", app)
        self.assertNotIn("function moveDraftMaterial(", app)
        start = app.split("async function startBrief(text)", 1)[1].split("\nasync function ", 1)[0]
        self.assertIn("captureComposerSend()", start)
        self.assertIn("draftMaterialRecord(item)", start)
        self.assertIn("data_base64", start)
        self.assertIn(".brief-input", css)
        self.assertIn(".material-label", css)

    def test_upload_inserts_inline_label_and_drag_moves_it_in_the_text(self):
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
            briefEl = document.createElement("div");
            briefEl.id = "brief";
            briefEl.className = "brief-input";
            briefEl.setAttribute("contenteditable", "true");
            document.body.append(briefEl);
            briefEl.appendChild(document.createTextNode("这是猫1:"));
            const cat1 = { name: "菜花.jpg", type: "image/jpeg" };
            const cat2 = { name: "菜花2.jpg", type: "image/jpeg" };
            const skipped = acceptDraftFiles([cat1, { name: "note.txt", type: "text/plain" }]);
            check("skip-non-image", { overflow: skipped.overflow, skipped: skipped.skipped, count: skipped.accepted.length }, { overflow: false, skipped: true, count: 1 });
            const first = insertMaterialLabelAtCaret(skipped.accepted[0], briefEl);
            check("first-in-editor", briefEl.contains(first), true);
            check("first-draggable", first.draggable === true, true);
            check("first-not-editable", String(first.contentEditable) === "false", true);
            check("first-visible-name", first.querySelector(".material-label-name").textContent, "U1 菜花");
            check("no-textarea", briefEl.querySelectorAll("textarea").length, 0);
            check("inline-text-after-upload", serializeComposer(briefEl), {
              text: "这是猫1:【U1 菜花】",
              ids: ["m1"],
            });
            briefEl.appendChild(document.createTextNode("\n这是猫2:"));
            const secondAdd = acceptDraftFiles([cat2]);
            const second = insertMaterialLabelAtCaret(secondAdd.accepted[0], briefEl);
            check("two-labels-in-text", serializeComposer(briefEl), {
              text: "这是猫1:【U1 菜花】\n这是猫2:【U2 菜花2】",
              ids: ["m1", "m2"],
            });
            const sendFirst = captureComposerSend(briefEl);
            check("send-initial", { text: sendFirst.text, materials: sendFirst.materials }, {
              text: "这是猫1:【U1 菜花】\n这是猫2:【U2 菜花2】",
              materials: [
                { filename: "菜花.jpg", narration: "" },
                { filename: "菜花2.jpg", narration: "" },
              ],
            });
            moveMaterialLabelBefore(first, null, briefEl);
            const movedNames = [...briefEl.querySelectorAll(".material-label")].map((node) => node.querySelector(".material-label-name").textContent);
            check("renumber-after-move", movedNames, ["U1 菜花2", "U2 菜花"]);
            const afterMove = captureComposerSend(briefEl);
            check("move-to-end", afterMove, {
              text: "这是猫1:\n这是猫2:【U1 菜花2】【U2 菜花】",
              materials: [
                { filename: "菜花2.jpg", narration: "" },
                { filename: "菜花.jpg", narration: "" },
              ],
              ids: ["m2", "m1"],
            });
            const labels = briefEl.querySelectorAll(".material-label");
            const startRange = document.createRange();
            startRange.setStart(briefEl, 0);
            startRange.collapse(true);
            moveMaterialLabelToRange(labels[1], startRange, briefEl);
            const restored = captureComposerSend(briefEl);
            check("drag-to-start-ids", restored.ids, ["m1", "m2"]);
            check("drag-to-start-payload", restored.materials, [
              { filename: "菜花.jpg", narration: "" },
              { filename: "菜花2.jpg", narration: "" },
            ]);
            check("drag-to-start-text", restored.text, "【U1 菜花】这是猫1:\n这是猫2:【U2 菜花2】");
            const afterFirst = document.createRange();
            afterFirst.setStartAfter(briefEl.querySelectorAll(".material-label")[0]);
            afterFirst.collapse(true);
            currentRange = afterFirst;
            check("backspace-removes-label", removeAdjacentMaterialLabel(-1, briefEl), true);
            const afterDelete = captureComposerSend(briefEl);
            check("after-delete", { ids: afterDelete.ids, materials: afterDelete.materials, text: afterDelete.text }, {
              ids: ["m2"],
              materials: [{ filename: "菜花2.jpg", narration: "" }],
              text: "这是猫1:\n这是猫2:【U1 菜花2】",
            });
            console.log(JSON.stringify({ cases: cases, movedText: afterMove.text, restoredText: restored.text }));
            """
        )
        failed = [item for item in report["cases"] if not item["ok"]]
        self.assertEqual(failed, [], msg=json.dumps(failed, ensure_ascii=False, indent=2))

    def test_material_title_matches_server(self):
        from end.intake import material_title

        cases = [
            ["菜花.jpg", 1],
            ["菜花2.jpg", 2],
            ["foo/车站.png", 1],
            ["weird!!name.webp", 1],
            ["", 3],
            ["!!!.jpg", 2],
            [".hidden.png", 1],
            ["a" * 40 + ".jpg", 1],
            ["菜  花.jpg", 1],
            ["foo bar_baz-qux—ok.jpg", 1],
            ["hello世界!!.png", 1],
            [".jpg", 4],
        ]
        report = run_js(
            "const cases = %s;\nconsole.log(JSON.stringify(cases.map((item) => ({ filename: item[0], index: item[1], title: materialTitle(item[0], item[1]) }))));"
            % json.dumps(cases, ensure_ascii=False)
        )
        self.assertEqual(
            [(item["filename"], item["index"], item["title"]) for item in report],
            [(filename, index, material_title(filename, index)) for filename, index in cases],
        )

    def test_helpers_are_named_functions(self):
        source = APP.read_text(encoding="utf-8")
        for name in (
            "serializeComposer",
            "captureComposerSend",
            "insertMaterialLabelAtCaret",
            "moveMaterialLabelToRange",
            "acceptDraftFiles",
            "removeAdjacentMaterialLabel",
            "materialTitle",
            "materialMark",
            "refreshMaterialLabels",
        ):
            self.assertIsNotNone(re.search(r"^function %s\(" % name, source, re.M), name)


if __name__ == "__main__":
    unittest.main()
