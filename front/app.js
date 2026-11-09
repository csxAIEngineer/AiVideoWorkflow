const briefEl = document.querySelector("#brief");
const form = document.querySelector("#composer");
const send = document.querySelector("#send");
const empty = document.querySelector("#empty");
const thread = document.querySelector("#thread");
const history = document.querySelector("#history");
const newChat = document.querySelector("#new-chat");
const main = document.querySelector(".main");
const debugEl = document.querySelector("#debug-llm");
const debugPromptEl = document.querySelector("#debug-prompt");
const debugIntentEl = document.querySelector("#debug-intent");
const soundtrackEl = document.querySelector("#soundtrack");
const modelEl = document.querySelector("#model");
const qwenEnvEl = document.querySelector("#qwen-env");
const minimaxVideoEl = document.querySelector("#minimax-video");
const minimaxVideoLabel = document.querySelector("#minimax-video-label");
const templateEl = document.querySelector("#template");
const modelFoot = document.querySelector("#model-foot");

const stageBar = document.querySelector("#stage-bar");
const canvasWrap = document.querySelector("#canvas-wrap");
const canvasEl = document.querySelector("#canvas");
const canvasNodes = document.querySelector("#canvas-nodes");
const canvasSvg = document.querySelector("#canvas-edges");
const debugDock = document.querySelector("#debug-dock");
const stageCenter = document.querySelector("#stage-center");
const previewPane = document.querySelector("#preview-pane");
const previewStage = document.querySelector("#preview-stage");
const previewEmpty = document.querySelector("#preview-empty");
const shotStrip = document.querySelector("#shot-strip");
const docNav = document.querySelector("#doc-nav");
const sideTabs = document.querySelector("#side-tabs");
const stageZoom = document.querySelector(".stage-zoom");

let projects = [];
let current = null;
let timer = null;
let clock = null;
let serverDebugDefault = false;
let otherOpen = false;
let sending = false;
let produceError = "";
let surface = "canvas";
let surfaceLock = "";
let surfaced = "";
let centerView = "canvas";
let previewShotId = "";
let sideTab = "history";
let canvasSig = "";
let canvasEdges = [];
let view = { x: 48, y: 48, scale: 1 };
const canvasMinScale = 0.25;
let pendingSaves = new Set();
let retouchSelection = null;
let retouchDrafts = {};
let retouchLocalError = "";
let retouchPending = false;
let keptPromptDiff = null;
let retouchDiffKey = "";
let retouchDiffSelection = "";
let seenRetouchError = "";
const pointer = { mode: "" };
let draftMaterials = [];
let seamKept = new Set();
const MAX_MATERIALS = 8;

const JOB_STEPS = ["立项锁定", "赛道分流", "剧本", "资产锁定", "分镜", "提示词封装", "投喂机检", "离线看板"];
const CREW_FALLBACK = [
  { id: "script", name: "编剧" },
  { id: "asset", name: "资产" },
  { id: "storyboard", name: "分镜" },
  { id: "check", name: "机检" },
  { id: "feed", name: "投喂" },
];
const DEBUG_FILE_LABELS = {
  skill_pack: "Skill 规则",
  system_prompt: "System Prompt",
  json_shape: "JSON 约束",
  raw_response: "原始回复",
  raw_response_repaired: "修复后回复",
  user_brief: "用户简要",
  drill_skill: "Drill 技能",
  drill_response: "Drill 回复",
  drill_response_repaired: "Drill 修复回复",
};

function isPromptDebug(project) {
  if (!project) return false;
  const progress = project.progress || {};
  return progress.debug_stop === "prompts" || progress.phase === "prompt_debug" || project.debug_stop === "prompts";
}

function isIntentDebug(project) {
  if (!project) return false;
  const progress = project.progress || {};
  return progress.debug_stop === "intent" || progress.phase === "intent_debug" || project.debug_stop === "intent";
}

function isDebugProject(project) {
  const phase = project && project.progress && project.progress.phase;
  return !!(project && (project.debug_llm || (project.progress && project.progress.debug_llm) || phase === "llm_debug" || phase === "image_debug"));
}

// Non-script stages End runs by itself after the script gate. Copy comes from
// phase, crew role, and the pipeline already on the project.
function autoStageKey(project) {
  const progress = (project && project.progress) || {};
  const pipeline = progress.pipeline || {};
  const phase = String(progress.phase || "");
  const role = String(progress.active_role || "");
  const current = String(pipeline.current || "");
  const action = String(pipeline.action || "");
  const message = String(progress.message || (project && project.stage) || "");
  if (phase === "gate_machine" || phase === "qc" || action === "finish" || role === "check" || pipeline.check === "machine") return "check";
  if ((message.indexOf("机检") >= 0 || message.indexOf("质检") >= 0) && message.indexOf("已过") < 0 && phase.indexOf("gate_") !== 0) return "check";
  if (current === "asset" || current === "storyboard" || current === "feed" || current === "script" || current === "recon") return current;
  if (role === "asset" || role === "storyboard" || role === "feed") return role;
  if (message.indexOf("资产") >= 0) return "asset";
  if (message.indexOf("分镜") >= 0) return "storyboard";
  if (message.indexOf("投喂") >= 0 || message.indexOf("封装") >= 0) return "feed";
  if (message.indexOf("剧本") >= 0) return "script";
  if (message.indexOf("分流") >= 0 || message.indexOf("对标") >= 0) return "recon";
  return "";
}

function repairAttempt(pipeline) {
  const stored = pipeline || {};
  const action = String(stored.action || "");
  const machine = Number(stored.machine_repairs) || 0;
  const repairs = Number(stored.repairs) || 0;
  const retries = Number(stored.auto_retries) || 0;
  if (action === "rewrite") {
    if (machine > 0 && repairs <= 0) return machine;
    if (repairs > 0) return repairs;
    if (machine > 0) return machine;
  }
  if (retries > 0 && action === "write") return retries;
  return 0;
}

function autoRun(project) {
  if (!project || project.status !== "writing") return null;
  if (isIntentDebug(project) || isPromptDebug(project) || isDebugProject(project)) return null;
  const progress = project.progress || {};
  const phase = progress.phase || "";
  if (phase && phase !== "writing" && phase !== "qc") return null;
  const key = autoStageKey(project);
  if (key !== "asset" && key !== "storyboard" && key !== "feed" && key !== "check") return null;
  const attempt = repairAttempt(progress.pipeline || {});
  let text = key === "check" ? "正在机检…" : ({
    asset: "正在自动写资产…",
    storyboard: "正在自动写分镜…",
    feed: "正在自动写投喂…",
  })[key];
  if (attempt) text += "，自动返修第 " + attempt + " 次";
  return { key: key, text: text, attempt: attempt };
}

function continueCue(text) {
  const raw = String(text || "");
  return raw.indexOf("继续才") >= 0
    || raw.indexOf("继续就") >= 0
    || raw.indexOf("继续之后") >= 0
    || raw.indexOf("点继续") >= 0
    || raw.indexOf("确认后才") >= 0;
}

function liveNodeKind(project) {
  if (!project || project.status !== "writing") return "";
  const key = autoStageKey(project);
  if (key === "asset") return "image";
  if (key === "storyboard" || key === "script") return key;
  return "";
}

function statusText(project) {
  const running = autoRun(project);
  if (running) return running.text;
  if (project.status === "gate") return project.stage || "停在门上";
  if (project.status === "drilling") {
    const phase = project.progress && project.progress.phase;
    if (phase === "lock_wait") return project.stage || "立项锁定";
    return phase === "drill_think" ? "正在想方向" : "选一个方向";
  }
  if (isIntentDebug(project) && project.status === "done") {
    return project.stage || "Debug：已停在意图解析";
  }
  if (isPromptDebug(project) && project.status === "done") {
    return project.stage || "Debug：已停在提示词";
  }
  if (isDebugProject(project) && project.status === "done") {
    const phase = project.progress && project.progress.phase;
    if (phase === "llm_debug") return project.stage || "Debug：已停在大模型";
    return project.stage || "Debug：已停在生图";
  }
  if (project.status === "done") {
    const phase = project.progress && project.progress.phase;
    if (phase === "prompts_ready") return project.stage || "提示词已就绪";
    return project.stage || "成片已生成";
  }
  if (project.status === "paused") return "已暂停";
  if (project.status === "error") return "未完成";
  const phase = project.progress && project.progress.phase;
  if (phase === "retry") return "重试";
  if (phase === "reconnect") return "重新连接";
  return project.stage || "处理中";
}

function writeStep(project) {
  const message = ((project.progress && project.progress.message) || project.stage || "");
  if (message.indexOf("看板") >= 0) return 7;
  if (message.indexOf("质检") >= 0 || message.indexOf("机检") >= 0) return 6;
  if (message.indexOf("投喂") >= 0 || message.indexOf("封装") >= 0) return 5;
  if (message.indexOf("分镜") >= 0) return 4;
  if (message.indexOf("资产") >= 0) return 3;
  if (message.indexOf("剧本") >= 0) return 2;
  if (message.indexOf("分流") >= 0 || message.indexOf("对标") >= 0) return 1;
  return 1;
}

function promptsReady(project) {
  return !!(project && project.status === "done" && project.progress && project.progress.phase === "prompts_ready");
}

function jobIndex(project) {
  const phase = (project.progress && project.progress.phase) || "";
  if (isIntentDebug(project)) {
    if (project.status === "done") return 2;
    if (project.status === "drilling" || project.status === "error") return 1;
    return 0;
  }
  if (isPromptDebug(project)) {
    if (project.status === "done") return 3;
    if (project.status === "writing" || project.script) return 2;
    if (project.status === "drilling" || project.status === "error") return 1;
    return 0;
  }
  if (isDebugProject(project)) {
    if (project.status === "done" && phase === "llm_debug") return 2;
    if (project.status === "done") return 3;
    if (project.status === "rendering" || project.status === "paused" || project.status === "building" || project.status === "error") return 2;
    if (phase === "images" || phase === "retry" || phase === "reconnect") return 2;
    if (project.status === "writing" || project.script) return 1;
    return 0;
  }
  if (project.status === "gate") {
    const gateSteps = {
      gate_intel: 1,
      gate_script: 2,
      gate_asset: 3,
      gate_storyboard: 4,
      gate_feed: 5,
      gate_fail: 6,
    };
    if (gateSteps[phase] != null) return gateSteps[phase];
  }
  if (project.status === "writing") {
    const mapped = { recon: 1, script: 2, asset: 3, storyboard: 4, feed: 5, check: 6 }[autoStageKey(project)];
    if (mapped != null) return mapped;
  }
  if (phase === "prompts_ready" || project.has_board) return JOB_STEPS.length;
  if (project.status === "done" || project.status === "building" || project.status === "rendering") return JOB_STEPS.length;
  if (project.status === "writing") return writeStep(project);
  if (project.status === "drilling" || phase === "aspect_wait") return 0;
  if (project.status === "error" || project.status === "paused") {
    const from = (project.progress && project.progress.paused_from) || "";
    if (from === "rendering" || from === "building") return JOB_STEPS.length;
    if (project.script || phase === "writing") return writeStep(project);
    return 0;
  }
  if (project.status === "queued") return 0;
  return project.script ? writeStep(project) : 0;
}

function formatSpan(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return hours + ":" + String(minutes % 60).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
  }
  return minutes + ":" + String(seconds).padStart(2, "0");
}

function elapsedLabel(project) {
  const start = Date.parse(project.started_at || project.created_at || "");
  if (!start) return "";
  const finished = project.status === "done" || project.status === "error";
  if (finished && !project.finished_at) return project.status === "done" ? "已完成" : "";
  const end = finished ? Date.parse(project.finished_at) : Date.now();
  if (!end) return "";
  return (finished ? "用时 " : "已用 ") + formatSpan(end - start);
}

function clockTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function durationText(task) {
  if (task.kind === "join") return "合计 " + task.duration + " 秒";
  if (task.kind === "audio") return "声轨";
  if (task.kind === "subtitles") return "烧录字幕";
  if (task.kind === "video") return task.duration + " 秒";
  return "生图";
}

function tickElapsed() {
  if (!current) return;
  const label = elapsedLabel(current);
  const node = document.querySelector(".job-elapsed");
  if (node && label) node.textContent = label;
  const dock = document.querySelector("#dock-elapsed");
  if (dock) dock.textContent = label;
}

function taskStateLabel(state) {
  if (state === "running") return "进行中";
  if (state === "done") return "已完成";
  if (state === "retrying") return "重试";
  if (state === "paused") return "已暂停";
  if (state === "error") return "失败";
  if (state === "ready") return "可运行";
  if (state === "muted") return "暂不执行";
  return "等待中";
}

function renderControls(project) {
  const rendering = project.status === "rendering" || project.status === "paused" || (project.status === "error" && (project.tasks || []).length);
  if (!rendering) return null;
  const bar = document.createElement("div");
  bar.className = "render-controls";
  const pause = document.createElement("button");
  pause.type = "button";
  pause.textContent = "暂停";
  pause.disabled = project.status !== "rendering";
  pause.addEventListener("click", () => postControl("pause"));
  const resume = document.createElement("button");
  resume.type = "button";
  resume.textContent = "继续";
  resume.disabled = project.status === "rendering";
  resume.addEventListener("click", () => onResumeClick());
  const reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = "重置当前步骤";
  reset.disabled = project.status === "rendering";
  reset.addEventListener("click", () => postControl("reset"));
  bar.append(pause, resume, reset);
  return bar;
}

document.querySelectorAll(".js-produce").forEach((button) => {
  button.addEventListener("click", () => postControl("render"));
});

function traceList(project) {
  const rows = (project && project.traces) || [];
  if (!project || !project.debug_trace || !rows.length) return null;
  const box = document.createElement("div");
  box.className = "trace-list";
  const title = document.createElement("h4");
  title.textContent = "过程";
  box.append(title);
  const list = document.createElement("ul");
  const busy = ["writing", "building", "rendering"].includes(project.status);
  rows.forEach((row) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = (row.label || row.stage || row.id) + ((row.error || row.pending) ? "（没写完）" : "");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "向前重放";
    button.disabled = busy || sending || !!row.pending || !!row.error;
    button.addEventListener("click", () => replayTrace(row.id));
    item.append(name, button);
    list.append(item);
  });
  box.append(list);
  return box;
}

async function replayTrace(traceId) {
  if (!current || sending || !traceId) return;
  sending = true;
  syncComposer();
  try {
    current = await rpc("projects.replay", { id: current.id, trace_id: traceId });
    const listed = projects.find((item) => item.id === current.id);
    if (listed) Object.assign(listed, current);
    renderHistory();
    renderThread();
    watch();
  } catch (error) {
    alert(error.message || "没有完成");
  } finally {
    sending = false;
    syncComposer();
    if (current) renderThread();
  }
}

async function postControl(action) {
  if (!current || sending) return;
  sending = true;
  syncComposer();
  try {
    if (action === "render") produceError = "";
    current = await rpc("projects." + action, { id: current.id });
    const listed = projects.find((item) => item.id === current.id);
    if (listed) Object.assign(listed, current);
    renderHistory();
    renderThread();
    watch();
  } catch (error) {
    const message = error.message || "没有完成";
    if (action === "render") {
      produceError = message;
      if (current) {
        try {
          const project = await rpc("projects.get", { id: current.id });
          if (current && project && project.id === current.id) current = project;
        } catch (_error) {
          /* The 409 text stays visible even if the refresh misses. */
        }
      }
      paintTranscript.key = "";
      if (current) renderThread();
    } else alert(message);
  } finally {
    sending = false;
    syncComposer();
  }
}

function clipCard(title, playUrl, downloadUrl) {
  const card = document.createElement("article");
  card.className = "clip-card";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const video = document.createElement("video");
  video.controls = true;
  video.preload = "metadata";
  video.src = playUrl;
  const link = document.createElement("a");
  link.className = "download";
  link.href = downloadUrl || playUrl;
  link.textContent = "下载";
  card.append(heading, video, link);
  return card;
}

function clipList(project) {
  const clips = (project && project.clips) || [];
  const film = project && project.has_video && project.video_url;
  if (!clips.length && !film) return null;
  const block = document.createElement("section");
  block.className = "clips";
  const title = document.createElement("h3");
  title.textContent = "生成的视频";
  const list = document.createElement("div");
  list.className = "clip-list";
  clips.forEach((clip) => {
    list.append(clipCard(clip.id || clip.name, clip.url, clip.download_url));
  });
  if (film) {
    list.append(clipCard("成片", project.video_url, project.video_download_url || project.video_url));
  }
  block.append(title, list);
  return block;
}

function imageGallery(project) {
  const images = (project && project.images) || [];
  if (!images.length) return null;
  const block = document.createElement("section");
  block.className = "stills";
  const title = document.createElement("h3");
  title.textContent = "生成的图片";
  const grid = document.createElement("div");
  grid.className = "still-grid";
  images.forEach((image) => {
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.src = image.url;
    img.alt = image.name;
    const caption = document.createElement("figcaption");
    caption.textContent = image.name.replace(/\.[^.]+$/, "");
    fig.append(img, caption);
    grid.append(fig);
  });
  block.append(title, grid);
  return block;
}

function progressPanel(project) {
  const panel = document.createElement("section");
  panel.className = "job";
  const head = document.createElement("div");
  head.className = "job-head";
  const title = document.createElement("b");
  title.textContent = isIntentDebug(project) || isPromptDebug(project) || isDebugProject(project) ? "任务进展 · Debug" : "任务进展";
  const elapsed = document.createElement("span");
  elapsed.className = "job-elapsed";
  elapsed.textContent = elapsedLabel(project);
  head.append(title, elapsed);
  panel.append(head);

  const steps = document.createElement("ol");
  steps.className = "job-steps";
  const index = jobIndex(project);
  const stepLabels = isIntentDebug(project)
    ? ["建立项目", "意图解析", "Debug 结束"]
    : isPromptDebug(project)
      ? ["建立项目", "意图解析", "写提示词", "Debug 结束"]
      : isDebugProject(project)
        ? ["建立项目", "写提示词", "生图", "Debug 结束"]
        : JOB_STEPS;
  const running = autoRun(project);
  stepLabels.forEach((label, step) => {
    const item = document.createElement("li");
    const earlyStop = isDebugProject(project) && (project.progress || {}).phase === "llm_debug";
    const done = (project.status === "done" && !earlyStop) || step < index;
    if (done) item.className = "done";
    else if ((project.status === "error" || project.status === "paused") && step === index) item.className = project.status === "paused" ? "paused" : "failed";
    else if (step === index) item.className = running ? "current live" : "current";
    item.textContent = label;
    steps.append(item);
  });
  panel.append(steps);

  const progress = project.progress || {};
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
  const barWrap = document.createElement("div");
  barWrap.className = "job-bar";
  const bar = document.createElement("div");
  bar.className = "job-bar-fill" + (project.status === "error" ? " failed" : "");
  bar.style.width = (project.status === "done" ? 100 : percent) + "%";
  const barLabel = document.createElement("span");
  barLabel.className = "job-bar-label";
  barLabel.textContent = (project.status === "done" ? 100 : percent) + "%";
  barWrap.append(bar, barLabel);
  panel.append(barWrap);

  const message = document.createElement("p");
  message.className = "job-message" + (running ? " auto-run" : "");
  message.textContent = running ? running.text : (progress.message || project.stage || (project.status === "error" ? "未完成" : "处理中"));
  panel.append(message);
  const calls = Array.isArray(progress.calls) ? progress.calls : [];
  if (calls.length) {
    const list = document.createElement("ul");
    list.className = "job-calls";
    calls.forEach((item) => {
      const row = document.createElement("li");
      const status = String(item.status || "");
      if (status === "完成") row.className = "done";
      else if (status === "进行中") row.className = "current";
      else if (status === "失败") row.className = "failed";
      const name = document.createElement("span");
      name.textContent = item.name || "";
      const mark = document.createElement("em");
      mark.textContent = status;
      row.append(name, mark);
      list.append(row);
    });
    panel.append(list);
  }
  if (explicitRenderEntry(project)) {
    const produce = document.createElement("button");
    produce.type = "button";
    produce.className = "produce";
    produce.textContent = "出图出片";
    produce.addEventListener("click", () => postControl("render"));
    panel.append(produce);
    if (produceError) {
      const failure = document.createElement("p");
      failure.className = "stage produce-error";
      failure.textContent = produceError;
      panel.append(failure);
    }
  }
  if (project.board_url) {
    const board = document.createElement("a");
    board.className = "download";
    board.href = project.board_url;
    board.target = "_blank";
    board.rel = "noopener";
    board.textContent = "打开看板";
    panel.append(board);
  }
  const controls = renderControls(project);
  if (controls) panel.append(controls);

  const events = project.events || [];
  if (events.length) {
    const log = document.createElement("ul");
    log.className = "job-log";
    events.slice(-40).forEach((event) => {
      const row = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = clockTime(event.at);
      const text = document.createElement("span");
      text.textContent = event.message || "";
      row.append(time, text);
      log.append(row);
    });
    panel.append(log);
  }

  const tasks = project.tasks || [];
  if (tasks.length) {
    const done = tasks.filter((task) => task.state === "done").length;
    const running = tasks.filter((task) => task.state === "running").length;
    const summary = document.createElement("p");
    summary.className = "task-summary";
    summary.textContent = "已完成 " + done + " / " + tasks.length + (running ? " · 进行中 " + running : "");
    panel.append(summary);
    const list = document.createElement("ol");
    list.className = "task-list";
    tasks.forEach((task) => {
      const row = document.createElement("li");
      row.className = "task task-" + (task.state || "pending");
      const name = document.createElement("b");
      name.textContent = task.id + (task.title && task.title !== task.id ? " " + task.title : "");
      const length = document.createElement("span");
      length.className = "task-duration";
      length.textContent = durationText(task);
      const state = document.createElement("span");
      state.className = "task-state";
      state.textContent = taskStateLabel(task.state);
      const detail = document.createElement("span");
      detail.className = "task-detail";
      detail.textContent = task.detail || "";
      row.append(name, length, state, detail);
      list.append(row);
    });
    panel.append(list);
  }
  return panel;
}

async function deleteProject(id) {
  const project = projects.find((item) => item.id === id);
  const label = (project && (project.title || project.brief)) || id;
  if (!window.confirm("删除这条历史记录？\n" + label)) return;
  try {
    await rpc("projects.delete", { id: id });
  } catch (error) {
    alert(error.message || "删除失败");
    return;
  }
  projects = projects.filter((item) => item.id !== id);
  if (current && current.id === id) {
    current = null;
    clearInterval(timer);
    clearInterval(clock);
  }
  renderHistory();
  renderThread();
}

function renderHistory() {
  history.innerHTML = "";
  projects.forEach((project) => {
    const row = document.createElement("div");
    row.className = "item-row" + (current && current.id === project.id ? " active" : "");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "item";
    const title = document.createElement("b");
    title.textContent = project.title || project.brief;
    const meta = document.createElement("span");
    meta.textContent = statusText(project);
    button.append(title, meta);
    button.addEventListener("click", () => openProject(project.id));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "item-delete";
    remove.title = "删除";
    remove.setAttribute("aria-label", "删除历史记录");
    remove.textContent = "×";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteProject(project.id);
    });
    row.append(button, remove);
    history.append(row);
  });
}

function emphasizeLine(text) {
  if (text.includes("==") || text.includes("**")) return text;
  if (/^\[(?:VO|画面文字|动作)\]/.test(text)) return "==" + text + "==";
  const duration = text.replace(/(时长[:：]\s*\d+\s*秒)/g, "==$1==");
  if (duration !== text) return duration;
  const dialogue = /^([^：:\n]{1,12}(?:（[^）]{1,16}）)?)[：:](.+)$/.exec(text);
  if (!dialogue) return text;
  const speaker = dialogue[1];
  if (/来源|景别|机位|运镜|起点|终点|声音|视觉|唯一动作|目的/.test(speaker)) return text;
  return speaker + "：" + "==" + dialogue[2].trim() + "==";
}

function appendInline(parent, text) {
  const pattern = /==([^=\n]+)==|\*\*([^*\n]+)\*\*|`([^`\n]+)`|\*([^*\n]+)\*/g;
  let last = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > last) parent.appendChild(document.createTextNode(text.slice(last, match.index)));
    if (match[1] != null || match[2] != null) {
      const mark = document.createElement("mark");
      mark.textContent = match[1] != null ? match[1] : match[2];
      parent.appendChild(mark);
    } else if (match[3] != null) {
      const code = document.createElement("code");
      code.textContent = match[3];
      parent.appendChild(code);
    } else {
      const em = document.createElement("em");
      em.textContent = match[4];
      parent.appendChild(em);
    }
    last = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

function renderMarkdown(target, source) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const fence = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        fence.push(lines[index]);
        index += 1;
      }
      index += 1;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = fence.join("\n");
      pre.appendChild(code);
      target.appendChild(pre);
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const node = document.createElement("h" + (heading[1].length + 2));
      appendInline(node, heading[2]);
      target.appendChild(node);
      index += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = document.createElement("blockquote");
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        const paragraph = document.createElement("p");
        appendInline(paragraph, emphasizeLine(lines[index].replace(/^>\s?/, "")));
        quote.appendChild(paragraph);
        index += 1;
      }
      target.appendChild(quote);
      continue;
    }
    if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length && /^\s*(?:[-*+]|\d+\.)\s+/.test(lines[index])) {
        const item = document.createElement("li");
        appendInline(item, emphasizeLine(lines[index].replace(/^\s*(?:[-*+]|\d+\.)\s+/, "")));
        list.appendChild(item);
        index += 1;
      }
      target.appendChild(list);
      continue;
    }
    const paragraph = document.createElement("p");
    appendInline(paragraph, emphasizeLine(line));
    target.appendChild(paragraph);
    index += 1;
  }
}

function promptRowsFromText(text) {
  const source = String(text || "").trim();
  if (!source) return [];
  const bodyLines = source.split("\n").filter((line) => /^\s*\|/.test(line) && !/^\s*\|[\s:|-]+\|?\s*$/.test(line));
  if (bodyLines.length >= 2) {
    const rows = bodyLines.map((line) => line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((cell) => cell.trim()));
    const header = rows[0].join(" ");
    const data = /编号|名称|提示词|prompt|title/i.test(header) ? rows.slice(1) : rows;
    return data.filter((cells) => cells.join("").trim()).map((cells) => ({
      id: cells[0] || "",
      title: cells[1] || "",
      depends_on: cells.length > 3 ? cells[2] : "",
      prompt: cells[cells.length - 1] || "",
    }));
  }
  const blocks = source.split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
  const rows = [];
  blocks.forEach((block) => {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    if (!lines.length) return;
    const head = lines[0].replace(/^#{1,3}\s+/, "");
    const prompt = lines.slice(1).join(" ").trim();
    const idMatch = /^([A-Za-z]{1,8}-\d+|P\d+)\s+(.+)$/.exec(head);
    if (idMatch && prompt) {
      rows.push({ id: idMatch[1], title: idMatch[2], depends_on: "", prompt: prompt });
      return;
    }
    lines.forEach((line) => {
      const item = line.replace(/^[-*]\s+/, "");
      const pair = /^(.+?)[:：]\s+(.+)$/.exec(item);
      if (pair && pair[2].length > 12) rows.push({ id: "", title: pair[1], depends_on: "", prompt: pair[2] });
    });
    if (!rows.length && prompt) rows.push({ id: "", title: head, depends_on: "", prompt: prompt });
  });
  return rows.filter((row) => row.prompt);
}

function stillForRow(images, row) {
  const id = String((row && row.id) || "").trim();
  if (!id) return null;
  return (images || []).find((image) => String(image.name || "").replace(/\.[^.]+$/, "") === id) || null;
}

function promptTable(text, assets, images) {
  const fromAssets = (assets || []).filter((item) => item && item.prompt);
  const rows = fromAssets.length ? fromAssets : promptRowsFromText(text);
  if (rows.length < 2) return null;
  const showRef = rows.some((row) => assetReferenceText(row));
  const showStill = rows.some((row) => stillForRow(images, row));
  const table = document.createElement("table");
  table.className = "prompt-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["编号", "名称"].concat(showStill ? ["图片"] : []).concat(showRef ? ["参考"] : []).concat(["提示词"]).forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.appendChild(cell);
  });
  head.appendChild(headRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const id = document.createElement("td");
    id.className = "col-id";
    id.textContent = row.id || "";
    const title = document.createElement("td");
    title.className = "col-name";
    appendInline(title, String(row.title || "").replace(/==/g, ""));
    tr.append(id, title);
    if (showStill) {
      const still = document.createElement("td");
      still.className = "col-still";
      const image = stillForRow(images, row);
      if (image) {
        const img = document.createElement("img");
        img.src = image.url;
        img.alt = row.id || image.name;
        still.appendChild(img);
      }
      tr.appendChild(still);
    }
    if (showRef) {
      const ref = document.createElement("td");
      ref.className = "col-ref";
      ref.textContent = assetReferenceText(row) || "—";
      tr.appendChild(ref);
    }
    const prompt = document.createElement("td");
    prompt.className = "col-prompt";
    appendInline(prompt, row.prompt);
    tr.appendChild(prompt);
    body.appendChild(tr);
  });
  table.append(head, body);
  return table;
}

function section(title, text, assets, images) {
  if (!text && !(assets && assets.length)) return null;
  const block = document.createElement("div");
  block.className = "block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  block.appendChild(heading);
  if (title === "生图提示词") {
    const table = promptTable(text, assets, images);
    if (table) {
      block.appendChild(table);
      return block;
    }
  }
  if (!text) return null;
  const body = document.createElement("div");
  body.className = "md";
  renderMarkdown(body, text);
  block.appendChild(body);
  return block;
}

function inDrill(project) {
  if (!project) return false;
  if (project.status === "drilling") return true;
  const phase = project.progress && project.progress.phase;
  return phase === "drill_think" || phase === "drill_wait";
}

function visibleMessages(project) {
  const messages = project.messages || [];
  if (messages.length) return messages;
  if (project.brief) return [{ role: "user", content: project.brief }];
  return [];
}

function latestChoiceMessage(project) {
  const messages = (project && project.messages) || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant") continue;
    if ((message.options || []).length) return message;
    return null;
  }
  return null;
}

function latestOptions(project) {
  const message = latestChoiceMessage(project);
  return message ? message.options : [];
}

function optionEffect(project, label) {
  const effects = project && project.option_effects;
  if (!effects || typeof effects !== "object") return null;
  const exact = effects[label];
  if (exact && typeof exact === "object") return exact;
  const wildcard = label === "*" ? null : effects["*"];
  return wildcard && typeof wildcard === "object" ? wildcard : null;
}

function optionRejected(project, label) {
  const effect = optionEffect(project, label);
  return !!(effect && effect.effect === "reject");
}

function freeReplyClosed(project) {
  const wildcard = optionEffect(project, "*");
  return !!(wildcard && wildcard.effect === "reject");
}

function retiredDeliveryNote(project) {
  return freeReplyClosed(project);
}

function explicitRenderEntry(project) {
  return promptsReady(project)
    || ((project.progress || {}).phase === "aspect_wait")
    || project.render_recovery === "projects.render";
}

function onResumeClick() {
  if (current && current.status === "gate") {
    if (current.render_recovery === "projects.render" || optionRejected(current, "继续")) return;
    sendText("继续");
    return;
  }
  postControl("resume");
}

function syncChoiceDisabled() {
  document.querySelectorAll("#thread button.choice, #thread select.style-select").forEach((control) => {
    control.disabled = !!sending;
  });
}

function multiChoices(options) {
  const list = document.createElement("div");
  list.className = "choices seam-choices";
  const boxes = [];
  options.forEach((label) => {
    const row = document.createElement("label");
    row.className = "seam-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = label;
    box.checked = seamKept.has(label);
    boxes.push(box);
    row.append(box, document.createTextNode(label));
    list.append(row);
  });
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "choice";
  submit.textContent = "继续";
  submit.addEventListener("click", () => {
    const labels = boxes.filter((box) => box.checked).map((box) => box.value);
    if (!labels.length || sending) return;
    sendText(labels.join("、"));
  });
  list.append(submit);
  return list;
}

function styleNote(card, lead) {
  const note = document.createElement("div");
  note.className = "style-note";
  if (lead) {
    const line = document.createElement("p");
    line.className = "style-lead";
    line.textContent = lead;
    note.append(line);
  }
  if (card && card.blurb) {
    const blurb = document.createElement("p");
    blurb.className = "style-blurb";
    blurb.textContent = card.blurb;
    note.append(blurb);
  }
  if (card && card.look) {
    const look = document.createElement("p");
    look.className = "style-look";
    look.textContent = card.look;
    note.append(look);
  }
  if (card && card.grade_label) {
    const grade = document.createElement("p");
    grade.className = "style-grade";
    grade.textContent = card.grade_label;
    note.append(grade);
  }
  return note;
}

function styleChoices(message) {
  const list = document.createElement("div");
  list.className = "choices style-picker";
  const cards = message.cards || [];
  const recommended = cards.find((card) => card.recommended) || null;
  const select = document.createElement("select");
  select.className = "style-select";
  select.setAttribute("aria-label", "导演风格");
  const recommendOption = document.createElement("option");
  recommendOption.value = "按你的推荐走";
  recommendOption.textContent = "按你的推荐走";
  recommendOption.selected = true;
  select.append(recommendOption);
  const groups = [];
  cards.forEach((card) => {
    const name = card.group || "导演风格";
    let group = groups.find((item) => item.name === name);
    if (!group) {
      group = { name: name, items: [] };
      groups.push(group);
    }
    group.items.push(card);
  });
  groups.forEach((group) => {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.name;
    group.items.forEach((card) => {
      const option = document.createElement("option");
      option.value = card.title || "";
      option.textContent = card.title || "";
      optgroup.append(option);
    });
    select.append(optgroup);
  });
  const noteHost = document.createElement("div");
  function show(title) {
    const card = title === "按你的推荐走"
      ? recommended
      : cards.find((item) => item.title === title);
    const lead = title === "按你的推荐走" && card
      ? "这支片子推荐：" + card.title
      : "";
    const next = styleNote(card, lead);
    noteHost.replaceChildren(...next.childNodes);
    noteHost.className = "style-note";
  }
  show(select.value);
  select.addEventListener("change", () => show(select.value));
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "choice";
  submit.textContent = "继续";
  submit.addEventListener("click", () => {
    if (sending || !select.value) return;
    sendText(select.value);
  });
  list.append(select, noteHost, submit);
  return list;
}

function choiceList(messageOrOptions) {
  const message = Array.isArray(messageOrOptions) ? { options: messageOrOptions } : (messageOrOptions || {});
  const options = message.options || [];
  const kind = message.choice_kind || "";
  if (kind === "multi") return multiChoices(options);
  if (kind === "cards") return styleChoices(message);
  const list = document.createElement("div");
  list.className = "choices";
  options.forEach((label, index) => {
    if (optionRejected(current, label)) return;
    const button = document.createElement("button");
    button.type = "button";
    const other = label === "其他";
    button.className = "choice" + (other ? " choice-other" : !kind && index === 0 ? " choice-rec" : "");
    if (!other && !kind && index === 0) {
      const tag = document.createElement("span");
      tag.className = "choice-tag";
      tag.textContent = "建议";
      button.append(tag);
    }
    button.append(document.createTextNode(label));
    if (sending) button.disabled = true;
    button.addEventListener("click", () => {
      if (sending) return;
      if (other) {
        if (freeReplyClosed(current)) return;
        otherOpen = true;
        syncComposer();
        briefEl.focus();
        return;
      }
      list.querySelectorAll("button").forEach((item) => {
        item.disabled = true;
      });
      sendText(label);
    });
    list.append(button);
  });
  return list;
}

function messageBubble(role, text) {
  const node = document.createElement("div");
  if (role === "user") {
    node.className = "user";
    node.textContent = text;
    return node;
  }
  node.className = "assistant-reply";
  const body = document.createElement("div");
  body.className = "md";
  renderMarkdown(body, text);
  node.appendChild(body);
  return node;
}

function syncComposer() {
  const thinking = current && current.status === "drilling" && !current.can_reply;
  const busy = current && !["drilling", "done", "error"].includes(current.status);
  if (modelEl) modelEl.disabled = sending || thinking || busy;
  if (templateEl) templateEl.disabled = sending || !!current;
  const options = current ? latestOptions(current) : [];
  const replyClosed = !!(current && freeReplyClosed(current));
  if (replyClosed) otherOpen = false;
  const choosing = !!(current && current.can_reply && options.length);
  const showOther = !replyClosed && ((choosing && otherOpen) || !!(current && current.can_reply && !options.length));
  const showBrief = !thinking && !busy && !choosing && !showOther && (!current || current.status === "done" || current.status === "error");
  const showInput = showOther || showBrief;
  briefEl.classList.toggle("hidden", !showInput);
  send.classList.toggle("hidden", !showInput);
  briefEl.disabled = !showInput || sending;
  send.disabled = !showInput || sending;
  form.classList.toggle("bare", !showInput);
  const materials = document.querySelector("#materials");
  if (materials) materials.classList.toggle("hidden", !showBrief);
  const materialFile = document.querySelector("#material-file");
  if (materialFile) materialFile.disabled = !showBrief || sending;
  if (showOther) briefEl.placeholder = "写下你的方向";
  else if (showBrief) briefEl.placeholder = "文字决定要拍成什么样：时长、广告、感觉、情节和结尾。用户没写的部分，用读图补上身份和场景。";
  // The ready reply is painted while `sending` is still true, and a done project stops polling.
  if (current) syncControlButtons(current);
}

function workflowMode(project) {
  if (!project || isIntentDebug(project)) return false;
  if (project.status === "gate") return true;
  if (inDrill(project)) return false;
  if (["writing", "building", "rendering", "paused", "done", "error"].includes(project.status)) return true;
  return !!project.script;
}

// Home hints only. Workflow keeps the canvas and the chat column together.
function chatHome(project) {
  if (!project) return false;
  const status = project.status;
  const progress = project.progress || {};
  if (status === "drilling" || status === "gate" || status === "writing") return true;
  return status === "paused" && (progress.paused_from || "") === "writing";
}

function canvasHome(project) {
  if (!project) return false;
  const status = project.status;
  const progress = project.progress || {};
  const phase = progress.phase || "";
  if (status === "done" && phase === "prompts_ready") return true;
  if (status === "rendering" || status === "building") return true;
  if (status === "paused") {
    const from = progress.paused_from || "";
    if (from === "rendering" || from === "building") return true;
  }
  return status === "done" && (phase === "done" || !!project.video_file);
}

function surfaceKey(project) {
  if (!project) return "";
  const progress = project.progress || {};
  if (canvasHome(project)) return "canvas:" + project.status + ":" + (progress.phase || "");
  if (chatHome(project)) return "chat:" + project.status;
  return "other:" + (project.status || "");
}

function settleSurface(project) {
  const key = surfaceKey(project);
  if (key === surfaced) return;
  surfaced = key;
  surfaceLock = "";
  surface = key.indexOf("canvas:") === 0 ? "canvas" : "chat";
}

function syncCenter() {
  const workflow = !!(main && main.classList.contains("workflow"));
  const preview = workflow && centerView === "preview";
  if (stageCenter) stageCenter.classList.toggle("hidden", !workflow);
  if (canvasWrap) canvasWrap.classList.toggle("hidden", !workflow || preview);
  if (previewPane) previewPane.classList.toggle("hidden", !preview);
  if (shotStrip) shotStrip.classList.toggle("hidden", !workflow);
  if (stageZoom) stageZoom.classList.toggle("hidden", preview);
  const canvasBtn = document.querySelector("#view-canvas");
  const previewBtn = document.querySelector("#view-preview");
  if (canvasBtn) canvasBtn.classList.toggle("on", workflow && !preview);
  if (previewBtn) previewBtn.classList.toggle("on", preview);
}

function setMenuOpen(open) {
  const menu = document.querySelector("#app-menu");
  const toggle = document.querySelector("#menu-toggle");
  if (menu) menu.classList.toggle("open", !!open);
  if (toggle) {
    toggle.classList.toggle("on", !!open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
}

function syncSideMode() {
  const workflow = !!(main && main.classList.contains("workflow"));
  if (sideTabs) sideTabs.classList.toggle("hidden", !workflow);
  const docs = workflow && sideTab !== "history";
  if (docNav) docNav.classList.toggle("hidden", !docs);
  if (history) history.classList.toggle("hidden", docs);
  const docsBtn = document.querySelector("#side-docs");
  const historyBtn = document.querySelector("#side-history");
  if (docsBtn) docsBtn.classList.toggle("on", docs);
  if (historyBtn) historyBtn.classList.toggle("on", workflow && !docs);
}

function crewNote(project) {
  const progress = (project && project.progress) || {};
  let text = String(progress.message || (project && project.stage) || "").replace(/\s+/g, " ").trim();
  const split = text.indexOf(" · ");
  if (split >= 0) text = text.slice(0, split).trim();
  text = text.replace(/^.{1,48}?正在/, "正在");
  text = text.split(/[。\n]/)[0].trim();
  if (text.length > 32) text = text.slice(0, 32) + "…";
  return text;
}

function syncCrew(project) {
  const box = document.querySelector("#crew");
  const toggle = document.querySelector("#crew-toggle");
  const roles = document.querySelector("#crew .crew-roles");
  const line = document.querySelector("#crew-line");
  if (!box || !toggle || !roles || !line) return;
  if (!project) {
    box.classList.add("settled");
    box.classList.remove("speaking");
    box.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "分工，现在没有人在写");
    line.textContent = "";
    syncCrew.sig = "";
    syncCrew.projectId = "";
    return;
  }
  const progress = project.progress || {};
  const crew = Array.isArray(progress.crew) && progress.crew.length ? progress.crew : CREW_FALLBACK;
  const active = String(progress.active_role || "");
  const running = autoRun(project);
  const live = !!(active && project.status === "writing");
  const note = running ? running.text : crewNote(project);
  const sig = crew.map((item) => (item.id || "") + ":" + (item.name || "")).join("|") + "#" + active + (live ? "*" : "") + "#" + note;
  box.classList.toggle("settled", !active);
  box.classList.toggle("speaking", !!running);
  if (syncCrew.projectId !== project.id) {
    box.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
    syncCrew.projectId = project.id;
  }
  const activeName = (crew.find((item) => item.id === active) || {}).name || "";
  toggle.setAttribute(
    "aria-label",
    running ? running.text : (activeName ? activeName + (live ? "正在进行" : "停在这一步") : "分工，现在没有人在写")
  );
  line.textContent = note;
  if (syncCrew.sig === sig) return;
  syncCrew.sig = sig;
  roles.replaceChildren();
  crew.forEach((item) => {
    const pill = document.createElement("span");
    pill.className = "crew-role";
    if (item.id && item.id === active) {
      pill.classList.add("on");
      if (live) pill.classList.add("live");
      pill.setAttribute("aria-current", "true");
    }
    pill.setAttribute("role", "listitem");
    const dot = document.createElement("span");
    dot.className = "crew-dot";
    dot.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = item.name || "";
    pill.append(dot, label);
    roles.append(pill);
  });
}

function liveCopy(project, kind) {
  const running = autoRun(project);
  if (!running || liveNodeKind(project) !== kind) return "";
  return running.text;
}

function restingMeta(project, node) {
  const kind = node.dataset.kind;
  if (kind === "image") return ((project.assets || []).length) + " 张 · 必须";
  if (kind === "shot") {
    const shot = (project.shots || []).find((item) => item.id === node.dataset.id);
    if (shot && shot.duration != null && shot.duration !== "") return shot.duration + " 秒";
    const count = node.querySelectorAll(".wf-thumb").length;
    return count ? count + " 张" : "";
  }
  return "";
}

function syncStageLive(project) {
  const bar = document.querySelector("#stage-livebar");
  const line = document.querySelector("#stage-live");
  const strip = document.querySelector("#stage-strip");
  if (!bar || !line || !strip) return;
  const running = project && workflowMode(project) ? autoRun(project) : null;
  const show = !!(project && workflowMode(project) && (running || project.status === "writing"));
  bar.classList.toggle("hidden", !show);
  if (!show) {
    line.textContent = "";
    strip.replaceChildren();
    syncStageLive.sig = "";
    return;
  }
  line.textContent = running ? running.text : (project.stage || "");
  line.classList.toggle("live", !!running);
  const index = jobIndex(project);
  const sig = line.textContent + "#" + index + "#" + project.status + "#" + (running ? running.key : "");
  if (syncStageLive.sig === sig && strip.childElementCount) return;
  syncStageLive.sig = sig;
  strip.replaceChildren();
  JOB_STEPS.forEach((label, step) => {
    const item = document.createElement("li");
    if (step < index) item.className = "done";
    else if (step === index) item.className = running ? "current live" : "current";
    item.textContent = label;
    strip.append(item);
  });
}

function hideWorkflow() {
  syncCrew(null);
  syncStageLive(null);
  if (stageBar) stageBar.classList.add("hidden");
  if (debugDock) {
    debugDock.classList.add("hidden");
    debugDock.classList.remove("open");
  }
  const dockToggle = document.querySelector("#dock-toggle");
  if (dockToggle) dockToggle.classList.remove("on");
  if (main) main.classList.remove("workflow");
  syncCenter();
  syncSideMode();
}

function showWorkflow() {
  syncCrew(current);
  syncStageLive(current);
  if (stageBar) stageBar.classList.remove("hidden");
  if (debugDock) debugDock.classList.remove("hidden");
  if (thread) thread.classList.remove("hidden");
  if (main) main.classList.add("workflow");
  syncCenter();
  syncSideMode();
  syncControlButtons(current);
}

function renderThread(options) {
  const opts = options || {};
  main.classList.toggle("fresh", !current);
  if (!current) {
    surface = "canvas";
    surfaceLock = "";
    surfaced = "";
    canvasSig = "";
    centerView = "canvas";
    previewShotId = "";
    hideWorkflow();
    empty.classList.remove("hidden");
    thread.classList.add("hidden");
    thread.innerHTML = "";
    clearChatPin();
    paintTranscript.key = "";
    paintTranscript.projectId = "";
    seamKept = new Set();
    syncRetouchPanel(null);
    syncComposer();
    return;
  }
  empty.classList.add("hidden");
  settleSurface(current);
  if (!workflowMode(current)) {
    hideWorkflow();
    thread.classList.remove("hidden");
    paintTranscript(opts, true);
    syncRetouchPanel(current);
    return;
  }
  showWorkflow();
  thread.classList.remove("hidden");
  paintTranscript(opts, false);
  syncCanvas(current);
  syncRetouchPanel(current);
  syncDocNav(current);
  syncShotStrip(current);
  syncPreview(current);
  syncDock(current);
  refreshLogs();
  syncComposer();
}

function transcriptKey(project) {
  const messages = visibleMessages(project);
  const last = messages.length ? messages[messages.length - 1] : {};
  const progress = project.progress || {};
  return [
    project.id,
    project.status,
    project.stage,
    project.can_reply,
    project.error,
    progress.phase,
    progress.percent,
    progress.message,
    progress.active_role,
    (progress.pipeline || {}).current,
    (progress.pipeline || {}).action,
    (progress.pipeline || {}).check,
    (progress.pipeline || {}).repairs,
    (progress.pipeline || {}).machine_repairs,
    (progress.pipeline || {}).auto_retries,
    (progress.calls || []).map((item) => `${item.name}:${item.status}`).join(","),
    messages.length,
    last.role,
    last.content,
    (last.options || []).join("\n"),
    last.choice_kind,
    (last.cards || []).map((card) => card.title).join("\n"),
    project.script,
    project.storyboard,
    project.image_prompts,
    project.debug_trace ? "trace" : "",
    (project.traces || []).map((row) => [row.id, row.label, row.error, row.pending].join(":")).join("|"),
    (project.images || []).length,
    (project.shots || []).length,
    (typeof ScriptRevisionEdit !== "undefined" && ScriptRevisionEdit.revisionSignature)
      ? ScriptRevisionEdit.revisionSignature(project.id)
      : "",
  ].join("\u0001");
}

function clearChatPin() {
  const pin = document.querySelector("#chat-pin");
  if (!pin) return;
  const progressHost = pin.querySelector(".chat-progress");
  const actionHost = pin.querySelector(".chat-actions");
  if (progressHost) progressHost.replaceChildren();
  if (actionHost) actionHost.replaceChildren();
  pin.classList.add("hidden");
}

function fillChatPin(progress, actions) {
  const pin = document.querySelector("#chat-pin");
  if (!pin) return;
  const progressHost = pin.querySelector(".chat-progress");
  const actionHost = pin.querySelector(".chat-actions");
  if (progressHost) {
    progressHost.replaceChildren();
    if (progress) progressHost.append(progress);
  }
  if (actionHost) {
    actionHost.replaceChildren();
    (actions || []).forEach((node) => {
      if (node) actionHost.append(node);
    });
  }
  const filled = (progressHost && progressHost.childElementCount) || (actionHost && actionHost.childElementCount);
  pin.classList.toggle("hidden", !filled);
}

function threadAnchor() {
  const top = thread.getBoundingClientRect().top;
  const nodes = thread.querySelectorAll("[data-msg]");
  for (let index = 0; index < nodes.length; index += 1) {
    const rect = nodes[index].getBoundingClientRect();
    if (rect.bottom > top + 4) {
      return { id: nodes[index].getAttribute("data-msg"), offset: rect.top - top };
    }
  }
  return null;
}

function settleThread(opts, follow, anchor) {
  if (opts.scroll === "top" || opts.scroll === "bottom" || follow) {
    thread.scrollTop = 0;
    return;
  }
  if (!anchor || !anchor.id) return;
  const node = thread.querySelector('[data-msg="' + anchor.id + '"]');
  if (!node) return;
  const delta = node.getBoundingClientRect().top - thread.getBoundingClientRect().top - anchor.offset;
  thread.scrollTop += delta;
}

function paintTranscript(options, full) {
  const opts = options || {};
  const checkedBefore = [...thread.querySelectorAll(".seam-option input")].map((box) => ({
    value: box.value,
    checked: box.checked,
  }));
  const key = current ? transcriptKey(current) : "";
  const sameProject = !!(current && paintTranscript.projectId === current.id);
  if (key && key === paintTranscript.key && sameProject && thread.childElementCount) {
    syncChoiceDisabled();
    if (opts.scroll === "top" || opts.scroll === "bottom") thread.scrollTop = 0;
    syncComposer();
    return;
  }
  const followLatest = thread.scrollTop <= 8;
  const anchor = followLatest ? null : threadAnchor();
  seamKept = new Set(
    sameProject ? checkedBefore.filter((box) => box.checked).map((box) => box.value) : []
  );
  thread.innerHTML = "";
  clearChatPin();
  paintTranscript.key = key;
  paintTranscript.projectId = current ? current.id : "";
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  const strip = materialStrip(current);
  if (strip) wrap.append(strip);
  if (!workflowMode(current)) {
    const traces = traceList(current);
    if (traces) wrap.append(traces);
  }
  const running = autoRun(current);
  const messages = visibleMessages(current);
  function transcriptBubble(message, index) {
    const bubble = messageBubble(message.role, message.content || "");
    bubble.setAttribute("data-msg", String(index));
    if (running && message.role !== "user" && continueCue(message.content)) bubble.classList.add("aside");
    return bubble;
  }
  if (messages.length) wrap.append(transcriptBubble(messages[messages.length - 1], messages.length - 1));
  if (current.status === "gate" && (current.progress || {}).phase === "gate_script" && current.script) {
    const gatePassed = ((current.progress || {}).pipeline || {}).passed !== false;
    const last = visibleMessages(current).slice(-1)[0] || {};
    if (gatePassed && String(last.content || "").indexOf("机检已过") < 0) {
      const note = document.createElement("p");
      note.className = "stage";
      note.textContent = "机检已过，没有打回项。看完这一稿，继续才进入资产。精修先选改法，再写一版，写完仍停在这里。";
      wrap.append(note);
    }
    const scriptNode = section("这一稿", current.script);
    if (scriptNode) wrap.append(scriptNode);
  }
  const pinActions = [];
  const revisionBox = scriptRevisionBox(current);
  if (revisionBox) pinActions.push(revisionBox);
  const choice = latestChoiceMessage(current);
  if (retiredDeliveryNote(current)) {
    const retired = document.createElement("p");
    retired.className = "stage delivery-retired-note";
    retired.textContent = "出片前语义审查已停用，历史问题不再阻断生成；生成仍需技术校验及你的确认";
    pinActions.push(retired);
  }
  if (current.can_reply && choice) pinActions.push(choiceList(choice));
  const showProgress = current.status === "gate" || (current.progress || {}).phase === "lock_wait" || running;
  if (!full) fillChatPin(showProgress ? progressPanel(current) : null, pinActions);
  else {
    pinActions.forEach((node) => wrap.append(node));
    if (running) {
      const note = document.createElement("p");
      note.className = "stage auto-run";
      note.textContent = running.text;
      wrap.append(note);
    }
    if (showProgress) wrap.append(progressPanel(current));
  }
  if (current.status === "drilling" && !current.can_reply) {
    const thinking = document.createElement("p");
    thinking.className = "stage";
    thinking.textContent = current.stage || "正在想方向";
    wrap.append(thinking);
  }
  if ((current.progress || {}).phase === "prompt_debug") {
    const note = document.createElement("p");
    note.className = "debug-note";
    note.textContent = "Debug 模式：提示词已经写好。不生图。";
    wrap.append(note);
  }
  for (let index = messages.length - 2; index >= 0; index -= 1) {
    wrap.append(transcriptBubble(messages[index], index));
  }
  if (!full) {
    thread.append(wrap);
    syncComposer();
    const runKey = running ? running.key + ":" + running.attempt : "";
    paintTranscript.runKey = runKey;
    settleThread(opts, followLatest, anchor);
    return;
  }
  const assistant = document.createElement("div");
  assistant.className = "assistant";
  if (!inDrill(current) && (current.progress || {}).phase !== "lock_wait" && current.status !== "gate" && !running) {
    assistant.append(progressPanel(current));
  }
  tickElapsed();
  if ((current.progress || {}).phase === "intent_debug") {
    const note = document.createElement("p");
    note.className = "debug-note";
    note.textContent = "Debug 模式：意图已经锁定。不写提示词，不生图。";
    assistant.append(note);
    const locked = section("锁定简报", current.brief);
    if (locked) assistant.append(locked);
  }
  if (current.status !== "drilling" && isDebugProject(current)) {
    const note = document.createElement("p");
    note.className = "debug-note";
    const earlyStop = (current.progress || {}).phase === "llm_debug";
    note.textContent = current.status === "done"
      ? (earlyStop
        ? "Debug 模式：这次停在大模型返回，没有生图。"
        : "Debug 模式：已停在生图。下方可检查提示词、分镜、资产和图片。视频与拼接暂不执行。")
      : "Debug 模式：写完提示词后只生图，不出视频、不拼接。";
    assistant.append(note);
  }
  ["提示词", "分镜", "生图提示词"].forEach((name, index) => {
    const key = ["script", "storyboard", "image_prompts"][index];
    const node = name === "生图提示词"
      ? section(name, current[key], current.assets, current.images)
      : section(name, current[key]);
    if (node) assistant.append(node);
  });
  if ((current.shots || []).length) {
    const shotLines = current.shots.map((shot) => {
      const images = (shot.images || []).join(", ");
      return "- **" + shot.id + "** · " + (shot.duration || "?") + "s"
        + (images ? " · images: " + images : "")
        + (shot.prompt ? "\n  " + shot.prompt : "");
    }).join("\n");
    const shotNode = section("镜头", shotLines);
    if (shotNode) assistant.append(shotNode);
  }
  const promptTableNode = assistant.querySelector(".prompt-table");
  if (!promptTableNode) {
    const gallery = imageGallery(current);
    if (gallery) assistant.append(gallery);
  }
  const videos = clipList(current);
  if (videos) assistant.append(videos);
  if (current.has_spec) {
    const spec = document.createElement("a");
    spec.className = "download";
    spec.href = "/media/projects/" + current.id + "/spec";
    spec.download = "spec-" + current.id + ".json";
    spec.textContent = "下载 spec.json";
    assistant.append(spec);
  }
  (current.debug_files || []).forEach((key) => {
    const link = document.createElement("a");
    link.className = "download";
    link.href = "/media/projects/" + current.id + "/debug/" + key;
    link.download = (DEBUG_FILE_LABELS[key] || key) + ".txt";
    link.textContent = "下载 " + (DEBUG_FILE_LABELS[key] || key);
    assistant.append(link);
  });
  if (current.error) {
    const error = document.createElement("div");
    error.className = "error";
    error.textContent = current.error;
    assistant.append(error);
  }
  if (assistant.childNodes.length) wrap.append(assistant);
  thread.append(wrap);
  const count = visibleMessages(current).length;
  if (count !== renderThread.lastCount) otherOpen = false;
  syncComposer();
  settleThread(opts, followLatest, anchor);
  renderThread.lastCount = count;
}

async function refreshList() {
  projects = await rpc("projects.list");
  if (current) {
    const fresh = projects.find((item) => item.id === current.id);
    if (fresh) current = fresh;
  }
  renderHistory();
  renderThread();
}

function scriptRevisionOptions() {
  return {
    project: current,
    rpc: rpc,
    getProjectId: function () { return current ? current.id : ""; },
    applyProject: function (next) {
      if (!next || !current || next.id !== current.id) return;
      adoptQuiet(next);
      watch();
    },
  };
}

function scriptRevisionBox(project) {
  if (typeof ScriptRevisionEdit === "undefined" || !project) return null;
  const entry = ScriptRevisionEdit.shouldShowEntry(project) ? ScriptRevisionEdit.createEntry(scriptRevisionOptions()) : null;
  const outside = ScriptRevisionEdit.createOutside(project);
  if (!entry && !outside) return null;
  if (entry && !outside) return entry;
  const stack = document.createElement("div");
  stack.className = "script-revision-stack";
  if (outside) stack.append(outside);
  if (entry) stack.append(entry);
  return stack;
}

async function probeRevision(project) {
  if (!project || !project.id || typeof ScriptRevisionEdit === "undefined") return;
  const progress = project.progress || {};
  const action = (progress.pipeline && progress.pipeline.action) || "";
  const mark = project.id + "|" + (project.status || "") + "|" + (progress.phase || "") + "|" + action;
  const follow = ScriptRevisionEdit.opinionInFlight && ScriptRevisionEdit.opinionInFlight(project.id);
  if (probeRevision.mark === mark && !follow) return;
  probeRevision.mark = mark;
  try {
    await ScriptRevisionEdit.prepareRevision(scriptRevisionOptions(), project.id);
    if (!current || current.id !== project.id) return;
    renderThread();
  } catch (_error) {
    if (probeRevision.mark === mark) probeRevision.mark = "";
  }
}

async function openProject(id) {
  if (typeof ScriptRevisionEdit !== "undefined") {
    const allowed = await ScriptRevisionEdit.confirmLeave(id);
    if (!allowed) return;
  }
  setMenuOpen(false);
  const switching = !current || current.id !== id;
  try {
    current = await rpc("projects.get", { id: id });
  } catch (_error) {
    return;
  }
  if (switching) {
    produceError = "";
    surfaceLock = "";
    surfaced = "";
    canvasSig = "";
    centerView = "canvas";
    previewShotId = "";
    syncDocNav.sig = "";
    syncShotStrip.sig = "";
    resetRetouch();
  }
  showProjectModel(current);
  showProjectTemplate(current);
  if (switching) probeRevision.mark = "";
  renderHistory();
  renderThread({ scroll: current.status === "drilling" ? "bottom" : "top" });
  probeRevision(current);
  watch();
}

function keepWatching(project) {
  if (!project) return false;
  if (project.progress && project.progress.retouching) return true;
  return project.status !== "done" && project.status !== "error";
}

function watch() {
  clearInterval(timer);
  clearInterval(clock);
  clock = setInterval(tickElapsed, 1000);
  if (!keepWatching(current)) return;
  timer = setInterval(async () => {
    if (!current) return;
    try {
      const project = await rpc("projects.get", { id: current.id });
      if (!current || current.id !== project.id) return;
      current = project;
    } catch (_error) {
      return;
    }
    const listed = projects.find((item) => item.id === current.id);
    if (listed) Object.assign(listed, current);
    renderHistory();
    renderThread();
    probeRevision(current);
    if (!keepWatching(current)) clearInterval(timer);
  }, 2000);
}

newChat.addEventListener("click", async () => {
  if (typeof ScriptRevisionEdit !== "undefined") {
    const allowed = await ScriptRevisionEdit.confirmLeave("");
    if (!allowed) return;
  }
  setMenuOpen(false);
  current = null;
  otherOpen = false;
  resetRetouch();
  clearDraftMaterials();
  briefEl.value = "";
  const savedModel = localStorage.getItem("aivideo.model");
  if (modelEl && savedModel && [...modelEl.options].some((option) => option.value === savedModel)) {
    modelEl.value = savedModel;
    syncModelFoot();
  }
  restoreTemplate();
  clearInterval(timer);
  clearInterval(clock);
  renderHistory();
  renderThread();
  syncComposer();
  briefEl.focus();
});

briefEl.addEventListener("input", () => {
  briefEl.style.height = "auto";
  briefEl.style.height = Math.min(briefEl.scrollHeight, 160) + "px";
});

briefEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function syncModelFoot() {
  if (!modelFoot || !modelEl) return;
  const option = modelEl.selectedOptions && modelEl.selectedOptions[0];
  modelFoot.textContent = (option ? option.textContent : "Qwen3.8 Flash") + " · 本机出片";
}

function fillModels(models) {
  if (!modelEl || !models || !models.length) return;
  const saved = localStorage.getItem("aivideo.model");
  modelEl.innerHTML = "";
  let group = null;
  let groupName = "";
  models.forEach((item) => {
    if (item.group !== groupName) {
      groupName = item.group;
      group = document.createElement("optgroup");
      group.label = item.group;
      modelEl.appendChild(group);
    }
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    group.appendChild(option);
  });
  if (saved && [...modelEl.options].some((option) => option.value === saved)) {
    modelEl.value = saved;
  }
  syncModelFoot();
}

function showProjectModel(project) {
  if (!modelEl || !project || !project.model) return;
  if ([...modelEl.options].some((option) => option.value === project.model)) {
    modelEl.value = project.model;
    syncModelFoot();
  }
}

function fillTemplates(templates) {
  if (!templateEl || !templates || !templates.length) return;
  const saved = localStorage.getItem("aivideo.template") || "short-drama-director";
  const preferred = (current && current.template) || saved;
  const fallback = templates[0].id || "short-drama-director";
  templateEl.innerHTML = "";
  templates.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    templateEl.appendChild(option);
  });
  const pick = [...templateEl.options].some((option) => option.value === preferred) ? preferred : fallback;
  if ([...templateEl.options].some((option) => option.value === pick)) {
    templateEl.value = pick;
  }
  const picker = templateEl.closest(".template-picker");
  if (picker) picker.classList.toggle("hidden", templates.length < 2);
}

function restoreTemplate() {
  if (!templateEl) return;
  const saved = localStorage.getItem("aivideo.template") || "short-drama-director";
  if ([...templateEl.options].some((option) => option.value === saved)) {
    templateEl.value = saved;
  }
}

function showProjectTemplate(project) {
  if (!templateEl || !project) return;
  const name = project.template || "short-drama-director";
  if ([...templateEl.options].some((option) => option.value === name)) {
    templateEl.value = name;
  }
}

function selectedTemplate() {
  return templateEl ? templateEl.value : "short-drama-director";
}

function selectedModel() {
  return modelEl ? modelEl.value : "";
}

function uncheckDebug(which) {
  if (which !== "images" && debugEl && !debugEl.disabled && debugEl.checked) {
    debugEl.checked = false;
    localStorage.setItem("aivideo.debug_llm", "0");
  }
  if (which !== "prompts" && debugPromptEl && debugPromptEl.checked) {
    debugPromptEl.checked = false;
    localStorage.setItem("aivideo.debug_prompt", "0");
  }
  if (which !== "intent" && debugIntentEl && debugIntentEl.checked) {
    debugIntentEl.checked = false;
    localStorage.setItem("aivideo.debug_intent", "0");
  }
}

function syncDebugToggle() {
  if (debugIntentEl) {
    debugIntentEl.checked = localStorage.getItem("aivideo.debug_intent") === "1";
  }
  if (debugPromptEl) {
    debugPromptEl.checked = localStorage.getItem("aivideo.debug_prompt") === "1";
  }
  if (debugEl) {
    const saved = localStorage.getItem("aivideo.debug_llm");
    if (saved === "1" || saved === "0") {
      debugEl.checked = saved === "1";
    } else {
      debugEl.checked = serverDebugDefault;
    }
    if (serverDebugDefault) {
      debugEl.checked = true;
      debugEl.disabled = true;
      debugEl.parentElement.title = "服务器已设置 AIVIDEO_DEBUG_LLM=1，全部项目只生图";
    }
  }
  if (debugIntentEl && debugIntentEl.checked) uncheckDebug("intent");
  else if (debugPromptEl && debugPromptEl.checked) uncheckDebug("prompts");
}

if (debugEl) {
  debugEl.addEventListener("change", () => {
    if (debugEl.disabled) return;
    localStorage.setItem("aivideo.debug_llm", debugEl.checked ? "1" : "0");
    if (debugEl.checked) uncheckDebug("images");
  });
}

if (debugPromptEl) {
  debugPromptEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.debug_prompt", debugPromptEl.checked ? "1" : "0");
    if (debugPromptEl.checked) uncheckDebug("prompts");
  });
}

if (debugIntentEl) {
  debugIntentEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.debug_intent", debugIntentEl.checked ? "1" : "0");
    if (debugIntentEl.checked) uncheckDebug("intent");
  });
}

function savedSoundtrack() {
  const saved = localStorage.getItem("aivideo.soundtrack");
  if (saved === "narration" || saved === "music_narration") return "narration";
  return "off";
}

if (soundtrackEl) {
  soundtrackEl.value = savedSoundtrack();
  soundtrackEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.soundtrack", soundtrackEl.value);
  });
}

if (modelEl) {
  modelEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.model", modelEl.value);
    syncModelFoot();
  });
}

if (templateEl) {
  templateEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.template", templateEl.value);
  });
}

function adoptProject(project, isNew) {
  otherOpen = false;
  briefEl.value = "";
  briefEl.style.height = "auto";
  if (isNew) projects.unshift(project);
  else {
    const listed = projects.find((item) => item.id === project.id);
    if (listed) Object.assign(listed, project);
  }
  current = project;
  showProjectModel(project);
  showProjectTemplate(project);
  renderHistory();
  renderThread({ scroll: "bottom" });
  watch();
}

function clearDraftMaterials() {
  draftMaterials.forEach((item) => {
    if (item.url) URL.revokeObjectURL(item.url);
  });
  draftMaterials = [];
  renderDraftMaterials();
}

function renderDraftMaterials() {
  const list = document.querySelector("#material-list");
  if (!list) return;
  list.replaceChildren();
  draftMaterials.forEach((item) => {
    const card = document.createElement("article");
    card.className = "material-card";
    const img = document.createElement("img");
    img.src = item.url;
    img.alt = item.file.name || "图素材";
    const field = document.createElement("textarea");
    field.rows = 2;
    field.placeholder = "这张图的旁白";
    field.value = item.narration;
    field.addEventListener("input", () => {
      item.narration = field.value;
    });
    field.addEventListener("keydown", (event) => event.stopPropagation());
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(item.url);
      draftMaterials = draftMaterials.filter((other) => other !== item);
      renderDraftMaterials();
    });
    card.append(img, field, remove);
    list.append(card);
  });
}

function materialStrip(project) {
  const items = (project && project.materials) || [];
  if (!items.length) return null;
  const block = document.createElement("div");
  block.className = "material-strip";
  items.forEach((item) => {
    const card = document.createElement("article");
    if (item.url) {
      const img = document.createElement("img");
      img.src = item.url;
      img.alt = item.title || item.id;
      card.append(img);
    }
    const text = document.createElement("p");
    const name = document.createElement("small");
    name.textContent = item.title || item.id;
    text.append(name);
    if (item.narration) {
      text.append(document.createElement("br"));
      text.append(document.createTextNode(item.narration));
    }
    card.append(text);
    block.append(card);
  });
  return block;
}

async function fileToBase64(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("没有读到图片"));
    reader.readAsDataURL(file);
  });
  const comma = dataUrl.indexOf(",");
  return comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
}

async function startBrief(text) {
  const brief = String(text || "").trim();
  if (!brief || sending) return;
  const materials = draftMaterials.slice();
  sending = true;
  syncComposer();
  try {
    const intentStop = !!(debugIntentEl && debugIntentEl.checked);
    const promptStop = !!(debugPromptEl && debugPromptEl.checked);
    const debugStop = intentStop ? "intent" : (promptStop ? "prompts" : "");
    const params = {
      brief: brief,
      debug_llm: debugStop ? false : !!(debugEl && debugEl.checked),
      debug_stop: debugStop,
      soundtrack: soundtrackEl ? soundtrackEl.value : "off",
      model: selectedModel(),
      template: selectedTemplate(),
    };
    if (materials.length) {
      params.materials = [];
      for (const item of materials) {
        params.materials.push({
          filename: (item.file && item.file.name) || "",
          narration: item.narration || "",
          data_base64: await fileToBase64(item.file),
        });
      }
    }
    const project = await rpc("projects.create", params);
    clearDraftMaterials();
    adoptProject(project, true);
  } catch (error) {
    alert(error.message || "没有开始");
  } finally {
    sending = false;
    syncComposer();
  }
}

async function sendText(text) {
  const body = String(text || "").trim();
  if (!body || sending || !current || !current.can_reply) return;
  if (optionRejected(current, body)) return;
  if (chatHome(current)) {
    surface = "chat";
    surfaceLock = "";
  }
  sending = true;
  syncComposer();
  if (chatHome(current)) renderThread();
  try {
    const project = await rpc("projects.send_message", {
      id: current.id,
      text: body,
      model: selectedModel(),
    });
    adoptProject(project, false);
  } catch (error) {
    alert(error.message || "没有发出去");
  } finally {
    sending = false;
    syncChoiceDisabled();
    syncComposer();
  }
}

const materialFile = document.querySelector("#material-file");
if (materialFile) {
  materialFile.addEventListener("change", () => {
    const picked = [...materialFile.files];
    materialFile.value = "";
    const room = MAX_MATERIALS - draftMaterials.length;
    if (!room || picked.length > room) alert("一次最多 8 张图");
    let skipped = false;
    picked.slice(0, Math.max(0, room)).forEach((file) => {
      const named = /\.(png|jpe?g|webp)$/i.test(file.name || "");
      const typed = /^image\/(png|jpeg|webp)$/.test(file.type || "");
      if (!named && !typed) {
        skipped = true;
        return;
      }
      draftMaterials.push({ file: file, url: URL.createObjectURL(file), narration: "" });
    });
    if (skipped) alert("只接受 png、jpeg、webp");
    renderDraftMaterials();
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (briefEl.disabled) return;
  const text = briefEl.value.trim();
  if (!text) return;
  if (current && current.can_reply) sendText(text);
  else startBrief(text);
});

function clampScale(value) {
  return Math.max(0.25, Math.min(2, value));
}

function loadPositions(projectId) {
  try {
    const data = JSON.parse(localStorage.getItem("aivideo.canvas." + projectId) || "{}");
    return data && typeof data === "object" ? data : {};
  } catch (_error) {
    return {};
  }
}

function savePositions(projectId) {
  const data = {};
  canvasNodes.querySelectorAll(".wf-node").forEach((node) => {
    data[node.dataset.id] = { x: Number(node.dataset.x) || 0, y: Number(node.dataset.y) || 0 };
  });
  localStorage.setItem("aivideo.canvas." + projectId, JSON.stringify(data));
}

function applyView() {
  if (!canvasEl) return;
  canvasEl.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.scale + ")";
}

function nodeById(id) {
  const nodes = canvasNodes.querySelectorAll(".wf-node");
  for (let index = 0; index < nodes.length; index += 1) {
    if (nodes[index].dataset.id === id) return nodes[index];
  }
  return null;
}

function placeNode(node, x, y) {
  node.dataset.x = String(x);
  node.dataset.y = String(y);
  node.style.transform = "translate(" + x + "px," + y + "px)";
}

function taskById(project, id) {
  return (project.tasks || []).find((task) => task.id === id) || null;
}

function nodeState(project, kind, id) {
  if (kind === "shot") {
    const mark = retouchDot(project, "shot", id);
    if (mark) return mark;
  }
  const task = taskById(project, id);
  if (task && task.state) return task.state;
  const live = liveNodeKind(project);
  if (kind === "script" || kind === "storyboard") {
    const text = kind === "script" ? project.script : project.storyboard;
    if (live || autoRun(project)) {
      if (live === kind) return "running";
      if (text) return "done";
      if (project.status === "paused" && !text) return "paused";
      return "pending";
    }
    if (project.status === "writing" || project.status === "building") return "running";
    if (project.status === "paused" && !text) return "paused";
    if (text) return "done";
    return "pending";
  }
  if (kind === "film") {
    if (project.has_video) return "done";
    if (project.status === "error") return "error";
    if (project.status === "paused") return "paused";
    if (project.status === "rendering") return "running";
    return "pending";
  }
  if (kind === "image") {
    const tasks = (project.tasks || []).filter((task) => task.kind === "image");
    if (tasks.some((task) => task.state === "error" || task.state === "failed")) return "error";
    if (tasks.some((task) => task.state === "running" || task.state === "retrying")) return "running";
    if (live === "image") return "running";
    if (tasks.length && tasks.every((task) => task.state === "done")) return "done";
    if ((project.assets || []).length && (project.images || []).length >= (project.assets || []).length) return "done";
    if (project.status === "paused") return "paused";
    if (project.status === "building" || project.status === "rendering") return "running";
    return "pending";
  }
  return "pending";
}

function fieldValue(project, key) {
  if (key === "script") return project.script || "";
  if (key === "storyboard") return project.storyboard || "";
  if (key.indexOf("asset:") === 0) {
    const asset = (project.assets || []).find((item) => item.id === key.slice(6));
    return asset ? asset.prompt || "" : "";
  }
  return "";
}

function imageUrlFor(project, id) {
  const found = (project.images || []).find((image) => String(image.name || "").replace(/\.[^.]+$/, "") === id);
  return found ? found.url : "";
}

function clipUrlFor(project, id) {
  const found = (project.clips || []).find((clip) => clip.id === id);
  return found ? found.url : "";
}

function readOnlyText(project, id, kind) {
  if (kind === "shot") {
    const shot = (project.shots || []).find((item) => item.id === id);
    if (!shot) return "";
    const lines = [];
    if (shot.prompt) lines.push(shot.prompt);
    if (shot.narration) lines.push("旁白：" + shot.narration);
    return lines.join("\n");
  }
  if (kind === "join") return "按镜头顺序拼接";
  if (kind === "audio") {
    const note = (project.progress && project.progress.soundtrack_note) || "";
    if (note === "没有旁白") return "没有旁白，保留镜头原声";
    return "按每镜旁白合成";
  }
  if (kind === "subtitles") return "烧录字幕";
  if (kind === "film") return project.has_video ? "成片可以播放" : "成片还没出来";
  return "";
}

function soundtrackMode(project) {
  const value = project && project.progress && project.progress.soundtrack;
  if (value === "narration" || value === "music_narration") return "narration";
  return "off";
}

function withSoundtrack(nodes, project) {
  const list = (nodes || []).filter((node) => node && node.kind !== "audio");
  if (soundtrackMode(project) === "off") return list;
  const audio = { id: "audio", kind: "audio", role: "function", title: "声轨", assets: [] };
  const joinAt = list.findIndex((node) => node.kind === "join");
  if (joinAt >= 0) list.splice(joinAt + 1, 0, audio);
  else list.push(audio);
  return list;
}

function columnMap(hasAudio) {
  const columns = { script: 0, storyboard: 1, image: 2, shot: 3, join: 4, subtitles: 5, film: 6 };
  if (hasAudio) {
    columns.audio = 5;
    columns.subtitles = 6;
    columns.film = 7;
  }
  return columns;
}

function sourceNodes(project) {
  if ((project.nodes || []).some((node) => node && node.kind === "image")) return project.nodes;
  const assets = project.assets || [];
  const byId = {};
  assets.forEach((asset) => {
    byId[asset.id] = asset;
  });
  const nodes = [
    { id: "script", kind: "script", role: "function", title: "提示词", assets: [] },
    { id: "storyboard", kind: "storyboard", role: "function", title: "分镜", assets: [] },
    { id: "image", kind: "image", role: "function", title: "Image-Node", required: true, assets: assets },
  ];
  (project.shots || []).forEach((shot) => {
    const used = (shot.images || []).map((id) => byId[id]).filter(Boolean);
    nodes.push({ id: shot.id, kind: "shot", role: "shot", title: shot.id, assets: used });
  });
  nodes.push(
    { id: "join", kind: "join", role: "function", title: "拼接", assets: [] },
    { id: "subtitles", kind: "subtitles", role: "function", title: "字幕", assets: [] },
    { id: "film", kind: "film", role: "function", title: "成片", assets: [] }
  );
  return nodes;
}

function nodePlan(project) {
  const rows = {};
  const source = withSoundtrack(sourceNodes(project), project);
  const columns = columnMap(source.some((item) => item.kind === "audio"));
  const nodes = source.map((item) => {
    const col = Object.prototype.hasOwnProperty.call(columns, item.kind) ? columns[item.kind] : 3;
    const node = {
      id: item.id,
      kind: item.kind,
      role: item.role || (item.kind === "shot" ? "shot" : "function"),
      title: item.title || item.id,
      required: !!item.required,
      assets: item.assets || [],
      shot: item.kind === "shot" ? (project.shots || []).find((shot) => shot.id === item.id) || null : null,
      col: col,
    };
    rows[col] = rows[col] || 0;
    node.row = rows[col];
    rows[col] += 1;
    return node;
  });
  const ids = {};
  nodes.forEach((node) => {
    ids[node.id] = true;
  });
  const edges = [];
  function link(from, to) {
    if (ids[from] && ids[to]) edges.push({ from: from, to: to });
  }
  link("script", "storyboard");
  link("storyboard", "image");
  const shots = nodes.filter((node) => node.kind === "shot");
  if (!shots.length) link("image", "join");
  shots.forEach((shot) => {
    link("image", shot.id);
    link(shot.id, "join");
  });
  if (ids.audio) {
    link("join", "audio");
    link("audio", "subtitles");
  } else {
    link("join", "subtitles");
  }
  link("subtitles", "film");
  return { nodes: nodes, edges: edges };
}

function layoutNodes(nodes, projectId) {
  const saved = loadPositions(projectId);
  const custom = nodes.some((node) => saved[node.id]);
  nodes.forEach((node) => {
    node.x = 48 + node.col * 320;
    node.y = 48 + node.row * 280;
    if (!custom) return;
    const stored = saved[node.id];
    if (stored && Number.isFinite(Number(stored.x)) && Number.isFinite(Number(stored.y))) {
      node.x = Number(stored.x);
      node.y = Number(stored.y);
    }
  });
  return custom;
}

function spreadColumns() {
  if (!canvasNodes || !canvasWrap) return false;
  const columns = [];
  canvasNodes.querySelectorAll(".wf-node").forEach((el) => {
    const col = Number(el.dataset.col) || 0;
    if (!columns[col]) columns[col] = [];
    columns[col].push(el);
  });
  const measured = [];
  columns.forEach((column) => {
    if (!column) return;
    column.sort((a, b) => {
      const byHeight = (b.offsetHeight || 0) - (a.offsetHeight || 0);
      if (byHeight) return byHeight;
      return (Number(a.dataset.row) || 0) - (Number(b.dataset.row) || 0);
    });
    let width = 0;
    let contentH = 0;
    column.forEach((el) => {
      width = Math.max(width, el.offsetWidth);
      contentH += el.offsetHeight;
    });
    if (!width || !contentH) return;
    measured.push({ column: column, width: width, contentH: contentH, gaps: Math.max(0, column.length - 1) });
  });
  if (!measured.length) return false;
  const minGapX = 56;
  const minGapY = 36;
  const pad = 28;
  const viewW = Math.max(1, canvasWrap.clientWidth - pad * 2);
  const viewH = Math.max(1, canvasWrap.clientHeight - pad * 2);
  const contentW = measured.reduce((sum, col) => sum + col.width, 0);
  const naturalW = contentW + minGapX * Math.max(0, measured.length - 1);
  const naturalH = measured.reduce((max, col) => Math.max(max, col.contentH + minGapY * col.gaps), 0);
  let gapX = minGapX;
  let gapY = minGapY;
  const fit = Math.min(viewW / Math.max(naturalW, 1), viewH / Math.max(naturalH, 1));
  if (fit >= canvasMinScale && naturalH > 0 && naturalW / naturalH < viewW / viewH && measured.length > 1) {
    gapX = Math.max(minGapX, (naturalH * (viewW / viewH) - contentW) / (measured.length - 1));
  }
  const columnHeights = measured.map((col) => col.contentH + gapY * col.gaps);
  const rowH = columnHeights.reduce((max, height) => Math.max(max, height), 0);
  let x = pad;
  measured.forEach((col, index) => {
    let y = pad + (rowH - columnHeights[index]) / 2;
    col.column.forEach((el, row) => {
      el.dataset.row = String(row);
      placeNode(el, x, y);
      y += el.offsetHeight + gapY;
    });
    x += col.width + gapX;
  });
  return true;
}

function planSignature(plan) {
  return plan.nodes.map((node) => {
    const ids = (node.assets || []).map((asset) => asset.id + ":" + (asset.view || "")).join(",");
    return node.kind + ":" + node.id + ":" + ids;
  }).join("|")
    + "#"
    + plan.edges.map((edge) => edge.from + ">" + edge.to).join("|");
}

function assetState(project, id) {
  const mark = retouchDot(project, "asset", id);
  if (mark) return mark;
  const task = taskById(project, id);
  if (task && task.state) return task.state;
  if (imageUrlFor(project, id)) return "done";
  return "pending";
}

function viewLabel(asset) {
  return { front: "正面", side: "侧面", back: "背面" }[asset && asset.view] || "";
}

function makeStill(project, asset) {
  const url = imageUrlFor(project, asset.id);
  if (url) {
    const img = document.createElement("img");
    img.className = "wf-still";
    img.alt = asset.title || asset.id;
    img.dataset.url = url;
    img.src = url;
    return img;
  }
  const hold = document.createElement("span");
  hold.className = "wf-hold";
  hold.textContent = viewLabel(asset) || asset.id;
  return hold;
}

function uploadControl(assetId) {
  const label = document.createElement("label");
  label.className = "wf-upload";
  label.append(document.createTextNode("替换图片"));
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp";
  input.addEventListener("pointerdown", (event) => event.stopPropagation());
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    input.value = "";
    if (file) uploadAsset(assetId, file);
  });
  label.append(input);
  return label;
}

function assetReferenceText(asset) {
  const names = [];
  if (asset && asset.depends_on) names.push(String(asset.depends_on));
  (asset && asset.references ? asset.references : []).forEach((item) => {
    const text = String(item || "");
    if (text && names.indexOf(text) < 0) names.push(text);
  });
  return names.join("、");
}

function renderAsset(project, asset) {
  const card = document.createElement("div");
  card.className = "wf-asset";
  card.dataset.asset = asset.id;
  const mark = document.createElement("span");
  mark.className = "wf-dot " + assetState(project, asset.id);
  card.append(mark);
  card.append(makeStill(project, asset));
  const name = document.createElement("b");
  name.textContent = asset.title || asset.id;
  card.append(name);
  const label = viewLabel(asset);
  if (label) {
    const badge = document.createElement("span");
    badge.className = "wf-view";
    badge.textContent = label;
    card.append(badge);
  }
  const referenceText = assetReferenceText(asset);
  if (referenceText) {
    const ref = document.createElement("small");
    ref.className = "wf-ref";
    ref.textContent = "参考 " + referenceText;
    card.append(ref);
  }
  const advanced = document.createElement("details");
  advanced.className = "wf-advanced";
  const summary = document.createElement("summary");
  summary.textContent = "高级 / 调试";
  advanced.append(summary, makeField(project, "asset:" + asset.id, 3));
  card.append(advanced);
  card.append(uploadControl(asset.id));
  if (retouchSelection && retouchSelection.node === "asset" && retouchSelection.targetId === asset.id) {
    card.classList.add("is-selected");
  }
  return card;
}

function renderStrip(project, assets) {
  const strip = document.createElement("div");
  strip.className = "wf-strip";
  assets.forEach((asset) => {
    const thumb = document.createElement("div");
    thumb.className = "wf-thumb";
    thumb.dataset.asset = asset.id;
    thumb.title = asset.title || asset.id;
    thumb.append(makeStill(project, asset));
    strip.append(thumb);
  });
  return strip;
}

function makeField(project, key, rows) {
  const field = document.createElement("textarea");
  field.dataset.field = key;
  field.rows = rows;
  field.value = fieldValue(project, key);
  field.addEventListener("input", () => pendingSaves.add(key));
  field.addEventListener("blur", () => saveField(field));
  field.addEventListener("pointerdown", (event) => event.stopPropagation());
  return field;
}

function renderNode(project, info) {
  const node = document.createElement("article");
  node.className = "wf-node " + info.kind;
  if (info.kind === "shot" && retouchSelection && retouchSelection.node === "shot" && retouchSelection.targetId === info.id) {
    node.classList.add("is-selected");
  }
  node.dataset.id = info.id;
  node.dataset.kind = info.kind;
  node.dataset.col = String(info.col);
  node.dataset.row = String(info.row);
  placeNode(node, info.x, info.y);
  const head = document.createElement("header");
  head.className = "wf-head";
  head.setAttribute("aria-label", "拖动" + (info.title || info.id));
  const dot = document.createElement("span");
  dot.className = "wf-dot " + nodeState(project, info.kind, info.id);
  const title = document.createElement("b");
  title.textContent = info.title || info.id;
  const meta = document.createElement("small");
  const liveText = liveCopy(project, info.kind);
  if (liveText) {
    node.classList.add("is-live");
    meta.textContent = liveText;
  } else if (info.kind === "image") meta.textContent = (info.assets || []).length + " 张 · 必须";
  else if (info.kind === "shot" && info.shot) meta.textContent = (info.shot.duration || "?") + " 秒";
  else if (info.kind === "shot") meta.textContent = (info.assets || []).length + " 张";
  head.append(dot, title, meta);
  node.append(head);
  if (info.kind === "script") node.append(makeField(project, "script", 8));
  else if (info.kind === "storyboard") node.append(makeField(project, "storyboard", 8));
  else if (info.kind === "image") {
    const grid = document.createElement("div");
    grid.className = "wf-assets";
    if ((info.assets || []).length) info.assets.forEach((asset) => grid.append(renderAsset(project, asset)));
    else {
      const empty = document.createElement("p");
      empty.className = "wf-read";
      empty.textContent = "片中用到的图片都在这里生成";
      grid.append(empty);
    }
    node.append(grid);
  } else {
    if ((info.assets || []).length) node.append(renderStrip(project, info.assets));
    const body = document.createElement("p");
    body.className = "wf-read";
    body.textContent = readOnlyText(project, info.id, info.kind);
    node.append(body);
    const url = info.kind === "film" ? (project.video_url || "") : info.kind === "shot" ? clipUrlFor(project, info.id) : "";
    if (url) node.append(makeVideo(url));
  }
  return node;
}

function makeVideo(url) {
  const video = document.createElement("video");
  video.controls = true;
  video.preload = "metadata";
  video.dataset.url = url;
  video.src = url;
  video.addEventListener("pointerdown", (event) => event.stopPropagation());
  return video;
}

function drawEdges(edges) {
  if (!canvasSvg) return;
  const list = edges || canvasEdges;
  canvasSvg.replaceChildren();
  let maxX = 40;
  let maxY = 40;
  list.forEach((edge) => {
    const from = nodeById(edge.from);
    const to = nodeById(edge.to);
    if (!from || !to) return;
    const x1 = (Number(from.dataset.x) || 0) + (from.offsetWidth || 240);
    const y1 = (Number(from.dataset.y) || 0) + (from.offsetHeight || 80) / 2;
    const x2 = Number(to.dataset.x) || 0;
    const y2 = (Number(to.dataset.y) || 0) + (to.offsetHeight || 80) / 2;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const mid = (x1 + x2) / 2;
    path.setAttribute("d", "M " + x1 + " " + y1 + " C " + mid + " " + y1 + ", " + mid + " " + y2 + ", " + x2 + " " + y2);
    canvasSvg.append(path);
    maxX = Math.max(maxX, x1, x2);
    maxY = Math.max(maxY, y1, y2);
  });
  canvasSvg.setAttribute("width", String(maxX + 80));
  canvasSvg.setAttribute("height", String(maxY + 80));
}

function fitCanvas(minScale) {
  if (!canvasWrap || canvasWrap.classList.contains("hidden")) return;
  const nodes = [...canvasNodes.querySelectorAll(".wf-node")];
  if (!nodes.length || !canvasWrap.clientWidth || !canvasWrap.clientHeight) return;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  nodes.forEach((node) => {
    const x = Number(node.dataset.x) || 0;
    const y = Number(node.dataset.y) || 0;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + (node.offsetWidth || 280));
    maxY = Math.max(maxY, y + (node.offsetHeight || 160));
  });
  const pad = 36;
  const boundsW = Math.max(1, maxX - minX);
  const boundsH = Math.max(1, maxY - minY);
  const fit = Math.min((canvasWrap.clientWidth - pad * 2) / boundsW, (canvasWrap.clientHeight - pad * 2) / boundsH);
  const floor = minScale == null ? canvasMinScale : minScale;
  if (fit < floor) {
    view.scale = floor;
    view.x = pad - minX * floor;
    view.y = pad - minY * floor;
    applyView();
    return;
  }
  const scale = Math.min(2, fit);
  view.scale = scale;
  view.x = (canvasWrap.clientWidth - boundsW * scale) / 2 - minX * scale;
  view.y = (canvasWrap.clientHeight - boundsH * scale) / 2 - minY * scale;
  applyView();
}

function zoomAt(next, clientX, clientY) {
  if (!canvasWrap) return;
  const scale = clampScale(next);
  const rect = canvasWrap.getBoundingClientRect();
  const px = clientX == null ? rect.width / 2 : clientX - rect.left;
  const py = clientY == null ? rect.height / 2 : clientY - rect.top;
  const worldX = (px - view.x) / view.scale;
  const worldY = (py - view.y) / view.scale;
  view.scale = scale;
  view.x = px - worldX * scale;
  view.y = py - worldY * scale;
  applyView();
}

let fittedSig = "";
let resizeFrame = 0;

function syncCanvas(project) {
  if (!canvasNodes) return;
  const plan = nodePlan(project);
  const sig = project.id + "::" + planSignature(plan);
  if (sig !== canvasSig) {
    const active = document.activeElement;
    const activeKey = active && active.dataset ? active.dataset.field : "";
    const activeValue = activeKey ? active.value : "";
    const caret = activeKey ? active.selectionStart : null;
    canvasSig = sig;
    fittedSig = "";
    const customLayout = layoutNodes(plan.nodes, project.id);
    canvasNodes.dataset.custom = customLayout ? "1" : "";
    canvasNodes.replaceChildren();
    plan.nodes.forEach((info) => canvasNodes.append(renderNode(project, info)));
    canvasEdges = plan.edges;
    if (activeKey) {
      const fields = canvasNodes.querySelectorAll("[data-field]");
      for (let index = 0; index < fields.length; index += 1) {
        if (fields[index].dataset.field === activeKey) {
          fields[index].value = activeValue;
          fields[index].focus();
          if (caret != null) fields[index].setSelectionRange(caret, caret);
          break;
        }
      }
    }
    applyView();
  } else {
    patchCanvas(project);
  }
  if (fittedSig !== canvasSig) {
    requestAnimationFrame(() => {
      if (!canvasWrap || canvasWrap.classList.contains("hidden") || !canvasWrap.clientWidth) return;
      if (!canvasNodes.dataset.custom) {
        if (!spreadColumns()) return;
      }
      drawEdges(canvasEdges);
      fitCanvas();
      fittedSig = canvasSig;
    });
  } else {
    drawEdges(canvasEdges);
  }
}

function patchCanvas(project) {
  canvasNodes.querySelectorAll(".wf-node").forEach((node) => {
    const id = node.dataset.id;
    const kind = node.dataset.kind;
    const dot = node.querySelector(".wf-dot");
    if (dot) dot.className = "wf-dot " + nodeState(project, kind, id);
    const liveText = liveCopy(project, kind);
    node.classList.toggle("is-live", !!liveText);
    const meta = node.querySelector(".wf-head small");
    if (meta) {
      const next = liveText || restingMeta(project, node);
      if (meta.textContent !== next) meta.textContent = next;
    }
    node.querySelectorAll("[data-field]").forEach((field) => {
      const key = field.dataset.field;
      if (document.activeElement === field || pendingSaves.has(key)) return;
      const value = fieldValue(project, key);
      if (field.value !== value) field.value = value;
    });
    const read = node.querySelector(".wf-read");
    if (read) {
      const text = readOnlyText(project, id, kind);
      if (read.textContent !== text) read.textContent = text;
    }
    node.querySelectorAll("[data-asset]").forEach((holder) => {
      syncStill(holder, project);
      const mark = holder.querySelector(".wf-dot");
      if (mark) mark.className = "wf-dot " + assetState(project, holder.dataset.asset);
    });
    if (kind === "shot" || kind === "film") {
      const url = kind === "film" ? (project.video_url || "") : clipUrlFor(project, id);
      let video = node.querySelector("video");
      if (url) {
        if (!video) video = node.appendChild(makeVideo(url));
        else if (video.dataset.url !== url) {
          video.dataset.url = url;
          video.src = url;
        }
      }
    }
  });
  syncRetouchChrome();
}

function setSaveState(text) {
  const node = document.querySelector("#save-state");
  if (node) node.textContent = text || "";
}

function adoptQuiet(project) {
  current = project;
  const listed = projects.find((item) => item.id === project.id);
  if (listed) Object.assign(listed, project);
  renderHistory();
  renderThread();
}

async function saveField(field) {
  if (!current || !field) return;
  const key = field.dataset.field;
  const value = field.value;
  if (value === fieldValue(current, key)) {
    pendingSaves.delete(key);
    return;
  }
  pendingSaves.add(key);
  setSaveState("保存中");
  const body = {};
  if (key === "script") body.script = value;
  else if (key === "storyboard") body.storyboard = value;
  else if (key.indexOf("asset:") === 0) body.assets = [{ id: key.slice(6), prompt: value }];
  else return;
  try {
    const project = await rpc("projects.save_content", Object.assign({ id: current.id }, body));
    if (field.value === value) pendingSaves.delete(key);
    setSaveState("已保存");
    adoptQuiet(project);
  } catch (error) {
    setSaveState(error.message || "没有保存");
  }
}

async function uploadAsset(assetId, file) {
  if (!current || !file) return;
  setSaveState("上传中");
  try {
    const project = await rpc("projects.set_asset_image", {
      id: current.id,
      asset_id: assetId,
      data_base64: await fileToBase64(file),
    });
    setSaveState("已换上图片");
    adoptQuiet(project);
  } catch (error) {
    setSaveState(error.message || "没有换上图片");
  }
}

function syncControlButtons(project) {
  if (!project) return;
  const canPause = ["writing", "building", "rendering"].includes(project.status);
  const canResume = project.status === "paused" || project.status === "gate";
  const canReset = project.status === "paused" || project.status === "error";
  document.querySelectorAll(".js-pause").forEach((button) => {
    button.disabled = !canPause;
  });
  document.querySelectorAll(".js-resume").forEach((button) => {
    button.disabled = !canResume;
  });
  document.querySelectorAll(".js-reset").forEach((button) => {
    button.disabled = !canReset;
  });
  const showProduce = explicitRenderEntry(project);
  document.querySelectorAll(".js-produce").forEach((button) => {
    button.classList.toggle("hidden", !showProduce);
    button.disabled = !showProduce || sending;
  });
}

function syncDock(project) {
  syncCrew(project);
  if (!debugDock) return;
  const phase = document.querySelector("#dock-phase");
  const percentNode = document.querySelector("#dock-percent");
  const message = document.querySelector("#dock-message");
  if (phase) phase.textContent = statusText(project);
  const percent = project.status === "done" ? 100 : Math.max(0, Math.min(100, Number((project.progress || {}).percent) || 0));
  if (percentNode) percentNode.textContent = percent + "%";
  if (message) message.textContent = (project.progress && project.progress.message) || project.stage || "";
  tickElapsed();
  const tasks = project.tasks || [];
  const taskSig = tasks.map((task) => [task.id, task.state, task.detail, task.title].join("\u0001")).join("\n");
  const taskList = document.querySelector("#dock-tasks");
  if (taskList && taskList.dataset.sig !== taskSig) {
    taskList.dataset.sig = taskSig;
    taskList.replaceChildren();
    tasks.forEach((task) => {
      const row = document.createElement("li");
      row.className = "task task-" + (task.state || "pending");
      const name = document.createElement("b");
      name.textContent = task.id + (task.title && task.title !== task.id ? " " + task.title : "");
      const state = document.createElement("span");
      state.className = "task-state";
      state.textContent = " " + taskStateLabel(task.state);
      const detail = document.createElement("div");
      detail.className = "task-detail";
      detail.textContent = task.detail || "";
      row.append(name, state, detail);
      taskList.append(row);
    });
  }
  const events = project.events || [];
  const eventSig = events.map((event) => (event.at || "") + (event.message || "")).join("\n");
  const eventList = document.querySelector("#dock-events");
  if (eventList && eventList.dataset.sig !== eventSig) {
    eventList.dataset.sig = eventSig;
    eventList.replaceChildren();
    events.slice(-40).forEach((event) => {
      const row = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = clockTime(event.at);
      row.append(time, document.createTextNode(event.message || ""));
      eventList.append(row);
    });
  }
  const errorNode = document.querySelector("#dock-error");
  if (errorNode && errorNode.textContent !== (project.error || "")) errorNode.textContent = project.error || "";
  syncDockFiles(project);
  const traceBox = document.querySelector("#dock-traces");
  if (traceBox) {
    const rows = project.traces || [];
    const sig = [
      project.debug_trace ? "1" : "0",
      project.status,
      sending ? "1" : "0",
      rows.map((row) => [row.id, row.label, row.error, row.pending].join(":")).join("|"),
    ].join("\n");
    if (traceBox.dataset.sig !== sig) {
      traceBox.dataset.sig = sig;
      traceBox.replaceChildren();
      const node = traceList(project);
      if (node) traceBox.append(node);
    }
  }
  syncControlButtons(project);
}

function syncDockFiles(project) {
  const box = document.querySelector("#dock-files");
  if (!box) return;
  const keys = (project.has_spec ? ["spec"] : []).concat(project.debug_files || []);
  const sig = project.id + ":" + keys.join(",");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.replaceChildren();
  if (project.has_spec) {
    const spec = document.createElement("a");
    spec.className = "download";
    spec.href = "/media/projects/" + project.id + "/spec";
    spec.textContent = "下载 spec.json";
    box.append(spec);
  }
  (project.debug_files || []).forEach((key) => {
    const link = document.createElement("a");
    link.className = "download";
    link.href = "/media/projects/" + project.id + "/debug/" + key;
    link.textContent = "下载 " + (DEBUG_FILE_LABELS[key] || key);
    box.append(link);
  });
}

async function refreshLogs() {
  if (!current || !workflowMode(current)) return;
  const projectId = current.id;
  try {
    const logs = await rpc("projects.logs", { id: projectId });
    if (!current || current.id !== projectId) return;
    const errorLog = document.querySelector("#dock-error-log");
    const renderLog = document.querySelector("#dock-render-log");
    const errorNode = document.querySelector("#dock-error");
    if (errorLog && errorLog.textContent !== (logs.error_log || "")) errorLog.textContent = logs.error_log || "";
    if (renderLog && renderLog.textContent !== (logs.render_log || "")) renderLog.textContent = logs.render_log || "";
    if (errorNode && logs.error && errorNode.textContent !== logs.error) errorNode.textContent = logs.error;
  } catch (_error) {
    return;
  }
}

function onCanvasPointerDown(event) {
  if (!canvasWrap || event.button !== 0) return;
  pointer.moved = false;
  const head = event.target.closest(".wf-head");
  if (head && canvasWrap.contains(head)) {
    const node = head.closest(".wf-node");
    pointer.mode = "node";
    pointer.id = node.dataset.id;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.nx = Number(node.dataset.x) || 0;
    pointer.ny = Number(node.dataset.y) || 0;
    node.classList.add("dragging");
    return;
  }
  if (event.target.closest(".wf-node")) return;
  pointer.mode = "pan";
  pointer.x = event.clientX;
  pointer.y = event.clientY;
  pointer.vx = view.x;
  pointer.vy = view.y;
  canvasWrap.classList.add("panning");
}

function onPointerMove(event) {
  if (pointer.mode) {
    const dx = event.clientX - pointer.x;
    const dy = event.clientY - pointer.y;
    if (dx * dx + dy * dy > 16) pointer.moved = true;
  }
  if (pointer.mode === "pan") {
    view.x = pointer.vx + (event.clientX - pointer.x);
    view.y = pointer.vy + (event.clientY - pointer.y);
    applyView();
  } else if (pointer.mode === "node") {
    const node = nodeById(pointer.id);
    if (!node) return;
    const scale = view.scale || 1;
    placeNode(node, pointer.nx + (event.clientX - pointer.x) / scale, pointer.ny + (event.clientY - pointer.y) / scale);
    drawEdges(canvasEdges);
  }
}

function onPointerUp() {
  if (pointer.mode === "node" && current) savePositions(current.id);
  pointer.mode = "";
  if (canvasWrap) canvasWrap.classList.remove("panning");
  canvasNodes.querySelectorAll(".wf-node.dragging").forEach((node) => node.classList.remove("dragging"));
}

function syncStill(holder, project) {
  const url = imageUrlFor(project, holder.dataset.asset);
  if (!url) return;
  let img = holder.querySelector("img.wf-still");
  if (!img) {
    img = document.createElement("img");
    img.className = "wf-still";
    img.alt = holder.dataset.asset || "";
    const hold = holder.querySelector(".wf-hold");
    if (hold) hold.replaceWith(img);
    else holder.insertBefore(img, holder.firstChild);
  }
  if (img.dataset.url !== url) {
    img.dataset.url = url;
    img.src = url;
  }
}

function onWheel(event) {
  if (!canvasWrap || canvasWrap.classList.contains("hidden")) return;
  const scroller = event.target.closest(".wf-assets, textarea, .wf-read");
  if (scroller && scroller.scrollHeight > scroller.clientHeight + 1) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.08 : 1 / 1.08;
  zoomAt(view.scale * factor, event.clientX, event.clientY);
}

function docHeading(text) {
  const heading = document.createElement("h4");
  heading.textContent = text;
  return heading;
}

function docButton(label, onClick, key) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "doc-link";
  button.textContent = label;
  if (key) button.dataset.doc = key;
  button.addEventListener("click", () => {
    onClick();
    markDocLink(button);
  });
  return button;
}

function markDocLink(node) {
  if (!docNav || !node) return;
  docNav.querySelectorAll(".doc-link, .doc-material").forEach((el) => {
    el.classList.toggle("on", el === node);
  });
}

function docFiles(project) {
  const box = document.createElement("div");
  if (project.board_url) {
    const link = document.createElement("a");
    link.className = "download";
    link.href = project.board_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "打开看板";
    box.append(link);
  }
  if (project.has_spec) {
    const spec = document.createElement("a");
    spec.className = "download";
    spec.href = "/media/projects/" + project.id + "/spec";
    spec.textContent = "下载 spec.json";
    box.append(spec);
  }
  (project.debug_files || []).forEach((key) => {
    const link = document.createElement("a");
    link.className = "download";
    link.href = "/media/projects/" + project.id + "/debug/" + key;
    link.textContent = DEBUG_FILE_LABELS[key] || key;
    box.append(link);
  });
  return box.childElementCount ? box : null;
}

function docSignature(project) {
  const materials = (project.materials || []).map((item) => [item.id, item.title, item.url, item.narration].join(":")).join("|");
  const assets = (project.assets || []).map((item) => item.id).join(",");
  return [
    project.id,
    project.brief || "",
    materials,
    assets,
    project.board_url || "",
    project.has_spec ? "1" : "",
    (project.debug_files || []).join(","),
  ].join("\u0001");
}

function syncDocNav(project) {
  if (!docNav || !project) return;
  const sig = docSignature(project);
  if (sig === syncDocNav.sig && docNav.childElementCount) return;
  syncDocNav.sig = sig;
  docNav.replaceChildren();
  const materials = project.materials || [];
  if (materials.length) {
    docNav.append(docHeading("素材"));
    materials.forEach((item) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "doc-material";
      if (item.url) {
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.title || item.id || "";
        row.append(img);
      } else {
        const hold = document.createElement("span");
        hold.className = "doc-hold";
        hold.textContent = item.id || "";
        row.append(hold);
      }
      const text = document.createElement("span");
      const name = document.createElement("b");
      name.textContent = item.title || item.id || "素材";
      text.append(name);
      if (item.narration) {
        text.append(document.createElement("br"));
        const note = document.createElement("small");
        note.textContent = item.narration;
        text.append(note);
      }
      row.append(text);
      row.addEventListener("click", () => {
        const asset = (project.assets || []).find((entry) => entry.id === item.id);
        focusCanvasField("image", asset ? "asset:" + asset.id : "");
        markDocLink(row);
      });
      docNav.append(row);
    });
  }
  docNav.append(docHeading("文稿"));
  if (project.brief) {
    docNav.append(docButton("简报", () => {
      const note = docNav.querySelector(".doc-brief");
      if (note) note.hidden = false;
    }));
    const note = document.createElement("p");
    note.className = "doc-brief";
    note.textContent = project.brief;
    docNav.append(note);
  }
  docNav.append(docButton("提示词", () => focusCanvasField("script", "script"), "script"));
  docNav.append(docButton("分镜", () => focusCanvasField("storyboard", "storyboard"), "storyboard"));
  const files = docFiles(project);
  if (files) {
    docNav.append(docHeading("文件"));
    docNav.append(files);
  }
}

function focusCanvasField(nodeId, fieldKey) {
  setMenuOpen(false);
  centerView = "canvas";
  syncCenter();
  requestAnimationFrame(() => {
    const node = nodeById(nodeId);
    if (!node) return;
    document.querySelectorAll(".wf-node.nav-focus").forEach((el) => el.classList.remove("nav-focus"));
    node.classList.add("nav-focus");
    const x = Number(node.dataset.x) || 0;
    const y = Number(node.dataset.y) || 0;
    const width = node.offsetWidth || 280;
    const height = node.offsetHeight || 160;
    if (canvasWrap && canvasWrap.clientWidth) {
      const scale = view.scale || 1;
      view.x = (canvasWrap.clientWidth - width * scale) / 2 - x * scale;
      view.y = (canvasWrap.clientHeight - height * scale) / 2 - y * scale;
      applyView();
    }
    if (!fieldKey) return;
    let field = null;
    node.querySelectorAll("[data-field]").forEach((el) => {
      if (el.dataset.field === fieldKey) field = el;
    });
    if (!field) return;
    const advanced = field.closest("details");
    if (advanced) advanced.open = true;
    field.focus({ preventScroll: true });
    const scroller = field.closest(".wf-assets");
    if (!scroller) return;
    const fieldBox = field.getBoundingClientRect();
    const box = scroller.getBoundingClientRect();
    if (fieldBox.top < box.top || fieldBox.bottom > box.bottom) {
      scroller.scrollTop += fieldBox.top - box.top - 8;
    }
  });
}

function shotThumbUrl(project, shot) {
  const ids = (shot && shot.images) || [];
  for (let index = 0; index < ids.length; index += 1) {
    const url = imageUrlFor(project, ids[index]);
    if (url) return url;
  }
  return shot ? imageUrlFor(project, shot.id) : "";
}

function previewUrl(project) {
  if (!project) return "";
  if (previewShotId) return clipUrlFor(project, previewShotId) || "";
  if (project.video_url) return project.video_url;
  const shots = project.shots || [];
  for (let index = 0; index < shots.length; index += 1) {
    const url = clipUrlFor(project, shots[index].id);
    if (url) return url;
  }
  return "";
}

function syncPreview(project) {
  if (!previewStage || !project) return;
  const url = previewUrl(project);
  if (previewEmpty) {
    previewEmpty.textContent = previewShotId ? "这一镜还没有片子" : "还没有可以预览的片子";
    previewEmpty.classList.toggle("hidden", !!url);
  }
  if (!url) {
    previewStage.replaceChildren();
    return;
  }
  const existing = previewStage.querySelector("video");
  if (existing && existing.dataset.url === url) return;
  previewStage.replaceChildren(makeVideo(url));
}

function shotStripSignature(project) {
  const shots = (project.shots || []).map((shot) => [
    shot.id,
    shot.duration,
    (shot.images || []).join(","),
    shotThumbUrl(project, shot),
    clipUrlFor(project, shot.id) ? "1" : "",
  ].join(":")).join("|");
  return [project.id, project.video_url || "", shots, previewShotId, centerView].join("\u0001");
}

function shotChip(shot) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "shot-chip";
  const film = !!shot.film;
  const selected = film ? !previewShotId : previewShotId === shot.id;
  button.classList.toggle("on", selected && centerView === "preview");
  const url = film || !current ? "" : shotThumbUrl(current, shot);
  if (url) {
    const img = document.createElement("img");
    img.src = url;
    img.alt = shot.id || "";
    img.draggable = false;
    button.append(img);
  } else {
    const hold = document.createElement("span");
    hold.className = "shot-hold";
    button.append(hold);
  }
  const name = document.createElement("b");
  name.textContent = film ? "成片" : (shot.id || "镜头");
  button.append(name);
  if (!film && shot.duration != null && shot.duration !== "") {
    const time = document.createElement("small");
    time.textContent = shot.duration + " 秒";
    button.append(time);
  }
  button.addEventListener("click", () => {
    previewShotId = film ? "" : shot.id;
    centerView = "preview";
    syncCenter();
    if (!current) return;
    syncShotStrip(current);
    syncPreview(current);
  });
  return button;
}

function syncShotStrip(project) {
  if (!shotStrip || !project) return;
  const sig = shotStripSignature(project);
  if (sig === syncShotStrip.sig && shotStrip.childElementCount) return;
  syncShotStrip.sig = sig;
  shotStrip.replaceChildren();
  if (project.video_url) shotStrip.append(shotChip({ id: "", film: true }));
  const shots = project.shots || [];
  if (!shots.length && !project.video_url) {
    const empty = document.createElement("p");
    empty.className = "shot-empty";
    empty.textContent = "还没有镜头";
    shotStrip.append(empty);
    return;
  }
  shots.forEach((shot) => shotStrip.append(shotChip(shot)));
}

function hasProducedOutput(project) {
  if (!project) return false;
  if (project.has_video || project.video_url) return true;
  if ((project.images || []).some((image) => image && image.url)) return true;
  if ((project.clips || []).some((clip) => clip && clip.url)) return true;
  return (project.tasks || []).some((task) => {
    const kind = task && task.kind;
    return (kind === "image" || kind === "video" || kind === "join") && task.state === "done";
  });
}

function projectBusy(project) {
  return !!(project && ["writing", "building", "rendering", "drilling"].includes(project.status));
}

function retouchLocked(project) {
  const progress = (project && project.progress) || {};
  return !!(retouchPending || progress.retouching || projectBusy(project));
}

function retouchDot(project, node, id) {
  const progress = (project && project.progress) || {};
  const active = progress.retouching;
  if (active && active.node === node && String(active.target_id || "") === String(id)) return "running";
  const error = progress.retouch_error;
  const key = error && typeof error === "object" ? String(error.key || "") : "";
  if (key === node + ":" + id) return "error";
  return "";
}

function resetRetouch() {
  retouchSelection = null;
  retouchLocalError = "";
  retouchPending = false;
  keptPromptDiff = null;
  retouchDiffKey = "";
  retouchDiffSelection = "";
  seenRetouchError = "";
}

function retouchDraftKey(project, node, targetId) {
  return (project && project.id ? project.id : "") + "\u0001" + node + "\u0001" + targetId;
}

function selectionAlive(project, selection) {
  if (!project || !selection) return false;
  if (selection.node === "asset") {
    return (project.assets || []).some((item) => item && item.id === selection.targetId);
  }
  if (selection.node === "shot") {
    return (project.shots || []).some((item) => item && item.id === selection.targetId);
  }
  return false;
}

function rememberPromptDiff(project) {
  if (!project) return;
  const diff = (project.progress || {}).prompt_diff;
  if (!diff || typeof diff !== "object") return;
  if (diff.before == null && diff.after == null) return;
  keptPromptDiff = { projectId: project.id, diff: diff };
}

function promptDiffFor(project) {
  if (!project) return null;
  rememberPromptDiff(project);
  const live = (project.progress || {}).prompt_diff;
  if (live && typeof live === "object" && (live.before != null || live.after != null)) return live;
  if (keptPromptDiff && keptPromptDiff.projectId === project.id) return keptPromptDiff.diff;
  return null;
}

function retouchTargetLabel(project, selection) {
  if (selection.node === "asset") {
    const asset = (project.assets || []).find((item) => item.id === selection.targetId);
    return "静帧 · " + ((asset && (asset.title || asset.id)) || selection.targetId);
  }
  const shot = (project.shots || []).find((item) => item.id === selection.targetId);
  const duration = shot && shot.duration != null && shot.duration !== "" ? " · " + shot.duration + " 秒" : "";
  return "镜头 · " + selection.targetId + duration;
}

function currentBlockPrompt(project, selection) {
  if (!project || !selection) return "";
  if (selection.node === "asset") {
    const asset = (project.assets || []).find((item) => item.id === selection.targetId);
    return asset ? asset.prompt || "" : "";
  }
  const shot = (project.shots || []).find((item) => item.id === selection.targetId);
  return shot ? shot.prompt || "" : "";
}

function retouchStatusText(project, selection) {
  const active = project && project.progress && project.progress.retouching;
  if (!active) return "";
  const phase = active.phase === "prompt" ? "正在改这一块提示词" : "正在按预览档重跑";
  const same = selection && active.node === selection.node && String(active.target_id || "") === selection.targetId;
  const who = same ? "" : " · " + (active.node === "shot" ? "镜头 " : "静帧 ") + (active.target_id || "");
  return "正在按你的感觉重修 · " + phase + who;
}

function retouchErrorText(project, selection) {
  if (retouchLocalError) return retouchLocalError;
  const error = project && project.progress && project.progress.retouch_error;
  if (!error) return "";
  const message = typeof error === "string" ? error : (error.message || "重修没有完成");
  const key = typeof error === "object" && error ? String(error.key || "") : "";
  if (!key || !selection) return message;
  const expected = selection.node + ":" + selection.targetId;
  if (key === expected) return message;
  return key + "：" + message;
}

function retouchErrorIdentity(project) {
  const error = project && project.progress && project.progress.retouch_error;
  if (!error) return "";
  if (typeof error === "string") return error;
  return [error.key || "", error.message || "", error.at || ""].join("\u0001");
}

function rpcMissing(error) {
  if (!error) return false;
  if (error.code === -32601) return true;
  const message = String(error.message || "");
  return message.indexOf("没有这个接口") >= 0 || message.indexOf("Method not found") >= 0;
}

function diffRows(before, after) {
  const leftLines = String(before == null ? "" : before).split("\n");
  const rightLines = String(after == null ? "" : after).split("\n");
  if (leftLines.length * rightLines.length > 160000) {
    return {
      left: leftLines.map((text) => ({ text: text, kind: "same" })),
      right: rightLines.map((text) => ({ text: text, kind: "same" })),
    };
  }
  const n = leftLines.length;
  const m = rightLines.length;
  const scores = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      scores[i][j] = leftLines[i] === rightLines[j]
        ? scores[i + 1][j + 1] + 1
        : Math.max(scores[i + 1][j], scores[i][j + 1]);
    }
  }
  const left = [];
  const right = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (leftLines[i] === rightLines[j]) {
      left.push({ text: leftLines[i], kind: "same" });
      right.push({ text: rightLines[j], kind: "same" });
      i += 1;
      j += 1;
    } else if (scores[i + 1][j] >= scores[i][j + 1]) {
      left.push({ text: leftLines[i], kind: "removed" });
      i += 1;
    } else {
      right.push({ text: rightLines[j], kind: "added" });
      j += 1;
    }
  }
  while (i < n) {
    left.push({ text: leftLines[i], kind: "removed" });
    i += 1;
  }
  while (j < m) {
    right.push({ text: rightLines[j], kind: "added" });
    j += 1;
  }
  return { left: left, right: right };
}

function fillDiffColumn(node, rows) {
  if (!node) return;
  node.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "retouch-diff-empty";
    empty.textContent = "（空）";
    node.append(empty);
    return;
  }
  rows.forEach((row) => {
    const line = document.createElement("div");
    line.className = "retouch-line " + row.kind;
    line.textContent = row.text.length ? row.text : " ";
    node.append(line);
  });
}

function diffMetaText(diff, selection) {
  if (!diff) return "还没有提示词差量。重修成功后留在这里。";
  const kind = diff.node === "shot" ? "镜头" : "静帧";
  const parts = [kind + " " + (diff.target_id || "")];
  if (diff.suggestion) parts.push("感觉：" + diff.suggestion);
  if (selection && (diff.node !== selection.node || String(diff.target_id || "") !== selection.targetId)) {
    parts.push("这是上一次成功的差量");
  }
  return parts.filter(Boolean).join(" · ");
}

function paintPromptDiff(diff, selection) {
  const meta = document.querySelector("#retouch-diff-meta");
  const before = document.querySelector("#retouch-before");
  const after = document.querySelector("#retouch-after");
  const cols = document.querySelector(".retouch-diff-cols");
  if (meta) meta.textContent = diffMetaText(diff, selection);
  if (cols) cols.classList.toggle("hidden", !diff);
  if (!diff) {
    if (before) before.replaceChildren();
    if (after) after.replaceChildren();
    return;
  }
  const rows = diffRows(diff.before, diff.after);
  fillDiffColumn(before, rows.left);
  fillDiffColumn(after, rows.right);
}

function syncRetouchChrome() {
  if (!canvasNodes) return;
  canvasNodes.querySelectorAll(".wf-asset.is-selected, .wf-node.is-selected").forEach((el) => {
    el.classList.remove("is-selected");
  });
  if (!retouchSelection) return;
  if (retouchSelection.node === "asset") {
    canvasNodes.querySelectorAll(".wf-asset").forEach((card) => {
      card.classList.toggle("is-selected", card.dataset.asset === retouchSelection.targetId);
    });
    return;
  }
  const node = nodeById(retouchSelection.targetId);
  if (node && node.dataset.kind === "shot") node.classList.add("is-selected");
}

function syncRetouchPanel(project) {
  const panel = document.querySelector("#retouch-panel");
  if (!panel) return;
  const selection = retouchSelection;
  if (project && selection && !selectionAlive(project, selection)) {
    retouchSelection = null;
  }
  const active = retouchSelection;
  const show = !!(
    project
    && active
    && workflowMode(project)
    && hasProducedOutput(project)
    && selectionAlive(project, active)
  );
  if (!show) {
    panel.classList.add("hidden");
    panel.setAttribute("aria-hidden", "true");
    panel.removeAttribute("aria-busy");
    syncRetouchChrome();
    return;
  }
  panel.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
  panel.setAttribute("aria-busy", retouchLocked(project) ? "true" : "false");
  const target = document.querySelector("#retouch-target");
  if (target) target.textContent = retouchTargetLabel(project, active);
  const scope = document.querySelector("#retouch-scope");
  if (scope) {
    scope.textContent = active.node === "shot"
      ? "只改这一镜，不动其他镜头。"
      : "只改这一张图，不动其他图。";
  }
  const suggestion = document.querySelector("#retouch-suggestion");
  const draft = retouchDrafts[retouchDraftKey(project, active.node, active.targetId)] || "";
  if (suggestion && document.activeElement !== suggestion && suggestion.value !== draft) suggestion.value = draft;
  const locked = retouchLocked(project);
  if (suggestion) suggestion.disabled = locked;
  const submit = document.querySelector("#retouch-submit");
  const generate = document.querySelector("#retouch-generate");
  if (submit) submit.disabled = locked;
  if (generate) generate.disabled = locked;
  const status = document.querySelector("#retouch-status");
  if (status) status.textContent = retouchStatusText(project, active);
  const errorNode = document.querySelector("#retouch-error");
  const errorText = retouchErrorText(project, active);
  if (errorNode) {
    errorNode.textContent = errorText;
    errorNode.classList.toggle("hidden", !errorText);
  }
  const note = document.querySelector("#retouch-advanced-note");
  if (note) {
    note.textContent = active.node === "shot"
      ? "只读这一镜的提示词。不改整份投喂稿。"
      : "手改原文在这张图卡片的「高级 / 调试」里，不是修片主路径。";
  }
  const prompt = document.querySelector("#retouch-prompt");
  const promptText = currentBlockPrompt(project, active);
  if (prompt && prompt.textContent !== promptText) prompt.textContent = promptText;
  const diff = promptDiffFor(project);
  const diffKey = diff
    ? [project.id, diff.node || "", diff.target_id || "", diff.at || "", diff.before || "", diff.after || "", diff.suggestion || ""].join("\u0001")
    : project.id + "\u0001";
  const selectionKey = active.node + ":" + active.targetId;
  const errorId = retouchErrorIdentity(project);
  if (errorId && errorId !== seenRetouchError) {
    seenRetouchError = errorId;
    const details = document.querySelector("#retouch-diff");
    if (details) details.open = true;
  }
  if (diffKey !== retouchDiffKey) {
    retouchDiffKey = diffKey;
    retouchDiffSelection = selectionKey;
    const details = document.querySelector("#retouch-diff");
    if (details) details.open = true;
    paintPromptDiff(diff, active);
  } else if (selectionKey !== retouchDiffSelection) {
    retouchDiffSelection = selectionKey;
    const meta = document.querySelector("#retouch-diff-meta");
    if (meta) meta.textContent = diffMetaText(diff, active);
  }
  syncRetouchChrome();
}

function selectRetouch(node, targetId) {
  if (!current || !hasProducedOutput(current)) return;
  const id = String(targetId || "");
  if (!id || (node !== "asset" && node !== "shot")) return;
  const same = retouchSelection && retouchSelection.node === node && retouchSelection.targetId === id;
  if (!same) retouchLocalError = "";
  retouchSelection = { node: node, targetId: id };
  syncRetouchPanel(current);
}

function clearRetouchSelection() {
  if (!retouchSelection && !retouchLocalError) {
    syncRetouchPanel(current);
    return;
  }
  retouchSelection = null;
  retouchLocalError = "";
  syncRetouchPanel(current);
}

function rememberRetouchDraft() {
  const suggestion = document.querySelector("#retouch-suggestion");
  if (!current || !retouchSelection || !suggestion) return "";
  const text = suggestion.value || "";
  retouchDrafts[retouchDraftKey(current, retouchSelection.node, retouchSelection.targetId)] = text;
  return text.trim();
}

async function submitRetouch() {
  if (!current || !retouchSelection || retouchLocked(current)) return;
  const projectId = current.id;
  const node = retouchSelection.node;
  const targetId = retouchSelection.targetId;
  const suggestion = rememberRetouchDraft();
  if (!suggestion) {
    retouchLocalError = "先写一下想怎么改";
    syncRetouchPanel(current);
    return;
  }
  retouchLocalError = "";
  retouchPending = true;
  syncRetouchPanel(current);
  try {
    const project = await rpc("projects.retouch", {
      id: projectId,
      node: node,
      target_id: targetId,
      suggestion: suggestion,
    });
    if (!current || current.id !== projectId) return;
    retouchLocalError = "";
    adoptQuiet(project);
    watch();
  } catch (error) {
    if (!current || current.id !== projectId) return;
    retouchLocalError = rpcMissing(error)
      ? "修片还没接到 End。需要 projects.retouch，以及 progress.retouching、prompt_diff、retouch_error。"
      : (error.message || "没有完成");
  } finally {
    if (current && current.id === projectId) {
      retouchPending = false;
      syncRetouchPanel(current);
    }
  }
}

async function submitRetouchGenerate() {
  if (!current || !retouchSelection || retouchLocked(current)) return;
  const projectId = current.id;
  const node = retouchSelection.node;
  const targetId = retouchSelection.targetId;
  retouchLocalError = "";
  retouchPending = true;
  syncRetouchPanel(current);
  try {
    const project = await rpc("projects.rerun", {
      id: projectId,
      node: node,
      target_id: targetId,
      grade: "generate",
    });
    if (!current || current.id !== projectId) return;
    retouchLocalError = "";
    adoptQuiet(project);
    watch();
  } catch (error) {
    if (!current || current.id !== projectId) return;
    retouchLocalError = rpcMissing(error)
      ? "成片档还没接到 End。需要 projects.rerun，grade 为 generate。重修仍只自动排队预览档。"
      : (error.message || "没有完成");
  } finally {
    if (current && current.id === projectId) {
      retouchPending = false;
      syncRetouchPanel(current);
    }
  }
}

function onCanvasClick(event) {
  if (pointer.moved) return;
  if (!canvasWrap || !canvasWrap.contains(event.target)) return;
  const asset = event.target.closest(".wf-asset");
  if (asset) {
    if (event.target.closest("textarea, input, button, a, .wf-upload")) return;
    selectRetouch("asset", asset.dataset.asset);
    return;
  }
  const node = event.target.closest(".wf-node");
  if (node && node.dataset.kind === "shot") {
    if (event.target.closest("video, textarea, input, button, a")) return;
    selectRetouch("shot", node.dataset.id);
    return;
  }
  if (!node) clearRetouchSelection();
}

function bindRetouch() {
  const panel = document.querySelector("#retouch-panel");
  if (!panel || panel.dataset.bound) return;
  panel.dataset.bound = "1";
  const suggestion = document.querySelector("#retouch-suggestion");
  if (suggestion) {
    suggestion.addEventListener("input", () => {
      rememberRetouchDraft();
    });
    suggestion.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitRetouch();
      }
    });
  }
  const submit = document.querySelector("#retouch-submit");
  if (submit) submit.addEventListener("click", () => submitRetouch());
  const generate = document.querySelector("#retouch-generate");
  if (generate) generate.addEventListener("click", () => submitRetouchGenerate());
  const close = document.querySelector("#retouch-close");
  if (close) close.addEventListener("click", () => clearRetouchSelection());
  if (canvasWrap) canvasWrap.addEventListener("click", onCanvasClick);
}

function bindCanvas() {
  const crewToggle = document.querySelector("#crew-toggle");
  const crewBox = document.querySelector("#crew");
  if (crewToggle && crewBox && !crewToggle.dataset.bound) {
    crewToggle.dataset.bound = "1";
    crewToggle.addEventListener("click", () => {
      const open = crewBox.classList.toggle("open");
      crewToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  if (!canvasWrap || canvasWrap.dataset.bound) return;
  canvasWrap.dataset.bound = "1";
  canvasWrap.addEventListener("pointerdown", onCanvasPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  canvasWrap.addEventListener("wheel", onWheel, { passive: false });
  const zoomOut = document.querySelector("#zoom-out");
  const zoomIn = document.querySelector("#zoom-in");
  const zoomFit = document.querySelector("#zoom-fit");
  if (zoomOut) zoomOut.addEventListener("click", () => zoomAt(view.scale / 1.1));
  if (zoomIn) zoomIn.addEventListener("click", () => zoomAt(view.scale * 1.1));
  if (zoomFit) zoomFit.addEventListener("click", () => {
    if (!canvasNodes.dataset.custom) spreadColumns();
    drawEdges(canvasEdges);
    fitCanvas(0.12);
  });
  window.addEventListener("resize", () => {
    if (!canvasWrap || canvasWrap.classList.contains("hidden") || canvasNodes.dataset.custom) return;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      if (!spreadColumns()) return;
      drawEdges(canvasEdges);
      fitCanvas();
    });
  });
  document.querySelectorAll(".js-pause").forEach((button) => {
    button.addEventListener("click", () => postControl("pause"));
  });
  document.querySelectorAll(".js-resume").forEach((button) => {
    button.addEventListener("click", () => onResumeClick());
  });
  document.querySelectorAll(".js-reset").forEach((button) => {
    button.addEventListener("click", () => postControl("reset"));
  });
  const dockToggle = document.querySelector("#dock-toggle");
  if (dockToggle && debugDock) {
    dockToggle.addEventListener("click", () => {
      debugDock.classList.toggle("open");
      dockToggle.classList.toggle("on", debugDock.classList.contains("open"));
    });
  }
  const menuToggle = document.querySelector("#menu-toggle");
  const appMenu = document.querySelector("#app-menu");
  if (menuToggle) {
    menuToggle.addEventListener("click", () => {
      const menu = document.querySelector("#app-menu");
      setMenuOpen(!(menu && menu.classList.contains("open")));
    });
  }
  document.addEventListener("click", (event) => {
    if (!appMenu || !appMenu.classList.contains("open")) return;
    if (appMenu.contains(event.target) || (menuToggle && menuToggle.contains(event.target))) return;
    setMenuOpen(false);
  });
  if (appMenu) {
    appMenu.addEventListener("click", (event) => {
      if (event.target.closest(".item, a")) setMenuOpen(false);
    });
  }
  const docsTab = document.querySelector("#side-docs");
  const historyTab = document.querySelector("#side-history");
  if (docsTab) docsTab.addEventListener("click", () => {
    sideTab = "docs";
    syncSideMode();
  });
  if (historyTab) historyTab.addEventListener("click", () => {
    sideTab = "history";
    syncSideMode();
  });
  const viewCanvasBtn = document.querySelector("#view-canvas");
  const viewPreviewBtn = document.querySelector("#view-preview");
  if (viewCanvasBtn) viewCanvasBtn.addEventListener("click", () => {
    centerView = "canvas";
    syncCenter();
    if (current && workflowMode(current)) {
      syncCanvas(current);
      syncShotStrip(current);
    }
  });
  if (viewPreviewBtn) viewPreviewBtn.addEventListener("click", () => {
    centerView = "preview";
    syncCenter();
    if (current && workflowMode(current)) {
      syncPreview(current);
      syncShotStrip(current);
    }
  });
}

bindCanvas();
bindRetouch();

async function loadConfig() {
  try {
    const config = await rpc("config.get");
    serverDebugDefault = !!config.debug_llm;
    fillModels(config.models || []);
    fillTemplates(config.templates || []);
    if (qwenEnvEl && (config.qwen_env === "prod" || config.qwen_env === "test")) {
      qwenEnvEl.value = config.qwen_env;
    }
    showMinimaxChoice(config);
  } catch (_error) {
    serverDebugDefault = false;
  }
  syncDebugToggle();
}

function showMinimaxChoice(config) {
  if (!config) return;
  if (minimaxVideoEl && (config.minimax_video === "local" || config.minimax_video === "cloud")) {
    minimaxVideoEl.value = config.minimax_video;
  }
  const base = config.minimax_base_url || "http://127.0.0.1:8765";
  const title =
    "由 config/video_catalog.json 的 default_video 决定，页面不能改。local 是 OmniRef Ref2VA，地址 " +
    base +
    "。当前 preview/generate 路线见同一文件的 routes。";
  if (minimaxVideoLabel) minimaxVideoLabel.title = title;
}

function applyTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  const button = document.querySelector("#theme-toggle");
  if (!button) return;
  const toLight = next === "dark";
  button.textContent = toLight ? "浅色" : "深色";
  button.setAttribute("aria-label", toLight ? "切换到浅色" : "切换到深色");
}

applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
const themeToggle = document.querySelector("#theme-toggle");
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("aivideo.theme", next);
    } catch (_error) {}
    applyTheme(next);
  });
}

function setSettingsOpen(open) {
  const panel = document.querySelector("#composer-settings");
  const button = document.querySelector("#settings-toggle");
  if (panel) panel.classList.toggle("open", !!open);
  if (button) {
    button.classList.toggle("on", !!open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
}

const settingsToggle = document.querySelector("#settings-toggle");
const settingsPanel = document.querySelector("#composer-settings");
if (settingsToggle) {
  settingsToggle.addEventListener("click", () => {
    setSettingsOpen(!(settingsPanel && settingsPanel.classList.contains("open")));
  });
}
document.addEventListener("click", (event) => {
  if (!settingsPanel || !settingsPanel.classList.contains("open")) return;
  if (settingsPanel.contains(event.target) || (settingsToggle && settingsToggle.contains(event.target))) return;
  setSettingsOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setSettingsOpen(false);
});

function refitCanvas() {
  requestAnimationFrame(() => {
    if (!canvasWrap || canvasWrap.classList.contains("hidden") || !canvasWrap.clientWidth) return;
    if (!canvasNodes.dataset.custom && !spreadColumns()) return;
    drawEdges(canvasEdges);
    fitCanvas();
  });
}

function setChatOpen(open, persist) {
  if (main) main.classList.toggle("chat-collapsed", !open);
  const button = document.querySelector("#chat-toggle");
  if (button) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.setAttribute("aria-label", open ? "收起对话" : "展开对话");
    const mark = button.querySelector(".chat-toggle-mark");
    if (mark) mark.textContent = open ? "›" : "‹";
    const label = button.querySelector(".chat-toggle-label");
    if (label) label.textContent = open ? "收起" : "对话";
  }
  if (!open) setSettingsOpen(false);
  if (persist !== false) {
    try {
      localStorage.setItem("aivideo.chat_open", open ? "1" : "0");
    } catch (_error) {}
  }
  refitCanvas();
}

function storedChatOpen() {
  try {
    const saved = localStorage.getItem("aivideo.chat_open");
    if (saved === "0") return false;
    if (saved === "1") return true;
  } catch (_error) {}
  return true;
}

setChatOpen(storedChatOpen(), false);
const chatToggle = document.querySelector("#chat-toggle");
if (chatToggle) {
  chatToggle.addEventListener("click", () => {
    setChatOpen(!!(main && main.classList.contains("chat-collapsed")));
  });
}

loadConfig();
refreshList();
