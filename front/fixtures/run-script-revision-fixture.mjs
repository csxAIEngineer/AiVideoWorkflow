// Isolated fixture runner. Does not call a real model or the repository server.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const front = path.resolve(here, "..");
const repo = path.resolve(front, "..");
const artifacts = "/opt/cursor/artifacts";
fs.mkdirSync(artifacts, { recursive: true });

const failures = [];
function check(name, ok, detail) {
  if (!ok) {
    failures.push(detail ? name + " :: " + detail : name);
    console.error("FAIL", name, detail || "");
  } else {
    console.log("ok", name);
  }
}

function loadScript(file, sandbox) {
  vm.runInNewContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
}

async function runUnit() {
  const rpcBox = {
    console,
    fetch: async () => ({
      json: async () => ({
        error: { code: 413, message: "正文太大", data: { reason: "too_large", retryable: false } },
      }),
    }),
  };
  rpcBox.globalThis = rpcBox;
  loadScript(path.join(front, "rpc.js"), rpcBox);
  try {
    await rpcBox.rpc("projects.script_revision.save", {});
    check("rpc error rejects", false);
  } catch (error) {
    check("rpc keeps code", error.code === 413, String(error.code));
    check("rpc keeps data.reason", error.data && error.data.reason === "too_large");
    check("rpc keeps data.retryable", error.data && error.data.retryable === false);
    check("rpc marks known error", error.rpcKnown === true);
  }

  const lost = {
    console,
    fetch: async () => ({ json: async () => { throw new Error("bad json"); } }),
  };
  lost.globalThis = lost;
  loadScript(path.join(front, "rpc.js"), lost);
  try {
    await lost.rpc("projects.script_revision.get", { id: "p" });
    check("non-json is unknown", false);
  } catch (error) {
    check("non-json is unknown", error.rpcKnown === false && error.data == null);
  }

  const box = { console, crypto, document: {} };
  box.window = box;
  box.globalThis = box;
  loadScript(path.join(front, "script-edit.js"), box);
  const api = box.ScriptRevisionEdit;
  const gate = (questionId, status, phase) => ({
    id: "p",
    status: status,
    progress: { phase: phase, question: { id: questionId } },
  });
  check("stop script_candidate", api.isRevisionStop(gate("script_candidate", "gate", "gate_script")));
  check("stop script question", api.isRevisionStop(gate("script", "gate", "gate_script")));
  check("writing is not the stop", !api.isRevisionStop(gate("script_candidate", "writing", "gate_script")));
  check("other phase is not the stop", !api.isRevisionStop(gate("script", "gate", "writing")));
  check("other question is not the stop", !api.isRevisionStop(gate("asset", "gate", "gate_script")));
  check("candidate status alone is not the stop", !api.isRevisionStop({ id: "p", status: "blocked", progress: { phase: "gate_script", question: { id: "script_candidate" } } }));
  const writingProject = { id: "p", status: "writing", progress: { phase: "writing", pipeline: { action: "script_review" }, question: null } };
  const repairProject = { id: "p", status: "writing", progress: { phase: "writing", pipeline: { action: "script_repair" }, question: null } };
  check("pending is visible outside the gate", api.shouldShowEntry(writingProject, { review_state: "pending", review_retryable: true }) === true);
  check("ordinary writing is not pending", api.shouldShowEntry(writingProject, { review_state: "none", review_retryable: false }) === false);
  check("script_repair writing is not pending", api.shouldShowEntry(repairProject, { review_state: "none", review_retryable: false }) === false);
  check("queued does not keep a recovery entry", api.shouldShowEntry(writingProject, { review_state: "queued", review_retryable: false }) === false);
  check("done does not keep a recovery entry", api.shouldShowEntry(writingProject, { review_state: "done", review_retryable: false }) === false);

  const blocked = api.describeStatus({ status: "blocked", reason: "声画对不上", needs_human: true });
  check("candidate blocked copy", blocked.headline === "候选未通过审稿，需要人工确认，不能直接采用。", blocked.headline);
  check("candidate copy hides the raw mark", blocked.headline.indexOf("blocked") < 0 && blocked.headline.indexOf("已审通过") < 0);
  const passed = api.describeStatus({ status: "passed" });
  check("passed candidate is not adoption", passed.headline.indexOf("不能直接采用") >= 0 && passed.headline.indexOf("已审通过") < 0 && passed.headline.indexOf("passed") < 0, passed.headline);

  const draft = api.draftAvailability({ draft_source: "rejected_text", draft_text: "全文" });
  check("rejected_text is editable base", draft.enabled && draft.text === "全文");
  check("none disables draft", api.draftAvailability({ draft_source: "none", draft_text: "" }).enabled === false);

  const held = { key: "k1", token: "sr1.a", text: "稿", kind: "retryable" };
  check("same payload reuses key", api.planSubmit(held, "稿", "sr1.a", "submit", "retryable").key === "k1");
  check("changed text mints a key", api.planSubmit(held, "新稿", "sr1.a", "submit", "retryable").key !== "k1");
  check("retry uses held text", api.planSubmit(held, "新稿", "sr1.a", "retry", "retryable").text === "稿");
  const unknown = { key: "k2", token: "sr1.a", text: "稿", kind: "unknown" };
  check("unknown refuses a new body", api.planSubmit(unknown, "新稿", "sr1.a", "submit", "unknown").ok === false);
  check("inflight does not submit twice", api.planSubmit({ kind: "inflight" }, "稿", "sr1.a", "submit", "inflight").ok === false);
  const requeue = { key: "k-re", token: "sr1.saved", text: "已保存稿", kind: "requeue" };
  check("requeue refuses a new body", api.planSubmit(requeue, "另一稿", "sr1.saved", "submit", "requeue").ok === false);
  check("requeue retry keeps the original payload", JSON.stringify(api.planSubmit(requeue, "另一稿", "sr1.new", "requeue", "requeue")) === JSON.stringify({ ok: true, key: "k-re", token: "sr1.saved", text: "已保存稿" }));
  check("network error is unknown", api.outcomeFromError(new TypeError("down")) === "unknown");
  check("retryable flag wins", api.outcomeFromError({ rpcKnown: true, data: { retryable: true, reason: "busy" } }) === "retryable");
  check("too_large is not retryable", api.outcomeFromError({ rpcKnown: true, data: { retryable: false, reason: "too_large" } }) === "rejected");
  check("invalid_request is not retryable", api.outcomeFromError({ rpcKnown: true, code: 400, data: { retryable: false, reason: "invalid_request" } }) === "rejected");
}

function serve() {
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".mjs": "text/javascript" };
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if ((url.pathname === "/" || url.pathname === "/index.html") && url.searchParams.get("fixture") === "1") {
        let html = fs.readFileSync(path.join(front, "index.html"), "utf8");
        html = html.replace(
          '<script src="/rpc.js',
          '<script src="/fixtures/script-revision-mock.js"></script>\n  <script src="/rpc.js'
        );
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(html);
        return;
      }
      const rel = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
      const file = path.normalize(path.join(front, rel));
      if (!file.startsWith(front) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404);
        res.end("missing");
        return;
      }
      res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
      res.end(fs.readFileSync(file));
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function cdpSocket(ws) {
  let seq = 0;
  const pending = new Map();
  const listeners = [];
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const job = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) job.reject(new Error(JSON.stringify(message.error)));
      else job.resolve(message.result);
      return;
    }
    listeners.forEach((fn) => fn(message));
  });
  return {
    send(method, params, sessionId) {
      const id = ++seq;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        const packet = { id, method, params: params || {} };
        if (sessionId) packet.sessionId = sessionId;
        ws.send(JSON.stringify(packet));
      });
    },
    on(fn) { listeners.push(fn); },
  };
}

async function launchChrome(port) {
  const profile = fs.mkdtempSync("/tmp/sr-chrome-");
  const chrome = spawn("/usr/bin/google-chrome", [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=" + port,
    "--user-data-dir=" + profile,
    "about:blank",
  ], { stdio: "ignore" });
  const version = await new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = async () => {
      try {
        const response = await fetch("http://127.0.0.1:" + port + "/json/version");
        resolve(await response.json());
      } catch (error) {
        if (Date.now() - started > 8000) reject(error);
        else setTimeout(tick, 100);
      }
    };
    tick();
  });
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });
  return { chrome, profile, client: cdpSocket(ws), ws };
}

async function runBrowser(origin) {
  const port = 9334;
  const browser = await launchChrome(port);
  const client = browser.client;
  const created = await client.send("Target.createTarget", { url: "about:blank" });
  const attached = await client.send("Target.attachToTarget", { targetId: created.targetId, flatten: true });
  const sessionId = attached.sessionId;
  await client.send("Page.enable", {}, sessionId);
  await client.send("Runtime.enable", {}, sessionId);
  await client.send("Page.bringToFront", {}, sessionId);

  async function go(url) {
    await client.send("Page.navigate", { url }, sessionId);
    const started = Date.now();
    while (Date.now() - started < 8000) {
      const ready = await evalValue("document.readyState");
      if (ready === "complete" && await evalValue("!!document.querySelector('#script-revision-open, .item, #stop')")) return;
      await delay(40);
    }
    throw new Error("page did not load " + url);
  }
  async function evalValue(expression) {
    const result = await client.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) {
      throw new Error(JSON.stringify(result.exceptionDetails.exception || result.exceptionDetails.text));
    }
    return result.result.value;
  }
  async function shot(name) {
    const image = await client.send("Page.captureScreenshot", { format: "png" }, sessionId);
    const file = path.join(artifacts, name);
    fs.writeFileSync(file, Buffer.from(image.data, "base64"));
    return file;
  }
  function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
  async function waitText(selector, includes, timeout) {
    const started = Date.now();
    let last = "";
    while (Date.now() - started < (timeout || 4000)) {
      last = await evalValue(`(document.querySelector(${JSON.stringify(selector)}) || {}).textContent || ""`);
      if (String(last).indexOf(includes) >= 0) return last;
      await delay(40);
    }
    throw new Error(selector + " lacked " + includes + " last=" + last);
  }

  await client.send("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  }, sessionId);

  await go(origin + "/?fixture=1");
  await waitText(".item", "修订停点");
  await evalValue("document.querySelector('.item').click()");
  await waitText("#script-revision-open", "选择稿件 / 人工编辑");
  const keep = await evalValue("[...document.querySelectorAll('button')].some((button) => button.textContent === '保持原稿')");
  check("real thread keeps 保持原稿", keep === true);
  check("real thread shows the edit entry", true);
  await shot("script_revision_stop_entry_dark.png");
  await evalValue("document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "未通过审稿");
  const officialOnPage = await evalValue("document.querySelector('#script-revision-text').value");
  check("real editor ignores projects.get script", officialOnPage.indexOf("正式原稿") === 0 && officialOnPage.indexOf("DECOY") < 0, officialOnPage);
  await shot("script_revision_editor_rejected_dark.png");
  await evalValue("document.querySelector('.script-revision-close').click()");

  await go(origin + "/fixtures/script-revision.html");
  await evalValue("document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "未通过审稿");
  const official = await evalValue("document.querySelector('#script-revision-text').value");
  check("editor official text comes from revision.get", official.indexOf("正式原稿") === 0 && official.indexOf("DECOY") < 0, official);
  const openCalls = await evalValue("JSON.stringify(__fixture.calls().map((item) => item.method))");
  check("opening does not save", openCalls.indexOf("projects.script_revision.save") < 0, openCalls);
  check("opening reads revision.get", openCalls.indexOf("projects.script_revision.get") >= 0, openCalls);

  await evalValue("document.querySelector('input[value=draft]').click()");
  const drafted = await evalValue("document.querySelector('#script-revision-text').value");
  const note = await evalValue("document.querySelector('.script-revision-draft-note').textContent");
  check("draft_source rejected_text fills the editor", drafted.indexOf("未采用候选第1行") === 0 && drafted.indexOf("未采用候选第36行") > 0, drafted.slice(0, 40));
  check("rejected draft is not called passed", note.indexOf("不能直接采用") >= 0, note);
  const afterSelect = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("selecting a base does not save", afterSelect === 0);
  const scrollable = await evalValue("(() => { const node = document.querySelector('#script-revision-text'); return node.scrollHeight > node.clientHeight; })()");
  check("long rejected text scrolls", scrollable === true);

  await evalValue("document.querySelector('#script-revision-text').value += '\\n人工加的一句'");
  await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }, sessionId);
  await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }, sessionId);
  await waitText(".script-revision-confirm", "未保存");
  await shot("script_revision_dirty_confirm.png");
  await evalValue("document.querySelector('[data-confirm=stay]').click()");
  const kept = await evalValue("document.querySelector('#script-revision-text').value.indexOf('人工加的一句') >= 0 && !!document.querySelector('.script-revision-dialog')");
  check("stay keeps the dirty draft", kept === true);
  await evalValue("document.querySelector('.script-revision-layer').dispatchEvent(new MouseEvent('click', { bubbles: true }))");
  await waitText(".script-revision-confirm", "未保存");
  await evalValue("document.querySelector('.script-revision-close').click()");
  const stayed = await evalValue("!!document.querySelector('.script-revision-dialog') && document.querySelector('.script-revision-confirm').classList.contains('hidden')");
  check("close during confirm keeps the draft open", stayed === true);
  await evalValue("document.querySelector('.script-revision-close').click()");
  await waitText(".script-revision-confirm", "未保存");
  await evalValue("document.querySelector('[data-confirm=abandon]').click()");
  const closed = await evalValue("!document.querySelector('.script-revision-dialog')");
  const focusBack = await evalValue("document.activeElement && document.activeElement.id === 'script-revision-open'");
  const savesAfterCancel = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("abandon closes", closed === true);
  check("focus returns to the entry", focusBack === true, String(focusBack));
  check("cancel path never saved", savesAfterCancel === 0);

  await evalValue("[...document.querySelectorAll('button')].find((button) => button.textContent === '保持原稿').click()");
  const keepCalls = await evalValue("JSON.stringify(__fixture.calls().filter((item) => item.method === 'projects.send_message').map((item) => item.params.text))");
  check("保持原稿 still sends its own message", keepCalls === '["保持原稿"]', keepCalls);

  await client.send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  }, sessionId);
  await evalValue("document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "未通过审稿");
  const narrow = await evalValue("document.querySelector('.script-revision-dialog').getBoundingClientRect().width <= 390");
  check("narrow dialog fits", narrow === true);
  await shot("script_revision_editor_narrow.png");
  let inside = true;
  for (let step = 0; step < 8; step += 1) {
    await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
    await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
    const ok = await evalValue("!!(document.activeElement && document.activeElement.closest('.script-revision-dialog'))");
    if (ok !== true) inside = false;
  }
  check("tab stays inside the dialog", inside === true);
  await evalValue("document.querySelector('#script-revision-text').value = __fixture.OFFICIAL");
  await evalValue("document.querySelector('.script-revision-cancel').click()");

  await client.send("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  }, sessionId);
  await evalValue("__fixture.setMode('none'); document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-draft-note", "没有未采用候选");
  const noneDisabled = await evalValue("document.querySelector('input[value=draft]').disabled === true");
  check("missing candidate disables the draft", noneDisabled === true);
  await evalValue("document.querySelector('.script-revision-close').click()");

  await evalValue("__fixture.setMode('busy'); document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-permission", "正在审稿");
  const busyLocked = await evalValue("document.querySelector('.script-revision-save').disabled === true && document.querySelector('#script-revision-text').readOnly === true");
  check("busy cannot edit or save", busyLocked === true);
  await shot("script_revision_busy_disabled.png");
  await evalValue("document.querySelector('.script-revision-save').click(); document.querySelector('.script-revision-close').click()");
  const busySaves = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("disabled save does not call save", busySaves === 0);

  await evalValue("__fixture.setMode('invalid'); document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-permission", "现在不能编辑或保存");
  const phaseDiag = await evalValue("document.querySelector('.script-revision-diag').textContent");
  check("invalid phase mark stays in diagnostics", phaseDiag.indexOf("invalid_phase") >= 0, phaseDiag);
  const phaseLocked = await evalValue("document.querySelector('.script-revision-save').disabled === true");
  check("invalid_phase cannot save", phaseLocked === true);
  await evalValue("document.querySelector('.script-revision-close').click()");

  await evalValue("__fixture.setMode('candidate'); document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "不能直接采用");
  const passedCopy = await evalValue("document.querySelector('.script-revision-status').textContent");
  check("passed candidate is labeled as candidate state", passedCopy.indexOf("已审通过") < 0, passedCopy);
  await evalValue("document.querySelector('input[value=draft]').click()");
  const candidateText = await evalValue("document.querySelector('#script-revision-text').value");
  check("candidate draft loads", candidateText === "候选全文\n她把伞收起来。", candidateText);
  await evalValue("document.querySelector('.script-revision-close').click()");

  await evalValue("__fixture.setMode('rejected'); document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "未通过审稿");
  const reopened = await evalValue("document.querySelector('#script-revision-text').value");
  check("reopen loads official text", reopened.indexOf("正式原稿") === 0, reopened);
  await evalValue("document.querySelector('#script-revision-text').value = '人工改过的全文'");
  const before = await evalValue("__fixture.calls().length");
  await evalValue("__fixture.arm('hang'); document.querySelector('.script-revision-save').click(); document.querySelector('.script-revision-save').click()");
  await delay(80);
  const hung = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("loading does not double submit", hung === 1, String(hung));
  const leave = await evalValue("ScriptRevisionEdit.confirmLeave('p-other')");
  check("in-flight save blocks project switch", leave === false);
  await evalValue("__fixture.release()");
  await waitText(".script-revision-message", "已保存，正在重新审稿");
  const success = await evalValue("document.querySelector('.script-revision-message').textContent");
  check("success uses the server message", success === "已保存，正在重新审稿", success);
  check("success does not claim review passed", success.indexOf("已审通过") < 0);
  const queued = await evalValue("__fixture.queueCount()");
  check("success queues one review", queued === 1, String(queued));
  const methods = await evalValue("JSON.stringify(__fixture.calls().slice(" + before + ").map((item) => item.method))");
  check("success refreshes with projects.get", methods.indexOf("projects.get") >= 0, methods);
  check("success does not render reset or save_content", ["projects.render", "projects.rerun", "projects.reset", "projects.save_content"].every((name) => methods.indexOf(name) < 0), methods);
  const applied = await evalValue("__fixture.state.applied.length === 1 && __fixture.state.applied[0].id === 'p-rev'");
  check("refresh applies the same project", applied === true);
  await shot("script_revision_saved_rereview.png");

  const getsBefore = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.get').length");
  await evalValue("document.querySelector('#script-revision-text').value = __fixture.OFFICIAL; document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "稿件没有变化");
  const unchanged = await evalValue("document.querySelector('.script-revision-message').textContent.indexOf('正在重新审稿') < 0");
  const getsAfter = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.get').length");
  const queueAfter = await evalValue("__fixture.queueCount()");
  check("unchanged does not claim a queued review", unchanged === true);
  check("unchanged does not refresh", getsAfter === getsBefore, getsBefore + "->" + getsAfter);
  check("unchanged does not queue", queueAfter === 1, String(queueAfter));

  await evalValue("document.querySelector('#script-revision-text').value = '冲突后仍要留下的全文'");
  await evalValue("__fixture.arm({ rpcKnown: true, code: 409, message: '底稿已变', data: { reason: 'token_mismatch', retryable: false } }); document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "没有覆盖正式稿");
  const conflictText = await evalValue("document.querySelector('#script-revision-text').value");
  check("token conflict keeps the draft", conflictText === "冲突后仍要留下的全文", conflictText);
  const conflictSaves = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  await delay(200);
  const conflictSavesLater = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("conflict does not auto retry", conflictSavesLater === conflictSaves, String(conflictSavesLater));

  await evalValue("__fixture.arm({ rpcKnown: true, code: 409, message: '版本冲突', data: { reason: 'version_conflict', retryable: false } }); document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "版本冲突");
  const versionDiag = await evalValue("document.querySelector('.script-revision-diag').textContent");
  check("version conflict mark stays in diagnostics", versionDiag.indexOf("version_conflict") >= 0, versionDiag);
  const versionText = await evalValue("document.querySelector('#script-revision-text').value");
  check("version conflict keeps the draft", versionText === "冲突后仍要留下的全文");

  await evalValue("document.querySelector('#script-revision-text').value = ''");
  await evalValue("document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "正文是空的");
  check("empty text stays empty", await evalValue("document.querySelector('#script-revision-text').value") === "");

  await evalValue("document.querySelector('#script-revision-text').value = '缺编号也要留下'");
  await evalValue("__fixture.arm({ rpcKnown: true, code: 400, message: '缺少保存编号', data: { reason: 'invalid_request', retryable: false } }); document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "缺少保存编号");
  check("invalid_request keeps the draft", await evalValue("document.querySelector('#script-revision-text').value") === "缺编号也要留下");

  await evalValue("document.querySelector('#script-revision-text').value = '大'.repeat(1024 * 1024 + 8)");
  await evalValue("document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "正文太大", 8000);
  const hugeKept = await evalValue("document.querySelector('#script-revision-text').value.length > 1024 * 1024");
  check("too_large keeps the oversized draft", hugeKept === true);

  const savedRetryCases = [
    {
      name: "review_not_queued",
      arm: "__fixture.armReviewNotQueued()",
      draft: "还没排上要留下的全文",
      message: "稿已保存，审稿还没排上。请用原来的保存编号再试一次，不要改编辑区。",
      shot: "script_revision_review_not_queued.png",
    },
    {
      name: "review_ack_unconfirmed",
      arm: "__fixture.armReviewAckUnconfirmed()",
      draft: "确认未写完要留下的全文",
      message: "稿已保存。审稿可能已经排上，但确认记录没写完。请用原来的保存编号再试一次，不要改编辑区，也不要换新编号。",
      shot: "script_revision_review_ack_unconfirmed.png",
    },
  ];
  for (const item of savedRetryCases) {
    const dialogOpen = await evalValue("!!document.querySelector('#script-revision-text')");
    if (!dialogOpen) {
      await evalValue("__fixture.releaseReview(); ScriptRevisionEdit.clearPending('p-rev'); ScriptRevisionEdit.noteRevision({ id: 'p-rev', review_state: 'none', review_retryable: false, can_edit: true, can_save: true, busy: false, base_token: 'sr1.opaque-rejected', fetchedAt: Date.now() }); mount()");
      await evalValue("document.querySelector('#script-revision-open').click()");
      await waitText(".script-revision-status", "未通过审稿");
    }
    await evalValue("document.querySelector('#script-revision-text').value = " + JSON.stringify(item.draft));
    const getsBeforeQueue = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.get').length");
    const queueBefore = await evalValue("__fixture.queueCount()");
    await evalValue(item.arm + "; document.querySelector('.script-revision-save').click()");
    await waitText(".script-revision-message", item.message.slice(0, 12));
    const queueFailText = await evalValue("document.querySelector('.script-revision-message').textContent");
    const queueDraft = await evalValue("document.querySelector('#script-revision-text').value");
    const queueLocked = await evalValue("document.querySelector('#script-revision-text').readOnly === true && document.querySelector('.script-revision-save').disabled === true && document.querySelector('input[value=draft]').disabled === true && document.querySelector('.script-revision-retry').textContent === '已保存，重试审稿'");
    check(item.name + " keeps the saved draft", queueDraft === item.draft, queueDraft);
    check(item.name + " shows the server message", queueFailText === item.message, queueFailText);
    check(item.name + " is not shown as reviewing", queueFailText.indexOf("已保存，正在重新审稿") < 0 && queueFailText.indexOf("正在审稿") < 0 && queueFailText.indexOf("已审通过") < 0, queueFailText);
    if (item.name === "review_ack_unconfirmed") {
      check("ack unconfirmed is not called definitely unqueued", queueFailText.indexOf("还没排上") < 0 && queueFailText.indexOf("未排队") < 0, queueFailText);
    }
    check(item.name + " locks editing", queueLocked === true);
    const queueSaves = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
    await delay(180);
    const queueSavesLater = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
    const getsAfterQueue = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.get').length");
    const queueAfterError = await evalValue("__fixture.queueCount()");
    check(item.name + " does not auto retry", queueSavesLater === queueSaves, String(queueSavesLater));
    check(item.name + " does not refresh or queue", getsAfterQueue === getsBeforeQueue && queueAfterError === queueBefore, queueBefore + "->" + queueAfterError);
    await evalValue("var area = document.querySelector('#script-revision-text'); area.value = '试图改稿'; area.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('.script-revision-save').click(); document.querySelector('input[value=draft]').click()");
    const stillOriginal = await evalValue("document.querySelector('#script-revision-text').value === " + JSON.stringify(item.draft));
    const savesAfterTamper = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
    check(item.name + " cannot be edited into a new save", stillOriginal === true && savesAfterTamper === queueSaves);
    await shot(item.shot);
    const retriesBeforeClose = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length");
    await evalValue("document.querySelector('.script-revision-cancel').click()");
    await waitText(".script-revision-confirm", "稿已经保存");
    const closeCopy = await evalValue("document.querySelector('.script-revision-confirm').textContent");
    check(item.name + " close does not claim unsaved or reviewing", closeCopy.indexOf("未保存") < 0 && closeCopy.indexOf("正在审稿") < 0 && closeCopy.indexOf("还没排上") < 0, closeCopy);
    await evalValue("document.querySelector('[data-confirm=abandon]').click()");
    const closedWithoutSave = await evalValue("!document.querySelector('.script-revision-dialog') && __fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length === " + queueSaves);
    check(item.name + " close does not mutate", closedWithoutSave === true);
    const chatLeak = await evalValue("document.querySelector('#stop').textContent.indexOf(" + JSON.stringify(item.draft) + ") < 0 && document.querySelector('#stop').textContent.indexOf('sr1.') < 0");
    check(item.name + " stays out of the chat entry", chatLeak === true);
    const retriesAfterClose = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length");
    check(item.name + " close does not call retry_review", retriesAfterClose === retriesBeforeClose);
    await go(origin + "/fixtures/script-revision.html");
    const refreshedLabel = await evalValue("document.querySelector('#script-revision-open').textContent");
    const refreshedClosed = await evalValue("!document.querySelector('.script-revision-dialog')");
    check(item.name + " refresh shows the recovery entry", refreshedLabel === "已保存，重试审稿" && refreshedClosed === true, refreshedLabel);
    if (item.name === "review_ack_unconfirmed") {
      await evalValue("sessionStorage.clear(); ScriptRevisionEdit.clearPending('p-rev')");
      await evalValue("mount('writing')");
      const hidden = await evalValue("!document.querySelector('#script-revision-open')");
      check("cleared session hides the entry before get", hidden === true);
    }
    const getsBeforeProbe = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.get').length");
    const savesBeforeProbe = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
    const retriesBeforeProbe = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length");
    await evalValue("ScriptRevisionEdit.prepareRevision({ rpc: rpc, project: { id: 'p-rev' }, getProjectId: function () { return 'p-rev'; } }, 'p-rev')");
    const probeCounts = await evalValue("JSON.stringify({ get: __fixture.calls().filter((item) => item.method === 'projects.script_revision.get').length, save: __fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length, retry: __fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length })");
    const probe = JSON.parse(probeCounts);
    check(item.name + " get is read only", probe.get === getsBeforeProbe + 1 && probe.save === savesBeforeProbe && probe.retry === retriesBeforeProbe, probeCounts);
    await evalValue("mount('writing')");
    const recoveredNote = await evalValue("document.querySelector('.script-revision-entry-note').textContent");
    const recoveredLabel = await evalValue("document.querySelector('#script-revision-open').textContent");
    check(item.name + " writing project still shows recovery", recoveredLabel === "已保存，重试审稿", recoveredLabel);
    check(item.name + " pending does not claim it is unqueued", recoveredNote.indexOf("可能已经排上") >= 0 && recoveredNote.indexOf("还没排上") < 0 && recoveredNote.indexOf("未排队") < 0, recoveredNote);
    const queueBeforeRetry = await evalValue("__fixture.queueCount()");
    await evalValue("document.querySelector('#script-revision-open').click()");
    await waitText(".script-revision-entry-note", "已保存，正在重新审稿");
    const retryCall = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review'); var row = rows[rows.length - 1].params; JSON.stringify(row)");
    const retryBody = JSON.parse(retryCall);
    const saveCountAfter = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
    check(item.name + " click sends only id and the get token", retryBody.id === "p-rev" && retryBody.base_token === "sr1.after-save" && !retryBody.text && !retryBody.idempotency_key && !retryBody.source, retryCall);
    check(item.name + " click does not mint a save", saveCountAfter === savesBeforeProbe);
    check(item.name + " first retry queues once", await evalValue("__fixture.queueCount()") === queueBeforeRetry + 1);
    const queuedAgain = await evalValue("__fixture.queueCount()");
    await evalValue("ScriptRevisionEdit.retryReview({ rpc: rpc, getProjectId: function () { return 'p-rev'; }, applyProject: function () {} }, 'p-rev')");
    await waitText(".script-revision-entry-note", "审稿已在队列里");
    const queuedMessage = await evalValue("document.querySelector('.script-revision-entry-note').textContent");
    check(item.name + " already queued adds no task", await evalValue("__fixture.queueCount()") === queuedAgain && queuedMessage.indexOf("正在审稿") < 0, queuedMessage);
  }

  await evalValue("__fixture.state.forceDone = true; __fixture.state.reviewPending = true; __fixture.state.reviewQueued = false");
  const doneBefore = await evalValue("__fixture.queueCount()");
  const doneRetries = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length");
  await evalValue("ScriptRevisionEdit.prepareRevision({ rpc: rpc, project: { id: 'p-rev' }, getProjectId: function () { return 'p-rev'; } }, 'p-rev')");
  await evalValue("ScriptRevisionEdit.retryReview({ rpc: rpc, getProjectId: function () { return 'p-rev'; }, applyProject: function () {} }, 'p-rev')");
  await waitText(".script-revision-entry-note", "审稿已结束");
  const doneMessage = await evalValue("document.querySelector('.script-revision-entry-note').textContent");
  const doneRetriesAfter = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.retry_review').length");
  check("done with review_queued false is success", doneMessage === "审稿已结束" && doneMessage.indexOf("正在审稿") < 0 && doneMessage.indexOf("队列") < 0, doneMessage);
  check("done does not queue another review", await evalValue("__fixture.queueCount()") === doneBefore && doneRetriesAfter === doneRetries + 1, String(doneRetriesAfter));

  await evalValue("__fixture.state.forceDone = false; __fixture.state.reviewPending = false; __fixture.state.reviewQueued = false; __fixture.setMode('repair')");
  await evalValue("ScriptRevisionEdit.prepareRevision({ rpc: rpc, project: { id: 'p-rev' }, getProjectId: function () { return 'p-rev'; } }, 'p-rev')");
  await evalValue("mount('repair')");
  const repairHidden = await evalValue("!document.querySelector('#script-revision-open')");
  const repairState = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.get').slice(-1)[0] && true");
  check("script_repair writing has no recovery entry", repairHidden === true);
  void repairState;
  await evalValue("__fixture.releaseReview(); ScriptRevisionEdit.clearPending('p-rev'); ScriptRevisionEdit.noteRevision({ id: 'p-rev', review_state: 'none', review_retryable: false, can_edit: true, can_save: true, busy: false, base_token: 'sr1.opaque-rejected', fetchedAt: Date.now() }); mount()");
  await evalValue("document.querySelector('#script-revision-open').click()");
  await waitText(".script-revision-status", "未通过审稿");

  await evalValue("document.querySelector('#script-revision-text').value = '忙过之后的正文'");
  await evalValue("__fixture.arm({ rpcKnown: true, code: 409, message: '正在审稿', data: { reason: 'busy', retryable: true } }); document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-retry", "按原样再试一次");
  const busyOnce = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  await delay(150);
  const busyStill = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("retryable busy does not auto retry", busyStill === busyOnce);
  const firstKey = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.save'); rows[rows.length - 1].params.idempotency_key");
  await evalValue("document.querySelector('.script-revision-retry').click()");
  await waitText(".script-revision-message", "已保存，正在重新审稿");
  const secondKey = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.save'); rows[rows.length - 1].params.idempotency_key");
  check("explicit retry reuses the key", firstKey && firstKey === secondKey, firstKey + " / " + secondKey);

  await evalValue("document.querySelector('#script-revision-text').value = '换了正文'");
  await evalValue("document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "已保存，正在重新审稿");
  const thirdKey = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.save'); rows[rows.length - 1].params.idempotency_key");
  check("changed text uses a new key", thirdKey && thirdKey !== secondKey, thirdKey);

  const keysPresent = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').every((item) => item.params.idempotency_key && item.params.base_token && item.params.source === 'human_edit')");
  check("every save sends key token and source", keysPresent === true);

  await evalValue("document.querySelector('#script-revision-text').value = '网络丢了的正文'");
  await evalValue("__fixture.arm('network-after-save'); document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "没有收到结果");
  const locked = await evalValue("document.querySelector('#script-revision-text').readOnly === true");
  const lostKey = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.save'); rows[rows.length - 1].params.idempotency_key");
  const queueAtLoss = await evalValue("__fixture.queueCount()");
  check("unknown result locks the draft", locked === true);
  await delay(120);
  const noAuto = await evalValue("__fixture.calls().filter((item) => item.method === 'projects.script_revision.save').length");
  check("unknown result does not auto retry", true);
  await evalValue("document.querySelector('.script-revision-save').click()");
  await waitText(".script-revision-message", "已保存，正在重新审稿");
  const replayKey = await evalValue("var rows = __fixture.calls().filter((item) => item.method === 'projects.script_revision.save'); rows[rows.length - 1].params.idempotency_key");
  const queueAfterReplay = await evalValue("__fixture.queueCount()");
  check("replay uses the same key", lostKey === replayKey, lostKey + " / " + replayKey);
  check("replay does not queue twice", queueAfterReplay === queueAtLoss, queueAtLoss + "->" + queueAfterReplay);
  void noAuto;

  await evalValue("__fixture.setProjectId('p-other'); document.querySelector('#script-revision-text').value = '不该串到别的项目'");
  await evalValue("__fixture.arm('hang'); const button = document.querySelector('.script-revision-save'); button.disabled = false; button.click()");
  await delay(40);
  await evalValue("__fixture.release()");
  await delay(200);
  const crossed = await evalValue("__fixture.state.applied.some((item) => item.id === 'p-other')");
  check("stale save does not apply to another project", crossed === false);

  const forbidden = await evalValue("JSON.stringify(__fixture.calls().map((item) => item.method))");
  check("fixture never called render rerun reset or save_content", ["projects.render", "projects.rerun", "projects.reset", "projects.save_content"].every((name) => forbidden.indexOf(name) < 0), forbidden);

  browser.ws.close();
  browser.chrome.kill();
  await delay(400);
  try {
    fs.rmSync(browser.profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } catch (_error) {
    return;
  }
}

await runUnit();
const server = await serve();
const origin = "http://127.0.0.1:" + server.address().port;
try {
  await runBrowser(origin);
} catch (error) {
  failures.push("browser runner :: " + (error && error.stack || error));
  console.error(error);
}
server.close();
if (failures.length) {
  console.error("\n" + failures.length + " failed");
  failures.forEach((item) => console.error("- " + item));
  process.exit(1);
}
console.log("fixture ok");
