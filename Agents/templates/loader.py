"""Load task skills and disclosed reference files.

Templates are program-invoked: the worker always loads the matching task
skill. There is no default fallback and no model-discovery router. A
missing task or turn fails loudly.

Progressive disclosure is done here, because the model call cannot fetch
files itself. The main skill holds steps and the reference every branch
needs. Sibling files in ``references/`` are inserted only when a pointer
in the skill remains after conditionals have run.
"""

from pathlib import Path

from Agents.templates.filler import apply_conditionals, fill, parse_messages, truthy

TEMPLATES_ROOT = Path(__file__).resolve().parent
TURNS = ("specialist", "director", "critic")
TASKS = ("script", "asset", "storyboard", "seam", "feed")

_INCLUDE = __import__("re").compile(r"\{\{@([A-Za-z][A-Za-z0-9_-]*)\}\}")
_FRONTMATTER = __import__("re").compile(r"\A---\s*\n(.*?)\n---\s*\n", __import__("re").S)


def template_path(task, turn):
    """Return the skill file for this task and turn. No default fallback."""
    turn = str(turn or "").strip()
    if turn not in TURNS:
        raise RuntimeError("没有这个模板回合 %s" % turn)
    task = str(task or "").strip()
    if not task:
        raise RuntimeError("缺少模板任务")
    path = TEMPLATES_ROOT / task / ("%s.md" % turn)
    if not path.is_file():
        raise RuntimeError("缺少模板 %s/%s" % (task, turn))
    return path


def reference_path(task, name):
    path = TEMPLATES_ROOT / str(task or "").strip() / "references" / ("%s.md" % name)
    if not path.is_file():
        raise RuntimeError("缺少参考 %s/references/%s.md" % (task, name))
    return path


def strip_frontmatter(text):
    raw = str(text or "")
    match = _FRONTMATTER.match(raw)
    if not match:
        return raw, {}
    meta = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return raw[match.end():], meta


def load_template(task, turn):
    """Main skill body, without frontmatter and without expanding references."""
    path = template_path(task, turn)
    text, _meta = strip_frontmatter(path.read_text(encoding="utf-8"))
    if not text.strip():
        raise RuntimeError("模板是空的：%s" % path.relative_to(TEMPLATES_ROOT))
    return text


def load_frontmatter(task, turn):
    path = template_path(task, turn)
    _text, meta = strip_frontmatter(path.read_text(encoding="utf-8"))
    return meta


def list_templates():
    """Known task skill files. Missing tasks are not hidden by a default."""
    found = []
    for task in TASKS:
        folder = TEMPLATES_ROOT / task
        if not folder.is_dir():
            continue
        for turn in TURNS:
            path = folder / ("%s.md" % turn)
            if path.is_file():
                found.append("%s/%s.md" % (task, turn))
    return found


def expand_includes(text, task):
    """Replace ``{{@name}}`` with ``references/name.md``. Missing files fail."""

    def replace(match):
        return reference_path(task, match.group(1)).read_text(encoding="utf-8")

    previous = None
    body = str(text or "")
    while previous != body:
        previous = body
        body = _INCLUDE.sub(replace, body)
        if _INCLUDE.search(body) and body == previous:
            break
    leftover = _INCLUDE.search(body)
    if leftover:
        raise RuntimeError("无法展开参考 %s" % leftover.group(1))
    return body


def node_duty(task, skill_path):
    """Distilled duty for one specialist node, if this task defines it."""
    relative = str(skill_path or "").strip()
    if not relative:
        return ""
    path = TEMPLATES_ROOT / str(task or "").strip() / "references" / "node-duties.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    re = __import__("re")
    matches = list(re.finditer(r"^##\s+(\S+)\s*$", text, re.M))
    for index, match in enumerate(matches):
        if match.group(1) != relative:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.end():end].strip()
    return ""


def assemble(task, turn, slots=None):
    """Load the skill, apply conditionals, then include disclosed references."""
    slots = dict(slots or {})
    text = load_template(task, turn)
    # Conditionals first, so a pointer inside {{#branch}} is only expanded
    # when that branch fired. Payload values are not inserted yet.
    opened = apply_conditionals(text, slots)
    opened = expand_includes(opened, task)
    # Includes may themselves contain conditionals.
    opened = apply_conditionals(opened, slots)
    return fill(opened, slots)


def render(task, turn, slots=None):
    return parse_messages(assemble(task, turn, slots))
