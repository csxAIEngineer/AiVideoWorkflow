"""Local image files, plus Tencent COS when remote services must fetch the still."""

import base64
import email.utils
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
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

    def owns_url(self, url):
        """True when this address is our own media host, which remote workers cannot open."""
        text = str(url or "").strip()
        if "/media/projects/" in text:
            return True
        base = _configured_public_base()
        return bool(base and (text == base or text.startswith(base + "/")))

    def ensure_remote(self, project_id, filename, current_url="", source=None):
        """Keep an address a remote worker can open. Our own media host is published again."""
        current = str(current_url or "").strip()
        if current and not self.owns_url(current):
            return current
        if source and Path(source).is_file():
            return self.publish(project_id, filename, Path(source).read_bytes())
        return self.url(project_id, filename)

    def publish(self, project_id, filename, payload):
        del project_id, filename, payload
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
        self.public_base()
        filename = image_id + ".png"
        folder = self.root() / project_id / "outputs" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(png_bytes)
        temporary.replace(target)
        return self.publish(project_id, filename, png_bytes)

    def publish(self, project_id, filename, payload):
        del payload
        return self._join(_project_id(project_id), _filename(filename))

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


class CosImageStore(ImageStore):
    """PUT stills to a public-read-write Tencent COS bucket and return that URL."""

    def __init__(self, bucket=None, domain=None, root=None):
        self._bucket = bucket
        self._domain = domain
        self._root = root

    def bucket(self):
        if self._bucket is not None:
            raw = self._bucket
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_COS_BUCKET") or ""
        name = str(raw).strip()
        if not name:
            raise ImageStoreError("未配置 AIVIDEO_COS_BUCKET，不能把图片上传到 COS")
        return name

    def domain(self):
        if self._domain is not None:
            raw = self._domain
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_COS_DOMAIN") or ""
        text = str(raw).strip().rstrip("/")
        if not text:
            raise ImageStoreError("未配置 AIVIDEO_COS_DOMAIN，不能给出 COS 图片地址")
        if "://" not in text:
            text = "https://" + text
        lowered = text.lower()
        if not (lowered.startswith("https://") or lowered.startswith("http://")):
            raise ImageStoreError("AIVIDEO_COS_DOMAIN 必须是 HTTP(S) 地址")
        return text

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
        filename = image_id + ".png"
        _write_local(self.root(), project_id, filename, png_bytes)
        return self.publish(project_id, filename, png_bytes)

    def url(self, project_id, filename):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        path = self.root() / project_id / "outputs" / "images" / filename
        if not path.is_file():
            raise ImageStoreError("本地没有这张图，不能上传到 COS")
        return self.publish(project_id, filename, path.read_bytes())

    def publish(self, project_id, filename, payload):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        if not payload:
            raise ImageStoreError("没有图片")
        key = "projects/%s/images/%s" % (project_id, filename)
        bucket = self.bucket()
        domain = self.domain()
        return _put_cos(domain, bucket, key, payload, _content_type(filename))


def get_image_store(kind=None):
    if kind is None:
        _load_dotenv()
        kind = os.environ.get("AIVIDEO_IMAGE_STORE") or "local"
    selected = str(kind).strip().lower() or "local"
    if selected == "local":
        return LocalImageStore()
    if selected == "oss":
        return OssImageStore()
    if selected == "cos":
        return CosImageStore()
    raise ImageStoreError("未知的图片存储：%s" % selected)


def _configured_public_base():
    try:
        return LocalImageStore().public_base()
    except ImageStoreError:
        return ""


def _write_local(root, project_id, filename, payload):
    folder = Path(root) / project_id / "outputs" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target


def _content_type(filename):
    suffix = Path(filename).suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _put_cos(domain, bucket, key, payload, content_type):
    digest = base64.b64encode(hashlib.md5(payload).digest()).decode("ascii")
    target = "%s/%s" % (domain.rstrip("/"), key)
    request = urllib.request.Request(target, data=payload, method="PUT")
    request.add_header("Content-Type", content_type)
    request.add_header("Content-MD5", digest)
    request.add_header("Date", email.utils.formatdate(usegmt=True))
    parsed = urllib.parse.urlparse(target)
    # #region agent log
    try:
        _line = json.dumps({"sessionId": "77976e", "runId": "pre-fix", "hypothesisId": "A", "location": "end/image_store/store.py:_put_cos", "message": "cos put start", "data": {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port, "bucket": bucket, "bytes": len(payload or b""), "content_type": content_type, "signed": bool(request.has_header("Authorization")), "python": ".".join(map(str, __import__("sys").version_info[:3]))}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False)
        with open("/Users/shaneson/AiVideo/.cursor/debug-77976e.log", "a", encoding="utf-8") as _handle:
            _handle.write(_line + "\n")
    except Exception:
        pass
    # #endregion
    status = None
    body = ""
    started = time.monotonic()
    error_kind = ""
    error_errno = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", 200)
            body = response.read(180).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        error_kind = "HTTPError"
        status = error.code
        try:
            body = error.read(180).decode("utf-8", "replace")
        except Exception:
            body = ""
    except Exception as error:
        error_kind = type(error).__name__
        reason = getattr(error, "reason", None)
        error_errno = getattr(reason, "errno", None) or getattr(error, "errno", None)
        status = None
        body = str(error)[:180]
    # #region agent log
    try:
        _line = json.dumps({"sessionId": "77976e", "runId": "pre-fix", "hypothesisId": "B", "location": "end/image_store/store.py:_put_cos", "message": "cos put result", "data": {"error_kind": error_kind, "errno": error_errno, "status": status, "elapsed_ms": int((time.monotonic() - started) * 1000), "body": body[:180]}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False)
        with open("/Users/shaneson/AiVideo/.cursor/debug-77976e.log", "a", encoding="utf-8") as _handle:
            _handle.write(_line + "\n")
    except Exception:
        pass
    # #endregion
    # #region agent log
    try:
        line = json.dumps(
            {
                "sessionId": "caf74c",
                "hypothesisId": "A",
                "location": "image_store/store.py:_put_cos",
                "message": "cos put",
                "data": {
                    "bucket": bucket,
                    "host": domain,
                    "key": key,
                    "status": status,
                    "bytes": len(payload),
                    "content_type": content_type,
                    "body": body[:180],
                },
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with open("/Users/shaneson/AiVideo/.cursor/debug-caf74c.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass
    # #endregion
    if status is None or status >= 300:
        raise ImageStoreError("COS 上传失败：HTTP %s %s" % (status, body[:180]))
    return target


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
    here = Path(__file__).resolve()
    candidates = []
    for parent in here.parents:
        candidates.append(parent / ".env")
        if (parent / ".git").exists():
            break
    candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
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
