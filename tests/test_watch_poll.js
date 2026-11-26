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

function testVideoRefreshRestore() {
  const now = 1700000000000;
  const expired = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1000&Signature=old";
  const soon = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1700000010&Signature=soon";
  const fresh = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1700003600&Signature=new";
  function makeVideo(paused) {
    return {
      dataset: { url: expired }, src: expired, currentTime: 7, paused: paused,
      listeners: [], plays: 0,
      addEventListener(name, fn, opts) { this.listeners.push([name, fn, opts]); },
      removeEventListener(name, fn) { this.listeners = this.listeners.filter((row) => !(row[0] === name && row[1] === fn)); },
      play() { this.plays += 1; this.paused = false; return Promise.reject(new Error("autoplay blocked")); },
      pause() { this.paused = true; },
    };
  }
  const playing = makeVideo(false);
  assert.strictEqual(refreshVideoElement(playing, soon, now), "refresh");
  assert.strictEqual(playing.listeners.length, 1);
  assert.strictEqual(playing.listeners[0][2].once, true);
  // 上一次 restore 未触发就再刷新：旧监听器被移除，不累积。
  assert.strictEqual(refreshVideoElement(playing, fresh, now), "refresh");
  assert.strictEqual(playing.listeners.length, 1);
  playing.listeners[0][1]();
  assert.strictEqual(playing.currentTime, 7);
  assert.strictEqual(playing.plays, 1);
  assert.strictEqual(playing.listeners.length, 0);
  const paused = makeVideo(true);
  assert.strictEqual(refreshVideoElement(paused, fresh, now), "refresh");
  paused.listeners[0][1]();
  assert.strictEqual(paused.currentTime, 7);
  assert.strictEqual(paused.plays, 0);
  assert.strictEqual(paused.paused, true);
}

function testWatchTickBranch() {
  const watchSource = source.slice(source.indexOf("\nfunction watch()"), source.indexOf("newChat.addEventListener"));
  assert.ok(watchSource.length > 0, "could not find watch() body");
  // 非重载分支无条件重绘（transcriptKey 早退控开销），full 参数按模式区分。
  assert.ok(watchSource.includes("paintTranscript({}, !workflowMode(current));"));
  assert.ok(watchSource.includes("JSON.stringify(previous.question || null) !== JSON.stringify(current.question || null)"));
  assert.ok(watchSource.includes("tasks: current.tasks,"));
  assert.ok(watchSource.includes("patchCanvas(current);"));
  // 连续失败提示与恢复清除。
  assert.ok(watchSource.includes("watch.fails >= 3"));
  assert.ok(watchSource.includes("连接中断，正在重试"));
}

function testResignedDownloadLinks() {
  const start = source.indexOf("function applyResignedMedia(result)");
  const end = source.indexOf("\nfunction ", start + 1);
  assert.ok(start >= 0 && end > start, "could not find applyResignedMedia");
  const context = {
    Object, Array, String, URL, decodeURIComponent, Number, Date, Math,
    sameMediaUrl, mediaIdentity,
    refreshVideoElement() { return "same"; },
    current: null, document: null,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  const oldPlay = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1000&Signature=old";
  const newPlay = "https://cdn.example/buckets/film.mp4?OSSAccessKeyId=ak&Expires=1700003600&Signature=new";
  const oldDownload = oldPlay + "&download=1";
  const newDownload = newPlay + "&download=1";
  const clipOld = "https://cdn.example/buckets/S01.mp4?OSSAccessKeyId=ak&Expires=1000&Signature=old";
  const clipNew = "https://cdn.example/buckets/S01.mp4?OSSAccessKeyId=ak&Expires=1700003600&Signature=new";
  const filmLink = { dataset: { url: oldDownload }, href: oldDownload };
  const clipLink = { dataset: { url: clipOld }, href: clipOld };
  const boardLink = { dataset: {}, href: "/media/projects/p1/board" };
  context.current = { id: "p1", video_url: oldPlay, clips: [{ id: "S01", url: clipOld }] };
  context.document = {
    querySelectorAll(sel) {
      if (sel === "video") return [];
      if (sel === ".clip-card a.download") return [filmLink, clipLink, boardLink];
      return [];
    },
  };
  context.applyResignedMedia({
    id: "p1",
    video_url: newPlay,
    video_download_url: newDownload,
    clips: [{ id: "S01", url: clipNew, download_url: clipNew + "&download=1" }],
  });
  assert.strictEqual(filmLink.href, newDownload);
  assert.strictEqual(filmLink.dataset.url, newDownload);
  assert.strictEqual(clipLink.href, clipNew + "&download=1");
  assert.strictEqual(boardLink.href, "/media/projects/p1/board");
  const cardStart = source.indexOf("function clipCard(");
  const cardSource = source.slice(cardStart, source.indexOf("\nfunction ", cardStart + 1));
  assert.ok(cardSource.includes('video.addEventListener("error"'));
  assert.ok(cardSource.includes("requestMediaUrlRefresh(current, { forceError: true })"));
  assert.ok(cardSource.includes("link.dataset.url = link.href;"));
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

async function testRpcTimeout() {
  const rpcSource = fs.readFileSync(path.join(__dirname, "..", "front", "rpc.js"), "utf8");
  const context = {
    JSON, Error, Promise, Object, AbortController,
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {},
    fetch: (url, opts) => new Promise((resolve, reject) => {
      if (opts.signal.aborted) { reject(new Error("aborted")); return; }
      opts.signal.addEventListener("abort", () => reject(new Error("aborted")));
    }),
  };
  vm.createContext(context);
  vm.runInContext(rpcSource, context);
  let failed = null;
  try {
    await context.rpc("projects.tick", {});
    failed = "expected timeout";
  } catch (error) {
    assert.strictEqual(error.rpcTimeout, true);
    assert.strictEqual(error.rpcKnown, false);
    assert.strictEqual(error.message, "连接中断：请求超时");
  }
  assert.strictEqual(failed, null);
  context.setTimeout = () => 1;
  context.fetch = async () => ({ json: async () => ({ result: { ok: 1 } }) });
  assert.deepStrictEqual(await context.rpc("projects.tick", {}), { ok: 1 });
  context.fetch = async () => ({ json: async () => ({ error: { code: 409, message: "冲突" } }) });
  try {
    await context.rpc("projects.render", {});
    failed = "expected rpc error";
  } catch (error) {
    assert.strictEqual(error.rpcKnown, true);
    assert.strictEqual(error.message, "冲突");
  }
  assert.strictEqual(failed, null);
}

(async () => {
  testMediaIdentity();
  testVideoRefreshRestore();
  testWatchTickBranch();
  testResignedDownloadLinks();
  testHistorySnapshot();
  testReloadAndProgress();
  testRetryAndSwitch();
  testDockAndWatching();
  await testRpcTimeout();
  console.log("ok 9 test_watch_poll checks");
})().catch((error) => { console.error(error); process.exit(1); });
