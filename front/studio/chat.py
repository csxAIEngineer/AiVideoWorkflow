#!/usr/bin/env python3
"""Multi-turn room. Drill first, then write with the selected template.

Skill files and model calls stay in the AI process. This room only reads lines.
"""

import json
import sys
from pathlib import Path

from ai_rpc import ai

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
RENDER_WORDS = ("出图出片", "生成图片", "跑图", "出片", "出图")
RENDER_BLOCKED = ("不出图", "不要出图", "别出图", "先不出图", "不出片", "不要出片", "别出片")


def wants_render(text):
    body = "".join(str(text or "").split())
    if not body or any(token in body for token in RENDER_BLOCKED):
        return False
    return any(token in body for token in RENDER_WORDS)


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def template_files(template):
    return ai("templates.files", {"template": template})["files"]


def template_label(template):
    return ai("templates.label", {"template": template})["label"]


def print_template_files(template):
    print(template_label(template))
    for relative in template_files(template):
        print("  %s" % relative)


def print_templates(current):
    catalog = ai("catalog")
    print("当前模版：%s" % template_label(current or catalog["default_template"]))
    for item in catalog["templates"]:
        mark = "  （当前）" if item["id"] == current else ""
        print("%s  %s%s" % (item["id"], item["label"], mark))


def known_templates():
    return {item["id"] for item in ai("catalog")["templates"]}


def repl(config, provider_name, template):
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
            print("/template 切换模版。默认是漫剧，出镜锁定万相 3.0。")
            print("锁定之后这里只写提示词和看板。出图出片回到页面按钮。")
            print("/skills   /provider qwen")
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
            if picked not in known_templates():
                print("没有这个模版。可选：%s" % ", ".join(sorted(known_templates())))
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
        provider = config["providers"][provider_name]
        if not drilling and wants_render(line):
            print("出图出片不在这里启动。提示词就绪后，回页面点「出图出片」，或在项目里发送「出图」。")
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
            result = ai(
                "studio.drill",
                {"template": template, "history": history, "line": line, "provider": provider},
                timeout=200,
            )
            if not result.get("parsed"):
                history.append({"role": "user", "content": line})
                history.append({"role": "assistant", "content": result.get("raw") or ""})
                print("\n" + (result.get("raw") or ""))
                continue
            history.append({"role": "user", "content": line})
            history.append({"role": "assistant", "content": result["reply"]})
            print("\n" + result["reply"])
            pending_options = list(result.get("options") or [])
            for index, option in enumerate(pending_options, 1):
                mark = "  （建议）" if index == 1 and option != "其他" else ""
                print("%s. %s%s" % (index, option, mark))
            if result.get("phase") == "ready":
                drilling = False
                locked_brief = result.get("brief") or ""
                print("\n已锁定，接下来按当前模版写提示词。\n" + locked_brief)
            continue
        result = ai(
            "studio.write",
            {
                "template": template,
                "history": history,
                "line": line,
                "provider": provider,
                "locked_brief": locked_brief,
            },
            timeout=240,
        )
        answer = result.get("raw") or ""
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": answer})
        print("\n" + answer)


def main(argv):
    config = load_config()
    template = ai("catalog")["default_template"]
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
