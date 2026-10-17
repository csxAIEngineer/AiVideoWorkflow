#!/usr/bin/env python3
"""Multi-turn room. Drill first, then write with the selected template."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG_PATH = ROOT / "studio" / "config.json"
SESSIONS = ROOT / "studio" / "sessions"

from skills.registry import (
    DEFAULT_TEMPLATE,
    build_system_prompt,
    drill_system_prompt,
    load_skill_pack,
    normalize_template,
    template_choices,
    template_files,
    template_label,
)


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_prompt(template, locked_brief=""):
    prompt = build_system_prompt(load_skill_pack(template), template)
    if locked_brief:
        prompt += "\n\n已经和用户确认的创作简报，必须原样遵守：\n%s" % locked_brief
    return prompt


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


def print_template_files(template):
    print(template_label(template))
    for relative in template_files(template):
        print("  %s" % relative)


def print_templates(current):
    print("当前模版：%s" % template_label(current))
    for item in template_choices():
        mark = "  （当前）" if item["id"] == normalize_template(current) else ""
        print("%s  %s%s" % (item["id"], item["label"], mark))


def repl(config, provider_name, template):
    from end.pipeline import parse_drill_text

    history = []
    drilling = True
    locked_brief = ""
    pending_options = []
    print(
        "%s。先问清楚你要拍什么。提供者 %s。输入 /help 查看命令。"
        % (template_label(template), provider_name)
    )
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
            print("先问清楚再写。确认之后按当前模版写提示词。")
            print("/template luxury-suv|apple-product")
            print("/skills   /provider qwen|deepseek")
            print("/reset 清空本轮对话    /save 文件路径")
            continue
        if line == "/skills":
            print_template_files(template)
            continue
        if line == "/template" or line.startswith("/template "):
            parts = line.split(None, 1)
            if len(parts) == 1:
                print_templates(template)
                continue
            picked = parts[1].strip()
            known = {item["id"] for item in template_choices()}
            if picked not in known:
                print("没有这个模版。可选：%s" % ", ".join(item["id"] for item in template_choices()))
                continue
            template = picked
            print("已切换到 %s。" % template_label(template))
            continue
        if line.startswith("/skill"):
            print("用 /template 切换模版。")
            print_templates(template)
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
            messages = [{"role": "system", "content": drill_system_prompt(template)}] + history
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
                print("\n已锁定，接下来按当前模版写提示词。\n" + locked_brief)
            continue
        messages = [{"role": "system", "content": write_prompt(template, locked_brief)}] + history
        messages.append({"role": "user", "content": line})
        answer = call_llm(config, provider_name, messages)
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": answer})
        print("\n" + answer)


def main(argv):
    config = load_config()
    template = DEFAULT_TEMPLATE
    if "--list" in argv:
        print_templates(template)
        print_template_files(template)
        return
    provider_name = config["provider"]
    if "--provider" in argv:
        provider_name = argv[argv.index("--provider") + 1]
    repl(config, provider_name, template)


if __name__ == "__main__":
    main(sys.argv[1:])
