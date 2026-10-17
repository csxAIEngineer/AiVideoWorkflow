"""iCreat 图片客户端。GPT Image 2 走异步两步：提交、查结果。

地址：https://api.icreat.ai
鉴权：Authorization: Bearer <ICREAT_API_KEY>
模型：openai/gpt-image-2/text-to-image

提交：POST /v1/task/submit/openai/gpt-image-2/text-to-image
查结果：POST /v1/task/result ，Body 只有 task_id

请求体是扁平字段。必填 prompt、size、quality。有参考图时把公网 URL 放进 image（数组），没有就不要传。
处理中 result 为 []。SUCCEEDED 时 result 为 [{type, url, download_url}]。
状态：SUBMITTED、IN_PROGRESS、SUCCEEDED、FAILED，查结果时也可能是 NOT_FOUND。
默认 size=1024x1024、quality=high，可用 ICREAT_IMAGE_SIZE / ICREAT_IMAGE_QUALITY 覆盖。尺寸必须是接口允许的值。
"""

from .client import (
    BASE_URL,
    ICreatClient,
    ICreatError,
    ImageTask,
    load_dotenv,
)

__all__ = [
    "BASE_URL",
    "ICreatClient",
    "ICreatError",
    "ImageTask",
    "load_dotenv",
]
