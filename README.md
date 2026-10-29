# AiVideo

漫剧的聊天入口。先锁定赛道、素材来源、画幅（16:9 / 9:16 / 21:9）、画风和动作强度。锁定之后按剧本、资产提示词、分镜、投喂稿分阶段写完，跑机检，生成离线看板，然后停在「提示词已就绪」。只有点「出图出片」，或消息里出现「出图」「生成图片」「跑图」「出片」，才会用千问生静帧、MiniMax H3 出镜，再用 ffmpeg 拼接并把 `字幕.ass` 烧进成片。没有锁定画幅时不会默认竖屏。

页面到成片的路径是 Front → End → AI。Front 和 End、End 和 AI 都只走 JSON-RPC，三个目录互不 import。AI 里面是 LangGraph → Agent → 千问：追问走意图图，写稿走文本图。出片不进这个布局，仍由 End 在本机编排，生图、出镜和旁白的 HTTP 调用发给 AI。模型工人数量用 `AIVIDEO_AGENT_WORKERS`，默认 4。进程重启时，还停在「正在想方向」或「正在写提示词」的项目会重新入队。

## 运行

需要 Python 3.9。在项目根目录：

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 里填写测试用的 `QWEN_API_KEY` 和生产用的 `TOKE_PLAN_QWEN_API_KEY`，然后：

```bash
python3 -m end.server
```

End 会在本机拉起 AI（`http://127.0.0.1:8791/rpc`）。已经单独启动 AI 时，设置 `AIVIDEO_AI_URL`。不要让 End 再拉起一份时，设置 `AIVIDEO_AI_AUTOSTART=0`。

打开 http://127.0.0.1:8787 。

画幅跟立项时锁下的 16:9、9:16 或 21:9 走。资产和镜头按故事需要增减。单次视频生成是 4 到 15 秒。写完的项目目录里有 `project.md`、`feed.md`、`board.html` 和 `spec.json`。出片进度写在 `run_state.json`。已经落盘的图和视频会跳过。页面上可以暂停、继续，或重置当前失败的那一步。网络中断时，已有远端任务号就继续查状态；还没有任务号就用同一个幂等键再提交。

命令行导演室（同一套入口：先按当前模版做意图补充，确认后用该模版写提示词。文本模型是 `qwen3.8-flash`）：

```bash
python3 front/studio/chat.py
```

## 千问

`AI/qwen_api/` 按当前千问环境选密钥。测试用 `QWEN_API_KEY`，Token Plan 生产用 `TOKE_PLAN_QWEN_API_KEY`。密钥只从项目根目录的 `.env` 读取。测试环境是 `https://maas.qianwenaiapi.com/compatible-mode/v1`，生产环境是 `https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1`。页面上的「千问」选择写到 `data/qwen_env`。没选过时用 `QWEN_ENV=test` 或 `prod`，两者都没有才用 `QWEN_BASE_URL`。没配当前环境的密钥时，读图写成「尚未读图」，其它调用报未配置对应的变量。

文本和意图用 `qwen3.8-flash`，走兼容模式 `/compatible-mode/v1` 的 Responses API。意图图只调用函数 `submit_intent`。请求带 `enable_thinking: false`，并且不传 `reasoning.effort`。带上 `low` 时仍会返回推理 token。解析仍用 `AI/script.py` 的 `parse_drill_text`。

读图用 `qwen3.8-flash`，请求带 `enable_thinking: false`。生产 Token Plan 不提供 `qwen3.8-omni-flash`。生图用 `wan2.7-image`：没有参考图是文生图，有参考图是同一模型的图生图。Token Plan 没有 `qwen-image-3.0`。万相不接受 `prompt_extend`，无参考图时关闭 `thinking_mode`，`watermark` 关闭。出镜用 `MiniMax/MiniMax-H3`，一个模型覆盖文生、首帧、首尾帧和参考素材，`resolution` 为 `768P`，`watermark` 关闭。`16:9`、`9:16`、`21:9` 原样传入，其它比例用 `adaptive`。云端出镜走当前千问主机。页面选「出镜：本地 MiniMax-H3」时，同一套提交和查询改打本机，见下一节。旁白用 `qwen-audio-3.0-tts-flash`，音色默认 `longanhuan_v3.6`，可用 `QWEN_TTS_VOICE` 覆盖。没有对等的器乐模型，`audio.compose` 直接失败，不再请求别的供应商。

生图和出镜都带 `X-DashScope-Async: enable` 提交，再用 `GET /api/v1/tasks/{task_id}` 查状态。生图把 `PENDING` / `RUNNING` 看成 `IN_PROGRESS`，`SUCCEEDED` 保持不变。出镜把 `SUCCEEDED` 看成 `completed`，`FAILED` 看成 `failed`。轮询节奏仍由 End 掌握。

## 本地 MiniMax-H3

出镜可以不走千问，改打本机上的 MiniMax-H3。文本、生图、读图和旁白仍用上面的千问环境。

页面「出镜」选「本地 MiniMax-H3」，或在没选过页面时设 `MINIMAX_VIDEO=local`。页面选择写到 `data/minimax_video`，优先于环境变量。选「云端 MiniMax」时行为与原来一致：提交到当前千问测试或生产主机上的 `MiniMax/MiniMax-H3`。

本地默认 `http://127.0.0.1:8765`，用 `MINIMAX_H3_BASE_URL` 改。AI 进程向这个地址发和云端相同的两步：

- `POST /api/v1/services/aigc/video-generation/video-synthesis`，头 `X-DashScope-Async: enable`
- `GET /api/v1/tasks/{task_id}`

成片仍是对结果里的视频地址做一次普通下载。时长、画幅和素材角色的规则不变。

本机需要已经有一个实现这两条路径的 H3 服务在听。没配 `MINIMAX_H3_API_KEY` 时请求带 `Authorization: Bearer local`，服务端可以不校验。Docker 里的 AI 容器访问不到宿主机的 `127.0.0.1`，把 `MINIMAX_H3_BASE_URL` 指到容器能访问的地址。

```python
from AI.qwen_api.image import QwenImageClient

client = QwenImageClient()
task = client.submit_image("雨夜车站里的女人", size="9:16")
while task.status not in ("SUCCEEDED", "FAILED"):
    task = client.query_status(task.id)
client.download_image(task.url, "hero.png")
```

## 目录

代码只有三个目录，互相不 import：

- `front/`：聊天页面，以及命令行编剧室 `front/studio/chat.py`。页面只请求 End 的 `/rpc`。编剧室只请求 AI 的 `/rpc`
- `end/`：项目接口、任务队列、图片地址和本机出片编排。需要模型、技能或供应商时只发 JSON-RPC
- `AI/`：Agent 布局、技能、读图，以及千问客户端。`python3 -m AI.server` 单独听 `8791`

`workflows/` 是可拖进 Comfy Desktop 的示例工作流。`pipeline/` 只留岷江未晚示例 JSON。

技能只有一套模版 `short-drama-director`。追问稿在 `AI/skills/short-drama-director/host/`。写稿按阶段读取同目录下的 `sd-*-engine`。上游原文来自 https://github.com/lixiaoxiao9888-create/manju-laoli-skill ，`main` 的 `a06451b07acc2a24a2dc909f88b4469e2495eb32`。页面默认停在提示词和看板，出图是另一步。
