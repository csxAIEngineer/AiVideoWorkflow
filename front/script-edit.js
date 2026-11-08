// Revision-stop manuscript editor.
// Reads projects.script_revision.get and saves only through projects.script_revision.save.
// The agreed contract is not treated as a finished integration. This file does not call
// save_content, send_message, render, rerun, or reset, and a save is not a passed review.
// revision.get status is the candidate record. The gate stop is projects.get status,
// phase, and question id. Overlay edit and save follow can_edit / can_save only.
(function (root) {
  const GET_METHOD = "projects.script_revision.get";
  const SAVE_METHOD = "projects.script_revision.save";
  const RETRY_METHOD = "projects.script_revision.retry_review";
  const REFRESH_METHOD = "projects.get";

  let session = null;
  let keydownHandler = null;
  const pendingQueue = new Map();
  const revisionViews = new Map();
  let retryingReview = false;
  const PENDING_PREFIX = "aivideo.scriptRevision.pending.";

  function noteRevision(view) {
    if (!view || !view.id) return;
    if (!view.fetchedAt) view.fetchedAt = Date.now();
    revisionViews.set(view.id, view);
  }

  function serverSettled(revision, pending) {
    if (!revision || !revision.fetchedAt) return false;
    if (revision.review_retryable === true) return false;
    const state = revision.review_state;
    if (state !== "none" && state !== "queued" && state !== "done") return false;
    if (!pending) return true;
    return revision.fetchedAt >= (pending.writtenAt || 0);
  }

  function revisionFor(projectId) {
    return revisionViews.get(projectId) || null;
  }

  function revisionSignature(projectId) {
    const view = revisionFor(projectId);
    const pending = readPending(projectId);
    if (!view && !pending) return "";
    return [
      view && view.review_state || "",
      view && view.review_retryable === true ? "1" : "0",
      view && view.base_token || "",
      pending ? "1" : "0",
    ].join(":");
  }

  function readPending(projectId) {
    if (!projectId) return null;
    if (pendingQueue.has(projectId)) return pendingQueue.get(projectId);
    try {
      const raw = root.sessionStorage && root.sessionStorage.getItem(PENDING_PREFIX + projectId);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.key || parsed.token == null || parsed.text == null) return null;
      pendingQueue.set(projectId, parsed);
      return parsed;
    } catch (_error) {
      return null;
    }
  }

  function writePending(projectId, payload) {
    if (!projectId || !payload) return;
    if (!payload.writtenAt) payload.writtenAt = Date.now();
    pendingQueue.set(projectId, payload);
    try {
      if (root.sessionStorage) {
        root.sessionStorage.setItem(PENDING_PREFIX + projectId, JSON.stringify(payload));
      }
    } catch (_error) {
      return;
    }
  }

  function clearPending(projectId) {
    if (!projectId) return;
    pendingQueue.delete(projectId);
    try {
      if (root.sessionStorage) root.sessionStorage.removeItem(PENDING_PREFIX + projectId);
    } catch (_error) {
      return;
    }
  }

  function keepsSavedPayload(error) {
    const data = error && error.data;
    return !!(data && data.saved === true && data.retry_same_key === true);
  }

  function entryCopy(pending) {
    if (!pending) {
      return {
        label: "选择稿件 / 人工编辑",
        note: "打开后只读取底稿。取消不会写入正式稿。只有「保存并重新审稿」才会提交。",
      };
    }
    if (pending.reason === "review_ack_unconfirmed") {
      return {
        label: "已保存，确认审稿状态",
        note: pending.message || "稿已保存。请用原来的保存编号确认这次审稿，不要改编辑区。",
      };
    }
    if (pending.reason === "review_not_queued") {
      return {
        label: "已保存，重试审稿",
        note: pending.message || "稿已保存，审稿还没排上。请用原来的保存编号再试一次，不要改编辑区。",
      };
    }
    return {
      label: "已保存，继续处理审稿",
      note: pending.message || "稿已保存。请用原来的保存编号再试一次，不要改编辑区。",
    };
  }

  function shouldShowEntry(project, view) {
    if (!project || !project.id) return false;
    const revision = view || revisionFor(project.id);
    if (revision && revision.review_retryable === true) return true;
    const pending = readPending(project.id);
    if (pending && !serverSettled(revision, pending)) return true;
    if (revision && (revision.review_state === "none" || revision.review_state === "queued" || revision.review_state === "done")) {
      return isRevisionStop(project);
    }
    return isRevisionStop(project);
  }

  // Read-only slot for a future server pending lookup. No method name is assumed.
  // Until that contract arrives, this performs no request and does not invent a save.
  let pendingReader = null;

  function configurePendingRead(reader) {
    pendingReader = typeof reader === "function" ? reader : null;
  }

  async function probeServerPending(rpc, project) {
    if (!project || !project.id) return { state: "unavailable", pending: null };
    const local = readPending(project.id);
    if (local) return { state: "local", pending: local };
    if (!pendingReader) return { state: "unavailable", pending: null };
    const found = await pendingReader(rpc, project);
    if (!found || found.pending !== true || !found.key || found.text == null || found.token == null) {
      return { state: "none", pending: null };
    }
    return { state: "server", pending: found };
  }

  function isRevisionStop(project) {
    // Gate stop comes from projects.get. revision.get status is the candidate, not this.
    if (!project || !project.id) return false;
    if (project.status !== "gate") return false;
    const progress = project.progress || {};
    if (progress.phase !== "gate_script") return false;
    const id = (progress.question && progress.question.id) || "";
    return id === "script" || id === "script_candidate";
  }

  function newIdempotencyKey() {
    if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    return "sr-edit-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2);
  }

  function planSubmit(held, text, token, mode, phase) {
    if (phase === "inflight" || (held && held.kind === "inflight")) {
      return { ok: false, message: "正在保存，没有重复提交。" };
    }
    if (phase === "requeue" || (held && held.kind === "requeue")) {
      if (mode !== "requeue" || !held) {
        return { ok: false, message: "稿已保存。请用「用原来的保存编号再试一次」，不要改编辑区，也不要换新编号。" };
      }
      return { ok: true, key: held.key, token: held.token, text: held.text };
    }
    if (mode === "retry") {
      if (!held || (held.kind !== "unknown" && held.kind !== "retryable")) {
        return { ok: false, message: "没有可以原样重试的一次保存。" };
      }
      return { ok: true, key: held.key, token: held.token, text: held.text };
    }
    if (held && held.kind === "unknown") {
      if (held.text !== text || held.token !== token) {
        return { ok: false, message: "上一次保存结果未知。请先按原样重试，不要换正文，以免重复排队。" };
      }
      return { ok: true, key: held.key, token: held.token, text: held.text };
    }
    if (held && held.kind === "retryable" && held.text === text && held.token === token) {
      return { ok: true, key: held.key, token: held.token, text: held.text };
    }
    return { ok: true, key: newIdempotencyKey(), token: token, text: text };
  }

  function outcomeFromError(error) {
    if (!error || !error.rpcKnown) return "unknown";
    const data = error.data || {};
    if (data.retryable === true) return "retryable";
    return "rejected";
  }

  function describeStatus(snapshot) {
    const status = snapshot && snapshot.status != null ? String(snapshot.status) : "";
    const reason = snapshot && snapshot.reason != null ? String(snapshot.reason) : "";
    const needsHuman = !!(snapshot && snapshot.needs_human);
    const failed = needsHuman || status === "blocked" || status === "failed" || status === "error" || status === "rejected";
    let headline = "还没有这份候选的审稿结论。不能直接采用。";
    if (failed) {
      headline = needsHuman
        ? "候选未通过审稿，需要人工确认，不能直接采用。"
        : "候选未通过审稿，不能直接采用。";
    } else if (status === "passed") {
      headline = "候选记录里没有未通过项。它还不是正式稿，不能直接采用。保存后会重新审稿。";
    } else if (status) {
      headline = "候选还没有通过审稿，不能直接采用。";
    }
    return { headline: headline, reason: reason, failed: failed };
  }

  function draftAvailability(snapshot) {
    const source = snapshot && snapshot.draft_source ? String(snapshot.draft_source) : "none";
    const text = snapshot && snapshot.draft_text != null ? String(snapshot.draft_text) : "";
    const status = describeStatus(snapshot);
    if ((source === "rejected_text" || source === "candidate") && text) {
      const note = status.failed
        ? "此稿为最新未采用候选的完整内容。候选未通过审稿，不能直接采用。"
        : "此稿为最新未采用候选的完整内容。选择它只更换编辑底稿，不会直接采用。";
      return { enabled: true, text: text, note: note };
    }
    return { enabled: false, text: "", note: "没有未采用候选。" };
  }

  function permissionNote(snapshot) {
    if (!snapshot) return "还没有读到底稿。";
    if (snapshot.busy) {
      const label = {
        writing: "正在写稿",
        reviewing: "正在审稿",
        rendering: "正在生成",
      }[snapshot.busy_reason] || "正在处理";
      return label + "，现在不能编辑或保存。";
    }
    if (!snapshot.can_edit || !snapshot.can_save) {
      const phase = String(snapshot.phase_reason || "");
      if (phase && phase !== "invalid_phase" && /[\u4e00-\u9fff]/.test(phase)) {
        return "现在不能编辑或保存。" + phase;
      }
      return "现在不能编辑或保存。";
    }
    if (!snapshot.base_token) return "现在不能保存。";
    return "当前可以编辑，保存后会重新审稿。";
  }

  function diagnosticText(snapshot) {
    if (!snapshot) return "";
    const lines = [];
    if (snapshot.status) lines.push("审稿标记：" + snapshot.status);
    if (snapshot.busy_reason) lines.push("忙碌标记：" + snapshot.busy_reason);
    if (snapshot.phase_reason) lines.push("停点标记：" + snapshot.phase_reason);
    if (snapshot.draft_source) lines.push("底稿来源：" + snapshot.draft_source);
    if (snapshot.attempts != null && snapshot.attempts !== "") lines.push("模型修订已用次数：" + snapshot.attempts);
    const validation = snapshot.validation;
    if (validation && typeof validation === "object") {
      if (validation.stage) lines.push("检查阶段：" + validation.stage);
      if (validation.source) lines.push("检查来源：" + validation.source);
      if (validation.evidence) lines.push("检查依据：" + validation.evidence);
    }
    return lines.join("\n");
  }

  function officialText(snapshot) {
    return snapshot && snapshot.official_text != null ? String(snapshot.official_text) : "";
  }

  function textForBase(base) {
    if (!session || !session.snapshot) return "";
    if (base === "draft") return session.draft.enabled ? session.draft.text : "";
    return officialText(session.snapshot);
  }

  function isDirty() {
    if (!session || !session.textarea) return false;
    return session.textarea.value !== session.loaded;
  }

  function canSaveNow() {
    const snapshot = session && session.snapshot;
    if (!snapshot || !snapshot.can_edit || !snapshot.can_save || snapshot.busy) return false;
    if (!snapshot.base_token) return false;
    if (session.phase === "inflight" || session.phase === "reading" || session.phase === "requeue") return false;
    return true;
  }

  function focusables(dialog) {
    const confirm = dialog.querySelector(".script-revision-confirm");
    const scope = confirm && !confirm.classList.contains("hidden") ? confirm : dialog;
    return Array.prototype.filter.call(scope.querySelectorAll("button, textarea, input, select"), (el) => {
      if (el.disabled) return false;
      if (el.closest(".hidden")) return false;
      return true;
    });
  }

  function focusDialog() {
    if (!session || !session.dialog) return;
    const items = focusables(session.dialog);
    if (items.length) items[0].focus();
    else session.dialog.focus();
  }

  function restoreFocus() {
    const live = document.getElementById("script-revision-open");
    const target = live && live.isConnected ? live : session && session.trigger;
    if (target && target.focus) target.focus();
  }

  function setMessage(text) {
    if (!session || !session.message) return;
    session.message.textContent = text || "";
  }

  function showDiagnostic(text) {
    if (!session || !session.diag) return;
    const has = !!text;
    session.diag.classList.toggle("hidden", !has);
    if (session.diagBody) session.diagBody.textContent = text || "";
  }

  function showRetry(show, label) {
    if (!session || !session.retryButton) return;
    session.retryButton.classList.toggle("hidden", !show);
    session.retryButton.textContent = label || "按原样再试一次";
  }

  function lockBases(locked) {
    if (!session) return;
    if (session.officialRadio) session.officialRadio.disabled = !!locked;
    if (session.draftRadio) {
      session.draftRadio.disabled = !!locked || !(session.draft && session.draft.enabled);
    }
    [session.officialRadio, session.draftRadio].forEach((input) => {
      if (!input) return;
      const label = input.closest("label");
      if (label) label.classList.toggle("is-disabled", input.disabled);
    });
  }

  function setBusy(busy) {
    if (!session) return;
    if (session.saveButton) session.saveButton.disabled = busy || !canSaveNow();
    if (session.dialog) session.dialog.setAttribute("aria-busy", busy ? "true" : "false");
    syncSaveEnabled();
  }

  function syncSaveEnabled() {
    if (!session || !session.saveButton) return;
    const enabled = canSaveNow() && session.phase !== "inflight";
    session.saveButton.disabled = !enabled;
  }

  function lockText(locked) {
    if (!session || !session.textarea) return;
    const snapshot = session.snapshot;
    const readOnly = locked || !snapshot || !snapshot.can_edit || !!snapshot.busy;
    session.textarea.readOnly = readOnly;
  }

  function renderPermission() {
    if (!session || !session.permission) return;
    session.permission.textContent = permissionNote(session.snapshot);
  }

  function applyBase(base) {
    session.base = base;
    const next = textForBase(base);
    session.textarea.value = next;
    session.loaded = next;
    if (session.officialRadio) session.officialRadio.checked = base !== "draft";
    if (session.draftRadio) session.draftRadio.checked = base === "draft";
  }

  function hideConfirm() {
    if (!session || !session.confirm) return;
    session.confirm.classList.add("hidden");
    session.confirmCallback = null;
  }

  function showConfirm(kind, callback) {
    if (!session || !session.confirm) return;
    const copy = {
      dirty: "有未保存的修改。放弃后不会写入正式稿。",
      switch: "更换底稿会丢掉还没保存的修改。不会写入正式稿。",
      unknown: "上一次保存没有收到结果。离开不会把结果写到别的项目。若服务端已经收下，可能已经排队。确定放弃这次编辑？",
      requeue: "稿已经保存。关闭不会再提交，也不会改稿。再次打开仍用原来的保存编号重试。",
    };
    session.confirmText.textContent = copy[kind] || copy.dirty;
    if (session.abandonButton) session.abandonButton.textContent = kind === "requeue" ? "关闭" : "放弃修改";
    session.confirm.classList.remove("hidden");
    session.confirmCallback = callback;
    const stay = session.confirm.querySelector("[data-confirm='stay']");
    if (stay) stay.focus();
  }

  function finishConfirm(abandon) {
    const callback = session && session.confirmCallback;
    hideConfirm();
    if (callback) callback(!!abandon);
  }

  function requestClose() {
    if (!session || session.closed) return;
    if (session.phase === "inflight") {
      setMessage("正在保存。请等这一次结束再关闭，避免结果写错地方。");
      return;
    }
    if (session.confirm && !session.confirm.classList.contains("hidden")) {
      finishConfirm(false);
      return;
    }
    if (session.phase === "requeue") {
      showConfirm("requeue", (abandon) => {
        if (abandon) closeSession();
      });
      return;
    }
    if (session.phase === "unknown") {
      showConfirm("unknown", (abandon) => {
        if (abandon) closeSession();
      });
      return;
    }
    if (isDirty()) {
      showConfirm("dirty", (abandon) => {
        if (abandon) closeSession();
      });
      return;
    }
    closeSession();
  }

  function closeSession() {
    if (!session) return;
    session.closed = true;
    session.generation += 1;
    if (keydownHandler) {
      document.removeEventListener("keydown", keydownHandler, true);
      keydownHandler = null;
    }
    if (session.layer && session.layer.parentNode) session.layer.parentNode.removeChild(session.layer);
    document.documentElement.style.overflow = session.previousOverflow || "";
    const ending = session;
    session = null;
    const live = document.getElementById("script-revision-open");
    const target = live && live.isConnected ? live : ending.trigger;
    if (target && target.focus) target.focus();
  }

  function onKeydown(event) {
    if (!session || session.closed || !session.dialog) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      requestClose();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables(session.dialog);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !session.dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !session.dialog.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  function alive(gen, projectId) {
    if (!session || session.closed || session.generation !== gen) return false;
    if (session.options.getProjectId && session.options.getProjectId() !== projectId) return false;
    return true;
  }

  function applySnapshot(snapshot) {
    session.snapshot = snapshot;
    session.token = snapshot && typeof snapshot.base_token === "string" ? snapshot.base_token : "";
    session.draft = draftAvailability(snapshot);
    session.phase = "ready";
    session.held = null;
    const status = describeStatus(snapshot);
    session.status.textContent = status.headline;
    session.reason.textContent = status.reason || "";
    showDiagnostic(diagnosticText(snapshot));
    session.draftNote.textContent = session.draft.note;
    session.draftRadio.disabled = !session.draft.enabled;
    session.draftRadio.closest("label").classList.toggle("is-disabled", !session.draft.enabled);
    session.dialog.dataset.baseToken = session.token;
    session.dialog.dataset.draftSource = snapshot && snapshot.draft_source ? String(snapshot.draft_source) : "none";
    renderPermission();
    applyBase("official");
    lockText(false);
    showRetry(false);
    syncSaveEnabled();
    setMessage(permissionNote(snapshot));
  }

  function showReadError(text) {
    session.phase = "ready";
    session.snapshot = {
      can_edit: false,
      can_save: false,
      busy: false,
      official_text: "",
      draft_text: "",
      draft_source: "none",
      base_token: "",
    };
    session.token = "";
    session.draft = draftAvailability(session.snapshot);
    session.status.textContent = "没有读到底稿。";
    session.reason.textContent = text || "";
    showDiagnostic("");
    session.draftNote.textContent = session.draft.note;
    session.draftRadio.disabled = true;
    session.textarea.value = "";
    session.loaded = "";
    session.textarea.readOnly = true;
    renderPermission();
    syncSaveEnabled();
    setMessage(text || "没有读到底稿");
  }

  async function loadSnapshot(projectId, gen) {
    let snapshot = null;
    try {
      snapshot = await session.options.rpc(GET_METHOD, { id: projectId });
    } catch (error) {
      if (!alive(gen, projectId)) return;
      showReadError((error && error.message) || "没有读到底稿");
      if (!restorePending(projectId)) focusDialog();
      return;
    }
    if (!alive(gen, projectId)) return;
    if (!snapshot || snapshot.id !== projectId) {
      showReadError("底稿响应和当前项目不一致，没有打开编辑。");
      if (!restorePending(projectId)) focusDialog();
      return;
    }
    applySnapshot(snapshot);
    if (!restorePending(projectId)) focusDialog();
  }

  function enterRequeue(projectId, payload, message, reason) {
    const stored = {
      key: payload.key,
      token: payload.token,
      text: payload.text,
      message: message || payload.message || "",
      reason: reason || payload.reason || "",
    };
    writePending(projectId, stored);
    session.phase = "requeue";
    session.held = { key: stored.key, token: stored.token, text: stored.text, kind: "requeue" };
    session.token = stored.token;
    session.textarea.value = stored.text;
    session.loaded = stored.text;
    session.textarea.readOnly = true;
    lockBases(true);
    if (session.dialog) session.dialog.dataset.baseToken = stored.token;
    showRetry(true, "已保存，重试审稿");
    if (session.permission) session.permission.textContent = "编辑已锁定。请按提示用原来的保存再试一次，不要改稿。";
    syncSaveEnabled();
    setMessage(stored.message);
  }

  function restorePending(projectId) {
    const pending = readPending(projectId);
    if (!pending || !session) return false;
    enterRequeue(projectId, pending, pending.message);
    return true;
  }

  async function submit(mode) {
    if (!session || session.closed) return;
    if (mode !== "requeue" && !canSaveNow()) return;
    const typed = session.textarea.value;
    const plan = planSubmit(session.held, typed, session.token, mode, session.phase);
    if (!plan.ok) {
      setMessage(plan.message);
      return;
    }
    const projectId = session.projectId;
    const gen = session.generation;
    const wasRequeue = session.phase === "requeue";
    session.phase = "inflight";
    session.held = { key: plan.key, token: plan.token, text: plan.text, kind: "inflight" };
    if (mode === "retry" || mode === "requeue") {
      session.textarea.value = plan.text;
      session.loaded = plan.text;
    }
    setBusy(true);
    setMessage(mode === "requeue" ? "正在用原来的保存编号再试。" : (mode === "retry" ? "正在按原样重试。" : "正在保存。"));
    let result = null;
    try {
      result = await session.options.rpc(SAVE_METHOD, {
        id: projectId,
        text: plan.text,
        base_token: plan.token,
        idempotency_key: plan.key,
        source: "human_edit",
      });
    } catch (error) {
      if (!alive(gen, projectId)) return;
      const outcome = outcomeFromError(error);
      const reason = error && error.data && error.data.reason ? String(error.data.reason) : "";
      const lead = (error && error.message) || "没有保存";
      if (keepsSavedPayload(error) || wasRequeue) {
        const previous = readPending(projectId) || {};
        enterRequeue(projectId, {
          key: (session.held && session.held.key) || previous.key,
          token: (session.held && session.held.token) || previous.token,
          text: (session.held && session.held.text) || previous.text,
          reason: keepsSavedPayload(error) ? reason : (previous.reason || ""),
          message: keepsSavedPayload(error) ? lead : (previous.message || lead),
        }, keepsSavedPayload(error) ? lead : (previous.message || lead), keepsSavedPayload(error) ? reason : (previous.reason || ""));
        try {
          await prepareRevision(session.options, projectId);
        } catch (_error) {
          setMessage(lead);
        }
        setBusy(false);
        return;
      }
      if (outcome === "unknown") {
        session.phase = "unknown";
        session.held.kind = "unknown";
        lockText(true);
        showRetry(true);
        setMessage("没有收到结果，没有自动重试。再次保存会沿用同一次请求，避免重复排队。");
      } else if (outcome === "retryable") {
        session.phase = "retryable";
        session.held.kind = "retryable";
        lockText(false);
        showRetry(true);
        showDiagnostic(reason ? "原因标记：" + reason : "");
        setMessage(lead + "。稿件还在。可以按原样再试一次，不会自动重试。");
      } else {
        session.phase = "ready";
        session.held = null;
        lockText(false);
        showRetry(false);
        const extraByReason = {
          token_mismatch: "这次冲突没有覆盖正式稿，也没有自动重试。请记下正文后关闭，重新打开再编辑。",
          version_conflict: "这次冲突没有覆盖正式稿，也没有自动重试。请记下正文后关闭，重新打开再编辑。",
          idempotency_mismatch: "同一次请求的正文不一致，没有覆盖正式稿，也没有自动重试。换正文需要新的保存编号。",
          invalid_request: "缺少保存编号，没有保存，也没有自动重试。稿件还在编辑框里。",
          too_large: "正文超过 1MB，没有保存，也没有自动重试。稿件还在编辑框里。",
          empty_text: "正文是空的，没有保存，也没有自动重试。",
          invalid_phase: "当前停点不能保存。稿件还在编辑框里，没有自动重试。",
          not_found: "没有这个项目。编辑框里的稿还在，没有自动重试。",
        };
        const extra = extraByReason[reason] || "稿件还在编辑框里，没有覆盖正式稿，也没有自动重试。";
        showDiagnostic(reason ? "原因标记：" + reason : "");
        setMessage(lead + "。" + extra);
      }
      setBusy(false);
      return;
    }
    if (!alive(gen, projectId)) return;
    if (!result || result.ok !== true) {
      session.phase = "ready";
      session.held = null;
      setBusy(false);
      setMessage("保存没有完成。稿件还在，没有自动重试。");
      return;
    }
    if (typeof result.base_token === "string" && result.base_token) session.token = result.base_token;
    if (session.dialog) session.dialog.dataset.baseToken = session.token;
    session.loaded = session.textarea.value;
    showRetry(false);
    const shown = typeof result.message === "string" ? result.message : "";
    if (result.saved === true && result.review_queued === true && result.unchanged !== true) {
      session.phase = "ready";
      session.held = null;
      clearPending(projectId);
      lockText(false);
      lockBases(false);
      setBusy(false);
      setMessage(shown);
      try {
        const project = await session.options.rpc(REFRESH_METHOD, { id: projectId });
        if (!alive(gen, projectId)) return;
        if (project && project.id === projectId && session.options.applyProject) session.options.applyProject(project);
      } catch (_error) {
        if (!alive(gen, projectId)) return;
        setMessage(shown);
      }
      return;
    }
    session.phase = "ready";
    session.held = null;
    setBusy(false);
    setMessage(shown);
  }

  function buildDialog(projectId) {
    const layer = document.createElement("div");
    layer.className = "script-revision-layer";
    const dialog = document.createElement("div");
    dialog.className = "script-revision-dialog";
    dialog.tabIndex = -1;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "script-revision-title");
    dialog.dataset.projectId = projectId || "";

    const head = document.createElement("div");
    head.className = "script-revision-head";
    const title = document.createElement("h2");
    title.id = "script-revision-title";
    title.textContent = "选择稿件 / 人工编辑";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "script-revision-close";
    close.setAttribute("aria-label", "关闭");
    close.textContent = "关闭";
    close.addEventListener("click", requestClose);
    head.append(title, close);

    const status = document.createElement("p");
    status.className = "script-revision-status";
    status.textContent = "正在读取底稿。";
    const reason = document.createElement("p");
    reason.className = "script-revision-reason";
    const permission = document.createElement("p");
    permission.className = "script-revision-permission";
    const diag = document.createElement("details");
    diag.className = "script-revision-diag hidden";
    const diagSummary = document.createElement("summary");
    diagSummary.textContent = "诊断详情";
    const diagBody = document.createElement("p");
    diag.append(diagSummary, diagBody);

    const fieldset = document.createElement("fieldset");
    fieldset.className = "script-revision-bases";
    const legend = document.createElement("legend");
    legend.textContent = "编辑底稿";
    const officialLabel = document.createElement("label");
    const officialRadio = document.createElement("input");
    officialRadio.type = "radio";
    officialRadio.name = "script-revision-base";
    officialRadio.value = "official";
    officialRadio.checked = true;
    officialLabel.append(officialRadio, document.createTextNode("正式原稿"));
    const draftLabel = document.createElement("label");
    const draftRadio = document.createElement("input");
    draftRadio.type = "radio";
    draftRadio.name = "script-revision-base";
    draftRadio.value = "draft";
    draftRadio.disabled = true;
    draftLabel.append(draftRadio, document.createTextNode("最新未采用候选"));
    const draftNote = document.createElement("p");
    draftNote.className = "script-revision-draft-note";
    draftNote.textContent = "没有未采用候选。";
    fieldset.append(legend, officialLabel, draftLabel, draftNote);

    const textLabel = document.createElement("label");
    textLabel.className = "script-revision-text-label";
    textLabel.setAttribute("for", "script-revision-text");
    textLabel.textContent = "全文";
    const textarea = document.createElement("textarea");
    textarea.id = "script-revision-text";
    textarea.className = "script-revision-text";
    textarea.rows = 16;
    textarea.readOnly = true;
    textarea.setAttribute("aria-label", "全文");
    textarea.addEventListener("input", () => {
      if (!session || session.phase !== "requeue" || !session.held) return;
      if (session.textarea.value !== session.held.text) session.textarea.value = session.held.text;
    });

    const message = document.createElement("p");
    message.className = "script-revision-message";
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");

    const actions = document.createElement("div");
    actions.className = "script-revision-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "script-revision-cancel";
    cancel.textContent = "取消";
    cancel.addEventListener("click", requestClose);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "script-revision-retry hidden";
    retry.textContent = "按原样再试一次";
    retry.addEventListener("click", () => {
      if (session && session.phase === "requeue") retryReview(session.options, session.projectId);
      else submit("retry");
    });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "script-revision-save";
    save.textContent = "保存并重新审稿";
    save.disabled = true;
    save.addEventListener("click", () => submit("submit"));
    actions.append(cancel, retry, save);

    const confirm = document.createElement("div");
    confirm.className = "script-revision-confirm hidden";
    confirm.setAttribute("role", "alertdialog");
    confirm.setAttribute("aria-modal", "true");
    confirm.setAttribute("aria-label", "放弃未保存的修改");
    const confirmText = document.createElement("p");
    const stay = document.createElement("button");
    stay.type = "button";
    stay.dataset.confirm = "stay";
    stay.textContent = "留下";
    const abandon = document.createElement("button");
    abandon.type = "button";
    abandon.dataset.confirm = "abandon";
    abandon.textContent = "放弃修改";
    stay.addEventListener("click", () => finishConfirm(false));
    abandon.addEventListener("click", () => finishConfirm(true));
    const confirmActions = document.createElement("div");
    confirmActions.className = "script-revision-confirm-actions";
    confirmActions.append(stay, abandon);
    confirm.append(confirmText, confirmActions);

    dialog.append(head, status, reason, permission, diag, fieldset, textLabel, textarea, message, actions, confirm);
    layer.append(dialog);
    layer.addEventListener("click", (event) => {
      if (event.target === layer) requestClose();
    });
    officialRadio.addEventListener("change", () => {
      if (!officialRadio.checked) return;
      chooseBase("official");
    });
    draftRadio.addEventListener("change", () => {
      if (!draftRadio.checked) return;
      chooseBase("draft");
    });

    return {
      layer: layer,
      dialog: dialog,
      status: status,
      reason: reason,
      permission: permission,
      diag: diag,
      diagBody: diagBody,
      officialRadio: officialRadio,
      draftRadio: draftRadio,
      draftNote: draftNote,
      textarea: textarea,
      message: message,
      saveButton: save,
      retryButton: retry,
      confirm: confirm,
      confirmText: confirmText,
      abandonButton: abandon,
    };
  }

  function chooseBase(next) {
    if (!session || session.phase === "inflight" || session.phase === "unknown" || session.phase === "requeue") {
      if (session && session.officialRadio) session.officialRadio.checked = session.base !== "draft";
      if (session && session.draftRadio) session.draftRadio.checked = session.base === "draft";
      if (session && session.phase === "unknown") setMessage("上一次保存结果未知。先按原样重试，再换底稿。");
      return;
    }
    if (next === "draft" && (!session.draft || !session.draft.enabled)) {
      if (session.officialRadio) session.officialRadio.checked = true;
      if (session.draftRadio) session.draftRadio.checked = false;
      return;
    }
    if (next === session.base) return;
    if (isDirty()) {
      showConfirm("switch", (abandon) => {
        if (!session) return;
        if (!abandon) {
          session.officialRadio.checked = session.base !== "draft";
          session.draftRadio.checked = session.base === "draft";
          return;
        }
        applyBase(next);
      });
      return;
    }
    applyBase(next);
  }

  function openEditor(options, trigger) {
    if (session && session.layer && session.layer.isConnected && !session.closed) {
      focusDialog();
      return;
    }
    const projectId = options.getProjectId ? options.getProjectId() : (options.project && options.project.id);
    if (!projectId) return;
    const built = buildDialog(projectId);
    session = {
      options: options,
      trigger: trigger || null,
      projectId: projectId,
      generation: 1,
      closed: false,
      phase: "reading",
      held: null,
      base: "official",
      loaded: "",
      token: "",
      snapshot: null,
      draft: draftAvailability(null),
      previousOverflow: document.documentElement.style.overflow,
      confirmCallback: null,
      abandonButton: built.abandonButton,
      layer: built.layer,
      dialog: built.dialog,
      status: built.status,
      reason: built.reason,
      permission: built.permission,
      diag: built.diag,
      diagBody: built.diagBody,
      officialRadio: built.officialRadio,
      draftRadio: built.draftRadio,
      draftNote: built.draftNote,
      textarea: built.textarea,
      message: built.message,
      saveButton: built.saveButton,
      retryButton: built.retryButton,
      confirm: built.confirm,
      confirmText: built.confirmText,
    };
    document.documentElement.style.overflow = "hidden";
    document.body.appendChild(session.layer);
    keydownHandler = onKeydown;
    document.addEventListener("keydown", keydownHandler, true);
    const gen = session.generation;
    loadSnapshot(projectId, gen);
  }

  async function prepareRevision(options, projectId) {
    const id = projectId || (options && options.getProjectId && options.getProjectId()) || (options && options.project && options.project.id);
    if (!id || !options || !options.rpc) return null;
    const view = await options.rpc(GET_METHOD, { id: id });
    if (!view || view.id !== id) return null;
    noteRevision(view);
    if (view.review_retryable !== true && (view.review_state === "none" || view.review_state === "queued" || view.review_state === "done")) {
      clearPending(id);
    }
    return view;
  }

  function showRecoveryMessage(text) {
    const note = document.querySelector(".script-revision-entry-note");
    if (note && text) note.textContent = text;
    if (session && session.message && text) session.message.textContent = text;
  }

  async function retryReview(options, projectId) {
    const id = projectId || (options.getProjectId && options.getProjectId()) || (options.project && options.project.id);
    const view = revisionFor(id);
    if (!id || !view || !view.base_token || retryingReview) return { did: "skip" };
    retryingReview = true;
    try {
      const result = await options.rpc(RETRY_METHOD, { id: id, base_token: view.base_token });
      const message = result && typeof result.message === "string" ? result.message : "";
      const success = !!(result && result.ok === true && (result.review_state === "queued" || result.review_state === "done"));
      if (success) {
        if (typeof result.base_token === "string" && result.base_token) {
          view.base_token = result.base_token;
          view.review_state = result.review_state;
          view.review_retryable = false;
          noteRevision(view);
        }
        clearPending(id);
        showRecoveryMessage(message);
        try {
          const project = await options.rpc(REFRESH_METHOD, { id: id });
          if (project && project.id === id && options.applyProject && (!options.getProjectId || options.getProjectId() === id)) {
            options.applyProject(project);
          }
        } catch (_error) {
          showRecoveryMessage(message);
        }
        return { did: "success", message: message };
      }
      showRecoveryMessage(message);
      return { did: "result", message: message };
    } catch (error) {
      const data = error && error.data || {};
      const lead = (error && error.message) || "没有完成";
      showRecoveryMessage(lead);
      if (data.saved === true && data.retry_same_key === true) {
        const previous = readPending(id) || {};
        const current = revisionFor(id) || {};
        writePending(id, {
          key: previous.key || "",
          token: current.base_token || previous.token || "",
          text: previous.text || "",
          message: lead,
          reason: data.reason || previous.reason || "",
        });
      }
      return { did: "error", message: lead };
    } finally {
      retryingReview = false;
    }
  }

  async function onEntryClick(options, button) {
    const projectId = (options.project && options.project.id) || (options.getProjectId && options.getProjectId());
    const revision = revisionFor(projectId);
    const pending = readPending(projectId);
    const recovery = (revision && revision.review_retryable === true) || (pending && !serverSettled(revision, pending));
    if (!recovery) {
      openEditor(options, button);
      return;
    }
    if (!revision || !revision.base_token) {
      await prepareRevision(options, projectId);
      return;
    }
    await retryReview(options, projectId);
  }

  function createEntry(options) {
    const projectId = options.project && options.project.id;
    const revision = revisionFor(projectId);
    const pending = readPending(projectId);
    const recovery = (revision && revision.review_retryable === true) || (pending && !serverSettled(revision, pending));
    const copy = recovery ? {
      label: "已保存，重试审稿",
      note: (revision && revision.review_retryable === true)
        ? "稿已保存。审稿可能已经排上，尚未确认。打开和刷新不会提交。"
        : ((pending && pending.message) || "稿已保存。审稿可能已经排上，尚未确认。打开和刷新不会提交。"),
    } : entryCopy(null);
    const box = document.createElement("div");
    box.className = "script-revision-entry-block";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice script-revision-entry";
    button.id = "script-revision-open";
    button.textContent = copy.label;
    button.addEventListener("click", () => onEntryClick(options, button));
    const note = document.createElement("p");
    note.className = "script-revision-entry-note";
    note.textContent = copy.note;
    box.append(button, note);
    return box;
  }

  function confirmLeave(nextId) {
    if (!session || session.closed || !session.layer || !session.layer.isConnected) return Promise.resolve(true);
    if (nextId && session.projectId === nextId) return Promise.resolve(true);
    if (session.phase === "inflight") {
      setMessage("正在保存。请等这一次结束再切换项目，避免结果写错项目。");
      return Promise.resolve(false);
    }
    if (session.phase === "requeue") {
      return new Promise((resolve) => {
        showConfirm("requeue", (abandon) => {
          if (!abandon) {
            resolve(false);
            return;
          }
          closeSession();
          resolve(true);
        });
      });
    }
    if (!isDirty() && session.phase !== "unknown") {
      closeSession();
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      showConfirm(session.phase === "unknown" ? "unknown" : "dirty", (abandon) => {
        if (!abandon) {
          resolve(false);
          return;
        }
        closeSession();
        resolve(true);
      });
    });
  }

  root.ScriptRevisionEdit = {
    GET_METHOD: GET_METHOD,
    SAVE_METHOD: SAVE_METHOD,
    isRevisionStop: isRevisionStop,
    shouldShowEntry: shouldShowEntry,
    prepareRevision: prepareRevision,
    revisionSignature: revisionSignature,
    noteRevision: noteRevision,
    retryReview: retryReview,
    probeServerPending: probeServerPending,
    configurePendingRead: configurePendingRead,
    clearPending: clearPending,
    readPending: readPending,
    createEntry: createEntry,
    confirmLeave: confirmLeave,
    planSubmit: planSubmit,
    outcomeFromError: outcomeFromError,
    describeStatus: describeStatus,
    draftAvailability: draftAvailability,
  };
})(typeof window !== "undefined" ? window : globalThis);
