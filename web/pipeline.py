"""Drill the user until the film is clear, then write it with the short-drama rules."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cr_api import TEXT_MODELS, is_thinking_model

DRILL_SKILL_PATH = ROOT / "skills" / "aivideo-work" / "SKILL.md"

DEFAULT_MODEL = "deepseek-v4-pro"
TEXT_MODEL = DEFAULT_MODEL
# 这两个 DeepSeek 走官方 API。其余文本模型走 CR API。
OFFICIAL_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
GROUP_ORDER = ("deepseek", "gpt", "glm")
GROUP_LABELS = {
    "deepseek": "DeepSeek",
    "gpt": "GPT",
    "glm": "GLM",
}
MODEL_LABELS = {
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.4": "GPT 5.4",
    "gpt-5.4-mini": "GPT 5.4 Mini",
    "gpt-5.4-nano": "GPT 5.4 Nano",
    "glm-5.3": "GLM 5.3",
    "glm-5.3-flash": "GLM 5.3 Flash",
    "glm-5.2": "GLM 5.2",
}

# 确认之后才用这套短规则填 JSON。完整技能仍在 skills/，不整篇塞进请求。
SKILL_PACK = """
这些规则只填进 JSON 字段。script 用剧本格式，storyboard 用分镜格式，image_prompts 和 prompt 用生图规则。整段回复不要在 JSON 外面再写正文。

剧本：标题写 # EP001 集名。场景写 ## EP001-SC001 内或外 · 地点 · 时间/天气。动作用现在时，写看得见的行为。对白写 角色：台词，或 角色（表演提示）：台词。不要用「短语：」开头的动作，以免和台词混在一起。标签只用 [VO]、[OS]、[SFX]、[画面文字]、[连续性]、[转场]。每场要有可见变化。对白是在争取、回避、试探或逼迫，不把已经演出来的事再解释一遍。开场第一拍就是钩子。人物外形和场景一旦写下，后面原样沿用。

资产：换了就不是同一个人、地或物，才新建身份。服装、伤势、天气、光线是变体，depends_on 填定妆 id。姿势、视线、机位不是资产。识别锚点必须能画出来：年龄、发型、服装、体态、固定物件。地点要写地理：内外、入口、区域怎么连、不能移动的锚点、主要材料。氛围词不能代替门窗位置。同一空间不因昼夜或天气再建成一个地点。

生图：prompt 用中文，能直接拿去文生图。顺序是身份锚点、当前变体、构图、光线和材质、背景边界、禁止项。写成实拍照片：自然皮肤纹理，可用光，材质有磨损，不要磨皮，不要塑料皮肤，不要CG。定妆和空镜的 depends_on 留空。空镜默认没有人，写清从哪看向哪、前后景、入口，以及一到三个固定锚点。禁止字幕、水印、logo 和乱码文字。不要占位符。人物按成年人写。不要写儿童、少年、未成年、中小学生，也不要写 18 岁以下的具体年龄。学生、课堂和校服可以保留，年龄锚点写成「二十岁上下」。图像接口遇到未成年人物会直接失败，而且只返回 FAILED、不说明原因。群像里的每一个人都按这条写。

分镜：每镜只有一个职责、一个主要动作。写下来源场景、景别、机位、运镜和理由、起点、动作、终点、声音。不要规定镜头秒数，也不要为了凑时长把动作拉长或在句尾停住。镜头 prompt 用中文，写这一镜要演完的动作，不要只写一张定格。有对白时 dialogue 填剧本原句，并写明谁在画内开口、口型跟这句；无对白时 dialogue 为空字符串，并写明本镜无对白。同一空间的下一镜写清切点动机，光线和服装接上一镜。images 按画面里要保持身份的资产排列。
""".strip()


def load_skill_pack():
    return SKILL_PACK


def model_choices():
    choices = []
    for group in GROUP_ORDER:
        for name in TEXT_MODELS.get(group, ()):
            choices.append({
                "id": name,
                "label": MODEL_LABELS.get(name, name),
                "group": GROUP_LABELS.get(group, group),
            })
    return choices


def normalize_model(model):
    name = str(model or "").strip()
    allowed = {item["id"] for item in model_choices()}
    if name in allowed:
        return name
    return DEFAULT_MODEL


def model_label(model):
    name = normalize_model(model)
    for item in model_choices():
        if item["id"] == name:
            return item["label"]
    return name


def _complete(messages, model, max_tokens, timeout):
    name = normalize_model(model)
    common = {
        "model": name,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "response_format": {"type": "json_object"},
    }
    if name in OFFICIAL_MODELS:
        from deepseek_api import DeepSeekClient, DeepSeekError

        try:
            return DeepSeekClient().chat(
                messages=messages,
                thinking={"type": "disabled"},
                **common
            )
        except DeepSeekError as error:
            raise RuntimeError(str(error))
    from cr_api import CRClient, CRError

    try:
        return CRClient().chat(messages=messages, **common)
    except CRError as error:
        raise RuntimeError(str(error))


def _empty_body_error(model):
    if normalize_model(model) in OFFICIAL_MODELS:
        return "模型没有返回正文。请检查 DEEPSEEK_API_KEY，或把 max_tokens 调大。"
    return "模型没有返回正文。请检查 CR_API_KEY，或把 max_tokens 调大。"


def drill_system_prompt():
    text = DRILL_SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4 :]
    text = text.strip()
    if not text:
        raise RuntimeError("缺少 skills/aivideo-work/SKILL.md")
    return text


def _extract_object(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有返回 JSON")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        raise ValueError("没有返回 JSON")
    if not isinstance(data, dict):
        raise ValueError("没有返回 JSON")
    return data


OTHER_CHOICE = "其他"


def choice_options(raw):
    """Keep one or two concrete futures, then always end with 其他."""
    concrete = []
    if isinstance(raw, list):
        for item in raw:
            text = " ".join(str(item or "").split())
            if not text or text == OTHER_CHOICE or text in concrete:
                continue
            concrete.append(text)
    concrete = concrete[:2]
    if not concrete:
        concrete = ["沿刚才的方向继续"]
    return concrete + [OTHER_CHOICE]


def conversation_brief(messages):
    """Turn the background and the chosen directions into the brief for the writer."""
    user_lines = []
    for item in messages or []:
        if item.get("role") != "user":
            continue
        text = " ".join(str(item.get("content") or "").split())
        if text:
            user_lines.append(text)
    if len(user_lines) < 2:
        return ""
    lines = [
        "用户已经通过多轮选择把这条竖屏短剧定下来了。写的时候保持这些原话，不要改人物关系，也不要换成另一个故事。",
        "背景：" + user_lines[0],
        "选定方向：",
    ]
    for text in user_lines[1:]:
        lines.append("- " + text)
    return "\n".join(lines)


def ready_from_conversation(messages):
    brief = conversation_brief(messages)
    if not brief:
        return None
    return {
        "phase": "ready",
        "reply": "方向已经够了，开始写剧本。",
        "brief": brief,
        "options": [],
    }


def parse_drill_text(text):
    data = _extract_object(text)
    phase = str(data.get("phase") or "").strip().lower()
    reply = str(data.get("reply") or "").strip()
    brief = str(data.get("brief") or "").strip()
    if phase not in ("ask", "ready") or not reply:
        raise ValueError("drill 回复缺少 phase 或 reply")
    if phase != "ready":
        return {
            "phase": "ask",
            "reply": reply,
            "brief": "",
            "options": choice_options(data.get("options")),
        }
    if not brief:
        raise ValueError("确认后的简报是空的")
    return {"phase": "ready", "reply": reply, "brief": brief, "options": []}


def call_drill(messages, debug_dir=None, model=None):
    """One drill-me turn. messages are user/assistant text, not the JSON wrapper."""
    name = normalize_model(model)
    system = drill_system_prompt()
    chat_messages = [{"role": "system", "content": system}]
    for item in messages:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            chat_messages.append({"role": role, "content": content})
    drill_tokens = 8000 if is_thinking_model(name) and name not in OFFICIAL_MODELS else 4000
    result = _complete(chat_messages, name, drill_tokens, 180)
    if debug_dir:
        folder = Path(debug_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "drill_skill.txt").write_text(system, encoding="utf-8")
        (folder / "drill_response.txt").write_text(_reply_text(result), encoding="utf-8")
    try:
        return parse_drill_text(result.content or result.reasoning_content)
    except ValueError:
        reply = _reply_text(result)
        if not reply:
            ready = ready_from_conversation(messages)
            if ready:
                return ready
            raise RuntimeError("模型没有返回问题。")
    repaired = _complete(
        [
            {"role": "system", "content": drill_system_prompt()},
            {"role": "user", "content": "把下面这次追问收成规定的 JSON。\n" + reply[:8000]},
        ],
        name,
        drill_tokens,
        180,
    )
    if debug_dir:
        (Path(debug_dir) / "drill_response_repaired.txt").write_text(
            _reply_text(repaired), encoding="utf-8"
        )
    try:
        return parse_drill_text(repaired.content or repaired.reasoning_content)
    except ValueError:
        ready = ready_from_conversation(messages)
        if ready:
            return ready
        preview = (repaired.content or reply).strip().replace("\n", " ")[:120]
        raise RuntimeError("追问没有返回 JSON。回复开头是：%s" % preview)


JSON_SHAPE = (
    "整段回复只能是一个 JSON 对象，不要 Markdown 围栏，不要在 JSON 外面写剧本。"
    "title 是字符串。script、storyboard、image_prompts 是 Markdown 字符串，不要 HTML。"
    "用 ## 分节，镜头用有序列表。人物外形锁定、场景、关键对白和镜头目的，用 ==重点== 包起来。"
    "assets 数组，每项含 id、title、prompt、depends_on。定妆和空镜的 depends_on 为空字符串。"
    "变体的 depends_on 填定妆资产的 id。最多 8 个资产。"
    "shots 数组，每项含 id、prompt、images、dialogue，不要写 duration。images 是资产 id 列表，顺序就是 Image 1、Image 2。最多 6 个镜头。"
    "dialogue 是这一镜说出口的原句，没有对白则为空字符串，必须能在 script 里逐字找到。"
    "画幅 9:16。script、storyboard、image_prompts、title，以及 assets 和 shots 里的 title、prompt 全部用中文。提示词里禁止字幕、水印和乱码文字。"
    "人物按成年人写。不要写儿童、少年、未成年、中小学生，也不要写 18 岁以下的具体年龄。学生写成「二十岁上下」。"
)


def _extract_json(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek 没有返回 JSON")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        raise ValueError("DeepSeek 没有返回 JSON")
    if not isinstance(data, dict) or not (data.get("script") or data.get("assets")):
        raise ValueError("DeepSeek 没有返回 JSON")
    return data


def _spec_from_result(result):
    for text in (result.content, result.reasoning_content):
        try:
            return _extract_json(text)
        except ValueError:
            continue
    raise ValueError("DeepSeek 没有返回 JSON")


def _reply_text(result):
    return ((result.content or "") + "\n" + (result.reasoning_content or "")).strip()


def build_system_prompt(skill_pack):
    return (
        "你是竖屏短剧编剧室。只根据用户给出的背景或简要创作，人物外形和场景一旦写下就不要改。\n"
        + JSON_SHAPE
        + "\n"
        + skill_pack
        + "\n再次确认：回复从 { 开始，到 } 结束。"
    )


def write_llm_debug(folder, brief, skill_pack, result=None, repaired=None):
    """Persist prompts and raw model text for skill / response inspection."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill_pack.txt").write_text(skill_pack or "", encoding="utf-8")
    (folder / "json_shape.txt").write_text(JSON_SHAPE, encoding="utf-8")
    (folder / "system_prompt.txt").write_text(build_system_prompt(skill_pack), encoding="utf-8")
    (folder / "user_brief.txt").write_text(brief or "", encoding="utf-8")
    if result is not None:
        (folder / "raw_response.txt").write_text(_reply_text(result), encoding="utf-8")
    if repaired is not None:
        (folder / "raw_response_repaired.txt").write_text(_reply_text(repaired), encoding="utf-8")


def call_deepseek(brief, skill_pack, debug_dir=None, model=None):
    name = normalize_model(model)
    system = build_system_prompt(skill_pack)
    result = None
    repaired = None
    try:
        result = _complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": brief},
            ],
            name,
            16000,
            1200,
        )
    except RuntimeError as error:
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack)
        raise error
    try:
        spec = _spec_from_result(result)
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack, result=result)
        return spec
    except ValueError:
        reply = _reply_text(result)
        if not reply:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result)
            raise RuntimeError(_empty_body_error(name))
    try:
        repaired = _complete(
            [
                {"role": "system", "content": "把用户给出的短剧材料收成一个 JSON 对象。" + JSON_SHAPE},
                {"role": "user", "content": reply[:12000]},
            ],
            name,
            16000,
            1200,
        )
    except RuntimeError as error:
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack, result=result)
        raise error
    try:
        spec = _spec_from_result(repaired)
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack, result=result, repaired=repaired)
        return spec
    except ValueError:
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack, result=result, repaired=repaired)
        preview = (repaired.content or reply).strip().replace("\n", " ")[:120]
        raise RuntimeError("模型没有返回 JSON。回复开头是：%s" % preview)


CLOUD_STATUS = {
    "pending": "云端排队中",
    "queued": "云端排队中",
    "allocated": "云端正在分配机器",
    "running": "云端正在出片",
    "executing": "云端正在出片",
    "in_progress": "云端正在出片",
    "unknown": "云端正在出片",
    "completed": "云端已完成，正在下载成片",
}


def cloud_workflow(workflow):
    """Cloud must execute every generation node. The downloadable graph stays muted."""
    graph = json.loads(json.dumps(workflow))
    for node in graph.get("nodes") or []:
        if node.get("type") == "Note":
            continue
        node["mode"] = 0
        title = str(node.get("title") or "")
        for suffix in (" · 已静音", " · 先运行"):
            title = title.replace(suffix, "")
        node["title"] = title
    return graph


def _comfy_bin():
    found = shutil.which("comfy")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "comfy"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("没有找到 comfy 命令。请先安装 comfy-cli，并执行 comfy cloud login。")


def _json_events(text):
    events = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


CLOUD_LOGIN_HINT = (
    "Comfy Cloud 未登录。服务器/Docker 请在 .env 写入 COMFY_API_KEY"
    "（在 https://cloud.comfy.org 创建 API Key），或执行：comfy cloud set-key --key <KEY>"
)


def _cloud_message(text):
    raw = str(text or "").strip()
    lowered = raw.lower()
    if "not signed in" in lowered or "signed_in" in lowered and "false" in lowered:
        return CLOUD_LOGIN_HINT
    return raw[:800]


def _comfy_error(events, fallback):
    for event in events:
        if event.get("ok") is False or event.get("type") in ("error", "execution_error"):
            error = event.get("error") or {}
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or ""
                hint = error.get("hint") or ""
                details = error.get("details") or {}
                detail_bits = []
                for item in details.get("errors") or []:
                    if isinstance(item, dict) and item.get("message"):
                        detail_bits.append(str(item["message"]))
                parts = [part for part in (message, hint, "；".join(detail_bits)) if part]
                message = " — ".join(parts) if parts else ""
            else:
                message = str(error)
            if message:
                return _cloud_message(message)
            if event.get("message"):
                return _cloud_message(event["message"])
    text = str(fallback or "").strip()
    return _cloud_message(text[-800:] or "comfy 命令失败")


def _require_cloud_login(comfy):
    # Headless servers usually use COMFY_API_KEY / COMFY_CLOUD_API_KEY instead of OAuth.
    if os.environ.get("COMFY_API_KEY") or os.environ.get("COMFY_CLOUD_API_KEY"):
        return
    code, output = _run_comfy([comfy, "--json", "cloud", "whoami"], timeout=30)
    events = _json_events(output)
    signed_in = False
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if data.get("signed_in") is True:
            signed_in = True
            break
        if data.get("api_key_source"):
            signed_in = True
            break
    if code != 0 or not signed_in:
        raise RuntimeError(CLOUD_LOGIN_HINT)


def _run_comfy(args, timeout):
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("comfy 命令超过 %s 秒没有返回" % timeout)
    return proc.returncode, output or ""


def _prompt_id(events):
    for event in events:
        if event.get("prompt_id"):
            return str(event["prompt_id"])
        data = event.get("data")
        if isinstance(data, dict) and data.get("prompt_id"):
            return str(data["prompt_id"])
    return ""


def _status_payload(events):
    for event in reversed(events):
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        if isinstance(data, dict) and data.get("status"):
            return data
    return {}


def _pick_video(files):
    paths = []
    for item in files or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(Path(item["path"]))
        elif isinstance(item, str):
            paths.append(Path(item))
    videos = [path for path in paths if path.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv") and path.is_file()]
    if not videos:
        return None
    videos.sort(key=lambda path: path.stat().st_size)
    return videos[-1]


def run_comfy_cloud(workflow_path, output_dir, on_progress=None):
    """Upload the workflow with comfy-cli and download the finished video."""
    comfy = _comfy_bin()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "comfy.log"

    def progress(message):
        if on_progress:
            on_progress(message)

    def note(text):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

    progress("正在检查 Comfy Cloud 登录")
    _require_cloud_login(comfy)

    progress("正在把工作流上传到 Comfy Cloud")
    code, output = _run_comfy(
        [
            comfy,
            "run",
            "--json",
            "--workflow",
            str(workflow_path),
            "--where",
            "cloud",
            "--allow-spend",
            "--no-watch",
            "--no-notify",
        ],
        timeout=180,
    )
    note(output)
    events = _json_events(output)
    prompt_id = _prompt_id(events)
    if code != 0 or not prompt_id:
        raise RuntimeError(_comfy_error(events, output))
    progress("已提交到云端，任务 %s" % prompt_id[:8])

    deadline = time.time() + 3600
    last_status = ""
    while time.time() < deadline:
        code, output = _run_comfy(
            [comfy, "--json", "jobs", "status", prompt_id, "--where", "cloud"],
            timeout=60,
        )
        note(output)
        events = _json_events(output)
        payload = _status_payload(events)
        status = str(payload.get("status") or "")
        if code != 0 and not status:
            raise RuntimeError(_comfy_error(events, output))
        if status and status != last_status:
            last_status = status
            progress(CLOUD_STATUS.get(status, "云端状态：" + status))
        if status == "completed":
            break
        if status in ("error", "cancelled", "canceled", "failed"):
            message = payload.get("error_message") or CLOUD_STATUS.get(status) or status
            raise RuntimeError("云端出片失败：%s" % message)
        time.sleep(8)
    else:
        raise RuntimeError("云端出片超过 60 分钟还没有完成，任务 %s" % prompt_id[:8])

    progress("云端已完成，正在下载成片")
    code, output = _run_comfy(
        [comfy, "--json", "download", prompt_id, "--where", "cloud", "-o", str(output_dir)],
        timeout=600,
    )
    note(output)
    events = _json_events(output)
    if code != 0:
        raise RuntimeError(_comfy_error(events, output))
    files = []
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        if isinstance(data, dict) and data.get("files"):
            files = data["files"]
    video = _pick_video(files)
    if video is None:
        raise RuntimeError("云端任务完成了，但没有下载到视频")
    target = output_dir / video.name
    if video.resolve() != target.resolve():
        target.write_bytes(video.read_bytes())
    return {"prompt_id": prompt_id, "video_file": target.name}
