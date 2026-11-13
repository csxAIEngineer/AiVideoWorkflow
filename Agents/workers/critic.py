"""One critic skill for every stage worker.

The critic's review standard is that stage's machine check. It does not read
the writing pack or the specialist originals. The deterministic checker still
runs after the report. The critic does not rewrite.
"""

import ast
import importlib
import inspect
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SUITE = _ROOT / "AI" / "skills" / "manju-laoli-skill"
_ASSET_SCRIPT = _SUITE / "short-drama-director" / "scripts" / "check_asset_blocks.py"
_MANUAL = _SUITE / "sd-qc-engine" / "references" / "machine-check-manual.md"
_VALIDATOR = _SUITE / "short-drama-director" / "scripts" / "validate_prompt.py"

_PREAMBLE = "\n".join((
    "评判标准只是这一阶段的机检。不要用写作技能原文，也不要看各节点的写作原文。",
    "你只对照下面的机检交报告。不要改稿，不要交一份新稿。",
    "机检脚本会在你之后原样再跑一遍。单独的 WARN 不算失败。FAIL 或 ERROR 必须写成 issue。",
))


def critic_user(task, *sections):
    """User message: the machine-check skill, then the stage's own notes."""
    parts = [critic_reference(task)]
    for section in sections:
        text = str(section or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def critic_reference(task):
    return "机检技能（只读。这是你的评判标准）：\n%s" % machine_check_skill(task)


@lru_cache(maxsize=8)
def machine_check_skill(task):
    loaders = {
        "script": _script_skill,
        "asset": _asset_skill,
        "storyboard": _storyboard_skill,
        "feed": _feed_skill,
    }
    try:
        body = loaders[task]()
    except KeyError:
        raise RuntimeError("没有这个阶段的机检")
    text = str(body or "").strip()
    if not text:
        raise RuntimeError("缺少机检技能")
    return _PREAMBLE + "\n\n" + text


def _script_skill():
    gates = importlib.import_module("end.gates")
    checks = importlib.import_module("Agents.checks")
    return "\n\n".join((
        "来源：Agents/checks.py check_script_draft，以及 end/gates.py 的未核验声称和编造上传编号。",
        "只判空稿、没看见图却写已核验、编造的上传图编号。文风不判。",
        _source(checks.check_script_draft),
        _source(gates.unverified_claim),
        _source(gates.invented_upload_ids),
        _source(gates.invented_upload_claim),
    ))


def _asset_skill():
    doc = _module_docstring(_ASSET_SCRIPT)
    if not doc.strip():
        raise RuntimeError("缺少 check_asset_blocks.py 的机检说明")
    return "\n\n".join((
        "来源：short-drama-director/scripts/check_asset_blocks.py",
        "A7 和 A8 是 WARN，退出码 0 仍然通过。只有 ERROR 才失败。",
        doc.strip(),
    ))


def _storyboard_skill():
    contract = importlib.import_module("end.storyboard_contract")
    checks = importlib.import_module("Agents.checks")
    return "\n\n".join((
        "来源：end/storyboard_contract.py 与 Agents/checks.py check_storyboard_draft。",
        "空稿、缺镜号、镜头不一致停给人。点名的 S01 缺字段退回生成员补那一个字段。",
        str(contract.STORYBOARD_INSTRUCTION or "").strip(),
        _source(contract.needs_human),
        _source(contract.project_shots),
        _source(contract.shots_conflict),
        _source(checks.storyboard_stops_for_person),
        _source(checks.check_storyboard_draft),
    ))


def _feed_skill():
    manual = _MANUAL.read_text(encoding="utf-8")
    runner = importlib.import_module("end.stage_runner")
    gates = importlib.import_module("end.gates")
    checks = importlib.import_module("Agents.checks")
    return "\n\n".join((
        "来源：machine-check-manual.md 的 H1~H12。脚本是 validate_prompt.py，由 check_feed / check_prompt 调用。",
        "路径：%s" % _VALIDATOR.relative_to(_ROOT),
        _slice(manual, "**退出码与交付铁律**", "\n\n"),
        _slice(manual, "## 三、H1~H12", "## 四、"),
        "本分支在 validate_prompt 之后还有两条硬门。超长跨度要拆开，禁止钳成 15 秒，总时长不得短于分镜。",
        _source(runner.feed_duration_failures),
        _doc(runner.patch_h3_voiceover),
        _source(gates.check_feed),
        _source(gates.check_prompt),
        _source(checks.check_feed_draft),
    ))


def _module_docstring(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def _slice(text, start, end):
    found = str(text or "")
    begin = found.find(start)
    if begin < 0:
        raise RuntimeError("机检文档缺少 %s" % start)
    stop = found.find(end, begin + len(start))
    if stop < 0:
        return found[begin:].strip()
    return found[begin:stop].strip()


def _source(obj):
    return inspect.getsource(obj).strip()


def _doc(obj):
    return (inspect.getdoc(obj) or "").strip()
