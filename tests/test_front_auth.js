"use strict";

// WS6 账户系统前端接入：401 识别、auth.me 引导分支、watch 轮询停跳、登出入口。
// 与 test_watch_poll.js 相同：不依赖 DOM，从源码切片进 vm 上下文验证。

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const appSource = fs.readFileSync(path.join(__dirname, "..", "front", "app.js"), "utf8");
const rpcSource = fs.readFileSync(path.join(__dirname, "..", "front", "rpc.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(__dirname, "..", "front", "index.html"), "utf8");

const authStart = appSource.indexOf("function authRedirectTarget(state)");
const authEnd = appSource.indexOf("\nfunction keepWatching(project)");
assert.ok(authStart >= 0 && authEnd > authStart, "could not find auth helpers in app.js");
const authBlock = appSource.slice(authStart, authEnd);

const watchStart = appSource.indexOf("\nfunction watch()");
const watchEnd = appSource.indexOf("newChat.addEventListener");
assert.ok(watchStart >= 0 && watchEnd > watchStart, "could not find watch() body");
const watchSource = appSource.slice(watchStart, watchEnd);

const openStart = appSource.indexOf("async function openProject(id)");
const openEnd = appSource.indexOf("\nfunction authRedirectTarget(state)");
assert.ok(openStart >= 0 && openEnd > openStart, "could not find openProject");
const openSource = appSource.slice(openStart, openEnd);

function rpcContext(fetchImpl, immediateTimeout) {
  const context = {
    JSON, Error, Promise, Object, AbortController,
    setTimeout: immediateTimeout ? (fn) => { fn(); return 1; } : () => 1,
    clearTimeout: () => {},
    fetch: fetchImpl,
  };
  vm.createContext(context);
  vm.runInContext(rpcSource, context);
  return context;
}

async function rpcFailure(fetchImpl, immediateTimeout) {
  const context = rpcContext(fetchImpl, immediateTimeout);
  try {
    await context.rpc("projects.tick", { id: "p1" });
  } catch (error) {
    return { error, context };
  }
  throw new Error("expected rpc to fail");
}

async function testRpcAuthCode() {
  const unauthorized = await rpcFailure(async () => ({
    json: async () => ({ error: { code: 401, message: "需要登录" } }),
  }));
  assert.strictEqual(unauthorized.error.code, 401);
  assert.strictEqual(unauthorized.error.rpcKnown, true);
  assert.strictEqual(unauthorized.error.message, "需要登录");
  assert.strictEqual(unauthorized.context.rpcNeedsLogin(unauthorized.error), true);

  const conflict = await rpcFailure(async () => ({
    json: async () => ({ error: { code: 409, message: "冲突" } }),
  }));
  assert.strictEqual(conflict.context.rpcNeedsLogin(conflict.error), false);

  // 网络中断与超时不是 401，不能误跳登录页（FE-7 行为保持）。
  const offline = await rpcFailure(async () => { throw new Error("fetch failed"); });
  assert.strictEqual(offline.error.rpcKnown, false);
  assert.strictEqual(offline.error.code, 0);
  assert.strictEqual(offline.context.rpcNeedsLogin(offline.error), false);
  const timedOut = await rpcFailure(
    (url, opts) => new Promise((resolve, reject) => {
      if (opts.signal.aborted) { reject(new Error("aborted")); return; }
      opts.signal.addEventListener("abort", () => reject(new Error("aborted")));
    }),
    true
  );
  assert.strictEqual(timedOut.error.rpcTimeout, true);
  assert.strictEqual(timedOut.context.rpcNeedsLogin(timedOut.error), false);
  assert.strictEqual(unauthorized.context.rpcNeedsLogin(null), false);
  assert.strictEqual(unauthorized.context.rpcNeedsLogin({ code: 401 }), false);
  return unauthorized.error;
}

// 用 rpc.js 里真实的 rpcNeedsLogin，避免测试与实现脱节。
const rpcBase = rpcContext(async () => ({ json: async () => ({ result: {} }) }));

function authContext(extra) {
  const context = Object.assign({
    Object, JSON, String, Number, Promise,
    rpc: async () => ({}),
    document: { querySelector() { return null; } },
    location: { href: "" },
    clearInterval() {},
    timer: null,
    clock: null,
    rpcNeedsLogin: rpcBase.rpcNeedsLogin,
  }, extra || {});
  vm.createContext(context);
  vm.runInContext(authBlock, context);
  return context;
}

function testAuthBranches() {
  const context = authContext();
  const decide = context.authRedirectTarget;
  // required + 未登录 → 登录页。
  assert.strictEqual(decide({ required: true, user: null }), "/login.html");
  // required + 已登录 → 放行。
  assert.strictEqual(decide({ required: true, user: { id: 1, username: "u" } }), "");
  // 非 required → 放行（无论有无 user）。
  assert.strictEqual(decide({ required: false, user: null }), "");
  assert.strictEqual(decide({ required: false, user: { id: 1 } }), "");
  // 已登录但必须改密 → 登录页（登录页处理改密），与 required 无关。
  assert.strictEqual(decide({ required: true, user: { must_change_password: true } }), "/login.html");
  assert.strictEqual(decide({ required: false, user: { must_change_password: true } }), "/login.html");
  // auth.me 调用失败（null）→ 降级放行。
  assert.strictEqual(decide(null), "");
  assert.strictEqual(decide(undefined), "");
}

async function testBootAccount() {
  async function boot(state, failure) {
    const context = authContext({
      rpc: async (method) => {
        assert.strictEqual(method, "auth.me");
        if (failure) throw failure;
        return state;
      },
    });
    const ready = await context.bootAccount();
    return { ready, href: context.location.href };
  }
  assert.deepStrictEqual(await boot({ required: true, user: null }), { ready: false, href: "/login.html" });
  assert.deepStrictEqual(await boot({ required: true, user: { id: 1 } }), { ready: true, href: "" });
  assert.deepStrictEqual(await boot({ required: false, user: null }), { ready: true, href: "" });
  assert.deepStrictEqual(await boot({ required: true, user: { must_change_password: true } }), { ready: false, href: "/login.html" });
  // 网络失败 / 后端未启用账户系统：不阻塞启动。
  const network = new Error("连接中断");
  network.rpcKnown = false;
  assert.deepStrictEqual(await boot(null, network), { ready: true, href: "" });
  const missing = new Error("没有完成");
  missing.rpcKnown = true;
  missing.code = -32601;
  assert.deepStrictEqual(await boot(null, missing), { ready: true, href: "" });
}

function watchContext(rpcImpl) {
  const intervals = [];
  const cleared = [];
  const notices = [];
  const context = authContext({
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval(id) { cleared.push(id); },
    tickElapsed() {},
    setSaveState(text) { notices.push(text); },
    current: { id: "p1", status: "writing", content_revision: "old" },
    projects: [],
    keepWatching() { return true; },
    rpc: rpcImpl,
    debugDock: null,
    debugDockIsOpen() { return false; },
    renderHistory() {},
    applyProjectTick(target, tick) { return Object.assign(target, tick); },
    applyWorkerQueue() {},
    assignMenuFields() {},
    shouldReloadAfterTick() { return true; },
  });
  vm.runInContext(watchSource, context);
  return { context, intervals, cleared, notices };
}

async function testWatchAuthStop(authError) {
  // projects.tick 返回 401：停轮询并跳登录页，不进重试计数。
  const tickFail = watchContext(async () => { throw authError; });
  tickFail.context.watch();
  const poll = tickFail.intervals.find((row) => row.ms === 2000);
  assert.ok(poll, "watch() should schedule a 2s poll");
  await poll.fn();
  assert.strictEqual(tickFail.context.location.href, "/login.html");
  assert.ok(tickFail.cleared.includes(tickFail.intervals.indexOf(poll) + 1), "poll timer cleared");
  assert.ok(tickFail.cleared.includes(1), "elapsed clock cleared");
  assert.strictEqual(tickFail.context.watch.fails, 0);
  assert.deepStrictEqual(tickFail.notices, []);
  assert.strictEqual(tickFail.context.watch.busy, false);

  // 重载分支 projects.get 返回 401：同样停跳。
  let calls = 0;
  const getFail = watchContext(async (method) => {
    calls += 1;
    if (method === "projects.tick") return { id: "p1", status: "writing", content_revision: "new" };
    throw authError;
  });
  getFail.context.watch();
  await getFail.intervals.find((row) => row.ms === 2000).fn();
  assert.strictEqual(calls, 2);
  assert.strictEqual(getFail.context.location.href, "/login.html");

  // 普通网络失败仍走 FE-7 重试提示，不跳转。
  const offline = new Error("连接中断");
  offline.rpcKnown = false;
  offline.code = 0;
  const retry = watchContext(async () => { throw offline; });
  retry.context.watch();
  const retryPoll = retry.intervals.find((row) => row.ms === 2000);
  await retryPoll.fn();
  await retryPoll.fn();
  await retryPoll.fn();
  assert.strictEqual(retry.context.location.href, "");
  assert.strictEqual(retry.context.watch.fails, 3);
  assert.ok(retry.notices.includes("连接中断，正在重试"));
  assert.strictEqual(retry.context.watch.busy, false);
}

async function testOpenProjectAuth(authError) {
  const context = authContext({
    setMenuOpen() {},
    current: null,
    rpc: async () => { throw authError; },
  });
  vm.runInContext(openSource, context);
  await context.openProject("p1");
  assert.strictEqual(context.location.href, "/login.html");
  assert.strictEqual(context.current, null);

  const offline = new Error("连接中断");
  offline.rpcKnown = false;
  const other = authContext({
    setMenuOpen() {},
    current: null,
    rpc: async () => { throw offline; },
  });
  vm.runInContext(openSource, other);
  await other.openProject("p1");
  assert.strictEqual(other.location.href, "");
}

function logoutFixture(rpcImpl) {
  const button = {
    classList: { removed: [], remove(name) { this.removed.push(name); } },
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    title: "",
    disabled: false,
  };
  const calls = [];
  const context = authContext({
    document: { querySelector(sel) { return sel === "#logout" ? button : null; } },
    rpc: async (method, params) => { calls.push([method, params]); return rpcImpl ? rpcImpl() : {}; },
  });
  return { button, calls, context };
}

async function testLogoutButton() {
  const ok = logoutFixture();
  ok.context.showLogout({ username: "导演" });
  assert.ok(ok.button.classList.removed.includes("hidden"));
  assert.ok(ok.button.title.includes("导演"));
  assert.strictEqual(typeof ok.button.listeners.click, "function");
  await ok.button.listeners.click();
  // vm 上下文里的对象原型不同，按序列化比较。
  assert.strictEqual(JSON.stringify(ok.calls), '[["auth.logout",{}]]');
  assert.strictEqual(ok.context.location.href, "/login.html");
  assert.strictEqual(ok.button.disabled, true);

  // auth.logout 失败也回登录页。
  const fail = logoutFixture(async () => { throw new Error("连接中断"); });
  fail.context.showLogout({ username: "u" });
  await fail.button.listeners.click();
  assert.strictEqual(fail.context.location.href, "/login.html");

  // 未登录（user=null）不显示登出。
  const anon = logoutFixture();
  anon.context.showLogout(null);
  assert.deepStrictEqual(anon.button.classList.removed, []);
  assert.strictEqual(anon.button.listeners.click, undefined);
}

function testStaticWiring() {
  // 启动引导包住原有初始化，auth.me 之前不做 RPC 初始化。
  const gate = appSource.indexOf("bootAccount().then((ready) => {");
  assert.ok(gate >= 0, "boot gate missing");
  ["loadConfig();", "refreshList();", "mountDurationFixture();"].forEach((call) => {
    assert.ok(appSource.indexOf(call, gate) > gate, call + " should run inside the boot gate");
  });
  assert.ok(appSource.trimEnd().endsWith("});"), "boot gate should be the last statement");
  // 登出按钮在 app-bar 内，默认隐藏，复用现有样式类。
  const barStart = indexHtml.indexOf('id="app-bar"');
  const barEnd = indexHtml.indexOf("</header>");
  const logoutAt = indexHtml.indexOf('id="logout"');
  assert.ok(barStart >= 0 && logoutAt > barStart && logoutAt < barEnd, "logout button should sit in app-bar");
  assert.ok(indexHtml.includes('id="logout" class="theme-toggle hidden"'));
  // 缓存版本参数保持不动（服务端按内容哈希注入）。
  assert.ok(indexHtml.includes("/styles.css?v=59"));
  assert.ok(indexHtml.includes("/rpc.js?v=2"));
  assert.ok(indexHtml.includes("/app.js?v=96"));
}

(async () => {
  const authError = await testRpcAuthCode();
  testAuthBranches();
  await testBootAccount();
  await testWatchAuthStop(authError);
  await testOpenProjectAuth(authError);
  await testLogoutButton();
  testStaticWiring();
  console.log("ok 7 test_front_auth checks");
})().catch((error) => { console.error(error); process.exit(1); });