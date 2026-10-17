"""DeepSeek 官方 API 客户端。OpenAI 兼容 Chat Completions。

地址：https://api.deepseek.com
鉴权：Authorization: Bearer <DEEPSEEK_API_KEY>

文本模型：
- deepseek-v4-pro（网站追问和写剧本用这个）
- deepseek-flash（DeepSeek-V4.1-Flash）

思考内容在 reasoning_content，正文在 content。可传 thinking 开关。
JSON 输出可设 response_format={"type": "json_object"}。
"""

from .client import (
    TEXT_MODELS,
    ChatResult,
    DeepSeekClient,
    DeepSeekError,
    load_dotenv,
)

__all__ = [
    "TEXT_MODELS",
    "ChatResult",
    "DeepSeekClient",
    "DeepSeekError",
    "load_dotenv",
]
