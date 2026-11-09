// Fixture double for projects.script_revision.get/save. Not the backend.
(function () {
  const OFFICIAL = "正式原稿\n站台上只有她。";
  const REJECTED = Array.from({ length: 36 }, function (_, index) {
    return "未采用候选第" + (index + 1) + "行：雨还在下，广播没有响。";
  }).join("\n");
  const CANDIDATE = "候选全文\n她把伞收起来。";
  const DECOY = "DECOY_FROM_PROJECT_GET_SCRIPT";
  const MAX_BYTES = 1024 * 1024;
  const state = {
    mode: "rejected",
    projectId: "p-rev",
    failNext: null,
    release: null,
    applied: [],
    seen: new Map(),
    queueCount: 0,
    calls: [],
    reviewPending: false,
    reviewQueued: false,
    pendingToken: "",
    forceDone: false,
  };
  try {
    const saved = JSON.parse(localStorage.getItem("aivideo.fixture.revision") || "null");
    if (saved && typeof saved === "object") {
      state.reviewPending = !!saved.reviewPending;
      state.reviewQueued = !!saved.reviewQueued;
      state.pendingToken = saved.pendingToken || "";
      state.forceDone = !!saved.forceDone;
      state.queueCount = Number(saved.queueCount) || 0;
      if (saved.mode) state.mode = saved.mode;
    }
  } catch (_error) {
    /* a fresh fixture starts at the gate */
  }
  function remember() {
    try {
      localStorage.setItem("aivideo.fixture.revision", JSON.stringify({
        reviewPending: !!state.reviewPending,
        reviewQueued: !!state.reviewQueued,
        pendingToken: state.pendingToken || "",
        forceDone: !!state.forceDone,
        queueCount: state.queueCount,
        mode: state.mode,
      }));
    } catch (_error) {
      return;
    }
  }

  function gateProject(id) {
    return {
      id: id,
      title: "修订停点",
      brief: "一支短片",
      status: "gate",
      stage: "剧本候选",
      script: DECOY,
      storyboard: "",
      image_prompts: "",
      assets: [],
      shots: [],
      messages: [{
        role: "assistant",
        content: "修订没有通过。正式剧本没有改。",
        options: ["查看未采用候选", "保持原稿"],
        choice_kind: "single",
      }],
      can_reply: true,
      progress: {
        phase: "gate_script",
        percent: 48,
        message: "修订没有通过",
        question: {
          id: "script_candidate",
          kind: "single",
          options: ["查看未采用候选", "保持原稿"],
          reply: "修订没有通过。正式剧本没有改。",
        },
        pipeline: { current: "script", passed: false, action: "wait" },
      },
      images: [],
      clips: [],
      materials: [],
      nodes: [],
      tasks: [],
      events: [],
      debug_files: [],
      traces: [],
    };
  }

  function snapshot(id) {
    const row = {
      id: id,
      official_text: OFFICIAL,
      draft_text: "",
      draft_source: "none",
      status: "blocked",
      reason: "声画对不上",
      validation: { stage: "judge", source: "graph", evidence: "音效没改" },
      needs_human: true,
      attempts: 2,
      can_edit: true,
      can_save: true,
      busy: false,
      busy_reason: "",
      phase_reason: "",
      base_token: "sr1.opaque-rejected",
      review_state: "none",
      review_retryable: false,
      opinion_state: "none",
      opinion_stale: false,
      opinion_id: "",
      opinion_revision: 0,
      opinion_base: "",
      opinion_text: "",
      opinion_candidate_text: "",
      opinion_error: "",
      opinion_retryable: false,
      opinion_model_remaining: 2,
      opinion_model_retryable: false,
      can_confirm_opinion: false,
      accept_sources: [
        { source: "official", available: true, text_hash: "hash-official", issue_state: "reviewed", review_status: "blocked", issues: [{ reason: "声画对不上" }], risk: null, related_review: null, can_accept: false, unavailable_reason: "closed" },
        { source: "draft", available: false, draft_source: "none", text_hash: "", issue_state: "unknown", review_status: "", issues: null, risk: null, related_review: null, can_accept: false, unavailable_reason: "closed" },
        { source: "opinion", available: false, opinion_id: "", opinion_revision: 0, text_hash: "", issue_state: "unknown", review_status: "", issues: null, risk: null, related_review: null, can_accept: false, unavailable_reason: "not_ready" },
      ],
      accept_state: "none",
      continue_state: "none",
      continue_retryable: false,
      accepted_source: "",
      accepted_script_hash: "",
      accepted_issues: null,
      human_accepted: false,
      review_passed: false,
    };
    if (state.opinionView) {
      Object.keys(state.opinionView).forEach(function (key) { row[key] = state.opinionView[key]; });
    }
    if (state.acceptView) {
      Object.keys(state.acceptView).forEach(function (key) { row[key] = state.acceptView[key]; });
    }
    if (state.mode === "repair") {
      row.can_edit = false;
      row.can_save = false;
      row.busy = true;
      row.busy_reason = "writing";
      row.review_state = "none";
      row.review_retryable = false;
      return row;
    }
    if (state.mode === "rejected" || state.mode === "busy") {
      row.draft_text = REJECTED;
      row.draft_source = "rejected_text";
    } else if (state.mode === "none") {
      row.reason = "没有未采用候选";
      row.base_token = "sr1.opaque-none";
    } else if (state.mode === "candidate") {
      row.draft_text = CANDIDATE;
      row.draft_source = "candidate";
      row.status = "passed";
      row.needs_human = false;
      row.reason = "";
      row.validation = null;
      row.base_token = "sr1.opaque-candidate";
    } else if (state.mode === "invalid") {
      row.can_edit = false;
      row.can_save = false;
      row.phase_reason = "invalid_phase";
      row.base_token = "sr1.opaque-phase";
    }
    if (state.mode === "busy") {
      row.can_edit = false;
      row.can_save = false;
      row.busy = true;
      row.busy_reason = "reviewing";
      row.base_token = "sr1.opaque-busy";
    }
    if ((state.reviewPending || state.reviewQueued) && state.pendingToken && state.mode !== "repair") {
      row.base_token = state.pendingToken;
    }
    if (state.reviewPending && state.mode !== "repair") {
      row.review_state = "pending";
      row.review_retryable = true;
      row.can_edit = false;
      row.can_save = false;
      row.busy = false;
      row.busy_reason = "";
      row.phase_reason = "review_pending";
      row.base_token = state.pendingToken || "sr1.after-save";
    } else if (state.reviewQueued && state.mode !== "repair") {
      row.review_state = "queued";
      row.review_retryable = false;
      row.busy = true;
      row.busy_reason = "reviewing";
      row.can_edit = false;
      row.can_save = false;
    }
    return row;
  }

  function rpcError(code, message, reason, retryable, saved, retrySameKey) {
    const error = new Error(message);
    error.rpcKnown = true;
    error.code = code;
    error.data = {
      reason: reason,
      retryable: !!retryable,
      saved: !!saved,
      retry_same_key: !!retrySameKey,
    };
    return error;
  }

  function retryReview(params) {
    const body = params || {};
    if (body.text != null || body.idempotency_key || body.source) {
      throw rpcError(400, "恢复不接受正文或新编号", "invalid_request", false, false, false);
    }
    if (!body.id || !body.base_token) {
      throw rpcError(400, "缺少底稿记号", "invalid_request", false, false, false);
    }
    const current = snapshot(body.id).base_token;
    if (body.base_token !== current) {
      throw rpcError(409, "稿件版本已经变了，编辑区不会被覆盖", "version_conflict", false, false, false);
    }
    if (state.forceDone) {
      return {
        ok: true,
        saved: true,
        review_queued: false,
        unchanged: true,
        review_state: "done",
        base_token: body.base_token,
        message: "审稿已结束",
      };
    }
    if (!state.reviewPending && !state.reviewQueued) {
      throw rpcError(409, "没有待补排的审稿", "nothing_pending", false, false, false);
    }
    if (state.reviewQueued) {
      return {
        ok: true,
        saved: true,
        review_queued: true,
        unchanged: true,
        review_state: "queued",
        base_token: body.base_token,
        message: "审稿已在队列里",
      };
    }
    state.reviewPending = false;
    state.reviewQueued = true;
    state.queueCount += 1;
    remember();
    return {
      ok: true,
      saved: true,
      review_queued: true,
      unchanged: true,
      review_state: "queued",
      base_token: body.base_token,
      message: "已保存，正在重新审稿",
    };
  }

  async function save(params) {
    const fail = state.failNext;
    state.failNext = null;
    if (fail === "network") throw Object.assign(new TypeError("network down"), { network: true });
    if (fail === "hang") await new Promise((resolve) => { state.release = resolve; });
    if (fail && typeof fail === "object") throw fail;
    if (!params || !params.idempotency_key) throw rpcError(400, "缺少保存编号", "invalid_request", false);
    if (params.source !== "human_edit") throw rpcError(400, "来源不对", "invalid_request", false);
    const size = new TextEncoder().encode(String(params.text || "")).length;
    if (size > MAX_BYTES) throw rpcError(413, "正文太大", "too_large", false);
    const prev = state.seen.get(params.idempotency_key);
    if (prev) {
      if (prev.text !== params.text || prev.token !== params.base_token) {
        throw rpcError(409, "同一次请求的内容不一致", "idempotency_mismatch", false);
      }
      return prev.result;
    }
    if (!String(params.text || "").trim()) throw rpcError(400, "正文是空的", "empty_text", false);
    let result;
    if (params.text === OFFICIAL) {
      result = {
        ok: true,
        saved: false,
        review_queued: false,
        unchanged: true,
        base_token: params.base_token,
        message: "稿件没有变化",
      };
    } else {
      state.queueCount += 1;
      result = {
        ok: true,
        saved: true,
        review_queued: true,
        unchanged: false,
        base_token: "sr1.next-token",
        message: "已保存，正在重新审稿",
      };
    }
    state.seen.set(params.idempotency_key, { text: params.text, token: params.base_token, result: result });
    if (fail === "network-after-save") throw Object.assign(new TypeError("response lost"), { network: true });
    return result;
  }

  function submitOpinion(params) {
    const body = params || {};
    if (!body.idempotency_key) throw rpcError(400, "没有保存编号", "invalid_request", false, false, false);
    if (!String(body.opinion || "").trim()) throw rpcError(400, "意见是空的", "empty_opinion", false, false, false);
    if (body.base !== "official" && body.base !== "draft") throw rpcError(400, "底稿只能是正式稿或未采用候选", "invalid_request", false, false, false);
    if (state.opinionReplayKey && body.idempotency_key === state.opinionReplayKey) {
      return { ok: true, official_saved: false, opinion_state: "failed", unchanged: true, opinion_id: "opfixturefail", base_token: body.base_token, message: "意见改稿没有完成：模型失败" };
    }
    if (state.opinionBudget === 0) throw rpcError(409, "这组底稿和意见的模型次数已用完", "budget_exhausted", false, false, false);
    state.opinionCalls = (state.opinionCalls || 0) + 1;
    state.opinionView = {
      opinion_state: "running",
      opinion_stale: false,
      opinion_id: "opfixture0001",
      opinion_revision: 1,
      opinion_base: body.base,
      opinion_text: body.opinion,
      opinion_candidate_text: "",
      opinion_error: "",
      opinion_retryable: false,
      opinion_model_remaining: 2,
      opinion_model_retryable: false,
      can_confirm_opinion: false,
    };
    return {
      ok: true,
      official_saved: false,
      opinion_state: "running",
      unchanged: false,
      opinion_id: "opfixture0001",
      base_token: body.base_token,
      message: "正在按意见改稿",
    };
  }

  function retryOpinionCall(params) {
    const body = params || {};
    if (body.opinion || body.idempotency_key || body.source || body.text) {
      throw rpcError(400, "补排不接受正文或新编号", "invalid_request", false, false, false);
    }
    if (state.opinionView && state.opinionView.opinion_state === "failed") {
      const error = rpcError(409, "这次意见改稿没有完成。补排不会再调模型。", "opinion_failed", false, true, false);
      error.data.official_saved = false;
      error.data.model_remaining = state.opinionView.opinion_model_remaining;
      throw error;
    }
    state.opinionView = Object.assign({}, state.opinionView, {
      opinion_state: "running",
      opinion_stale: false,
      opinion_retryable: false,
      can_confirm_opinion: false,
    });
    return { ok: true, official_saved: false, opinion_state: "running", unchanged: true, base_token: body.base_token, message: "正在按意见改稿" };
  }

  function acceptAsIs(params) {
    const body = params || {};
    if (body.acknowledge_risk !== true) throw rpcError(400, "要按这一稿继续，需要明确确认审查没通过的风险", "risk_not_acknowledged", false);
    if (body.text || body.accept_unreviewed_edit) throw rpcError(400, "这个来源不接收正文，也不会静默保存编辑区", "text_forbidden", false);
    if (!body.text_hash || !body.idempotency_key || !body.base_token) throw rpcError(400, "缺少所选稿摘要", "invalid_request", false);
    if (body.source === "opinion" && (!body.opinion_id || body.opinion_revision == null || body.opinion_revision === "")) {
      throw rpcError(400, "按意见候选需要同时给出 opinion_id 和 opinion_revision", "invalid_request", false);
    }
    if (state.acceptFail) {
      const fail = state.acceptFail;
      state.acceptFail = null;
      throw fail;
    }
    state.continueState = "queued";
    state.acceptView = Object.assign({}, state.acceptView, { continue_state: "queued", continue_retryable: false, accept_state: "accepted" });
    return {
      ok: true,
      saved: true,
      official_saved: body.source !== "official",
      review_queued: false,
      accept_state: "accepted",
      continue_state: "queued",
      continue_stage: "asset",
      continue_paid: false,
      source: body.source,
      base_token: "sr1.after-accept",
      message: "已按这一稿继续。原来的审稿结论还在，没有改成通过，也没有出图。",
      unresolved_issues: [],
      unreviewed_edit: false,
    };
  }

  function acceptRetry(params) {
    const body = params || {};
    if (body.text || body.idempotency_key || body.source || body.text_hash || body.acknowledge_risk != null) {
      throw rpcError(400, "补排只接受项目和版本", "invalid_request", false);
    }
    if (!body.base_token) throw rpcError(400, "缺少版本", "invalid_request", false);
    state.continueState = "queued";
    return { ok: true, saved: true, official_saved: false, continue_state: "queued", base_token: body.base_token, message: "下一段已在队列里" };
  }

  async function handle(method, params) {
    state.calls.push({ method: method, params: params || {} });
    if (method === "config.get") return { models: [], templates: [], debug_llm: false };
    if (method === "projects.list") return [gateProject(state.projectId)];
    if (method === "projects.get") return gateProject(params.id || state.projectId);
    if (method === "projects.script_revision.get") return snapshot(params.id);
    if (method === "projects.send_message") return gateProject(params.id);
    if (method === "projects.script_revision.save") return save(params);
    if (method === "projects.script_revision.retry_review") return retryReview(params);
    if (method === "projects.script_revision.opinion") return submitOpinion(params);
    if (method === "projects.script_revision.opinion_retry") return retryOpinionCall(params);
    if (method === "projects.script_revision.accept_as_is") return acceptAsIs(params);
    if (method === "projects.script_revision.accept_retry") return acceptRetry(params);
    const blocked = {
      "projects.render": true,
      "projects.rerun": true,
      "projects.reset": true,
      "projects.save_content": true,
    };
    if (blocked[method]) throw rpcError(400, "fixture 不允许这个调用", "invalid_request", false);
    return {};
  }

  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = async function (url, opts) {
    const path = typeof url === "string" ? url : (url && url.url) || "";
    if (path.indexOf("/rpc") === -1) {
      if (!realFetch) throw new Error("no fetch");
      return realFetch(url, opts);
    }
    const body = JSON.parse(opts.body);
    try {
      const result = await handle(body.method, body.params);
      return { ok: true, json: async () => ({ jsonrpc: "2.0", id: body.id, result: result }) };
    } catch (error) {
      if (!error.rpcKnown) throw error;
      return {
        ok: true,
        json: async () => ({
          jsonrpc: "2.0",
          id: body.id,
          error: { code: error.code, message: error.message, data: error.data },
        }),
      };
    }
  };

  window.__fixture = {
    OFFICIAL: OFFICIAL,
    REJECTED: REJECTED,
    CANDIDATE: CANDIDATE,
    DECOY: DECOY,
    state: state,
    setMode: function (mode) { state.mode = mode; remember(); },
    setProjectId: function (id) { state.projectId = id; },
    arm: function (fail) { state.failNext = fail; },
    savedRetryError: function (message, reason) {
      state.failNext = {
        rpcKnown: true,
        code: 409,
        message: message,
        data: { reason: reason, retryable: true, saved: true, retry_same_key: true },
      };
    },
    armReviewNotQueued: function () {
      state.reviewPending = true;
      state.pendingToken = "sr1.after-save";
      remember();
      this.savedRetryError("稿已保存，审稿还没排上。请用原来的保存编号再试一次，不要改编辑区。", "review_not_queued");
    },
    armReviewAckUnconfirmed: function () {
      state.reviewPending = true;
      state.pendingToken = "sr1.after-save";
      remember();
      this.savedRetryError("稿已保存。审稿可能已经排上，但确认记录没写完。请用原来的保存编号再试一次，不要改编辑区，也不要换新编号。", "review_ack_unconfirmed");
    },
    release: function () {
      const resolve = state.release;
      state.release = null;
      if (resolve) resolve();
    },
    calls: function () { return state.calls.map((item) => ({ method: item.method, params: item.params })); },
    queueCount: function () { return state.queueCount; },
    releaseReview: function () {
      state.reviewPending = false;
      state.reviewQueued = false;
      state.forceDone = false;
      state.pendingToken = "";
      state.mode = "rejected";
      remember();
    },
  };
})();
