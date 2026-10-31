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
const viewCanvasBtn = document.querySelector("#view-canvas");
const viewChatBtn = document.querySelector("#view-chat");

let projects = [];
let current = null;
let timer = null;
let clock = null;
let serverDebugDefault = false;
let otherOpen = false;
let sending = false;
let surface = "canvas";
let surfaceLock = "";
let surfaced = "";
let canvasSig = "";
let canvasEdges = [];
let view = { x: 48, y: 48, scale: 1 };
const canvasMinScale = 0.55;
let pendingSaves = new Set();
const pointer = { mode: "" };
let draftMaterials = [];
let seamKept = new Set();
const MAX_MATERIALS = 8;

const JOB_STEPS = ["立项锁定", "赛道分流", "剧本", "资产锁定", "分镜", "提示词封装", "独立质检", "离线看板"];
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

function statusText(project) {
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
    current = await rpc("projects." + action, { id: current.id });
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
  stepLabels.forEach((label, step) => {
    const item = document.createElement("li");
    const earlyStop = isDebugProject(project) && (project.progress || {}).phase === "llm_debug";
    const done = (project.status === "done" && !earlyStop) || step < index;
    if (done) item.className = "done";
    else if ((project.status === "error" || project.status === "paused") && step === index) item.className = project.status === "paused" ? "paused" : "failed";
    else if (step === index) item.className = "current";
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
  message.className = "job-message";
  message.textContent = progress.message || project.stage || (project.status === "error" ? "未完成" : "处理中");
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
  if (promptsReady(project) || (project.progress || {}).phase === "aspect_wait") {
    const produce = document.createElement("button");
    produce.type = "button";
    produce.className = "produce";
    produce.textContent = "出图出片";
    produce.addEventListener("click", () => postControl("render"));
    panel.append(produce);
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

function onResumeClick() {
  if (current && current.status === "gate") sendText("继续");
  else postControl("resume");
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
  const choosing = !!(current && current.can_reply && options.length);
  const showOther = (choosing && otherOpen) || !!(current && current.can_reply && !options.length);
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
}

function workflowMode(project) {
  if (!project || isIntentDebug(project)) return false;
  if (project.status === "gate") return true;
  if (inDrill(project)) return false;
  if (["writing", "building", "rendering", "paused", "done", "error"].includes(project.status)) return true;
  return !!project.script;
}

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
  if (key !== surfaced) {
    const previous = surfaced;
    surfaced = key;
    if (key.indexOf("canvas:") === 0) {
      surface = "canvas";
      surfaceLock = "";
      return;
    }
    if (key.indexOf("chat:") === 0 && surfaceLock !== "canvas") {
      surface = "chat";
      return;
    }
    if (key.indexOf("other:") === 0 && previous.indexOf("chat:") === 0) {
      surface = "chat";
      surfaceLock = "";
      return;
    }
  }
  if (key.indexOf("chat:") === 0 && surfaceLock !== "canvas") surface = "chat";
  else if (key.indexOf("canvas:") === 0 && surfaceLock !== "chat") surface = "canvas";
}

function hideWorkflow() {
  if (stageBar) stageBar.classList.add("hidden");
  if (canvasWrap) canvasWrap.classList.add("hidden");
  if (debugDock) debugDock.classList.add("hidden");
}

function showWorkflow() {
  if (stageBar) stageBar.classList.remove("hidden");
  if (debugDock) debugDock.classList.remove("hidden");
  const chat = surface === "chat";
  if (canvasWrap) canvasWrap.classList.toggle("hidden", chat);
  if (viewCanvasBtn) viewCanvasBtn.classList.toggle("on", !chat);
  if (viewChatBtn) viewChatBtn.classList.toggle("on", chat);
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
    hideWorkflow();
    empty.classList.remove("hidden");
    thread.classList.add("hidden");
    thread.innerHTML = "";
    paintTranscript.key = "";
    paintTranscript.projectId = "";
    seamKept = new Set();
    syncComposer();
    return;
  }
  empty.classList.add("hidden");
  settleSurface(current);
  if (!workflowMode(current)) {
    hideWorkflow();
    thread.classList.remove("hidden");
    paintTranscript(opts, true);
    return;
  }
  showWorkflow();
  const chat = surface === "chat";
  thread.classList.toggle("hidden", !chat);
  if (chat) paintTranscript(opts, false);
  syncCanvas(current);
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
  ].join("\u0001");
}

function paintTranscript(options, full) {
  const opts = options || {};
  const prevScroll = thread.scrollTop;
  const checkedBefore = [...thread.querySelectorAll(".seam-option input")].map((box) => ({
    value: box.value,
    checked: box.checked,
  }));
  const key = current ? transcriptKey(current) : "";
  const sameProject = !!(current && paintTranscript.projectId === current.id);
  if (key && key === paintTranscript.key && sameProject && thread.childElementCount) {
    syncChoiceDisabled();
    if (opts.scroll === "top") thread.scrollTop = 0;
    else if (opts.scroll === "bottom") thread.scrollTop = thread.scrollHeight;
    syncComposer();
    return;
  }
  seamKept = new Set(
    sameProject ? checkedBefore.filter((box) => box.checked).map((box) => box.value) : []
  );
  thread.innerHTML = "";
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
  visibleMessages(current).forEach((message) => {
    wrap.append(messageBubble(message.role, message.content || ""));
  });
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
  const choice = latestChoiceMessage(current);
  if (current.can_reply && choice) {
    wrap.append(choiceList(choice));
  }
  if (current.status === "gate" || (current.progress || {}).phase === "lock_wait") {
    wrap.append(progressPanel(current));
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
  if (!full) {
    thread.append(wrap);
    syncComposer();
    if (opts.scroll === "top") thread.scrollTop = 0;
    else if (opts.scroll === "bottom") thread.scrollTop = thread.scrollHeight;
    else thread.scrollTop = prevScroll;
    return;
  }
  const assistant = document.createElement("div");
  assistant.className = "assistant";
  if (!inDrill(current) && (current.progress || {}).phase !== "lock_wait" && current.status !== "gate") {
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
  if (opts.scroll === "top") thread.scrollTop = 0;
  else if (opts.scroll === "bottom" || count !== renderThread.lastCount) thread.scrollTop = thread.scrollHeight;
  else thread.scrollTop = prevScroll;
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

async function openProject(id) {
  const switching = !current || current.id !== id;
  try {
    current = await rpc("projects.get", { id: id });
  } catch (_error) {
    return;
  }
  if (switching) {
    surfaceLock = "";
    surfaced = "";
    canvasSig = "";
  }
  showProjectModel(current);
  showProjectTemplate(current);
  renderHistory();
  renderThread({ scroll: current.status === "drilling" ? "bottom" : "top" });
  watch();
}

function keepWatching(project) {
  return !!(project && project.status !== "done" && project.status !== "error");
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
    if (!keepWatching(current)) clearInterval(timer);
  }, 2000);
}

newChat.addEventListener("click", () => {
  current = null;
  otherOpen = false;
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
  const task = taskById(project, id);
  if (task && task.state) return task.state;
  if (kind === "script" || kind === "storyboard") {
    const text = kind === "script" ? project.script : project.storyboard;
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
    column.sort((a, b) => (Number(a.dataset.row) || 0) - (Number(b.dataset.row) || 0));
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
  } else if (fit >= canvasMinScale && naturalH > 0 && naturalW / naturalH > viewW / viewH) {
    const targetH = naturalW / (viewW / viewH);
    const tallest = measured.reduce((best, col) => {
      return col.contentH + minGapY * col.gaps > best.contentH + minGapY * best.gaps ? col : best;
    });
    if (tallest.gaps > 0) gapY = Math.max(minGapY, (targetH - tallest.contentH) / tallest.gaps);
  }
  let x = pad;
  measured.forEach((col) => {
    let y = pad;
    col.column.forEach((el) => {
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
  card.append(makeField(project, "asset:" + asset.id, 3));
  card.append(uploadControl(asset.id));
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
  if (info.kind === "image") meta.textContent = (info.assets || []).length + " 张 · 必须";
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
  if (surface !== "chat") {
    if (fittedSig !== canvasSig) {
      requestAnimationFrame(() => {
        if (canvasWrap.classList.contains("hidden") || !canvasWrap.clientWidth) return;
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
}

function patchCanvas(project) {
  canvasNodes.querySelectorAll(".wf-node").forEach((node) => {
    const id = node.dataset.id;
    const kind = node.dataset.kind;
    const dot = node.querySelector(".wf-dot");
    if (dot) dot.className = "wf-dot " + nodeState(project, kind, id);
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
  const showProduce = promptsReady(project) || ((project.progress || {}).phase === "aspect_wait");
  document.querySelectorAll(".js-produce").forEach((button) => {
    button.classList.toggle("hidden", !showProduce);
    button.disabled = !showProduce || sending;
  });
}

function syncDock(project) {
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

function bindCanvas() {
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
  if (viewCanvasBtn) {
    viewCanvasBtn.addEventListener("click", () => {
      surface = "canvas";
      surfaceLock = "canvas";
      renderThread();
    });
  }
  if (viewChatBtn) {
    viewChatBtn.addEventListener("click", () => {
      surface = "chat";
      surfaceLock = canvasHome(current) ? "chat" : "";
      renderThread();
    });
  }
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
    dockToggle.addEventListener("click", () => debugDock.classList.toggle("open"));
  }
}

bindCanvas();

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

if (qwenEnvEl) {
  qwenEnvEl.addEventListener("change", async () => {
    const choice = qwenEnvEl.value;
    try {
      const saved = await rpc("config.set_qwen_env", { qwen_env: choice });
      if (saved && (saved.qwen_env === "prod" || saved.qwen_env === "test")) {
        qwenEnvEl.value = saved.qwen_env;
      }
    } catch (_error) {
      await loadConfig();
    }
  });
}

function showMinimaxChoice(config) {
  if (!config) return;
  if (minimaxVideoEl && (config.minimax_video === "local" || config.minimax_video === "cloud")) {
    minimaxVideoEl.value = config.minimax_video;
  }
  const base = config.minimax_base_url || "http://121.196.162.203:8765";
  const title =
    "云端仍是 768P。本地是 FastH3 Trim（模型 MiniMax/MiniMax-H3，不传宽高时 16:9 为 832×480，片长用这一镜的 duration），地址 " +
    base +
    "。";
  if (minimaxVideoLabel) minimaxVideoLabel.title = title;
}

if (minimaxVideoEl) {
  minimaxVideoEl.addEventListener("change", async () => {
    const choice = minimaxVideoEl.value;
    try {
      const saved = await rpc("config.set_minimax_video", { minimax_video: choice });
      showMinimaxChoice(saved);
    } catch (_error) {
      await loadConfig();
    }
  });
}

loadConfig();
refreshList();
