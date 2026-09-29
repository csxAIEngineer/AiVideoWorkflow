#!/usr/bin/env python3
"""Multi-turn short-drama room. Loads downloaded SKILL.md packs and calls Qwen or DeepSeek."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SKILLS = ROOT / "skills"
CONFIG_PATH = ROOT / "studio" / "config.json"
SESSIONS = ROOT / "studio" / "sessions"

ROLE_EXTRA = {
    "short-drama-write": ["references/screenplay-format.md"],
    "short-drama-image-prompts": ["references/common-recipe.md"],
    "short-drama-assets": ["references/location-and-view.md"],
    "short-drama-storyboard": ["references/shot-craft.md"],
}


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    body = text[end + 4 :].lstrip("\n")
    return meta, body


def discover_skills():
    found = []
    for path in sorted(SKILLS.rglob("SKILL.md")):
        if ".git" in path.parts:
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        name = meta.get("name") or path.parent.name
        found.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "path": path,
                "dir": path.parent,
                "body": body,
            }
        )
    return found


def skill_by_name(skills, name):
    for item in skills:
        if item["name"] == name:
            return item
    raise SystemExit("没有这个技能：%s\n可用：%s" % (name, ", ".join(item["name"] for item in skills)))


def extra_text(skill, user_text):
    chunks = []
    extras = list(ROLE_EXTRA.get(skill["name"], []))
    if skill["name"] == "short-drama-image-prompts" and any(word in user_text for word in ("场景", "空镜", "地点")):
        extras.append("references/location-plate.md")
    if skill["name"] == "short-drama-image-prompts" and any(word in user_text for word in ("人物", "定妆", "角色")):
        extras.append("references/character-and-look.md")
    for relative in extras:
        path = skill["dir"] / relative
        if path.is_file():
            chunks.append("\n\n# 附件 %s\n\n%s" % (relative, path.read_text(encoding="utf-8")))
    return "".join(chunks)


def system_prompt(skill, user_text):
    return (
        "你是短剧编剧室。严格按当前技能的格式写，不要另起一套模板。\n"
        "这是多轮对话：已经确认的人物外形、场景和道具必须原样沿用，不要换脸、换装或改地名。\n"
        "只产出文字。不要声称已经出图或出视频。\n"
        "当前技能：%s\n%s\n\n%s%s"
        % (skill["name"], skill["description"], skill["body"], extra_text(skill, user_text))
    )


def pick_role(config, text, current):
    for label, name in config["roles"].items():
        if label in text:
            return name
    if any(word in text for word in ("分镜", "镜头", "storyboard")):
        return config["roles"]["分镜"]
    if any(word in text for word in ("生图", "提示词", "定妆", "空镜")):
        return config["roles"]["生图"]
    if any(word in text for word in ("场景", "资产", "道具", "角色表")):
        return config["roles"]["场景"]
    if any(word in text for word in ("剧本", "对白", "场次")):
        return config["roles"]["剧本"]
    return current


def call_llm(config, provider_name, messages):
    provider = config["providers"][provider_name]
    if provider.get("via") == "cr":
        from cr_api import CRClient, CRError

        try:
            result = CRClient().chat(messages, model=provider["model"], max_tokens=8000)
        except CRError as error:
            raise SystemExit(str(error))
        return result.content
    api_key = os.environ.get(provider["api_key_env"], "")
    if not api_key:
        raise SystemExit("缺少环境变量 %s" % provider["api_key_env"])
    payload = json.dumps(
        {"model": provider["model"], "messages": messages},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        provider["base_url"].rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit("接口返回 %s\n%s" % (error.code, detail[:800]))
    return data["choices"][0]["message"]["content"]


def print_skills(skills):
    for item in skills:
        print("%s\n  %s" % (item["name"], item["description"][:120]))


def repl(config, skills, provider_name):
    current_name = config["roles"]["剧本"]
    history = []
    print("短剧编剧室。提供者 %s。输入 /help 查看命令。" % provider_name)
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        if line == "/help":
            print("/剧本 /分镜 /生图 /场景  切换技能")
            print("/skill 名称   /skills   /provider qwen|deepseek")
            print("/reset 清空本轮对话    /save 文件路径")
            continue
        if line == "/skills":
            print_skills(skills)
            continue
        if line == "/reset":
            history = []
            print("对话已清空。")
            continue
        if line.startswith("/provider "):
            provider_name = line.split(None, 1)[1].strip()
            if provider_name not in config["providers"]:
                print("可选：%s" % ", ".join(config["providers"]))
                continue
            print("已切换到 %s。" % provider_name)
            continue
        if line.startswith("/skill "):
            current_name = line.split(None, 1)[1].strip()
            skill_by_name(skills, current_name)
            print("当前技能：%s" % current_name)
            continue
        if line.startswith("/") and line[1:] in config["roles"]:
            current_name = config["roles"][line[1:]]
            print("当前技能：%s" % current_name)
            continue
        if line.startswith("/save "):
            target = Path(line.split(None, 1)[1].strip())
            last = next((item["content"] for item in reversed(history) if item["role"] == "assistant"), "")
            target.write_text(last, encoding="utf-8")
            print("已写入 %s" % target)
            continue
        chosen = pick_role(config, line, current_name)
        if chosen != current_name:
            current_name = chosen
            print("（按这句话改用技能 %s）" % current_name)
        skill = skill_by_name(skills, current_name)
        messages = [{"role": "system", "content": system_prompt(skill, line)}] + history
        messages.append({"role": "user", "content": line})
        answer = call_llm(config, provider_name, messages)
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": answer})
        print("\n" + answer)


def main(argv):
    config = load_config()
    skills = discover_skills()
    if not skills:
        raise SystemExit("skills/ 里没有 SKILL.md")
    if "--list" in argv:
        print_skills(skills)
        return
    provider_name = config["provider"]
    if "--provider" in argv:
        provider_name = argv[argv.index("--provider") + 1]
    repl(config, skills, provider_name)


if __name__ == "__main__":
    main(sys.argv[1:])
