#!/usr/bin/env python3
"""Copy WorkBuddy builtin skills, shell templates, and downloaded experts.

Reads the local app bundle and ~/.workbuddy. Does not modify those sources
and does not download experts that are only listed in the marketplace manifest.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "workbuddy_prompts"

APP_BUILTIN = Path(
    "/Applications/WorkBuddy.app/Contents/Resources"
    "/app.asar.unpacked/resources/plugins/workbuddy-builtin"
)
APP_TEMPLATES = Path(
    "/Applications/WorkBuddy.app/Contents/Resources"
    "/app.asar.unpacked/resources/templates"
)
EXPERTS = Path.home() / ".workbuddy" / "plugins" / "marketplaces" / "experts" / "plugins"
MANIFEST = Path.home() / ".workbuddy" / "app" / "cache" / "experts" / "manifest.json"

SKIP_NAMES = {".DS_Store", "__pycache__", ".git"}


def is_fence(line):
    token = line.strip()
    while token.startswith("\\"):
        token = token[1:].lstrip()
    return token == "---"


def frontmatter(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or not is_fence(lines[0]):
        return {}
    block = []
    for line in lines[1:]:
        if is_fence(line):
            break
        block.append(line)
    data = {}
    key = None
    buf = []

    def flush():
        nonlocal key, buf
        if key is None:
            return
        data[key] = unwrap_scalar("\n".join(buf))
        key = None
        buf = []

    for line in block:
        if line.startswith((" ", "\t")):
            if key is not None:
                buf.append(line.strip())
            continue
        if ":" in line:
            flush()
            name, _, value = line.partition(":")
            key = name.strip()
            buf = [value.strip()] if value.strip() else []
            continue
        if key is not None:
            buf.append(line.strip())
    flush()
    return data


def unwrap_scalar(raw):
    raw = raw.strip()
    if raw.startswith((">-", "|-", ">", "|")):
        parts = raw.split(None, 1)
        raw = parts[1] if len(parts) > 1 else ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1]
    return " ".join(raw.split())


def one_line(text, limit=200):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def description_of(meta):
    for key in ("description", "description_zh", "description_en", "summary"):
        value = one_line(meta.get(key, ""))
        if value:
            return value
    return ""


def label_of(meta, fallback):
    for key in ("name", "title"):
        value = one_line(meta.get(key, ""))
        if value:
            return value
    return fallback


def ignore_junk(_dirpath, names):
    return {name for name in names if name in SKIP_NAMES or name.endswith(".pyc")}


def skill_roots(base):
    return sorted(path.parent for path in base.rglob("SKILL.md") if path.is_file())


def copy_skill(skill_dir, dest, roots):
    nested = {path for path in roots if path != skill_dir and skill_dir in path.parents}

    def ignore(dirpath, names):
        skipped = ignore_junk(dirpath, names)
        parent = Path(dirpath)
        for name in names:
            if parent / name in nested:
                skipped.add(name)
        return skipped

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest, ignore=ignore)


def copy_templates(dest_root):
    copied = []
    if not APP_TEMPLATES.is_dir():
        return copied
    for path in sorted(APP_TEMPLATES.glob("*.tpl")):
        target = dest_root / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append((path, target))
    style = APP_TEMPLATES / "style"
    if style.is_dir():
        for path in sorted(style.glob("*.md")):
            target = dest_root / "style" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append((path, target))
    return copied


def copy_tree(src, dest):
    if not src.is_dir():
        return
    shutil.copytree(src, dest, ignore=ignore_junk)


def copy_expert(plugin_dir, dest_plugin):
    agents = []
    agent_dir = plugin_dir / "agents"
    if agent_dir.is_dir():
        for path in sorted(agent_dir.glob("*.md")):
            target = dest_plugin / "agents" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            agents.append((path, target))
    copy_tree(plugin_dir / "skills", dest_plugin / "skills")
    copy_tree(plugin_dir / "references", dest_plugin / "references")
    for path in sorted(plugin_dir.glob("*.md")):
        shutil.copy2(path, dest_plugin / path.name)
    return agents


def rel_link(path):
    return path.relative_to(OUT).as_posix()


def bullet(title, detail, source):
    lines = [f"- {title}"]
    if detail:
        lines[0] += f" — {detail}"
    lines.append(f"  - 来源：`{source}`")
    return lines


def linked(name, exported, detail, source):
    title = f"[{name}]({rel_link(exported)})"
    return bullet(title, detail, source)


def load_manifest():
    if not MANIFEST.is_file():
        return [], []
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    categories = data.get("categories") or []
    experts = data.get("experts") or []
    return categories, experts


def zh(value):
    if isinstance(value, dict):
        return value.get("zh") or value.get("en") or ""
    return value or ""


def prompt_key(prompt_file):
    path = (prompt_file or "").replace("\\", "/")
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def write_index(skills, templates, expert_plugins, categories, manifest_experts, local_by_prompt):
    lines = [
        "# WorkBuddy 提示词",
        "",
        "从本机 WorkBuddy 安装目录复制。只读来源，未改客户端文件，也没有下载市场里尚未落地的专家正文。",
        "",
        f"- 内置 skill：{len(skills)}",
        f"- 系统模板：{len(templates)}",
        f"- 已下载专家插件：{len(expert_plugins)}",
        f"- 专家市场条目：{len(manifest_experts)}",
        "",
        "## 内置 skill",
        "",
    ]
    for source, exported, meta in skills:
        name = label_of(meta, exported.parent.name)
        lines.extend(linked(name, exported, description_of(meta), source))
    lines.extend(["", "## 系统模板", ""])
    for source, exported in templates:
        lines.extend(linked(exported.name, exported, "", source))
    lines.extend(["", "## 已下载专家", ""])
    if not expert_plugins:
        lines.append("本机 `plugins/marketplaces/experts/plugins` 里没有已下载的专家。")
        lines.append("")
    for plugin_name, agents, skill_files in expert_plugins:
        lines.append(f"### {plugin_name}")
        lines.append("")
        for source, exported in agents:
            meta = frontmatter(exported)
            name = label_of(meta, exported.stem)
            lines.extend(linked(name, exported, description_of(meta), source))
        if skill_files:
            lines.append("")
            lines.append("附带 skill：")
            lines.append("")
            for source, exported in skill_files:
                meta = frontmatter(exported)
                name = label_of(meta, exported.parent.name)
                lines.extend(linked(name, exported, description_of(meta), source))
        lines.append("")

    lines.extend(["## 专家市场", ""])
    lines.append("已下载的条目标了导出链接。其余只有名称和 `promptFile`，正文不在本机。")
    lines.append("")
    by_category = {}
    for expert in manifest_experts:
        by_category.setdefault(expert.get("categoryId") or "", []).append(expert)
    ordered = [(item.get("id"), zh(item.get("name")) or item.get("id")) for item in categories]
    seen = {item_id for item_id, _name in ordered}
    for item_id in by_category:
        if item_id not in seen:
            ordered.append((item_id, item_id or "未分类"))
    downloaded = 0
    for item_id, label in ordered:
        group = by_category.get(item_id) or []
        if not group:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for expert in sorted(group, key=lambda item: zh(item.get("displayName"))):
            name = zh(expert.get("displayName")) or expert.get("id") or "未命名"
            profession = zh(expert.get("profession"))
            label_name = f"{name}（{profession}）" if profession else name
            detail = one_line(zh(expert.get("description")))
            key = prompt_key(expert.get("promptFile"))
            exported = local_by_prompt.get(key)
            title = label_name
            if exported is not None:
                downloaded += 1
                title = f"[{label_name}]({rel_link(exported)})"
                status = "已下载"
            else:
                status = "未下载"
            line = f"- {title}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
            lines.append(f"  - promptFile：`{key or '无'}`")
            lines.append(f"  - 状态：{status}")
        lines.append("")
    lines.append(f"市场中已下载 {downloaded}，未下载 {len(manifest_experts) - downloaded}。")
    lines.append("")
    (OUT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    return downloaded


def export():
    missing = [str(path) for path in (APP_BUILTIN, APP_TEMPLATES, EXPERTS) if not path.exists()]
    if missing:
        print("缺少来源目录：", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    roots = skill_roots(APP_BUILTIN)
    skills = []
    for skill_dir in roots:
        dest = OUT / "builtin-skills" / skill_dir.relative_to(APP_BUILTIN)
        copy_skill(skill_dir, dest, set(roots))
        skill_md = dest / "SKILL.md"
        skills.append((skill_dir / "SKILL.md", skill_md, frontmatter(skill_md)))

    templates = copy_templates(OUT / "templates")

    expert_plugins = []
    local_by_prompt = {}
    for plugin_dir in sorted(path for path in EXPERTS.iterdir() if path.is_dir()):
        dest_plugin = OUT / "experts" / plugin_dir.name
        dest_plugin.mkdir(parents=True, exist_ok=True)
        agents = copy_expert(plugin_dir, dest_plugin)
        for source, exported in agents:
            local_by_prompt[f"/plugins/{plugin_dir.name}/agents/{exported.name}"] = exported
        skill_files = []
        skill_root = dest_plugin / "skills"
        if skill_root.is_dir():
            for skill_md in sorted(skill_root.rglob("SKILL.md")):
                source = plugin_dir / skill_md.relative_to(dest_plugin)
                skill_files.append((source, skill_md))
        expert_plugins.append((plugin_dir.name, agents, skill_files))

    categories, manifest_experts = load_manifest()
    downloaded = write_index(
        skills, templates, expert_plugins, categories, manifest_experts, local_by_prompt
    )
    agent_count = sum(len(agents) for _name, agents, _skills in expert_plugins)
    print(f"写出 {OUT}")
    print(f"内置 skill {len(skills)}")
    print(f"系统模板 {len(templates)}")
    print(f"已下载专家插件 {len(expert_plugins)}，agent {agent_count}")
    print(f"专家市场 {len(manifest_experts)}，其中已下载 {downloaded}")
    return 0


if __name__ == "__main__":
    sys.exit(export())
