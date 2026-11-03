# MiniMax 接口与参数

出镜走 MiniMax，经千问 DashScope 异步接口调用模型 `MiniMax/MiniMax-H3`。代码在 `AI/qwen_api/video.py`，由 `end/render_loop.py` 按镜头提交。

旁白变量名叫 `minimax`，实际请求的是千问 TTS `qwen-audio-3.0-tts-flash`。生图走万相 `wan2.7-image`。这两处都不是 MiniMax。

## 主机与鉴权

主机按页面上的千问环境选择：

| 环境 | 主机 |
|---|---|
| 测试 | `https://maas.qianwenaiapi.com` |
| 生产 | `https://token-plan.maas.qianwenaiapi.com` |

鉴权头：`Authorization: Bearer <当前环境的千问密钥>`。

一次出镜有三步：提交、轮询、下载成片。

## 1. 提交出镜

```
POST /api/v1/services/aigc/video-generation/video-synthesis
```

额外请求头：`X-DashScope-Async: enable`。提交超时 180 秒。

```json
{
  "model": "MiniMax/MiniMax-H3",
  "input": {
    "prompt": "这一镜的投喂正文",
    "media": [{ "type": "reference_image", "url": "https://..." }]
  },
  "parameters": {
    "resolution": "768P",
    "ratio": "16:9",
    "duration": 14,
    "watermark": false
  }
}
```

没有参考素材时不传 `input.media`。调用方传入的 `model` 和 `idempotency_key` 在发出前被丢掉。请求体里的模型名默认是 `MiniMax/MiniMax-H3`。某一套模式的 JSON 写了 `model` 时用那个名字。

| 参数 | 实际取值 |
|---|---|
| `resolution` | 固定 `768P` |
| `duration` | 这一镜的秒数，整数，只允许 4–15 |
| `watermark` | 固定 `false` |
| `ratio` | 纯文生时用锁定画幅 `16:9`、`9:16` 或 `21:9`。带图时传入空值，客户端写成 `adaptive`。文档里的比例还有 `4:3`、`1:1`、`3:4`，其它值也会变成 `adaptive` |
| `prompt` | 投喂稿正文。带首帧时再追加「从这张首帧开始」和无字幕约束 |

`preview` 和 `generate` 各有一套配置，字段相同：`resolution`、`ratio`、`duration`、`watermark`、`preset`、`url`、`model`。默认两套一样。`ratio` 和 `duration` 留空时用这一镜的画幅和秒数。`url` 留空时，云端用当前千问主机，本地用 `MINIMAX_H3_BASE_URL`。`model` 留空时仍是 `MiniMax/MiniMax-H3`。`url` 和 `model` 不进入 `parameters`。出片固定传 `mode=generate`。`video.submit` 不传 `mode`、或传空字符串时，也按 `generate` 组装，请求体与上面的示例一致。调试要换一套时传 `mode=preview`（也认 `预览` / `生成`）。

覆盖写在环境变量里，值是 JSON 对象，只写要改的字段：

```
MINIMAX_VIDEO_PREVIEW_PARAMETERS={"resolution":"768P","watermark":false,"preset":"fast","url":"http://127.0.0.1:8765","model":"MiniMax/MiniMax-H3"}
MINIMAX_VIDEO_GENERATE_PARAMETERS={"duration":8,"preset":"quality","model":"MiniMax/MiniMax-H3"}
```

没写的字段保持默认。`preset` 只在平台声明 `emit_local_defaults` 时进入请求，默认只有 `omniref`。云端示例仍是上面四个字段。提交后的轮询地址跟这次主机走。页面「预览」播放的是已经落盘的成片，不切换这套参数。

平台、模型、参数档是三套登记，提交时各取一个：

| 登记 | 默认 | 环境变量 |
|---|---|---|
| 平台 | 页面选本地时是 `omniref`，否则是 `qwen`。管主机、Bearer、协议、素材规则 | `VIDEO_PLATFORMS` |
| 模型 | `minimax-h3`，请求体 id 为 `MiniMax/MiniMax-H3`。不含参数 | `VIDEO_MODELS` |
| 参数档 | `preview` 与 `generate` 相同 | `VIDEO_PROFILES` |
| 路线 | 把某一档绑到平台和模型。不写平台时用页面上的云端或本地 | `VIDEO_ROUTES` |

`MINIMAX_VIDEO_PREVIEW_PARAMETERS` 和 `MINIMAX_VIDEO_GENERATE_PARAMETERS` 仍可用。参数字段并进对应参数档；`url`、`model`、`platform` 并进这一档的路线。同一条路线里再写 `VIDEO_ROUTES` 时，路线字段以 `VIDEO_ROUTES` 为准。协议默认是 DashScope 异步 video-synthesis。另一种提交协议通过注册适配器接入，不必改提交函数里的分支。

`media[].type` 由这一镜的模式决定。一次请求不能同时带首尾帧和参考素材。

| 模式 | 何时使用 | `media` |
|---|---|---|
| 文生 | 这一镜没有参考图 | 不传 |
| 首帧 | 只有一张用户上传的起始图，且没有结束帧 | 一条 `first_frame` |
| 首尾帧 | 有起始图，并且镜头写了 `end_image` | `first_frame` + `last_frame` |
| 参考 | 其余有图的镜头，以及要接上一镜的镜头 | 最多 9 条 `reference_image`。资产图在前，上一镜尾帧有地址时占最后一条 |

接口还接受 `reference_video` 和 `reference_audio`。当前出片不会提交这两种。上一镜的 mp4 本身也不上传，只上传它的最后一帧。

## 2. 查询任务

```
GET /api/v1/tasks/{task_id}
```

无请求体。End 每 10 秒查一次，单次超时 60 秒。返回里的 `SUCCEEDED` 看成完成，`FAILED` 看成失败。

## 3. 下载成片

任务完成后，对结果里的视频地址发一次普通 `GET`，超时 300 秒，存到 `outputs/clips/<镜头id>.mp4`。这不是 MiniMax 的另一个业务接口。

## 本地 MiniMax-H3（OmniRef Ref2VA）

页面「出镜」选本地时，上面三步改打 `MINIMAX_H3_BASE_URL`。本仓库是 `http://121.196.162.203:8765`，进程是 `local_api --mode ref2va`，权重是 FastH3-Omni-FP8。没设变量时回退到 `http://127.0.0.1:8765`。没配 `MINIMAX_H3_API_KEY` 时鉴权是 `Authorization: Bearer local`。服务不校验这个头，`X-DashScope-Async` 也可以不带。客户端仍会带上。

模型名默认发 `MiniMax/MiniMax-H3`。这一套配置写了 `model` 时改发那个名字。服务端也认短名 `MiniMax-H3`。别的名字返回 400 `model must be MiniMax/MiniMax-H3`。主机同样可以由这一套的 `url` 覆盖；不写时仍是 `MINIMAX_H3_BASE_URL`。

请求体在云端四个字段之外再带 `preset: fast`。`preset` 取当前 `preview` 或 `generate` 那一套，默认是 `fast`。不传 `width`、`height`、`frames`、`steps`。

| 参数 | 取值 |
|---|---|
| `model` | `MiniMax/MiniMax-H3` |
| `input.prompt` | 必填。空字符串返回 400 `input.prompt is required`。服务不解析 `<Picture 1>` 一类标签 |
| `input.media` | 至少一条 `reference_image` 或 `reference_video`。`reference_audio` 可以附在后面。字段名是 `type` 和 `url` |
| `parameters.duration` | 必填，整数 4–15。参考素材齐了但缺这个字段时返回 400 `parameters.duration is required`。超出范围是 `parameters.duration must be an integer from 4 to 15` |
| `parameters.resolution` | 固定 `768P`。这是标签，不是 768p 像素。其它值返回 400 `parameters.resolution must be 768P` |
| `parameters.ratio` | 与云端相同。不传宽高时，16:9 由服务画成 832×480，短边 480 |
| `preset` | `fast`。服务接受，不改步数，不改画布 |
| `width` / `height` | 不传。传入会盖掉默认画布 |

素材规则和云端不同。客户端在发出前按同一套规则拒绝，避免把 Trim 的首尾帧打到 Ref2VA 上。

| 提交 | 结果 |
|---|---|
| 一条或多条 `reference_image` | 接受。`media[].type` 原样为 `reference_image` |
| `reference_video`，可再加 `reference_image` | 接受 |
| `reference_audio` 跟在图或视频后面 | 接受 |
| 只有 `reference_audio` | 400 `reference_audio requires a reference_image or reference_video` |
| 没有 `media` | 400 `OmniRef Ref2VA requires at least one reference_image or reference_video` |
| `first_frame` 或 `last_frame` | 400 `OmniRef Ref2VA does not accept first_frame/last_frame; use reference_image, reference_video, or reference_audio` |

提交返回 `output.task_id` 和 `output.task_status`（`PENDING`）。轮询 `GET /api/v1/tasks/{task_id}`，未知任务 404 `task not found`。`SUCCEEDED` 时 `output.video_url` 是 `http://<host>/api/v1/videos/<task_id>.mp4`，`Content-Type` 为 `video/mp4`。样例任务 `3136f78c-5afa-4ffc-a65a-3129c683d538` 的 `usage.size` 是 `832*480`，`usage.image_count` 是 1。不存在的成片地址返回 404 `video not found`。`FAILED` 时错误在 `output.code` 和 `output.message`。
