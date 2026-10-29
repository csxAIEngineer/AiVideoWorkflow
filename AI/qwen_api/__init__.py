"""Qwen text, stills, video, speech, and image reading.

Test uses QWEN_API_KEY on https://maas.qianwenaiapi.com. Token Plan uses
TOKE_PLAN_QWEN_API_KEY on https://token-plan.maas.qianwenaiapi.com. Choose
with QWEN_ENV or data/qwen_env. QWEN_BASE_URL still overrides when neither
of those is set, and that custom host keeps QWEN_API_KEY. Video can instead
use a local MiniMax-H3 server; see AI.qwen_api.video.
"""

from AI.qwen_api.client import DEFAULT_BASE_URL, QwenClient, QwenError, load_dotenv

__all__ = [
    "DEFAULT_BASE_URL",
    "QwenClient",
    "QwenError",
    "load_dotenv",
]
