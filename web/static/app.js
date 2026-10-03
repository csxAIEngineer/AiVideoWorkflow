const briefEl = document.querySelector("#brief");
const form = document.querySelector("#composer");
const send = document.querySelector("#send");
const empty = document.querySelector("#empty");
const thread = document.querySelector("#thread");
const history = document.querySelector("#history");
const newChat = document.querySelector("#new-chat");
const main = document.querySelector(".main");
const debugEl = document.querySelector("#debug-llm");
const modelEl = document.querySelector("#model");
const modelFoot = document.querySelector("#model-foot");

let projects = [];
let current = null;
let timer = null;
let clock = null;
let serverDebugDefault = false;
let otherOpen = false;
let sending = false;

const JOB_STEPS = ["建立项目", "写剧本", "生图", "出视频", "完成"];
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

function isDebugProject(project) {
  const phase = project && project.progress && project.progress.phase;
  return !!(project && (project.debug_llm || (project.progress && project.progress.debug_llm) || phase === "llm_debug" || phase === "image_debug"));
}

function statusText(project) {
  if (project.status === "drilling") {
    const phase = project.progress && project.progress.phase;
    return phase === "drill_think" ? "正在想方向" : "选一个方向";
  }
  if (isDebugProject(project) && project.status === "done") {
    const phase = project.progress && project.progress.phase;
    if (phase === "llm_debug") return project.stage || "Debug：已停在大模型";
    return project.stage || "Debug：已停在生图";
  }
  if (project.status === "done") return project.stage || "成片已生成";
  if (project.status === "paused") return "已暂停";
  if (project.status === "error") return "未完成";
  const phase = project.progress && project.progress.phase;
  if (phase === "retry") return "重试";
  if (phase === "reconnect") return "重新连接";
  return project.stage || "处理中";
}

function jobIndex(project) {
  const phase = (project.progress && project.progress.phase) || "";
  if (isDebugProject(project)) {
    if (project.status === "done" && phase === "llm_debug") return 2;
    if (project.status === "done") return 3;
    if (project.status === "rendering" || project.status === "paused" || project.status === "building" || project.status === "error") return 2;
    if (phase === "images" || phase === "retry" || phase === "reconnect") return 2;
    if (project.status === "writing" || project.script) return 1;
    return 0;
  }
  if (project.status === "done") return 4;
  if (project.status === "error" || project.status === "paused" || project.status === "rendering") {
    const tasks = project.tasks || [];
    const active = tasks.find((task) => ["running", "retrying", "paused", "error"].includes(task.state));
    if (active) return active.kind === "image" ? 2 : 3;
    if (tasks.some((task) => task.kind !== "image" && task.state === "done")) return 3;
    if (tasks.some((task) => task.kind === "image" && task.state === "done")) return 2;
    if (phase === "videos" || phase === "join") return 3;
    if (phase === "images" || phase === "retry" || phase === "reconnect" || phase === "paused") return 2;
    if (project.script) return 1;
    return 1;
  }
  const order = { queued: 0, writing: 1, building: 2, rendering: 2 };
  return order[project.status] == null ? 0 : order[project.status];
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
  if (task.kind === "video") return task.duration + " 秒";
  return "生图";
}

function tickElapsed() {
  const node = document.querySelector(".job-elapsed");
  if (!node || !current) return;
  const label = elapsedLabel(current);
  if (label) node.textContent = label;
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
  resume.addEventListener("click", () => postControl("resume"));
  const reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = "重置当前步骤";
  reset.disabled = project.status === "rendering";
  reset.addEventListener("click", () => postControl("reset"));
  bar.append(pause, resume, reset);
  return bar;
}

async function postControl(action) {
  if (!current || sending) return;
  sending = true;
  syncComposer();
  try {
    const response = await fetch("/api/projects/" + current.id + "/" + action, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(body.error || "没有完成");
      return;
    }
    current = body;
    const listed = projects.find((item) => item.id === current.id);
    if (listed) Object.assign(listed, current);
    renderHistory();
    renderThread();
    watch();
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
  title.textContent = isDebugProject(project) ? "任务进展 · Debug" : "任务进展";
  const elapsed = document.createElement("span");
  elapsed.className = "job-elapsed";
  elapsed.textContent = elapsedLabel(project);
  head.append(title, elapsed);
  panel.append(head);

  const steps = document.createElement("ol");
  steps.className = "job-steps";
  const index = jobIndex(project);
  const stepLabels = isDebugProject(project)
    ? ["建立项目", "写剧本", "生图", "Debug 结束"]
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
  const response = await fetch("/api/projects/" + id, { method: "DELETE" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    alert(body.error || "删除失败");
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
  const showRef = rows.some((row) => row.depends_on);
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
      ref.textContent = row.depends_on || "—";
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

function latestOptions(project) {
  const messages = (project && project.messages) || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && (message.options || []).length) return message.options;
  }
  return [];
}

function choiceList(options) {
  const list = document.createElement("div");
  list.className = "choices";
  options.forEach((label, index) => {
    const button = document.createElement("button");
    button.type = "button";
    const other = label === "其他";
    button.className = "choice" + (other ? " choice-other" : index === 0 ? " choice-rec" : "");
    if (!other && index === 0) {
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
  if (showOther) briefEl.placeholder = "写下你的方向";
  else if (showBrief) briefEl.placeholder = current ? "写下另一条片子的背景" : "写下这段片子的背景";
}

function renderThread(options) {
  const opts = options || {};
  const prevScroll = thread.scrollTop;
  thread.innerHTML = "";
  main.classList.toggle("fresh", !current);
  if (!current) {
    empty.classList.remove("hidden");
    thread.classList.add("hidden");
    syncComposer();
    return;
  }
  empty.classList.add("hidden");
  thread.classList.remove("hidden");
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  visibleMessages(current).forEach((message) => {
    wrap.append(messageBubble(message.role, message.content || ""));
  });
  if (current.can_reply && latestOptions(current).length) {
    wrap.append(choiceList(latestOptions(current)));
  }
  if (current.status === "drilling" && !current.can_reply) {
    const thinking = document.createElement("p");
    thinking.className = "stage";
    thinking.textContent = current.stage || "正在想方向";
    wrap.append(thinking);
  }
  const assistant = document.createElement("div");
  assistant.className = "assistant";
  if (!inDrill(current)) assistant.append(progressPanel(current));
  tickElapsed();
  if (current.status !== "drilling" && isDebugProject(current)) {
    const note = document.createElement("p");
    note.className = "debug-note";
    const earlyStop = (current.progress || {}).phase === "llm_debug";
    note.textContent = current.status === "done"
      ? (earlyStop
        ? "Debug 模式：这次停在大模型返回，没有生图。"
        : "Debug 模式：已停在生图。下方可检查剧本、分镜、资产和图片。视频与拼接暂不执行。")
      : "Debug 模式：写完剧本后只生图，不出视频、不拼接。";
    assistant.append(note);
  }
  ["剧本", "分镜", "生图提示词"].forEach((name, index) => {
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
    spec.href = "/api/projects/" + current.id + "/spec";
    spec.download = "spec-" + current.id + ".json";
    spec.textContent = "下载 spec.json";
    assistant.append(spec);
  }
  (current.debug_files || []).forEach((key) => {
    const link = document.createElement("a");
    link.className = "download";
    link.href = "/api/projects/" + current.id + "/debug/" + key;
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
  const response = await fetch("/api/projects");
  projects = await response.json();
  if (current) {
    const fresh = projects.find((item) => item.id === current.id);
    if (fresh) current = fresh;
  }
  renderHistory();
  renderThread();
}

async function openProject(id) {
  const response = await fetch("/api/projects/" + id);
  if (!response.ok) return;
  current = await response.json();
  showProjectModel(current);
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
    const response = await fetch("/api/projects/" + current.id);
    current = await response.json();
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
  briefEl.value = "";
  const savedModel = localStorage.getItem("aivideo.model");
  if (modelEl && savedModel && [...modelEl.options].some((option) => option.value === savedModel)) {
    modelEl.value = savedModel;
    syncModelFoot();
  }
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
  modelFoot.textContent = (option ? option.textContent : "DeepSeek V4 Pro") + " · 本机出片";
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

function selectedModel() {
  return modelEl ? modelEl.value : "";
}

function syncDebugToggle() {
  if (!debugEl) return;
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

if (debugEl) {
  debugEl.addEventListener("change", () => {
    if (debugEl.disabled) return;
    localStorage.setItem("aivideo.debug_llm", debugEl.checked ? "1" : "0");
  });
}

if (modelEl) {
  modelEl.addEventListener("change", () => {
    localStorage.setItem("aivideo.model", modelEl.value);
    syncModelFoot();
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
  renderHistory();
  renderThread({ scroll: "bottom" });
  watch();
}

async function startBrief(text) {
  const brief = String(text || "").trim();
  if (!brief || sending) return;
  sending = true;
  syncComposer();
  try {
    const response = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief,
        debug_llm: !!(debugEl && debugEl.checked),
        model: selectedModel(),
      }),
    });
    const project = await response.json();
    if (!response.ok) {
      alert(project.error || "没有开始");
      return;
    }
    adoptProject(project, true);
  } finally {
    sending = false;
    syncComposer();
  }
}

async function sendText(text) {
  const body = String(text || "").trim();
  if (!body || sending || !current || !current.can_reply) return;
  sending = true;
  syncComposer();
  try {
    const response = await fetch("/api/projects/" + current.id + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: body, model: selectedModel() }),
    });
    const project = await response.json();
    if (!response.ok) {
      alert(project.error || "没有发出去");
      return;
    }
    adoptProject(project, false);
  } finally {
    sending = false;
    syncComposer();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (briefEl.disabled) return;
  const text = briefEl.value.trim();
  if (!text) return;
  if (current && current.can_reply) sendText(text);
  else startBrief(text);
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) return;
    const config = await response.json();
    serverDebugDefault = !!config.debug_llm;
    fillModels(config.models || []);
  } catch (_error) {
    serverDebugDefault = false;
  }
  syncDebugToggle();
}

loadConfig();
refreshList();
