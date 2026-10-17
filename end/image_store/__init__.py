"""上传素材的图片存储。本地文件仍留在 data/web_projects，远程地址走腾讯云 COS。"""

from .store import (
    CosImageStore,
    ImageStore,
    ImageStoreError,
    LocalImageStore,
    OssImageStore,
    get_image_store,
)

__all__ = [
    "CosImageStore",
    "ImageStore",
    "ImageStoreError",
    "LocalImageStore",
    "OssImageStore",
    "get_image_store",
]
