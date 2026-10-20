# AiVideo

汽车视频的聊天入口。先写一段背景，再用当前模版的意图补充把这段背景扩成 2 到 3 个方向让你选，最后一项是「其他」；选到「就按这个做」之后，才按该模版写出提示词、分镜和生图提示词。写完后仍由本机生图出片：iCreat 生图、CR MiniMax H3 出镜、ffmpeg 拼接，最后用 ffmpeg 把 `字幕.ass` 烧进成片。不再把成片提交到 Comfy Cloud。

页面到成片的路径是 Front → End → AI。Front 和 End、End 和 AI 都只走 JSON-RPC，三个目录互不 import。AI 里面是 LangGraph → Agent → LLM Model：图把这一问交给 Agent，Agent 组好请求，LLM Model 再去调 DeepSeek 或 CR。出片不进这个布局，仍由 End 在本机编排，生图、出镜和声轨的 HTTP 调用发给 AI。模型工人数量用 `AIVIDEO_AGENT_WORKERS`，默认 4。进程重启时，还停在「正在想方向」或「正在写提示词」的项目会重新入队。

## 运行

需要 Python 3.9。在项目根目录：

```bash
cp .env.example .env
```

在 `.env` 里填写 `DEEPSEEK_API_KEY`（写提示词）、`ICREAT_API_KEY`（生图）和 `CR_API_KEY`（出镜），然后：

```bash
python3 -m end.server
```

End 会在本机拉起 AI（`http://127.0.0.1:8791/rpc`）。已经单独启动 AI 时，设置 `AIVIDEO_AI_URL`。不要让 End 再拉起一份时，设置 `AIVIDEO_AI_AUTOSTART=0`。

打开 http://127.0.0.1:8787 。

画幅 9:16。资产和镜头按故事需要增减。单次视频生成是 4 到 15 秒。出片进度写在项目目录的 `run_state.json`。已经落盘的图和视频会跳过。页面上可以暂停、继续，或重置当前失败的那一步。网络中断时，已有远端任务号就继续查状态；还没有任务号就用同一个幂等键再提交。

命令行导演室（同一套入口：先按当前模版做意图补充，确认后用该模版写提示词。DeepSeek 走 CR API）：

```bash
python3 front/studio/chat.py
```

## CR API 模块

`AI/cr_api/` 按 [CR API](https://api.creative-reasoning.com) 的用法写成，只用 Python 标准库。Key 从环境变量或项目根目录的 `.env` 读取，变量名是 `CR_API_KEY`。

文本模型兼容 OpenAI Chat Completions。DeepSeek 用 `deepseek-v4-pro`：

```python
from AI.cr_api import CRClient

client = CRClient()
result = client.chat(
    model="deepseek-v4-pro",
    messages=[{"role": "user", "content": "你好"}],
    max_tokens=2000,
)
print(result.content)
print(result.reasoning_content)
```

GLM 和 DeepSeek 会先思考。思考内容在 `reasoning_content`，并占用 `max_tokens`。不要把这个上限设得小于 2000，也可以不传。流式用 `stream=True`。MiniMax 文本模型流式必须写 `MiniMax-M3`。

可用文本模型：`gpt-5.5`、`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.4-nano`、`glm-5.3`、`glm-5.3-flash`、`glm-5.2`、`MiniMax-M3`、`deepseek-v4-pro`、`deepseek-v4-flash`、`deepseek-v4.1-flash`。

MiniMax H3 视频是异步的。先提交，每 10 秒轮询，完成后再下载。下载地址不需要 Key，拿到后尽快保存。

```python
task = client.submit_video(
    "海边日落，镜头缓慢推进",
    model="minimax-h3-text-to-video",
    duration=6,
    aspect_ratio="16:9",
)
task = client.wait_video(task)
client.download_video(task, "sunset.mp4")
```

另外两个视频模型：

- `minimax-h3-image-to-video`：图片 `role` 用 `first_frame`，尾帧用 `last_frame`
- `minimax-h3-reference-to-video`：`role` 用 `reference_image`、`reference_video` 或 `reference_audio`

同一次提交里，首尾帧和参考素材不能一起出现。

`duration` 是 4 到 15 的整数。文生视频要传比例，可以是 `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9`。图生视频和参考图生视频不要传 `aspect_ratio`，比例跟输入图走；图片必须是 CR 能直接打开的 HTTPS 网址，不要传 `data:` 图片，否则会返回 `invalid_parameter`。不要传 `resolution`，也不要传 `generate_audio: false`。每个任务用不同的 `idempotency_key`；重试同一次提交时沿用同一个值。

## iCreat 生图

`AI/icreat_api/` 按 iCreat 的 GPT Image 2 两步调用写成，只用 Python 标准库。Key 从环境变量或项目根目录的 `.env` 读取，变量名是 `ICREAT_API_KEY`。

提交走 `POST /v1/task/submit/openai/gpt-image-2/text-to-image`，请求体是扁平的 `prompt`、`size`、`quality`。大约每 4 秒 `POST /v1/task/result` 查同一个 `task_id`。`SUCCEEDED` 后从 `result` 里的 `url` 下载。默认 `size=1024x1024`、`quality=high`，可用 `ICREAT_IMAGE_SIZE` 和 `ICREAT_IMAGE_QUALITY` 覆盖。尺寸必须是接口允许的值，例如 `1024x1024`，或 9:16 的 `720x1280`、`1440x2560`、`2160x3840`。没有参考图时不要传 `image`。变体把上一张图的公网 URL 放进 `image`，用来锁定同一张脸。

```python
from AI.icreat_api import ICreatClient

client = ICreatClient()
task = client.submit_image("雨夜车站里的女人，竖屏")
while task.status not in ("SUCCEEDED", "FAILED"):
    task = client.query_result(task.id)
client.download_image(task.url, "hero.png")
```

## 目录

代码只有三个目录，互相不 import：

- `front/`：聊天页面，以及命令行编剧室 `front/studio/chat.py`。页面只请求 End 的 `/rpc`。编剧室只请求 AI 的 `/rpc`
- `end/`：项目接口、任务队列、图片地址和本机出片编排。需要模型、技能或供应商时只发 JSON-RPC
- `AI/`：Agent 布局、技能、读图，以及 CR、DeepSeek、iCreat、MiniMax 客户端。`python3 -m AI.server` 单独听 `8791`

`workflows/` 是可拖进 Comfy Desktop 的示例工作流。`pipeline/` 只留岷江未晚示例 JSON。

技能有四支模版，各自带齐提示词：导演、意图分析、意图补充、成稿、写提示词、生图。`luxury-suv`、`drama-product-skill` 和 `drama-product-skill2` 另有案例。

- `AI/skills/luxury-suv/`：高奢感 SUV
- `AI/skills/apple-product/`：Apple 风产品特写
- `AI/skills/drama-product-skill/`：短剧植入，把一件商品嵌进一场竖屏小戏
- `AI/skills/drama-product-skill2/`：短剧植入 · 导演。导演原文来自 https://github.com/wuwangzhang1216/DirectorSKILL ，commit `c65ae0d14457053efb1e354c7e7f7e120d97fad1`（cinematic-director 2.1.0，MIT）

速度片案例来自 https://github.com/LearnPrompt/awesome-seedance 。写提示词时先选一个案例当锚。
