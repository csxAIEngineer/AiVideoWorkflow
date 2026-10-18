"""Load a team template and assemble its drill prompt and write prompt.

Each template is one directory under skills/. The directory owns the full
prompt set: 导演, 意图分析, 意图补充, 成稿, and the write-time skills.
"""

import json
from pathlib import Path

SKILLS = Path(__file__).resolve().parent

DEFAULT_TEMPLATE = "luxury-suv"

REQUIRED = (
    "label",
    "director",
    "intent_analyze",
    "intent_fill",
    "output",
    "policy",
)

JSON_SHELL = """
整段回复只能是一个 JSON 对象，不要 Markdown 围栏，不要在 JSON 外面写提示词。title 是字符串。script、storyboard、image_prompts 是 Markdown 字符串，不要 HTML。用 ## 分节，镜头用有序列表。assets 数组，每项含 id、title、prompt、depends_on、references、subject、subject_kind、view。subject_kind 只能是 character、prop、location、other。全部图片都属于必须的 Image-Node。上传图只是参考，不是资产。要参考上传图时，references 填上传图 id，不要把上传图 id 写进 depends_on 或镜头 images。资产个数按片子需要，不设上限。shots 数组不设个数上限，每项含 id、duration、prompt、images、dialogue、narration。duration 是 4 到 15 的整数秒，一镜一个机位。images 只列这一镜看得见、且已经写在 Image-Node 里的生成资产 id。分镜不新造图片。dialogue 没有对白则为空字符串。有人声时 dialogue 填这一镜要说的原句，并写明谁在画内开口。narration 是这一镜的画外旁白，没有则为空字符串。用户给出的图旁白必须原句写进使用对应生成资产的镜头。画幅 9:16。script、storyboard、image_prompts、title，以及 assets 和 shots 里的 title、prompt 全部用中文。提示词里禁止字幕、水印和乱码文字。
""".strip()


def _skill_body(path):
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4 :]
    return text.strip()


def _rel(template_id, filename):
    name = str(filename or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise RuntimeError("模版 %s 的文件名不合法：%s" % (template_id, filename))
    return "%s/%s" % (template_id, name)


def _templates():
    found = {}
    for path in sorted(SKILLS.iterdir()):
        manifest = path / "template.json"
        if not path.is_dir() or not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        name = str(data.get("id") or "").strip()
        if name != path.name:
            raise RuntimeError("模版目录和 id 不一致：%s" % path.name)
        for key in REQUIRED:
            if not str(data.get(key) or "").strip():
                raise RuntimeError("模版 %s 缺少 %s" % (name, key))
        skills = data.get("skills") or []
        if not isinstance(skills, list) or not skills:
            raise RuntimeError("模版没有技能：%s" % name)
        found[name] = data
    if DEFAULT_TEMPLATE not in found:
        raise RuntimeError("缺少默认模版 %s" % DEFAULT_TEMPLATE)
    return found


def normalize_template(template):
    name = str(template or "").strip()
    found = _templates()
    if name in found:
        return name
    return DEFAULT_TEMPLATE


def load_template(template=None):
    return _templates()[normalize_template(template)]


def template_choices():
    items = list(_templates().values())
    items.sort(key=lambda item: (item["id"] != DEFAULT_TEMPLATE, item["id"]))
    return [
        {"id": item["id"], "label": item["label"], "policy": item["policy"]}
        for item in items
    ]


def template_label(template=None):
    return load_template(template)["label"]


def template_policy(template=None):
    return load_template(template)["policy"]


def template_files(template=None):
    item = load_template(template)
    name = item["id"]
    files = [
        _rel(name, item["director"]),
        _rel(name, item["intent_fill"]),
        _rel(name, item["intent_analyze"]),
        _rel(name, item["output"]),
    ]
    files.extend(_rel(name, filename) for filename in item.get("skills") or [])
    return files


def _piece(template_id, filename):
    relative = _rel(template_id, filename)
    path = SKILLS / relative
    if not path.is_file():
        raise RuntimeError("缺少 skills/%s" % relative)
    if path.suffix.lower() == ".md":
        return _skill_body(path)
    return path.read_text(encoding="utf-8").strip()


def drill_system_prompt(template=None):
    item = load_template(template)
    name = item["id"]
    parts = [
        _piece(name, item["director"]),
        _piece(name, item["intent_fill"]),
        _piece(name, item["intent_analyze"]),
    ]
    text = "\n\n".join(part for part in parts if part).strip()
    if not text:
        raise RuntimeError("模版 %s 没有追问提示词" % name)
    return text


def load_skill_pack(template=None):
    item = load_template(template)
    name = item["id"]
    chunks = []
    for filename in item.get("skills") or []:
        relative = _rel(name, filename)
        body = _piece(name, filename)
        if not body:
            raise RuntimeError("缺少 skills/%s" % relative)
        chunks.append("# %s\n\n%s" % (relative, body))
    if not chunks:
        raise RuntimeError("模版没有技能：%s" % name)
    return "\n\n".join(chunks)


def json_shape(template=None):
    item = load_template(template)
    output = _piece(item["id"], item["output"])
    return (JSON_SHELL + "\n" + output).strip()


def build_system_prompt(skill_pack, template=None):
    item = load_template(template)
    return (
        _piece(item["id"], item["director"])
        + "\n\n"
        + json_shape(item["id"])
        + "\n\n"
        + (skill_pack or "").strip()
        + "\n再次确认：回复从 { 开始，到 } 结束。"
    )


def brief_lead(template=None):
    return str(load_template(template).get("brief_lead") or "").strip()


def repair_lead(template=None):
    return str(load_template(template).get("repair_lead") or "").strip()


def _fenced(text, label):
    needle = "```%s" % label
    start = text.find(needle)
    if start < 0:
        return ""
    start = text.find("\n", start)
    if start < 0:
        return ""
    end = text.find("```", start + 1)
    if end < 0:
        return ""
    return text[start + 1 : end].strip()


def template_render(template=None):
    """Lines a template's own skills append at render time. Other templates contribute nothing."""
    item = load_template(template)
    name = item["id"]
    found = {
        "image": "",
        "image_from": "",
        "video_lock": "",
        "video_next": "",
        "video_tail": "",
        "video_from": "",
    }
    labels = (
        ("image", "render-image", "image_from"),
        ("video_lock", "render-video-lock", "video_from"),
        ("video_next", "render-video-next", "video_from"),
        ("video_tail", "render-video-tail", "video_from"),
    )
    for filename in item.get("skills") or []:
        relative = _rel(name, filename)
        body = _piece(name, filename)
        for key, label, source in labels:
            line = _fenced(body, label)
            if line and not found[key]:
                found[key] = line
                if not found[source]:
                    found[source] = relative
    return found
