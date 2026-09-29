const briefEl = document.querySelector("#brief");
const form = document.querySelector("#composer");
const send = document.querySelector("#send");
const empty = document.querySelector("#empty");
const thread = document.querySelector("#thread");
const history = document.querySelector("#history");
const newChat = document.querySelector("#new-chat");
const main = document.querySelector(".main");

let projects = [];
let current = null;
let timer = null;

function statusText(project) {
  if (project.status === "done") return "已完成";
  if (project.status === "error") return "未完成";
  return project.stage || "处理中";
}

function renderHistory() {
  history.innerHTML = "";
  projects.forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "item" + (current && current.id === project.id ? " active" : "");
    const title = document.createElement("b");
    title.textContent = project.title || project.brief;
    const meta = document.createElement("span");
    meta.textContent = statusText(project);
    button.append(title, meta);
    button.addEventListener("click", () => openProject(project.id));
    history.append(button);
  });
}

function section(title, text) {
  if (!text) return null;
  const block = document.createElement("div");
  block.className = "block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const body = document.createElement("pre");
  body.textContent = text;
  block.append(heading, body);
  return block;
}

function renderThread() {
  thread.innerHTML = "";
  main.classList.toggle("fresh", !current);
  if (!current) {
    empty.classList.remove("hidden");
    thread.classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  thread.classList.remove("hidden");
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  const user = document.createElement("div");
  user.className = "user";
  user.textContent = current.brief;
  const assistant = document.createElement("div");
  assistant.className = "assistant";
  const stage = document.createElement("div");
  stage.className = "stage";
  stage.textContent = statusText(current);
  assistant.append(stage);
  ["剧本", "分镜", "生图提示词"].forEach((name, index) => {
    const key = ["script", "storyboard", "image_prompts"][index];
    const node = section(name, current[key]);
    if (node) assistant.append(node);
  });
  if (current.has_workflow) {
    const link = document.createElement("a");
    link.className = "download";
    link.href = "/api/projects/" + current.id + "/workflow";
    link.textContent = "下载 ComfyUI 工作流";
    assistant.append(link);
  }
  if (current.error) {
    const error = document.createElement("div");
    error.className = "error";
    error.textContent = current.error;
    assistant.append(error);
  }
  wrap.append(user, assistant);
  thread.append(wrap);
  thread.scrollTop = thread.scrollHeight;
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
  renderHistory();
  renderThread();
  watch();
}

function watch() {
  clearInterval(timer);
  if (!current || current.status === "done" || current.status === "error") return;
  timer = setInterval(async () => {
    const response = await fetch("/api/projects/" + current.id);
    current = await response.json();
    const listed = projects.find((item) => item.id === current.id);
    if (listed) Object.assign(listed, current);
    renderHistory();
    renderThread();
    if (current.status === "done" || current.status === "error") clearInterval(timer);
  }, 2000);
}

newChat.addEventListener("click", () => {
  current = null;
  briefEl.value = "";
  clearInterval(timer);
  renderHistory();
  renderThread();
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const brief = briefEl.value.trim();
  if (!brief) return;
  send.disabled = true;
  try {
    const response = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief }),
    });
    const project = await response.json();
    if (!response.ok) {
      alert(project.error || "没有创建成功");
      return;
    }
    briefEl.value = "";
    briefEl.style.height = "auto";
    projects.unshift(project);
    current = project;
    renderHistory();
    renderThread();
    watch();
  } finally {
    send.disabled = false;
  }
});

refreshList();
