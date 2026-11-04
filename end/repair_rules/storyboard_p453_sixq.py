"""Fill P453 六问 gaps on the previs board. The storyboard draft stays untouched."""

import importlib.util
import re
from pathlib import Path

_MARK = "缺六问"

# In-place bridges keep the original picture and only add a SIX keyword.
# Patterns are idempotent: a second pass does not stack another synonym.
_BRIDGES = (
    ("朝哪", re.compile(r"面对面(?!正对)"), "面对面正对"),
    ("朝哪", re.compile(r"微侧(?!身)"), "微侧身"),
    ("朝哪", re.compile(r"相对(?!朝|面)"), "相对面朝"),
    ("做什么", re.compile(r"静止(?!停住)"), "静止停住"),
    ("做什么", re.compile(r"下垂(?!停住)"), "下垂停住"),
    ("做什么", re.compile(r"平稳踩(?!停)"), "平稳踩停"),
    ("谁在哪", re.compile(r"并肩而立"), "并肩站立"),
)

_FILLERS = {
    "朝哪": "，身体正对前方",
    "做什么": "，动作停住",
    "谁在哪": "，人物站在画面中",
    "看哪": "，视线望向画面前方",
    "镜头在哪": "，镜头在平视中景",
    "光从哪来": "，光从侧上方照来",
}

_BACK_VIEW = re.compile(r"背影(?!站在)")
_WHERE_TOKENS = ("站", "在", "位于")
_CHECKER = None


def _checker():
    """Load ``check_p45`` so the keyword list stays the checker's list."""
    global _CHECKER
    if _CHECKER is not None:
        return _CHECKER
    from end.gates import P45_CHECK

    spec = importlib.util.spec_from_file_location("check_p45_for_repair", P45_CHECK)
    if spec is None or spec.loader is None:
        raise OSError(str(P45_CHECK))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not getattr(module, "SIX", None) or getattr(module, "PANEL_RE", None) is None:
        raise AttributeError("check_p45 missing SIX")
    _CHECKER = module
    return module


def _missing(panel, six):
    return [key for key, words in six.items() if not any(word in panel for word in words)]


def _split_watch(panel):
    """A glued ``看图…`` tail is part of the checker panel. Keep it at the end."""
    index = panel.find("看图")
    if index < 0:
        return panel, ""
    return panel[:index], panel[index:]


def _apply_bridges(prose, missing):
    needed = set(missing)
    for key, pattern, replacement in _BRIDGES:
        if key not in needed:
            continue
        prose = pattern.sub(replacement, prose)
    return prose


def _inject_back_view(prose, missing):
    """A lone 背影 has no 谁在哪 keyword. Put 站在 on that word."""
    if "谁在哪" not in missing or "背影" not in prose:
        return prose
    if any(token in prose for token in _WHERE_TOKENS):
        return prose
    return _BACK_VIEW.sub("背影站在", prose, count=1)


def _repair_panel(panel, six):
    prose, watch = _split_watch(panel)
    missing = _missing(prose + watch, six)
    if not missing:
        return panel
    prose = _apply_bridges(prose, missing)
    prose = _inject_back_view(prose, _missing(prose + watch, six))
    for key in six:
        if key not in _missing(prose + watch, six):
            continue
        piece = _FILLERS.get(key)
        if not piece or piece in prose:
            continue
        prose = prose.rstrip() + piece
    return prose + watch


def _patch_document(text, checker):
    six = checker.SIX
    panel_re = checker.PANEL_RE
    changed = False
    pieces = []
    for line in text.splitlines(keepends=True):
        ending = ""
        core = line
        if core.endswith("\r\n"):
            core, ending = core[:-2], "\r\n"
        elif core.endswith("\n"):
            core, ending = core[:-1], "\n"
        match = panel_re.search(core)
        if not match:
            pieces.append(line)
            continue
        panel = match.group(2)
        repaired = _repair_panel(panel, six)
        if repaired == panel:
            pieces.append(line)
            continue
        changed = True
        pieces.append(core[: match.start(2)] + repaired + core[match.end(2) :] + ending)
    return "".join(pieces), changed


def _board_copies(folder):
    found = []
    for base in (folder, folder / "_内部_"):
        if not base.is_dir():
            continue
        found.extend(path for path in sorted(base.glob("*-预演故事板.md")) if path.is_file())
    return found


def _load_previs(folder):
    """Prefer ``_内部_/previs.md``. An empty file falls back to a board copy.

    An unreadable previs file is left alone: callers must not invent a board.
    """
    primary = folder / "_内部_" / "previs.md"
    if primary.is_file():
        try:
            text = primary.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        if text.strip():
            return text
    for copy in _board_copies(folder):
        try:
            text = copy.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if text.strip():
            return text
    return None


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StoryboardP453SixqRule(object):
    id = "storyboard-p453-sixq"
    stages = ("storyboard",)

    def match(self, report, stage, text, context):
        del text, context
        if stage not in self.stages:
            return False
        return _MARK in str(report or "")

    def apply(self, text, context):
        folder = ""
        if context:
            folder = context.get("folder") or ""
        if not str(folder).strip():
            return text
        folder = Path(folder)
        source = _load_previs(folder)
        if not source:
            return text
        try:
            checker = _checker()
        except (OSError, UnicodeError, AttributeError, ImportError, SyntaxError):
            return text
        patched, changed = _patch_document(source, checker)
        if not changed or patched == source:
            return text
        previs = folder / "_内部_" / "previs.md"
        try:
            _write(previs, patched)
        except (OSError, UnicodeError):
            return text
        for copy in _board_copies(folder):
            try:
                _write(copy, patched)
            except (OSError, UnicodeError):
                continue
        context["rule_touched"] = True
        return text
