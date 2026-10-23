"""Public delivery is at most the main document, the board, and the previs board."""

import json
import re
from pathlib import Path

from end.prompt_gate import build_board

INTERNAL = "_内部_"
PREVIS_SUFFIX = "-预演故事板.md"
_TITLE = re.compile(r"[^\w\u4e00-\u9fff-]+")


def safe_title(title):
    cleaned = _TITLE.sub("", str(title or ""))[:40]
    return cleaned or "未命名"


def internal_dir(folder):
    path = Path(folder) / INTERNAL
    path.mkdir(parents=True, exist_ok=True)
    return path


def previs_name(title):
    return "%s%s" % (safe_title(title), PREVIS_SUFFIX)


def compose_main(title, aspect, model, material_mode, image_prompts, feed):
    """Body is asset prompts and storyboard prompts. The first line states the material mode."""
    return "\n".join(
        [
            "素材形态：%s" % (material_mode or "M1"),
            "",
            "# %s" % (str(title or "").strip() or "未命名"),
            "【画幅】：%s        【模型版本】：%s" % (aspect or "未锁定", model or ""),
            "",
            "## P2.5 · 资产出图提示词",
            str(image_prompts or "").strip(),
            "",
            "## P4 · 分镜提示词",
            str(feed or "").strip(),
            "",
        ]
    )


def write_main(folder, text):
    path = Path(folder) / "project.md"
    path.write_text(text, encoding="utf-8")
    return path


def compile_board(markdown_path, html_path):
    return build_board(markdown_path, html_path)


def publish(folder, locks, title, image_prompts, feed, previs_text=""):
    """Write the body, compile the board only when delivery asks for it, and list at most three files."""
    folder = Path(folder)
    locks = locks or {}
    mode = locks.get("material_mode") or "M1"
    markdown = compose_main(
        title,
        locks.get("aspect"),
        locks.get("model"),
        mode,
        image_prompts,
        feed,
    )
    main_path = write_main(folder, markdown)
    delivery = str(locks.get("delivery") or "")
    names = []
    if delivery in ("2", "3"):
        names.append(main_path.name)
    if delivery in ("1", "3"):
        html_path = folder / "board.html"
        compile_board(main_path, html_path)
        names.append(html_path.name)
    elif (folder / "board.html").is_file():
        (folder / "board.html").unlink()
    previs_path = None
    if str(previs_text or "").strip():
        body = previs_text.strip()
        if not body.startswith("素材形态："):
            body = "素材形态：%s\n\n%s" % (mode, body)
        previs_path = folder / previs_name(title)
        previs_path.write_text(body + "\n", encoding="utf-8")
        names.append(previs_path.name)
    names = [name for name in names if INTERNAL not in name][:3]
    manifest = {"files": names}
    (folder / "delivery.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return names


def list_deliverables(folder):
    path = Path(folder) / "delivery.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return []
    return [str(name) for name in files if name and INTERNAL not in str(name)][:3]

