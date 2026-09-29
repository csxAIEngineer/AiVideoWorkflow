"""One user brief becomes one video project: skills, DeepSeek V4 Pro, then a Comfy workflow."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

SKILL_NAMES = (
    "short-drama-write",
    "short-drama-assets",
    "short-drama-image-prompts",
    "short-drama-storyboard",
)
EXTRA = {
    "short-drama-write": "references/screenplay-format.md",
    "short-drama-assets": "references/location-and-view.md",
    "short-drama-image-prompts": "references/location-plate.md",
    "short-drama-storyboard": "references/shot-craft.md",
}


def _frontmatter_name(text, fallback):
    if not text.startswith("---"):
        return fallback
    end = text.find("\n---", 3)
    block = text[3:end] if end > 0 else ""
    for line in block.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"')
    return fallback


def load_skill_pack():
    chunks = []
    by_dir = {}
    for path in SKILLS.rglob("SKILL.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        by_dir[_frontmatter_name(text, path.parent.name)] = path.parent
    for name in SKILL_NAMES:
        folder = by_dir.get(name)
        if folder is None:
            continue
        chunks.append("\n\n# 技能 %s\n\n%s" % (name, (folder / "SKILL.md").read_text(encoding="utf-8")))
        extra = folder / EXTRA[name]
        if extra.is_file():
            chunks.append("\n\n# 附件 %s\n\n%s" % (extra.name, extra.read_text(encoding="utf-8")[:12000]))
    if not chunks:
        raise RuntimeError("没有找到短剧技能包")
    return "".join(chunks)


def _extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek 没有返回 JSON")
    return json.loads(text[start : end + 1])


def call_deepseek(brief, skill_pack):
    from cr_api import CRClient, CRError

    system = (
        "你是竖屏短剧编剧室。下面是短剧剧本、场景资产、生图提示词和分镜技能。"
        "只根据用户给出的背景或简要创作，人物外形和场景一旦写下就不要改。\n"
        "只输出一个 JSON 对象，不要 Markdown 围栏。字段：\n"
        "title 字符串；script 剧本正文；storyboard 分镜正文；image_prompts 给人读的生图提示词正文；\n"
        "assets 数组，每项含 id、title、prompt、depends_on。定妆和空镜的 depends_on 为空字符串。"
        "变体的 depends_on 填定妆资产的 id。最多 8 个资产。\n"
        "shots 数组，每项含 id、duration、prompt、images。duration 为 4 到 15 的整数。images 是资产 id 列表，顺序就是 Image 1、Image 2。最多 6 个镜头。\n"
        "画幅 9:16。提示词里禁止字幕、水印和乱码文字。\n"
        + skill_pack
    )
    try:
        result = CRClient().chat(
            model="deepseek-v4-pro",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": brief},
            ],
            max_tokens=16000,
        )
    except CRError as error:
        raise RuntimeError(str(error))
    if not result.content.strip():
        raise RuntimeError("DeepSeek 没有返回正文。思考模型会占用 max_tokens，请把上限调大。")
    return _extract_json(result.content)


def run_comfy_cloud(workflow_path, output_dir):
    comfy = shutil.which("comfy")
    if not comfy:
        raise RuntimeError("没有找到 comfy 命令。请先安装 comfy-cli，并执行 comfy cloud login。")
    completed = subprocess.run(
        [comfy, "run", "--where", "cloud", "--workflow", str(workflow_path), "--wait"],
        cwd=str(output_dir),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    log = (completed.stdout or "") + (completed.stderr or "")
    (output_dir / "comfy.log").write_text(log, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(log[-1200:] or "comfy run 失败，退出码 %s" % completed.returncode)
    return log[-1200:]
