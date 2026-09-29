"""CR API 客户端。文本模型走 OpenAI Chat Completions，视频走 MiniMax H3 异步任务。

地址：https://api.creative-reasoning.com
鉴权：Authorization: Bearer <CR_API_KEY>

文本模型：
- GPT：gpt-5.5、gpt-5.4、gpt-5.4-mini、gpt-5.4-nano
- GLM：glm-5.3、glm-5.3-flash、glm-5.2
- MiniMax：MiniMax-M3（流式必须用这个大小写，minimax-m3 不支持流式）
- DeepSeek：deepseek-v4-pro、deepseek-v4-flash、deepseek-v4.1-flash

GLM 和 DeepSeek 会先思考再回答。思考在 reasoning_content，并占用 max_tokens。
不传 max_tokens，或至少设 2000，否则正文会被截断甚至为空。

视频模型：
- minimax-h3-text-to-video
- minimax-h3-image-to-video（首帧 first_frame，可选尾帧 last_frame）
- minimax-h3-reference-to-video（参考图 reference_image）

视频是异步的：提交、每 10 秒轮询、再下载。duration 为 4 到 15。
文生视频比例：16:9、9:16、1:1、4:3、3:4、21:9。图生视频比例跟输入图走。
不要传 resolution，也不要传 generate_audio: false。
"""

from cr_api.client import (
    ASPECT_RATIOS,
    TEXT_MODELS,
    VIDEO_MODELS,
    CRClient,
    CRError,
    ChatResult,
    VideoTask,
    is_thinking_model,
    load_dotenv,
)

__all__ = [
    "ASPECT_RATIOS",
    "TEXT_MODELS",
    "VIDEO_MODELS",
    "CRClient",
    "CRError",
    "ChatResult",
    "VideoTask",
    "is_thinking_model",
    "load_dotenv",
]
