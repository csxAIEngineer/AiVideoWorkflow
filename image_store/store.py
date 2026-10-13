"""Local image files plus a public URL. OSS is only a placeholder."""

import os
import re
from pathlib import Path

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_IMAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(?:png|jpe?g|webp)$", re.IGNORECASE)


class ImageStoreError(RuntimeError):
    pass


class ImageStore(object):
    def save(self, project_id, image_id, png_bytes):
        raise NotImplementedError

    def url(self, project_id, filename):
        raise NotImplementedError


class LocalImageStore(ImageStore):
    """Write png bytes under data/web_projects and build {base}/media/projects/..."""

    def __init__(self, base=None, root=None):
        self._base = base
        self._root = root

    def public_base(self):
        if self._base is not None:
            raw = self._base
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_PUBLIC_BASE") or ""
        base = str(raw).strip().rstrip("/")
        if not base:
            raise ImageStoreError("未配置 AIVIDEO_PUBLIC_BASE，不能给出 CR 能打开的图片地址")
        lowered = base.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ImageStoreError("AIVIDEO_PUBLIC_BASE 必须是 HTTP(S) 地址")
        return base

    def root(self):
        if self._root is not None:
            return Path(self._root)
        from end.db import DATA

        return Path(DATA)

    def save(self, project_id, image_id, png_bytes):
        project_id = _project_id(project_id)
        image_id = _image_id(image_id)
        if not png_bytes:
            raise ImageStoreError("没有图片")
        base = self.public_base()
        filename = image_id + ".png"
        folder = self.root() / project_id / "outputs" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(png_bytes)
        temporary.replace(target)
        return "%s/media/projects/%s/images/%s" % (base, project_id, filename)

    def url(self, project_id, filename):
        """File is already on disk. Build the public address and do not upload again."""
        return self._join(_project_id(project_id), _filename(filename))

    def _join(self, project_id, filename):
        return "%s/media/projects/%s/images/%s" % (self.public_base(), project_id, filename)


class OssImageStore(ImageStore):
    def save(self, project_id, image_id, png_bytes):
        del project_id, image_id, png_bytes
        raise ImageStoreError("OSS 图片存储尚未接入")

    def url(self, project_id, filename):
        del project_id, filename
        raise ImageStoreError("OSS 图片存储尚未接入")


def get_image_store(kind=None):
    if kind is None:
        _load_dotenv()
        kind = os.environ.get("AIVIDEO_IMAGE_STORE") or "local"
    selected = str(kind).strip().lower() or "local"
    if selected == "local":
        return LocalImageStore()
    if selected == "oss":
        return OssImageStore()
    raise ImageStoreError("未知的图片存储：%s" % selected)


def _project_id(value):
    text = str(value or "").strip()
    if not _PROJECT_ID.match(text):
        raise ImageStoreError("项目编号无效")
    return text


def _image_id(value):
    text = str(value or "").strip()
    if not _IMAGE_ID.match(text):
        raise ImageStoreError("图片编号无效")
    return text


def _filename(value):
    text = str(value or "").strip()
    if not _FILENAME.match(text) or text != Path(text).name:
        raise ImageStoreError("图片文件名无效")
    return text


def _load_dotenv():
    root = Path(__file__).resolve().parents[1]
    for candidate in (root / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            _apply_dotenv(candidate)
            return candidate
    return None


def _apply_dotenv(path):
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
