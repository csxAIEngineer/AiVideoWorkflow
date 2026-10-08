# AiVideo

竖屏短剧的聊天入口。先写一段背景，再用 drill-me 把这段背景扩成 2 到 3 个方向让你选，最后一项是「其他」；选到「就按这个做」之后，才用短剧技能写出剧本、分镜和生图提示词。写完后由本机编排出片：iCreat 生图、CR MiniMax H3 出镜、ffmpeg 拼接。不再把成片提交到 Comfy Cloud。

## 运行

需要 Python 3.9。在项目根目录：

```bash
cp .env.example .env
```

在 `.env` 里填写 `DEEPSEEK_API_KEY`（写剧本）、`ICREAT_API_KEY`（生图）和 `CR_API_KEY`（出镜），然后：

```bash
python3 -m web.server
```

打开 http://127.0.0.1:8787 。

画幅 9:16。资产和镜头按故事需要增减。单次视频生成是 4 到 15 秒。出片进度写在项目目录的 `run_state.json`。已经落盘的图和视频会跳过。页面上可以暂停、继续，或重置当前失败的那一步。网络中断时，已有远端任务号就继续查状态；还没有任务号就用同一个幂等键再提交。

命令行编剧室（同一套入口：先 drill-me，确认后再切剧本 / 分镜 / 生图。DeepSeek 走 CR API）：

```bash
python3 studio/chat.py
```

## CR API 模块

`cr_api/` 按 [CR API](https://api.creative-reasoning.com) 的用法写成，只用 Python 标准库。以后的项目把这个目录拷走就能用。Key 从环境变量或项目根目录的 `.env` 读取，变量名是 `CR_API_KEY`。

文本模型兼容 OpenAI Chat Completions。DeepSeek 用 `deepseek-v4-pro`：

```python
from cr_api import CRClient

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
- `minimax-h3-reference-to-video`：`role` 用 `reference_image`

图片可以是网址，也可以是 `data:image/jpeg;base64,...`。`duration` 是 4 到 15 的整数。文生视频比例可以是 `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9`。不要传 `resolution`，也不要传 `generate_audio: false`。每个任务用不同的 `idempotency_key`；重试同一次提交时沿用同一个值。

## iCreat 生图

`icreat_api/` 按 iCreat 的 GPT Image 2 两步调用写成，只用 Python 标准库。Key 从环境变量或项目根目录的 `.env` 读取，变量名是 `ICREAT_API_KEY`。

提交走 `POST /v1/task/submit/openai/gpt-image-2/text-to-image`，请求体是扁平的 `prompt`、`size`、`quality`。大约每 4 秒 `POST /v1/task/result` 查同一个 `task_id`。`SUCCEEDED` 后从 `result` 里的 `url` 下载。默认竖屏 `size=1440x2560`、`quality=high`，可用 `ICREAT_IMAGE_SIZE` 和 `ICREAT_IMAGE_QUALITY` 覆盖。尺寸必须是接口允许的值，例如 9:16 的 `720x1280`、`1440x2560`、`2160x3840`。没有参考图时不要传 `image`。变体把上一张图的公网 URL 放进 `image`，用来锁定同一张脸。

```python
from icreat_api import ICreatClient

client = ICreatClient()
task = client.submit_image("雨夜车站里的女人，竖屏")
while task.status not in ("SUCCEEDED", "FAILED"):
    task = client.query_result(task.id)
client.download_image(task.url, "hero.png")
```

## 目录

- `web/`：聊天页面、项目接口和本机出片编排
- `cr_api/`：CR 的文本和视频客户端
- `icreat_api/`：iCreat GPT Image 2 客户端
- `skills/`：短剧剧本、场景、生图提示词、分镜技能
- `pipeline/`：岷江未晚示例工作流的生成脚本
- `workflows/`：可拖进 Comfy Desktop 的示例工作流
- `studio/`：命令行多轮编剧室

技能来源：

- https://github.com/worldwonderer/drama-skills
- https://github.com/Nianan29/seedance-short-drama
- https://github.com/kianaliang-dev/drama-director-skill
- https://github.com/NBchitu/cinematic-storyboard-generator
