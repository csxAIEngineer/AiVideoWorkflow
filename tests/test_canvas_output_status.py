"""Canvas shot/film nodes show 已出片/未出片 and never load video."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "front" / "app.js"
CSS = ROOT / "front" / "styles.css"
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
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this.style = {};
    this._text = "";
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
      contains(name) {
        return classes(self).includes(name);
      },
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  append(...nodes) {
    nodes.forEach((node) => {
      if (node == null) return;
      if (typeof node === "string") node = textNode(node);
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.children.push(node);
    });
  }
  appendChild(node) {
    this.append(node);
    return node;
  }
  removeChild(node) {
    this.children = this.children.filter((child) => child !== node);
    if (node) node.parentNode = null;
    return node;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    return walk(this, false).filter((el) => match(el, sel));
  }
  addEventListener() {}
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    this._text = String(value);
  }
}
function textNode(value) {
  return { textContent: String(value), parentNode: null, children: [], tagName: "" };
}
function classes(el) {
  return String(el.className || "").trim().split(/\s+/).filter(Boolean);
}
function walk(root, includeSelf) {
  const out = [];
  if (includeSelf && root.tagName) out.push(root);
  (root.children || []).forEach((child) => {
    if (child.tagName) {
      out.push(child);
      out.push(...walk(child, false));
    }
  });
  return out;
}
function matchSimple(el, part) {
  const parsed = part.match(/^([a-zA-Z][\w-]*)?(?:\.([\w-]+))*(\[([\w-]+)\])?$/);
  if (!parsed) return false;
  const tag = parsed[1];
  const attr = parsed[4];
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  const classNames = part.match(/\.([\w-]+)/g) || [];
  if (classNames.some((item) => !classes(el).includes(item.slice(1)))) return false;
  if (attr) {
    const key = attr.replace(/^data-/, "");
    const camel = key.replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
    if (el.dataset && el.dataset[camel] != null) return true;
    if (el.getAttribute && el.getAttribute(attr) != null) return true;
    return false;
  }
  return true;
}
function match(el, selector) {
  const parts = String(selector).trim().split(/\s+/);
  if (parts.length === 1) return matchSimple(el, parts[0]);
  // descendant: last part matches el, ancestors match earlier parts in order
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
  createElement(tag) { return new El(tag); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  activeElement: null,
};
"""


def helpers():
    source = APP.read_text(encoding="utf-8")
    pieces = [
        js_dom_shim(),
        extract_function(source, "clipUrlFor"),
        extract_function(source, "hasClipOutput"),
        extract_function(source, "hasFilmOutput"),
        extract_function(source, "outputStatusText"),
        extract_function(source, "outputStatusLabel"),
        extract_function(source, "syncOutputStatus"),
        extract_function(source, "makeVideo"),
        extract_function(source, "readOnlyText"),
        extract_function(source, "renderNode"),
        extract_function(source, "patchCanvas"),
        extract_function(source, "renderContractVideo"),
        r"""
function placeNode(node, x, y) {
  node.dataset.x = String(x || 0);
  node.dataset.y = String(y || 0);
}
function nodeState() { return "pending"; }
function liveCopy() { return ""; }
function shotDurationLabel(shot, empty) { return (shot && shot.duration ? shot.duration + " 秒" : empty || ""); }
function renderContractTags() {
  const meta = document.createElement("span");
  meta.className = "contract-tags";
  return meta;
}
function renderStrip() {
  return document.createElement("div");
}
function restingMeta() { return ""; }
function fieldValue() { return ""; }
function syncStill() {}
function assetState() { return "pending"; }
function syncRetouchChrome() {}
const retouchSelection = null;
const contractShotId = "";
const pendingSaves = new Set();
let canvasNodes = document.createElement("div");
canvasNodes.className = "canvas-nodes";
""",
    ]
    return "\n".join(pieces)


def run_js(body):
    result = subprocess.run(
        ["node", "-e", helpers() + "\n" + body],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node failed")
    return json.loads(result.stdout)


class CanvasOutputStatusTest(unittest.TestCase):
    def test_cache_and_source_keep_video_out_of_the_canvas(self):
        app = APP.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/app.js?v=86", index)
        self.assertIn("/styles.css?v=54", index)
        render_node = app.split("function renderNode(project, info)", 1)[1].split("\nfunction ", 1)[0]
        patch_canvas = app.split("function patchCanvas(project)", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("makeVideo", render_node)
        self.assertNotIn("makeVideo", patch_canvas)
        self.assertNotIn('createElement("video")', render_node)
        self.assertNotIn('createElement("video")', patch_canvas)
        self.assertNotIn("video.src", render_node)
        self.assertNotIn("video.src", patch_canvas)
        self.assertNotIn("videoHold", app)
        self.assertNotIn("wf-video-hold", css)
        self.assertIn("outputStatusLabel", render_node)
        self.assertIn("syncOutputStatus", patch_canvas)
        self.assertIn("已出片", app)
        self.assertIn("未出片", app)
        contract = app.split("function renderContractColumn(project, shot)", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("renderContractVideo(project, shot)", contract)
        video_fn = app.split("function renderContractVideo(project, shot)", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("makeVideo(url)", video_fn)
        self.assertIn("clipUrlFor(project, shot && shot.id)", video_fn)
        self.assertIn(".wf-output-status", css)
        self.assertIn(".contract-video video", css)

    def test_canvas_renders_status_labels_and_shot_details_keep_video(self):
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
            const bare = { id: "p1", shots: [{ id: "S01", prompt: "雨巷" }, { id: "S02", prompt: "客厅" }], clips: [], video_url: "" };
            check("shot-empty-text", outputStatusText(bare, "shot", "S01"), "未出片");
            check("film-empty-text", outputStatusText(bare, "film", "film"), "未出片");
            const ready = {
              id: "p1",
              shots: [{ id: "S01", prompt: "雨巷" }, { id: "S02", prompt: "客厅" }],
              clips: [
                { id: "S01", url: "/media/projects/p1/clips/S01.mp4" },
                { id: "S03", url: "/media/projects/p1/clips/S03.mp4" },
                { id: "S02", url: "" },
              ],
              video_url: "/media/projects/p1/video",
              has_video: true,
            };
            check("shot-ready-text", outputStatusText(ready, "shot", "S01"), "已出片");
            check("shot-empty-url-text", outputStatusText(ready, "shot", "S02"), "未出片");
            check("shot-missing-clip-text", outputStatusText(ready, "shot", "S04"), "未出片");
            check("film-ready-text", outputStatusText(ready, "film", "film"), "已出片");
            check("film-ignores-has-video-only", outputStatusText({ has_video: true, video_url: "" }, "film", "film"), "未出片");

            const shotReady = renderNode(ready, { id: "S01", kind: "shot", title: "镜头 S01", shot: ready.shots[0], col: 0, row: 0, x: 0, y: 0, assets: [] });
            const shotPending = renderNode(ready, { id: "S02", kind: "shot", title: "镜头 S02", shot: ready.shots[1], col: 0, row: 1, x: 0, y: 120, assets: [] });
            const filmReady = renderNode(ready, { id: "film", kind: "film", title: "成片", col: 1, row: 0, x: 300, y: 0, assets: [] });
            const filmPending = renderNode(bare, { id: "film", kind: "film", title: "成片", col: 1, row: 0, x: 300, y: 0, assets: [] });
            function snapshot(node) {
              return {
                videos: node.querySelectorAll("video").length,
                holds: node.querySelectorAll(".wf-video-hold").length,
                labels: node.querySelectorAll(".wf-output-status").map((el) => ({
                  text: el.textContent,
                  ready: el.classList.contains("is-ready"),
                  pending: el.classList.contains("is-pending"),
                })),
              };
            }
            check("shot-ready-render", snapshot(shotReady), { videos: 0, holds: 0, labels: [{ text: "已出片", ready: true, pending: false }] });
            check("shot-pending-render", snapshot(shotPending), { videos: 0, holds: 0, labels: [{ text: "未出片", ready: false, pending: true }] });
            check("film-ready-render", snapshot(filmReady), { videos: 0, holds: 0, labels: [{ text: "已出片", ready: true, pending: false }] });
            check("film-pending-render", snapshot(filmPending), { videos: 0, holds: 0, labels: [{ text: "未出片", ready: false, pending: true }] });

            const leftover = renderNode(bare, { id: "S01", kind: "shot", title: "镜头 S01", shot: bare.shots[0], col: 0, row: 0, x: 0, y: 0, assets: [] });
            leftover.appendChild(makeVideo("/media/projects/p1/clips/S01.mp4"));
            const hold = document.createElement("span");
            hold.className = "wf-video-hold";
            leftover.appendChild(hold);
            canvasNodes.replaceChildren = null;
            canvasNodes.children = [];
            leftover.dataset.id = "S01";
            leftover.dataset.kind = "shot";
            canvasNodes.appendChild(leftover);
            patchCanvas(ready);
            check("patch-strips-video", snapshot(leftover), { videos: 0, holds: 0, labels: [{ text: "已出片", ready: true, pending: false }] });
            patchCanvas(bare);
            check("patch-to-pending", snapshot(leftover), { videos: 0, holds: 0, labels: [{ text: "未出片", ready: false, pending: true }] });

            const detailsReady = renderContractVideo(ready, ready.shots[0]);
            const detailsPending = renderContractVideo(ready, ready.shots[1]);
            check("details-ready-video-count", detailsReady.querySelectorAll("video").length, 1);
            check("details-ready-src", detailsReady.querySelector("video").src, "/media/projects/p1/clips/S01.mp4");
            check("details-ready-preload", detailsReady.querySelector("video").preload, "metadata");
            check("details-ready-class", detailsReady.className, "contract-video");
            check("details-pending-video-count", detailsPending.querySelectorAll("video").length, 0);
            check("details-pending-text", detailsPending.textContent, "未出片");
            check("details-pending-empty-class", detailsPending.querySelector(".contract-video-empty") != null, true);
            console.log(JSON.stringify({ cases: cases }));
            """
        )
        failed = [item for item in report["cases"] if not item["ok"]]
        self.assertEqual(failed, [], msg=json.dumps(failed, ensure_ascii=False, indent=2))

    def test_front_helpers_are_present_as_named_functions(self):
        source = APP.read_text(encoding="utf-8")
        for name in (
            "hasClipOutput",
            "hasFilmOutput",
            "outputStatusText",
            "outputStatusLabel",
            "syncOutputStatus",
            "renderContractVideo",
        ):
            self.assertIsNotNone(re.search(r"^function %s\(" % name, source, re.M), name)


if __name__ == "__main__":
    unittest.main()
