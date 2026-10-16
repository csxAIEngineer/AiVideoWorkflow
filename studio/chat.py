#!/usr/bin/env python3
"""Multi-turn car-video room. Drill first, then write with seedance-car-vehicle."""

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

CAR_SKILL = "seedance-car-vehicle"
ROLE_EXTRA = {
    CAR_SKILL: ["references/cases.md"],
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


def extra_text(skill, _user_text):
    chunks = []
    for relative in ROLE_EXTRA.get(skill["name"], []):
        path = skill["dir"] / relative
        if path.is_file():
            chunks.append("\n\n# 附件 %s\n\n%s" % (relative, path.read_text(encoding="utf-8")))
    return "".join(chunks)


def system_prompt(skill, user_text, locked_brief=""):
    locked = ""
    if locked_brief:
        locked = "已经和用户确认的创作简报，必须原样遵守：\n%s\n\n" % locked_brief
    return (
        "你是汽车视频导演。严格按当前技能的格式写，不要另起一套模板。\n"
        "这是多轮对话：锁定这台车、这条路和选定的机位，不要换成另一台车。\n"
        "先选 references/cases.md 里的一个案例当锚，不要把几条案例混成一条。\n"
        "画幅固定 9:16。每镜时长写成 4 到 15 秒，一镜一个机位，不要把技能里的一秒一镜原样交给出片。\n"
        "只产出文字。不要声称已经出图或出视频。\n"
        + locked
        + "当前技能：%s\n%s\n\n%s%s"
        % (skill["name"], skill["description"], skill["body"], extra_text(skill, user_text))
    )


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
    from end.pipeline import drill_system_prompt, parse_drill_text

    history = []
    drilling = True
    locked_brief = ""
    pending_options = []
    print("汽车视频导演室。先问清楚你要拍什么。提供者 %s。输入 /help 查看命令。" % provider_name)
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
            print("先问清楚再写。确认之后只用 %s 写提示词。" % CAR_SKILL)
            print("/skills   /provider qwen|deepseek")
            print("/reset 清空本轮对话    /save 文件路径")
            continue
        if line == "/skills":
            print_skills(skills)
            continue
        if line == "/reset":
            history = []
            drilling = True
            locked_brief = ""
            pending_options = []
            print("对话已清空。重新问你要拍什么。")
            continue
        if line.startswith("/provider "):
            provider_name = line.split(None, 1)[1].strip()
            if provider_name not in config["providers"]:
                print("可选：%s" % ", ".join(config["providers"]))
                continue
            print("已切换到 %s。" % provider_name)
            continue
        if line.startswith("/skill ") or (line.startswith("/") and line[1:] in config.get("roles", {})):
            print("确认之后只用 %s，不再切换短剧技能。" % CAR_SKILL)
            continue
        if line.startswith("/save "):
            target = Path(line.split(None, 1)[1].strip())
            last = next((item["content"] for item in reversed(history) if item["role"] == "assistant"), "")
            target.write_text(last, encoding="utf-8")
            print("已写入 %s" % target)
            continue
        if drilling:
            if pending_options and line.isdigit():
                index = int(line) - 1
                if 0 <= index < len(pending_options):
                    picked = pending_options[index]
                    if picked == "其他":
                        try:
                            line = input("其他：").strip()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            return
                        if not line:
                            continue
                    else:
                        line = picked
                        print("（选了：%s）" % line)
            messages = [{"role": "system", "content": drill_system_prompt()}] + history
            messages.append({"role": "user", "content": line})
            answer = call_llm(config, provider_name, messages)
            try:
                parsed = parse_drill_text(answer)
            except ValueError:
                history.append({"role": "user", "content": line})
                history.append({"role": "assistant", "content": answer})
                print("\n" + answer)
                continue
            history.append({"role": "user", "content": line})
            history.append({"role": "assistant", "content": parsed["reply"]})
            print("\n" + parsed["reply"])
            pending_options = list(parsed.get("options") or [])
            for index, option in enumerate(pending_options, 1):
                mark = "  （建议）" if index == 1 and option != "其他" else ""
                print("%s. %s%s" % (index, option, mark))
            if parsed["phase"] == "ready":
                drilling = False
                locked_brief = parsed["brief"]
                print("\n已锁定，接下来按载具速度片技能写提示词。\n" + locked_brief)
            continue
        skill = skill_by_name(skills, CAR_SKILL)
        messages = [{"role": "system", "content": system_prompt(skill, line, locked_brief)}] + history
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
