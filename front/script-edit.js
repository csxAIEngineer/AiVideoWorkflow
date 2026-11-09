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
  const OPINION_METHOD = "projects.script_revision.opinion";
  const OPINION_RETRY_METHOD = "projects.script_revision.opinion_retry";
  const ACCEPT_METHOD = "projects.script_revision.accept_as_is";
  const ACCEPT_RETRY_METHOD = "projects.script_revision.accept_retry";
  const REFRESH_METHOD = "projects.get";
  const ACCEPT_COST = "继续下一段提示词写作，会调用模型并可能修复；不自动出图或视频，完成后仍需确认。";

  let session = null;
  let acceptDialog = null;
  let keydownHandler = null;
  const pendingQueue = new Map();
  const revisionViews = new Map();
  const outsideNotices = new Map();
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
    const notice = outsideNotices.get(projectId);
    if (!view && !pending && !notice) return "";
    return [
      view && view.review_state || "",
      view && view.review_retryable === true ? "1" : "0",
      view && view.base_token || "",
      pending ? "1" : "0",
      notice && notice.review_state || "",
      notice && notice.message || "",
      view && view.continue_state || "",
      view && view.continue_retryable === true ? "1" : "0",
    ].join(":");
  }

  function noticeFromView(view) {
    if (!view || !view.opinion_state || view.opinion_state === "none") return null;
    if (view.opinion_stale === true) {
      return { kind: "opinion", opinion_state: view.opinion_state, stale: true, message: "底稿或要求已经变了，这轮意见候选不能确认。意见还留着。", error: "" };
    }
    if (view.opinion_state === "ready") return { kind: "opinion", opinion_state: "ready", message: "意见候选已生成，可查看确认", error: "" };
    if (view.opinion_state === "running") return { kind: "opinion", opinion_state: "running", message: "正在按意见改稿", error: "" };
    if (view.opinion_state === "pending") return { kind: "opinion", opinion_state: "pending", message: "意见已记下，改稿还没确认排上", error: "" };
    if (view.opinion_state === "failed") return { kind: "opinion", opinion_state: "failed", message: view.opinion_error || "意见改稿没有完成", error: view.opinion_error || "" };
    return null;
  }

  function progressText(notice) {
    if (!notice) return "";
    if (notice.kind === "accept") return "";
    if (notice.kind === "opinion") {
      if (notice.stale === true) return "";
      if (notice.opinion_state === "failed") return notice.error || "意见改稿没有完成";
      if (notice.opinion_state === "ready") return "";
      if (notice.opinion_state === "pending") return "";
      if (notice.opinion_state === "running") return "";
      return "";
    }
    if (notice.review_state === "done") return "审稿已结束";
    const progress = notice.progress || {};
    const action = (progress.pipeline && progress.pipeline.action) || "";
    const message = progress.message || "";
    if (action === "script_review") return message || "正在重新审稿";
    return message;
  }

  function fillOutside(node, notice) {
    const message = node.querySelector(".script-revision-outside-message");
    const progress = node.querySelector(".script-revision-outside-progress");
    if (message) message.textContent = notice.message || "";
    const line = progressText(notice);
    if (progress) {
      progress.textContent = line;
      progress.hidden = !line;
    }
  }

  function buildOutside(projectId, notice) {
    const node = document.createElement("div");
    node.className = "script-revision-outside";
    node.dataset.projectId = projectId;
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    const message = document.createElement("p");
    message.className = "script-revision-outside-message";
    const progress = document.createElement("p");
    progress.className = "script-revision-outside-progress";
    node.append(message, progress);
    fillOutside(node, notice);
    return node;
  }

  function clearOpinionOutside(projectId) {
    const current = outsideNotices.get(projectId);
    if (!current || current.kind !== "opinion") return;
    outsideNotices.delete(projectId);
    const node = document.querySelector('.script-revision-outside[data-project-id="' + projectId + '"]');
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function publishOutside(projectId, notice) {
    if (!projectId || !notice) return;
    outsideNotices.set(projectId, notice);
    const node = document.querySelector('.script-revision-outside[data-project-id="' + projectId + '"]');
    if (node) {
      fillOutside(node, notice);
      return;
    }
    const created = buildOutside(projectId, notice);
    const anchor = document.querySelector(".script-revision-entry-block");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(created, anchor);
    else {
      const stop = document.querySelector("#stop");
      if (stop) stop.insertBefore(created, stop.firstChild);
      else document.body.append(created);
    }
  }

  function createOutside(project) {
    if (!project || !project.id) return null;
    const notice = outsideNotices.get(project.id);
    if (!notice) return null;
    if (notice.review_state !== "done" && project.progress) notice.progress = project.progress;
    return buildOutside(project.id, notice);
  }

  function settleSuccessClose() {
    if (!session || session.closed) return;
    if (session.textarea) session.loaded = session.textarea.value;
    session.phase = "ready";
    session.held = null;
    closeSession();
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
    if (revision && (revision.opinion_retryable === true || revision.opinion_model_retryable === true || (revision.opinion_state === "ready" && revision.opinion_stale !== true))) return true;
    if (revision && (revision.continue_retryable === true || acceptChoiceOpen(revision))) return true;
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
    if (session.opinionBase) {
      session.opinionBase.textContent = base === "draft"
        ? "这条意见对着最新未采用候选。正式稿不会因此改写。"
        : "这条意见对着正式原稿。正式稿不会因此改写。";
    }
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
    if (acceptDialog && acceptDialog.layer && acceptDialog.layer.isConnected) return;
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
    noteRevision(snapshot);
    if (snapshot.opinion_text && session.opinion && !String(session.opinion.value || "").trim()) {
      session.opinion.value = snapshot.opinion_text;
    }
    if (snapshot.opinion_state === "ready" && snapshot.opinion_stale !== true && snapshot.opinion_candidate_text) {
      session.base = snapshot.opinion_base === "draft" ? "draft" : "official";
      session.textarea.value = snapshot.opinion_candidate_text;
      session.loaded = snapshot.opinion_candidate_text;
      if (session.officialRadio) session.officialRadio.checked = session.base !== "draft";
      if (session.draftRadio) session.draftRadio.checked = session.base === "draft";
      if (session.opinionBase) {
        session.opinionBase.textContent = session.base === "draft"
          ? "这条意见对着最新未采用候选。正式稿不会因此改写。"
          : "这条意见对着正式原稿。正式稿不会因此改写。";
      }
    } else {
      applyBase(session.base === "draft" ? "draft" : "official");
    }
    lockText(false);
    showRetry(false);
    syncOpinionChrome(snapshot);
    syncSaveEnabled();
    if (snapshot.opinion_stale === true) setMessage("这轮意见已经过期，不能确认。意见还留着，编辑区里的文字也还在。");
    else if (snapshot.opinion_state === "failed") setMessage(snapshot.opinion_error || "意见改稿没有完成");
    else if (snapshot.opinion_state === "pending") setMessage("意见已记下，改稿还没确认排上。打开和刷新不会提交。");
    else if (snapshot.opinion_state === "running") setMessage("正在按意见改稿");
    else if (snapshot.opinion_state === "ready") setMessage("意见候选已写好。可以改全文，再确认。这还不是正式稿。");
    else setMessage(permissionNote(snapshot));
  }

  function syncOpinionChrome(snapshot) {
    if (!session) return;
    const retryable = snapshot && snapshot.opinion_retryable === true;
    const modelRetry = snapshot && snapshot.opinion_model_retryable === true;
    const remaining = snapshot && Number(snapshot.opinion_model_remaining);
    const canConfirm = snapshot && snapshot.can_confirm_opinion === true && snapshot.opinion_stale !== true;
    const exhausted = snapshot && snapshot.opinion_state === "failed" && snapshot.opinion_stale !== true && remaining === 0;
    if (session.opinionButton) session.opinionButton.textContent = "让AI按意见改稿";
    if (session.opinionRetryButton) session.opinionRetryButton.classList.toggle("hidden", !retryable);
    if (session.opinionModelButton) {
      session.opinionModelButton.classList.toggle("hidden", !modelRetry);
      if (modelRetry) session.opinionModelButton.textContent = "再次按这些意见改稿（剩余" + remaining + "次）";
    }
    if (session.opinionBudget) {
      session.opinionBudget.textContent = exhausted ? "这组底稿和意见的模型次数已用完。意见还留着，仍可以手工改全文。" : "";
    }
    if (session.confirmOpinionButton) session.confirmOpinionButton.classList.toggle("hidden", !canConfirm);
  }

  function opinionPayload(base, token, opinion, key) {
    return {
      id: session.projectId,
      base: base,
      base_token: token,
      opinion: opinion,
      idempotency_key: key,
    };
  }

  async function refreshOpinion(projectId, gen) {
    try {
      const project = await session.options.rpc(REFRESH_METHOD, { id: projectId });
      if (!alive(gen, projectId)) return;
      if (project && project.id === projectId && session.options.applyProject) session.options.applyProject(project);
    } catch (_error) {
      if (!alive(gen, projectId)) return;
    }
    try {
      await prepareRevision(session.options, projectId);
    } catch (_error) {
      return;
    }
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
    if (mode === "confirm") {
      const snap = session.snapshot || {};
      if (snap.can_confirm_opinion !== true || snap.opinion_stale === true || !snap.opinion_id || snap.opinion_revision == null || snap.opinion_revision === "") {
        setMessage("这轮意见不能确认。意见和编辑区里的文字都还在，正式稿没有改。");
        return;
      }
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
    setMessage(mode === "confirm" ? "正在确认这版意见候选。" : (mode === "requeue" ? "正在用原来的保存编号再试。" : (mode === "retry" ? "正在按原样重试。" : "正在保存。")));
    let result = null;
    const saveBody = {
      id: projectId,
      text: plan.text,
      base_token: plan.token,
      idempotency_key: plan.key,
      source: "human_edit",
    };
    if (mode === "confirm") {
      saveBody.opinion_id = session.snapshot.opinion_id;
      saveBody.opinion_revision = session.snapshot.opinion_revision;
    }
    try {
      result = await session.options.rpc(SAVE_METHOD, saveBody);
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
      const notice = {
        message: shown,
        review_state: "queued",
        review_queued: true,
        progress: null,
      };
      publishOutside(projectId, notice);
      try {
        const project = await session.options.rpc(REFRESH_METHOD, { id: projectId });
        if (!alive(gen, projectId)) return;
        if (project && project.progress) notice.progress = project.progress;
        publishOutside(projectId, notice);
        if (project && project.id === projectId && session.options.applyProject) session.options.applyProject(project);
      } catch (_error) {
        if (!alive(gen, projectId)) return;
      }
      settleSuccessClose();
      return;
    }
    session.phase = "ready";
    session.held = null;
    setBusy(false);
    setMessage(shown);
  }

  function requestOpinion() {
    return sendOpinion(false);
  }

  function resubmitFailedOpinion(options, projectId) {
    return sendFailedAgain(options || (session && session.options), projectId || (session && session.projectId));
  }

  async function sendOpinion(failedAgain) {
    if (!session || session.closed || session.phase === "inflight") return { did: "skip" };
    const snap = session.snapshot || {};
    const base = failedAgain ? (snap.opinion_base === "draft" ? "draft" : "official") : (session.base === "draft" ? "draft" : "official");
    const opinion = failedAgain ? String(snap.opinion_text || "") : (session.opinion ? session.opinion.value : "");
    const token = session.token || snap.base_token || "";
    if (!String(opinion).trim()) {
      setMessage("先写下剧情修改意见，再让 AI 按意见改稿。");
      return { did: "skip" };
    }
    if (failedAgain && snap.opinion_model_retryable !== true) {
      const remaining = Number(snap.opinion_model_remaining);
      setMessage(remaining === 0
        ? "这组底稿和意见的模型次数已用完。意见还留着，仍可以手工改全文。换一个保存编号也不能再调用模型。"
        : "现在不能再按这些意见改稿。意见还留着。");
      return { did: "skip" };
    }
    if (!token) {
      setMessage("还没有读到底稿版本，这次没有提交。");
      return { did: "skip" };
    }
    const sameHeld = session.opinionHeld
      && session.opinionHeld.reason
      && session.opinionHeld.base === base
      && session.opinionHeld.token === token
      && session.opinionHeld.opinion === opinion;
    const key = failedAgain || !sameHeld ? newIdempotencyKey() : session.opinionHeld.key;
    const projectId = session.projectId;
    const gen = session.generation;
    session.phase = "inflight";
    session.opinionHeld = { key: key, token: token, base: base, opinion: opinion, reason: "" };
    setBusy(true);
    setMessage(failedAgain ? "正在用新的保存编号再按这些意见改稿。" : "正在提交意见。正式稿还不会改。");
    let result = null;
    try {
      result = await session.options.rpc(OPINION_METHOD, opinionPayload(base, token, opinion, key));
    } catch (error) {
      if (!alive(gen, projectId)) return { did: "error" };
      session.phase = "ready";
      setBusy(false);
      const data = error && error.data || {};
      const lead = (error && error.message) || "没有提交";
      if (session.opinion) session.opinion.value = opinion;
      if (data.reason === "opinion_not_queued" || data.reason === "opinion_ack_unconfirmed") {
        session.opinionHeld.reason = data.reason;
      } else {
        session.opinionHeld = null;
      }
      setMessage(lead);
      try { await prepareRevision(session.options, projectId); } catch (_error) { return { did: "error", message: lead }; }
      if (session && session.snapshot) syncOpinionChrome(session.snapshot);
      return { did: "error", message: lead };
    }
    if (!alive(gen, projectId)) return { did: "skip" };
    session.phase = "ready";
    setBusy(false);
    const message = result && typeof result.message === "string" ? result.message : "";
    if (!(result && result.ok === true && result.official_saved === false && result.opinion_state === "running")) {
      if (session.opinion) session.opinion.value = opinion;
      setMessage(message || "意见没有送出。正式稿没有改。");
      return { did: "result", message: message };
    }
    session.opinionHeld = null;
    publishOutside(projectId, { kind: "opinion", opinion_state: "running", message: message, error: "" });
    await refreshOpinion(projectId, gen);
    if (!alive(gen, projectId)) return { did: "success", message: message };
    settleSuccessClose();
    return { did: "success", message: message };
  }

  async function sendFailedAgain(options, projectId) {
    const id = projectId || (options && options.getProjectId && options.getProjectId());
    if (!id || !options || !options.rpc) return { did: "skip" };
    let view = revisionFor(id);
    if (!view || !view.base_token) view = await prepareRevision(options, id);
    if (!view || view.opinion_model_retryable !== true) {
      const note = document.querySelector(".script-revision-entry-note");
      const remaining = view && Number(view.opinion_model_remaining);
      const text = remaining === 0
        ? "这组底稿和意见的模型次数已用完。意见还留着，仍可以手工改全文。换一个保存编号也不能再调用模型。"
        : "现在不能再按这些意见改稿。意见还留着。";
      if (note) note.textContent = text;
      if (session && session.projectId === id) setMessage(text);
      return { did: "skip" };
    }
    const key = newIdempotencyKey();
    let result = null;
    try {
      result = await options.rpc(OPINION_METHOD, {
      id: id,
      base: view.opinion_base === "draft" ? "draft" : "official",
      base_token: view.base_token,
      opinion: view.opinion_text,
      idempotency_key: key,
    });
    } catch (error) {
      const lead = (error && error.message) || "没有提交";
      publishOutside(id, { kind: "opinion", opinion_state: "failed", message: lead, error: lead });
      if (session && session.projectId === id) setMessage(lead);
      return { did: "error", message: lead };
    }
    const message = result && typeof result.message === "string" ? result.message : "";
    if (!(result && result.ok === true && result.official_saved === false && result.opinion_state === "running")) {
      publishOutside(id, { kind: "opinion", opinion_state: "failed", message: message || "意见没有送出。正式稿没有改。", error: message });
      return { did: "result", message: message };
    }
    publishOutside(id, { kind: "opinion", opinion_state: "running", message: message, error: "" });
    try {
      const project = await options.rpc(REFRESH_METHOD, { id: id });
      if (project && project.id === id && options.applyProject && (!options.getProjectId || options.getProjectId() === id)) options.applyProject(project);
    } catch (_error) { return { did: "success", message: message }; }
    try { await prepareRevision(options, id); } catch (_error) { return { did: "success", message: message }; }
    if (session && session.projectId === id) settleSuccessClose();
    return { did: "success", message: message, key: key };
  }

  async function retryOpinion(options, projectId) {
    const id = projectId || (options && options.getProjectId && options.getProjectId()) || (session && session.projectId);
    const rpc = (options && options.rpc) || (session && session.options && session.options.rpc);
    if (!id || !rpc) return { did: "skip" };
    let view = revisionFor(id);
    if (!view || !view.base_token) view = await prepareRevision(options || session.options, id);
    if (!view || !view.base_token) return { did: "skip" };
    try {
      const result = await rpc(OPINION_RETRY_METHOD, { id: id, base_token: view.base_token });
      const message = result && typeof result.message === "string" ? result.message : "";
      if (!(result && result.ok === true && result.official_saved === false && (result.opinion_state === "running" || result.opinion_state === "ready"))) {
        if (session && session.projectId === id) setMessage(message || "没有补排。正式稿没有改。");
        return { did: "result", message: message };
      }
      publishOutside(id, { kind: "opinion", opinion_state: result.opinion_state, message: message, error: "" });
      try {
        const project = await rpc(REFRESH_METHOD, { id: id });
        if (project && project.id === id && options && options.applyProject && (!options.getProjectId || options.getProjectId() === id)) options.applyProject(project);
      } catch (_error) { return { did: "success", message: message }; }
      try { await prepareRevision(options || session.options, id); } catch (_error) { return { did: "success", message: message }; }
      if (session && session.projectId === id) settleSuccessClose();
      return { did: "success", message: message };
    } catch (error) {
      const data = error && error.data || {};
      const lead = (error && error.message) || "没有补排";
      publishOutside(id, { kind: "opinion", opinion_state: "failed", message: lead, error: lead });
      if (session && session.projectId === id && session.opinion && view.opinion_text && !String(session.opinion.value || "").trim()) {
        session.opinion.value = view.opinion_text;
      }
      if (session && session.projectId === id) setMessage(lead);
      showRecoveryMessage(lead);
      return { did: "error", message: lead, saved: data.saved === true, official_saved: data.official_saved === true, reason: data.reason || "" };
    }
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

    const opinionLabel = document.createElement("label");
    opinionLabel.className = "script-revision-opinion-label";
    opinionLabel.setAttribute("for", "script-revision-opinion");
    opinionLabel.textContent = "剧情修改意见";
    const opinion = document.createElement("textarea");
    opinion.id = "script-revision-opinion";
    opinion.className = "script-revision-opinion";
    opinion.rows = 4;
    opinion.setAttribute("aria-label", "剧情修改意见");
    const opinionBase = document.createElement("p");
    opinionBase.className = "script-revision-opinion-base";
    opinionBase.textContent = "这条意见对着正式原稿。正式稿不会因此改写。";
    const opinionActions = document.createElement("div");
    opinionActions.className = "script-revision-opinion-actions";
    const opinionButton = document.createElement("button");
    opinionButton.type = "button";
    opinionButton.className = "script-revision-opinion-submit";
    opinionButton.textContent = "让AI按意见改稿";
    opinionButton.addEventListener("click", requestOpinion);
    const opinionRetry = document.createElement("button");
    opinionRetry.type = "button";
    opinionRetry.className = "script-revision-opinion-retry hidden";
    opinionRetry.textContent = "补排意见改稿";
    opinionRetry.addEventListener("click", () => retryOpinion(session && session.options, session && session.projectId));
    const opinionModel = document.createElement("button");
    opinionModel.type = "button";
    opinionModel.className = "script-revision-opinion-model hidden";
    opinionModel.textContent = "再次按这些意见改稿（剩余0次）";
    opinionModel.addEventListener("click", () => resubmitFailedOpinion());
    const opinionBudget = document.createElement("p");
    opinionBudget.className = "script-revision-opinion-budget";
    opinionActions.append(opinionButton, opinionRetry, opinionModel, opinionBudget);

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
    const confirmOpinion = document.createElement("button");
    confirmOpinion.type = "button";
    confirmOpinion.className = "script-revision-confirm-opinion hidden";
    confirmOpinion.textContent = "确认这版意见候选";
    confirmOpinion.addEventListener("click", () => submit("confirm"));
    actions.append(cancel, retry, confirmOpinion, save);

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

    dialog.append(head, status, reason, permission, diag, fieldset, textLabel, textarea, opinionLabel, opinion, opinionBase, opinionActions, message, actions, confirm);
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
      opinion: opinion,
      opinionBase: opinionBase,
      opinionRetryButton: opinionRetry,
      opinionModelButton: opinionModel,
      opinionBudget: opinionBudget,
      confirmOpinionButton: confirmOpinion,
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
      opinion: built.opinion,
      opinionBase: built.opinionBase,
      opinionRetryButton: built.opinionRetryButton,
      opinionModelButton: built.opinionModelButton,
      opinionBudget: built.opinionBudget,
      confirmOpinionButton: built.confirmOpinionButton,
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
    if (!view.opinion_state || view.opinion_state === "none") clearOpinionOutside(view.id);
    else {
      const derived = noticeFromView(view);
      const current = outsideNotices.get(view.id);
      if (derived && (!current || current.kind === "opinion")) publishOutside(view.id, derived);
    }
    publishAcceptFromGet(view);
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
        const notice = {
          message: message,
          review_state: result.review_state,
          review_queued: result.review_queued === true,
          progress: null,
        };
        publishOutside(id, notice);
        try {
          const project = await options.rpc(REFRESH_METHOD, { id: id });
          if (result.review_state !== "done" && project && project.progress) notice.progress = project.progress;
          publishOutside(id, notice);
          if (project && project.id === id && options.applyProject && (!options.getProjectId || options.getProjectId() === id)) {
            options.applyProject(project);
          }
        } catch (_error) {
          showRecoveryMessage(message);
        }
        settleSuccessClose();
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

  function storedSources(view) {
    const list = view && Array.isArray(view.accept_sources) ? view.accept_sources : [];
    return list.filter((item) => item && (item.source === "official" || item.source === "draft" || item.source === "opinion"));
  }

  function sourceLabel(source) {
    if (source === "official") return "正式原稿";
    if (source === "draft") return "最新未采用候选";
    if (source === "opinion") return "意见候选";
    return "这一稿";
  }

  function acceptChoiceOpen(view) {
    if (!view) return false;
    const state = view.continue_state || "none";
    if (state === "pending" || state === "queued" || state === "done" || state === "failed") return false;
    return storedSources(view).some((item) => item.can_accept === true);
  }

  function editorDirty() {
    return !!(session && !session.closed && session.layer && session.layer.isConnected && session.textarea && session.textarea.value !== session.loaded);
  }

  function riskSentence(source) {
    if (!source || source.issues == null || source.issue_state === "unreviewed" || source.issue_state === "unknown") {
      const parts = ["这一份没有对上的剧本审查，不能当成没有问题。"];
      const risk = source && source.risk;
      if (risk && (risk.reason || risk.status || risk.validation)) {
        parts.push("候选记录写着：" + (risk.reason || risk.status || "有记录") + "。");
        if (risk.validation && risk.validation.evidence) parts.push(String(risk.validation.evidence) + "。");
        parts.push("这是候选记录，不是选中稿自身的审查。");
      }
      const related = source && source.related_review;
      if (related) {
        const who = related.applies_to === "official" ? "正式稿" : "另一份稿";
        parts.push("另有审查对着" + who + "：" + (related.reason || related.status || "结论还在") + "。这不是选中稿自身的审查。");
      }
      parts.push("未审或对不上已有审查，接受风险，不是通过。");
      return parts.join("");
    }
    const lines = (source.issues || []).map((item) => item && item.reason ? String(item.reason) : "").filter(Boolean);
    return "审稿仍有问题：" + (lines.length ? lines.join("；") : "审稿结论仍在。") + "接受风险，不是通过。";
  }

  function unavailableCopy(source) {
    const reason = source && source.unavailable_reason;
    if (reason === "review_passed") return "这份正式稿的审稿已经通过。请用原来的继续。";
    if (reason === "stale") return "这轮意见已经过期，不能按这一稿确认。";
    if (reason === "not_ready") return "意见候选还没写好。";
    if (reason === "empty") return "没有这一稿。";
    if (source && source.available === false && source.source === "draft") return "没有未采用的候选。";
    if (reason === "closed") return "现在不能按这一稿继续。";
    return "";
  }

  function acceptNoticeFromView(view) {
    if (!view || !view.continue_state || view.continue_state === "none") return null;
    if (view.continue_state === "pending") return { kind: "accept", continue_state: "pending", message: "已接受这一稿。下一段可能已经排上，尚未确认。" };
    if (view.continue_state === "queued") return { kind: "accept", continue_state: "queued", message: "下一段已在队列里" };
    if (view.continue_state === "done") return { kind: "accept", continue_state: "done", message: "已经离开剧本停点" };
    if (view.continue_state === "failed") return { kind: "accept", continue_state: "failed", message: "下一段没有写完。不会自动重排，也不会出图。" };
    return null;
  }

  function clearAcceptOutside(projectId) {
    const current = outsideNotices.get(projectId);
    if (!current || current.kind !== "accept") return;
    outsideNotices.delete(projectId);
    const node = document.querySelector('.script-revision-outside[data-project-id="' + projectId + '"]');
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function publishAcceptFromGet(view) {
    if (!view || !view.id) return;
    const derived = acceptNoticeFromView(view);
    if (!derived) {
      clearAcceptOutside(view.id);
      return;
    }
    publishOutside(view.id, derived);
  }

  function setAcceptMessage(text) {
    if (acceptDialog && acceptDialog.message) acceptDialog.message.textContent = text || "";
  }

  function selectedAcceptSource() {
    if (!acceptDialog || !acceptDialog.dialog) return null;
    const chosen = acceptDialog.dialog.querySelector("input[name=script-accept-source]:checked");
    if (!chosen) return null;
    const view = revisionFor(acceptDialog.projectId);
    const sources = storedSources(view);
    for (let i = 0; i < sources.length; i += 1) {
      if (sources[i].source === chosen.value) return sources[i];
    }
    return null;
  }

  function paintAcceptRisk() {
    if (!acceptDialog || !acceptDialog.risk) return;
    const source = selectedAcceptSource();
    acceptDialog.risk.textContent = source ? ("你要确认的是" + sourceLabel(source.source) + "。" + riskSentence(source)) : "先选择要确认的一稿。";
  }

  function closeAccept() {
    if (!acceptDialog) return;
    if (acceptDialog.keydown) {
      document.removeEventListener("keydown", acceptDialog.keydown, true);
    }
    if (acceptDialog.layer && acceptDialog.layer.parentNode) acceptDialog.layer.parentNode.removeChild(acceptDialog.layer);
    acceptDialog = null;
  }

  function sameProject(options, projectId) {
    if (!options || !options.getProjectId) return true;
    return options.getProjectId() === projectId;
  }

  async function refreshAccept(options, projectId) {
    const view = await prepareRevision(options, projectId);
    if (!sameProject(options, projectId)) return null;
    try {
      const project = await options.rpc(REFRESH_METHOD, { id: projectId });
      if (project && project.id === projectId && sameProject(options, projectId) && options.applyProject) options.applyProject(project);
    } catch (_error) {
      return view;
    }
    return revisionFor(projectId) || view;
  }

  function acceptPayload(projectId, view, source) {
    const body = {
      id: projectId,
      base_token: view.base_token,
      idempotency_key: newIdempotencyKey(),
      source: source.source,
      text_hash: source.text_hash,
      acknowledge_risk: true,
    };
    if (source.source === "opinion") {
      body.opinion_id = source.opinion_id;
      body.opinion_revision = source.opinion_revision;
    }
    return body;
  }

  async function submitAccept(options) {
    if (!acceptDialog || acceptDialog.inflight) return;
    const projectId = acceptDialog.projectId;
    const source = selectedAcceptSource();
    const view = revisionFor(projectId);
    if (!source || source.can_accept !== true) {
      setAcceptMessage("这一稿现在不能接受。没有提交。");
      return;
    }
    if (!view || !view.base_token || !source.text_hash) {
      setAcceptMessage("还没有读到这一稿的版本。没有提交。");
      return;
    }
    if (source.source === "opinion" && (!source.opinion_id || source.opinion_revision == null || source.opinion_revision === "")) {
      setAcceptMessage("这版意见候选缺少身份，没有提交。");
      return;
    }
    if (editorDirty()) {
      setAcceptMessage("编辑区有还没保存的修改。这一版还不能确认。可以回到编辑，或先单独保存。这里不会把保存和接受并成一步，也不会丢掉这些字。");
      return;
    }
    const body = acceptPayload(projectId, view, source);
    acceptDialog.inflight = true;
    if (acceptDialog.confirm) acceptDialog.confirm.disabled = true;
    setAcceptMessage("正在按这一稿继续。");
    let result = null;
    try {
      result = await options.rpc(ACCEPT_METHOD, body);
    } catch (error) {
      if (!acceptDialog || acceptDialog.projectId !== projectId || !sameProject(options, projectId)) return;
      acceptDialog.inflight = false;
      if (acceptDialog.confirm) acceptDialog.confirm.disabled = false;
      const data = error && error.data || {};
      const lead = (error && error.message) || "没有继续";
      setAcceptMessage(lead);
      if (data.reason === "continue_not_queued" || data.reason === "continue_ack_unconfirmed") {
        if (acceptDialog.confirm) acceptDialog.confirm.disabled = true;
        await refreshAccept(options, projectId);
        if (!acceptDialog || acceptDialog.projectId !== projectId) return;
        setAcceptMessage(lead);
      } else {
        try { await prepareRevision(options, projectId); } catch (_error) { return; }
      }
      return;
    }
    if (!acceptDialog || acceptDialog.projectId !== projectId || !sameProject(options, projectId)) return;
    const message = result && typeof result.message === "string" ? result.message : "";
    const queued = result && result.ok === true && result.accept_state === "accepted" && result.continue_state === "queued";
    const done = result && result.ok === true && result.accept_state === "accepted" && result.continue_state === "done";
    if (!queued && !done) {
      acceptDialog.inflight = false;
      if (acceptDialog.confirm) acceptDialog.confirm.disabled = false;
      setAcceptMessage(message || "没有继续。没有再提交。");
      return;
    }
    publishOutside(projectId, { kind: "accept", continue_state: result.continue_state, message: message });
    closeAccept();
    await refreshAccept(options, projectId);
  }

  async function retryAccept(options, projectId) {
    if (acceptDialog && acceptDialog.inflight) return;
    const id = projectId || (options && options.getProjectId && options.getProjectId());
    let view = revisionFor(id);
    if (!view || !view.base_token) view = await prepareRevision(options, id);
    if (!view || view.continue_retryable !== true || !view.base_token) return;
    if (acceptDialog) acceptDialog.inflight = true;
    let result = null;
    try {
      result = await options.rpc(ACCEPT_RETRY_METHOD, { id: id, base_token: view.base_token });
    } catch (error) {
      if (!sameProject(options, id)) return;
      const lead = (error && error.message) || "没有补排";
      publishOutside(id, { kind: "accept", continue_state: "pending", message: lead });
      if (acceptDialog && acceptDialog.projectId === id) {
        acceptDialog.inflight = false;
        setAcceptMessage(lead);
      }
      try { await prepareRevision(options, id); } catch (_error) { return; }
      return;
    }
    if (!sameProject(options, id)) return;
    const message = result && typeof result.message === "string" ? result.message : "";
    const state = result && result.continue_state;
    if (result && result.ok === true && (state === "queued" || state === "done")) {
      publishOutside(id, { kind: "accept", continue_state: state, message: message });
      closeAccept();
      await refreshAccept(options, id);
      return;
    }
    if (acceptDialog && acceptDialog.projectId === id) {
      acceptDialog.inflight = false;
      setAcceptMessage(message || "没有补排。");
    }
  }

  function renderAcceptSources(view) {
    const box = document.createElement("fieldset");
    box.className = "script-revision-accept-sources";
    const legend = document.createElement("legend");
    legend.textContent = "确认哪一稿";
    box.append(legend);
    const sources = storedSources(view);
    let picked = false;
    sources.forEach((source) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "script-accept-source";
      input.value = source.source;
      input.id = "script-revision-accept-" + source.source;
      const enabled = source.can_accept === true;
      input.disabled = !enabled;
      if (enabled && !picked) {
        input.checked = true;
        picked = true;
      }
      input.addEventListener("change", paintAcceptRisk);
      const name = document.createElement("span");
      name.textContent = sourceLabel(source.source);
      label.append(input, name);
      const extra = unavailableCopy(source);
      if (!enabled && extra) {
        const note = document.createElement("small");
        note.textContent = extra;
        label.append(note);
      }
      box.append(label);
    });
    return box;
  }

  async function openAccept(options, trigger) {
    const projectId = (options.project && options.project.id) || (options.getProjectId && options.getProjectId());
    if (!projectId || !options || !options.rpc) return;
    const view = await prepareRevision(options, projectId);
    if (!view || !sameProject(options, projectId) || !acceptChoiceOpen(view)) return;
    closeAccept();
    const layer = document.createElement("div");
    layer.className = "script-revision-layer script-revision-accept-layer";
    const dialog = document.createElement("div");
    dialog.className = "script-revision-dialog script-revision-accept-dialog";
    dialog.tabIndex = -1;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "script-revision-accept-title");
    const title = document.createElement("h2");
    title.id = "script-revision-accept-title";
    title.textContent = "就按这一稿";
    const cost = document.createElement("p");
    cost.className = "script-revision-accept-cost";
    cost.textContent = ACCEPT_COST;
    const risk = document.createElement("p");
    risk.className = "script-revision-accept-risk";
    const message = document.createElement("p");
    message.className = "script-revision-accept-message";
    message.setAttribute("role", "status");
    const actions = document.createElement("div");
    actions.className = "script-revision-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "script-revision-accept-cancel";
    cancel.textContent = "取消";
    cancel.addEventListener("click", closeAccept);
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "script-revision-accept-confirm";
    confirm.textContent = "仍接受这一稿";
    confirm.addEventListener("click", () => submitAccept(options));
    actions.append(cancel, confirm);
    dialog.append(title, cost, renderAcceptSources(view), risk, message, actions);
    layer.append(dialog);
    layer.addEventListener("click", (event) => {
      if (event.target === layer) closeAccept();
    });
    const keydown = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      closeAccept();
    };
    acceptDialog = { projectId: projectId, options: options, trigger: trigger || null, layer: layer, dialog: dialog, risk: risk, message: message, confirm: confirm, inflight: false, keydown: keydown };
    paintAcceptRisk();
    document.body.appendChild(layer);
    document.addEventListener("keydown", keydown, true);
    confirm.focus();
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
    if (revision && revision.opinion_state === "ready" && revision.opinion_stale !== true) {
      const openCandidate = document.createElement("button");
      openCandidate.type = "button";
      openCandidate.className = "choice script-revision-opinion-open";
      openCandidate.id = "script-revision-opinion-open";
      openCandidate.textContent = "查看意见候选";
      openCandidate.addEventListener("click", () => openEditor(options, openCandidate));
      box.append(openCandidate);
    }
    if (revision && revision.opinion_retryable === true) {
      const queue = document.createElement("button");
      queue.type = "button";
      queue.className = "choice script-revision-opinion-retry";
      queue.id = "script-revision-opinion-retry";
      queue.textContent = "补排意见改稿";
      queue.addEventListener("click", () => retryOpinion(options, projectId));
      box.append(queue);
    }
    if (revision && revision.opinion_model_retryable === true) {
      const again = document.createElement("button");
      again.type = "button";
      again.className = "choice script-revision-opinion-model";
      again.id = "script-revision-opinion-model";
      again.textContent = "再次按这些意见改稿（剩余" + Number(revision.opinion_model_remaining) + "次）";
      again.addEventListener("click", () => resubmitFailedOpinion(options, projectId));
      box.append(again);
    } else if (revision && revision.opinion_state === "failed" && revision.opinion_stale !== true && Number(revision.opinion_model_remaining) === 0) {
      const spent = document.createElement("p");
      spent.className = "script-revision-opinion-budget";
      spent.textContent = "这组底稿和意见的模型次数已用完。意见还留着，仍可以手工改全文。";
      box.append(spent);
    }
    if (revision && revision.continue_retryable === true) {
      const acceptNote = document.createElement("p");
      acceptNote.className = "script-revision-accept-note";
      acceptNote.textContent = "已接受这一稿。下一段可能已经排上，尚未确认。打开和刷新不会再提交。";
      const retryContinue = document.createElement("button");
      retryContinue.type = "button";
      retryContinue.className = "choice script-revision-accept-retry";
      retryContinue.id = "script-revision-accept-retry";
      retryContinue.textContent = "已接受，重试继续下一段";
      retryContinue.addEventListener("click", () => retryAccept(options, projectId));
      box.append(acceptNote, retryContinue);
    } else if (acceptChoiceOpen(revision)) {
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "choice script-revision-accept-open";
      accept.id = "script-revision-accept-open";
      accept.textContent = "就按这一稿";
      accept.addEventListener("click", () => openAccept(options, accept));
      box.append(accept);
    }
    return box;
  }

  function opinionInFlight(projectId) {
    const view = revisionFor(projectId);
    if (!view) return false;
    return view.opinion_state === "pending" || view.opinion_state === "running";
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
    retryOpinion: retryOpinion,
    resubmitFailedOpinion: resubmitFailedOpinion,
    opinionInFlight: opinionInFlight,
    probeServerPending: probeServerPending,
    configurePendingRead: configurePendingRead,
    clearPending: clearPending,
    readPending: readPending,
    createEntry: createEntry,
    createOutside: createOutside,
    confirmLeave: confirmLeave,
    planSubmit: planSubmit,
    outcomeFromError: outcomeFromError,
    describeStatus: describeStatus,
    draftAvailability: draftAvailability,
  };
})(typeof window !== "undefined" ? window : globalThis);
