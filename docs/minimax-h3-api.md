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

没有参考素材时不传 `input.media`。调用方传入的 `model` 和 `idempotency_key` 在发出前被丢掉，请求体里的模型名始终是 `MiniMax/MiniMax-H3`。

| 参数 | 实际取值 |
|---|---|
| `resolution` | 固定 `768P` |
| `duration` | 这一镜的秒数，整数，只允许 4–15 |
| `watermark` | 固定 `false` |
| `ratio` | 纯文生时用锁定画幅 `16:9`、`9:16` 或 `21:9`。带图时传入空值，客户端写成 `adaptive`。文档里的比例还有 `4:3`、`1:1`、`3:4`，其它值也会变成 `adaptive` |
| `prompt` | 投喂稿正文。带首帧时再追加「从这张首帧开始」和无字幕约束 |

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

## 本地 MiniMax-H3

页面「出镜」选本地时，上面三步改打 `http://127.0.0.1:8765`，可用 `MINIMAX_H3_BASE_URL` 改。没配 `MINIMAX_H3_API_KEY` 时鉴权是 `Authorization: Bearer local`。

请求体在云端四个字段之外再带：

| 参数 | 取值 |
|---|---|
| `preset` | `fast` |
| `width` / `height` | `256` / `256` |

不传 `frames`。片长仍是 `duration`。`resolution` 仍是 `768P`。
