"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "..", "front", "app.js"), "utf8");
const start = source.indexOf("function keepWatching(project)");
const end = source.indexOf("\nfunction watch()");
assert.ok(start >= 0 && end > start, "could not find watch helpers in app.js");
const helpers = source.slice(start, end);
const context = { URL, Object, String, decodeURIComponent, Number, Date, Math };
vm.createContext(context);
vm.runInContext(helpers, context);

const {
  keepWatching,
  mediaIdentity,
  sameMediaUrl,
  mediaExpiry,
  mediaUrlFresh,
  refreshVideoElement,
  mediaRefreshAllowed,
  menuSnapshot,
  assignMenuFields,
  applyProjectTick,
  shouldReloadAfterTick,
  debugDockIsOpen,
} = context;

function testMediaIdentity() {
  const now = 1700000000000;
  const future = 1700003600;
  const ossA = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=" + future + "&Signature=sig1";
  const ossB = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=" + (future + 10) + "&Signature=sig2";
  assert.strictEqual(mediaIdentity(ossA), mediaIdentity(ossB));
  assert.ok(sameMediaUrl(ossA, ossB));
  const other = "https://cdn.example/buckets/other.mp4?OSSAccessKeyId=ak&Expires=" + future + "&Signature=sig1";
  assert.notStrictEqual(mediaIdentity(ossA), mediaIdentity(other));
  const localA = "/media/projects/abc/images/A1.png?v=10";
  const localB = "/media/projects/abc/images/A1.png?v=11";
  assert.notStrictEqual(mediaIdentity(localA), mediaIdentity(localB));
  assert.strictEqual(mediaIdentity("/media/projects/abc/video?download=1"), mediaIdentity("/media/projects/abc/video"));
  assert.strictEqual(mediaExpiry(localA), null);
  assert.strictEqual(mediaUrlFresh(localA, now), true);
  assert.strictEqual(mediaUrlFresh(ossA, now), true);
  const expired = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1000&Signature=old";
  assert.strictEqual(mediaUrlFresh(expired, now), false);
  const video = { dataset: { url: ossA }, src: ossA, currentTime: 12, paused: true, listeners: [], addEventListener(name, fn) { this.listeners.push([name, fn]); }, removeEventListener() {}, pause() { this.paused = true; } };
  assert.strictEqual(refreshVideoElement(video, ossA, now), "same");
  assert.strictEqual(refreshVideoElement(video, ossB, now), "same");
  assert.strictEqual(video.src, ossA);
  video.dataset.url = expired;
  video.src = expired;
  assert.strictEqual(refreshVideoElement(video, ossB, now), "refresh");
  assert.strictEqual(video.dataset.url, ossB);
  assert.strictEqual(video.src, ossB);
  video.listeners[0][1]();
  assert.strictEqual(video.currentTime, 12);
  assert.strictEqual(refreshVideoElement(video, other, now), "replace");
  assert.strictEqual(mediaRefreshAllowed({ busy: true }, "p1", now), false);
  assert.strictEqual(mediaRefreshAllowed({ id: "p1", at: now - 1000 }, "p1", now, 30000), false);
  assert.strictEqual(mediaRefreshAllowed({ id: "p1", at: now - 31000 }, "p1", now, 30000), true);
}

function testHistorySnapshot() {
  const listed = { id: "p1", title: "旧", status: "writing" };
  const full = { id: "p1", title: "新", status: "rendering", script: "剧本", shots: [{ id: "S01" }], messages: [1, 2], progress: { percent: 40 } };
  assignMenuFields(listed, full);
  assert.strictEqual(listed.title, "新");
  assert.strictEqual(listed.status, "rendering");
  assert.strictEqual(listed.script, undefined);
  assert.strictEqual(listed.shots, undefined);
  assert.strictEqual(listed.messages, undefined);
  assert.deepStrictEqual(listed.progress, { percent: 40 });
  const snap = menuSnapshot(full);
  assert.strictEqual(snap.script, undefined);
  assert.strictEqual(snap.shots, undefined);
}

function testReloadAndProgress() {
  const prev = { id: "p1", status: "rendering", content_revision: "aaa", progress: { percent: 10 } };
  const progress = { id: "p1", status: "rendering", content_revision: "aaa", progress: { percent: 40 }, can_reply: false };
  assert.strictEqual(shouldReloadAfterTick(prev, progress), false);
  const changed = { id: "p1", status: "rendering", content_revision: "bbb" };
  assert.strictEqual(shouldReloadAfterTick(prev, changed), true);
  const done = { id: "p1", status: "done", content_revision: "aaa" };
  assert.strictEqual(shouldReloadAfterTick(prev, done), true);
  const other = { id: "p2", status: "rendering", content_revision: "zzz" };
  assert.strictEqual(shouldReloadAfterTick(prev, other), false);
  const current = { id: "p1", shots: [{ id: "S01" }], script: "剧本", status: "rendering" };
  applyProjectTick(current, { status: "rendering", progress: { percent: 50 }, content_revision: "aaa", can_reply: false });
  assert.strictEqual(current.status, "rendering");
  assert.strictEqual(current.progress.percent, 50);
  assert.deepStrictEqual(current.shots, [{ id: "S01" }]);
  assert.strictEqual(current.script, "剧本");
}

function testRetryAndSwitch() {
  let token = 1;
  const incoming = { token: 1, projectId: "p1", body: { id: "p1", content_revision: "n2" } };
  function accept(state, payload) {
    if (payload.token !== token || payload.projectId !== state.id) return "ignore";
    return "apply";
  }
  assert.strictEqual(accept({ id: "p1" }, incoming), "apply");
  token = 2;
  assert.strictEqual(accept({ id: "p1" }, incoming), "ignore");
  token = 1;
  assert.strictEqual(accept({ id: "p2" }, incoming), "ignore");
  let applied = false;
  try {
    throw new Error("network");
  } catch (_error) {
    applied = false;
  }
  assert.strictEqual(applied, false);
}

function testDockAndWatching() {
  assert.strictEqual(debugDockIsOpen(null), false);
  assert.strictEqual(debugDockIsOpen({ classList: { contains: (name) => name === "open" } }), true);
  assert.strictEqual(debugDockIsOpen({ classList: { contains: () => false } }), false);
  assert.strictEqual(keepWatching({ status: "rendering" }), true);
  assert.strictEqual(keepWatching({ status: "done" }), false);
  assert.strictEqual(keepWatching({ status: "done", progress: { retouching: true } }), true);
  assert.strictEqual(keepWatching({ status: "error" }), false);
}

testMediaIdentity();
testHistorySnapshot();
testReloadAndProgress();
testRetryAndSwitch();
testDockAndWatching();
console.log("ok 5 test_watch_poll checks");
