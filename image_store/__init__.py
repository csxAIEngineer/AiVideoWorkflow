"""上传素材的图片存储。本地实现落到 data/web_projects，并拼出 CR 能打开的地址。"""

from image_store.store import (
    ImageStore,
    ImageStoreError,
    LocalImageStore,
    OssImageStore,
    get_image_store,
)

__all__ = [
    "ImageStore",
    "ImageStoreError",
    "LocalImageStore",
    "OssImageStore",
    "get_image_store",
]
