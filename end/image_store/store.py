"""Local image files, plus Tencent COS or Aliyun OSS when remote services must fetch the still."""

import base64
import email.utils
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_IMAGE_SUFFIX = re.compile(r"\.(?:png|jpe?g|webp)\Z", re.IGNORECASE)
_VIDEO_SUFFIX = re.compile(r"\.(?:mp4|webm|mov)\Z", re.IGNORECASE)
_MAX_STEM = 64


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

    def refresh_url(self, url):
        """Return an address a browser or remote worker can open now."""
        return str(url or "").strip()

    def persistent_url(self, url):
        """Stable address to remember. Access-only query strings are removed by stores that need it."""
        return str(url or "").strip()

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

    def access_image(self, project_id, filename, source=None):
        """Return a usable image URL if already published. Does not upload."""
        del source
        return self.url(project_id, filename)

    def access_clip(self, project_id, filename, source=None):
        """Return a usable clip URL if already published. Does not upload."""
        return self.ensure_clip(project_id, filename, source=source)

    def access_video(self, project_id, filename, source=None):
        """Return a usable film URL if already published. Does not upload."""
        return self.ensure_video(project_id, filename, source=source)


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
            raise ImageStoreError("未配置 AIVIDEO_PUBLIC_BASE，不能交给出镜能打开的图片地址")
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

    def publish_clip(self, project_id, filename, payload):
        del payload
        return self.ensure_clip(project_id, filename)

    def ensure_clip(self, project_id, filename, source=None):
        del source
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        return "/media/projects/%s/clips/%s" % (project_id, filename)

    def ensure_video(self, project_id, filename, source=None):
        del filename, source
        return "/media/projects/%s/video" % _project_id(project_id)

    def access_clip(self, project_id, filename, source=None):
        return self.ensure_clip(project_id, filename, source=source)

    def access_video(self, project_id, filename, source=None):
        return self.ensure_video(project_id, filename, source=source)

    def _join(self, project_id, filename):
        key = "media/projects/%s/images/%s" % (project_id, filename)
        return _http_object_url(self.public_base(), key)


class OssImageStore(ImageStore):
    """PUT stills and clips to a private Aliyun OSS bucket and issue temporary GET URLs."""

    def __init__(self, bucket=None, domain=None, root=None, access_key_id=None, access_key_secret=None):
        self._bucket = bucket
        self._domain = domain
        self._root = root
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret

    def bucket(self):
        if self._bucket is not None:
            raw = self._bucket
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_OSS_BUCKET") or ""
        name = str(raw).strip()
        if not name:
            raise ImageStoreError("未配置 AIVIDEO_OSS_BUCKET，不能把图片上传到 OSS")
        return name

    def domain(self):
        if self._domain is not None:
            raw = self._domain
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_OSS_DOMAIN") or ""
        text = str(raw).strip().rstrip("/")
        if not text:
            raise ImageStoreError("未配置 AIVIDEO_OSS_DOMAIN，不能给出 OSS 图片地址")
        if "://" not in text:
            text = "https://" + text
        lowered = text.lower()
        if not (lowered.startswith("https://") or lowered.startswith("http://")):
            raise ImageStoreError("AIVIDEO_OSS_DOMAIN 必须是 HTTP(S) 地址")
        return text

    def access_key_id(self):
        if self._access_key_id is not None:
            raw = self._access_key_id
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_OSS_ACCESS_KEY_ID") or ""
        value = str(raw).strip()
        if not value:
            raise ImageStoreError("未配置 AIVIDEO_OSS_ACCESS_KEY_ID，不能把图片上传到 OSS")
        return value

    def access_key_secret(self):
        if self._access_key_secret is not None:
            raw = self._access_key_secret
        else:
            _load_dotenv()
            raw = os.environ.get("AIVIDEO_OSS_ACCESS_KEY_SECRET") or ""
        value = str(raw).strip()
        if not value:
            raise ImageStoreError("未配置 AIVIDEO_OSS_ACCESS_KEY_SECRET，不能把图片上传到 OSS")
        return value

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
        hosted = self.access_image(project_id, filename)
        if hosted:
            return hosted
        raise ImageStoreError("这张图还没有发布到 OSS")

    def publish(self, project_id, filename, payload):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        if not payload:
            raise ImageStoreError("没有图片")
        key = self._object_key("images", project_id, filename)
        url = self._put(key, payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / "images" / filename
        if path.is_file():
            write_publish_marker(path, self.persistent_url(url) or url, payload, "oss")
        return url

    def clip_url(self, project_id, filename):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        return self._access_url(self._object_key("clips", project_id, filename))

    def video_url(self, project_id, filename):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        return self._access_url(self._object_key("video", project_id, filename))

    def publish_clip(self, project_id, filename, payload, before=None):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if not payload:
            raise ImageStoreError("没有这段视频")
        url = self._put(self._object_key("clips", project_id, filename), payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / "clips" / filename
        if path.is_file():
            write_publish_marker(path, self.persistent_url(url) or url, payload, "oss", before=before)
        return url

    def publish_video(self, project_id, filename, payload, before=None):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if not payload:
            raise ImageStoreError("没有这段视频")
        url = self._put(self._object_key("video", project_id, filename), payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / filename
        if path.is_file():
            write_publish_marker(path, self.persistent_url(url) or url, payload, "oss", before=before)
        return url

    def ensure_clip(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="clips",
            publish=self.publish_clip,
            upload=True,
        )

    def ensure_video(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="video",
            publish=self.publish_video,
            upload=True,
        )

    def access_clip(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="clips",
            publish=self.publish_clip,
            upload=False,
        )

    def access_video(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="video",
            publish=self.publish_video,
            upload=False,
        )

    def access_image(self, project_id, filename, source=None):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        path = Path(source) if source else self.root() / project_id / "outputs" / "images" / filename
        if not path.is_file():
            return ""
        expected = self._canonical_url(self._object_key("images", project_id, filename))
        data = read_publish_marker(path, "oss")
        if not publish_marker_matches(data, path, expected, self.persistent_url):
            return ""
        return self._access_url(self._object_key("images", project_id, filename))

    def ensure_remote(self, project_id, filename, current_url="", source=None):
        current = str(current_url or "").strip()
        if current and self._key_from_url(current):
            expected = self._canonical_url(
                self._object_key("images", _project_id(project_id), _filename(filename))
            )
            # The re-sign shortcut must not vouch for stale remote bytes. It
            # only applies to this object's key, and when a local source exists
            # its publish marker has to match; otherwise publish the source again.
            if self.persistent_url(current) == expected:
                if source and Path(source).is_file():
                    path = Path(source)
                    data = read_publish_marker(path, "oss")
                    if publish_marker_matches(data, path, expected, self.persistent_url):
                        return self.refresh_url(current)
                else:
                    return self.refresh_url(current)
        elif current and not self.owns_url(current):
            return current
        if source and Path(source).is_file():
            return self.refresh_url(self.publish(project_id, filename, Path(source).read_bytes()))
        hosted = self.access_image(project_id, filename)
        if hosted:
            return self.refresh_url(hosted)
        path = self.root() / _project_id(project_id) / "outputs" / "images" / _filename(filename)
        if path.is_file():
            return self.refresh_url(self.publish(project_id, filename, path.read_bytes()))
        raise ImageStoreError("本地没有这张图，不能上传到 OSS")

    def refresh_url(self, url):
        key = self._key_from_url(url)
        if not key:
            return str(url or "").strip()
        return self._access_url(key)

    def persistent_url(self, url):
        key = self._key_from_url(url)
        if not key:
            return str(url or "").strip()
        return self._canonical_url(key)

    def owns_url(self, url):
        """True for any address on our own bucket, signed or not.

        The bucket is private: an unsigned canonical address is NOT openable by
        remote workers, so it must be treated as ours and refreshed (presigned)
        before it is handed to a generation API. The base class only knows the
        local /media host and AIVIDEO_PUBLIC_BASE, which would misread an OSS
        canonical URL as a foreign public URL and send it unsigned (HTTP 403).
        """
        if "/media/projects/" in str(url or ""):
            return True
        return bool(self._key_from_url(url))

    def _object_key(self, folder, project_id, filename):
        return "projects/%s/%s/%s" % (project_id, folder, filename)

    def _canonical_url(self, key):
        return _http_object_url(self.domain(), key)

    def _access_url(self, key):
        return _oss_presign_get(
            self.domain(),
            self.bucket(),
            key,
            self.access_key_id(),
            self.access_key_secret(),
        )

    def _key_from_url(self, url):
        text = str(url or "").strip()
        if not text:
            return ""
        parsed = urllib.parse.urlparse(text)
        domain = urllib.parse.urlparse(self.domain())
        if parsed.scheme.lower() not in ("http", "https"):
            return ""
        if parsed.netloc.lower() != domain.netloc.lower():
            return ""
        return urllib.parse.unquote(parsed.path.lstrip("/"))

    def _put(self, key, payload, content_type):
        bucket = self.bucket()
        domain = self.domain()
        access_key_id = self.access_key_id()
        access_key_secret = self.access_key_secret()
        _put_oss(
            domain,
            bucket,
            key,
            payload,
            content_type,
            access_key_id,
            access_key_secret,
        )
        return self._canonical_url(key)

    def _ensure_video_object(self, project_id, filename, source, folder, publish, upload=True):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if source:
            path = Path(source)
        elif folder == "clips":
            path = self.root() / project_id / "outputs" / "clips" / filename
        else:
            path = self.root() / project_id / "outputs" / filename
        if not path.is_file():
            if upload:
                raise ImageStoreError("本地没有这段视频，不能上传到 OSS")
            return ""
        key = self._object_key(folder, project_id, filename)
        expected = self._canonical_url(key)
        data = read_publish_marker(path, "oss")
        if publish_marker_matches(data, path, expected, self.persistent_url, verify_sha256=True):
            return self._access_url(key)
        if not upload:
            return ""
        before = path.stat()
        if before.st_size <= 0:
            raise ImageStoreError("没有这段视频")
        # Stream the file instead of read_bytes: a film must not sit whole in
        # memory. The marker is guarded by the pre-upload stat, so a rewrite
        # during the upload does not get a "published" voucher.
        with path.open("rb") as handle:
            url = publish(project_id, filename, handle, before=before)
            write_publish_marker(path, self.persistent_url(url) or expected, handle, "oss", before=before)
        return self.refresh_url(url) or self._access_url(key)


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
        hosted = self.access_image(project_id, filename)
        if hosted:
            return hosted
        raise ImageStoreError("这张图还没有发布到 COS")

    def publish(self, project_id, filename, payload):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        if not payload:
            raise ImageStoreError("没有图片")
        key = "projects/%s/images/%s" % (project_id, filename)
        bucket = self.bucket()
        domain = self.domain()
        url = _put_cos(domain, bucket, key, payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / "images" / filename
        if path.is_file():
            write_publish_marker(path, url, payload, "cos")
        return url

    def clip_url(self, project_id, filename):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        return _http_object_url(self.domain(), "projects/%s/clips/%s" % (project_id, filename))

    def video_url(self, project_id, filename):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        return _http_object_url(self.domain(), "projects/%s/video/%s" % (project_id, filename))

    def publish_clip(self, project_id, filename, payload):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if not payload:
            raise ImageStoreError("没有这段视频")
        key = "projects/%s/clips/%s" % (project_id, filename)
        bucket = self.bucket()
        domain = self.domain()
        url = _put_cos(domain, bucket, key, payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / "clips" / filename
        if path.is_file():
            write_publish_marker(path, url, payload, "cos")
        return url

    def publish_video(self, project_id, filename, payload):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if not payload:
            raise ImageStoreError("没有这段视频")
        key = "projects/%s/video/%s" % (project_id, filename)
        bucket = self.bucket()
        domain = self.domain()
        url = _put_cos(domain, bucket, key, payload, _content_type(filename))
        path = self.root() / project_id / "outputs" / filename
        if path.is_file():
            write_publish_marker(path, url, payload, "cos")
        return url

    def ensure_clip(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="clips",
            publish=self.publish_clip,
            url_for=self.clip_url,
            upload=True,
        )

    def ensure_video(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="video",
            publish=self.publish_video,
            url_for=self.video_url,
            upload=True,
        )

    def access_clip(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="clips",
            publish=self.publish_clip,
            url_for=self.clip_url,
            upload=False,
        )

    def access_video(self, project_id, filename, source=None):
        return self._ensure_video_object(
            project_id,
            filename,
            source=source,
            folder="video",
            publish=self.publish_video,
            url_for=self.video_url,
            upload=False,
        )

    def access_image(self, project_id, filename, source=None):
        project_id = _project_id(project_id)
        filename = _filename(filename)
        path = Path(source) if source else self.root() / project_id / "outputs" / "images" / filename
        if not path.is_file():
            return ""
        expected = "projects/%s/images/%s" % (project_id, filename)
        expected_url = _http_object_url(self.domain(), expected)
        data = read_publish_marker(path, "cos")
        if not publish_marker_matches(data, path, expected_url):
            return ""
        return expected_url

    def ensure_remote(self, project_id, filename, current_url="", source=None):
        current = str(current_url or "").strip()
        if current and not self.owns_url(current):
            return current
        if current and current.startswith("http"):
            expected = _http_object_url(self.domain(), "projects/%s/images/%s" % (_project_id(project_id), _filename(filename)))
            if current == expected or current.startswith(expected + "?"):
                return current
        if source and Path(source).is_file():
            return self.publish(project_id, filename, Path(source).read_bytes())
        hosted = self.access_image(project_id, filename)
        if hosted:
            return hosted
        path = self.root() / _project_id(project_id) / "outputs" / "images" / _filename(filename)
        if path.is_file():
            return self.publish(project_id, filename, path.read_bytes())
        raise ImageStoreError("本地没有这张图，不能上传到 COS")

    def _ensure_video_object(self, project_id, filename, source, folder, publish, url_for, upload=True):
        project_id = _project_id(project_id)
        filename = _clip_filename(filename)
        if source:
            path = Path(source)
        elif folder == "clips":
            path = self.root() / project_id / "outputs" / "clips" / filename
        else:
            path = self.root() / project_id / "outputs" / filename
        if not path.is_file():
            if upload:
                raise ImageStoreError("本地没有这段视频，不能上传到 COS")
            return ""
        expected = url_for(project_id, filename)
        data = read_publish_marker(path, "cos")
        if publish_marker_matches(data, path, expected):
            return expected
        if not upload:
            return ""
        payload = path.read_bytes()
        url = publish(project_id, filename, payload)
        write_publish_marker(path, url, payload, "cos")
        return url



def _mtime_ns(stat):
    return int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))


def _publish_marker_path(path, kind):
    suffix = ".oss.json" if kind == "oss" else ".cos.json"
    return path.with_name(path.name + suffix)


def _hash_chunks(handle, digest):
    """Feed a file object to a hash in chunks. Returns the byte count read."""
    size = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return size


def _payload_sha256(payload):
    if hasattr(payload, "read"):
        payload.seek(0)
        digest = hashlib.sha256()
        _hash_chunks(payload, digest)
        payload.seek(0)
        return digest.hexdigest()
    return hashlib.sha256(payload or b"").hexdigest()


def write_publish_marker(path, url, payload, kind, before=None):
    """Record the published object and the exact local bytes that were sent.

    ``before`` is the stat taken before the payload was read. When the file
    changed during the upload the marker is not written, so a later access
    never vouches for bytes that were not sent. The marker lands atomically.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return
    if before is not None and (
        stat.st_size != before.st_size or _mtime_ns(stat) != _mtime_ns(before)
    ):
        return
    marker = _publish_marker_path(path, kind)
    body = {
        "url": str(url or "").strip(),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "mtime_ns": _mtime_ns(stat),
        "sha256": _payload_sha256(payload),
    }
    temporary = marker.with_name(marker.name + ".part")
    temporary.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    temporary.replace(marker)


def read_publish_marker(path, kind):
    marker = _publish_marker_path(path, kind)
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def publish_marker_matches(data, path, expected_url="", canonical=None, verify_sha256=False):
    """True when the marker still describes this file and published object.

    ``verify_sha256`` re-hashes the file after size/mtime hit, so a rewrite
    that kept size and mtime (cp -p, a restore) still invalidates the marker.
    """
    if not data:
        return False
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return False
    if int(data.get("size") or -1) != stat.st_size:
        return False
    if data.get("mtime_ns") is not None:
        if int(data.get("mtime_ns") or -1) != _mtime_ns(stat):
            return False
    elif int(data.get("mtime") or -1) != int(stat.st_mtime):
        return False
    if verify_sha256 and data.get("sha256"):
        try:
            with path.open("rb") as handle:
                digest = hashlib.sha256()
                _hash_chunks(handle, digest)
        except OSError:
            return False
        if digest.hexdigest() != str(data.get("sha256")):
            return False
    stored = str(data.get("url") or "").strip()
    if expected_url:
        wanted = str(expected_url).strip()
        if canonical:
            if canonical(stored) != canonical(wanted):
                return False
        elif stored and stored != wanted:
            return False
    return True


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
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


_COS_PUT_ATTEMPTS = 4
# Short pauses between a reset, a timeout, or an HTTP-None urlopen error.
_COS_PUT_BACKOFF = (0.4, 0.8, 1.6)


def _cos_put_retryable(error):
    """True for a connection reset, a timeout, a urlopen error, or HTTP None."""
    lowered = str(error or "").lower()
    if "http none" in lowered:
        return True
    needles = (
        "connection reset",
        "reset by peer",
        "timed out",
        "timeout",
        "urlopen error",
        "urlerror",
    )
    return any(token in lowered for token in needles)


def _put_cos(domain, bucket, key, payload, content_type):
    """PUT one object. A transient connection failure is tried a few times."""
    last = None
    for attempt in range(_COS_PUT_ATTEMPTS):
        try:
            return _put_cos_once(domain, bucket, key, payload, content_type)
        except ImageStoreError as error:
            last = error
            if attempt + 1 >= _COS_PUT_ATTEMPTS or not _cos_put_retryable(error):
                raise
            time.sleep(_COS_PUT_BACKOFF[min(attempt, len(_COS_PUT_BACKOFF) - 1)])
    raise last


def _put_cos_once(domain, bucket, key, payload, content_type):
    digest = base64.b64encode(hashlib.md5(payload).digest()).decode("ascii")
    target = _http_object_url(domain, key)
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



def _oss_canonical_resource(bucket, key):
    # OSS signs the raw UTF-8 object name. The request URL is still percent-encoded.
    return "/%s/%s" % (bucket, str(key).lstrip("/"))


def _oss_authorization(access_key_id, access_key_secret, method, content_md5, content_type, date, oss_headers, resource):
    canonical_headers = ""
    if oss_headers:
        items = sorted((str(name).lower(), str(value).strip()) for name, value in oss_headers.items())
        canonical_headers = "".join("%s:%s\n" % (name, value) for name, value in items)
    string_to_sign = "%s\n%s\n%s\n%s\n%s%s" % (
        method,
        content_md5,
        content_type,
        date,
        canonical_headers,
        resource,
    )
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")
    return "OSS %s:%s" % (access_key_id, signature)


def _put_oss(domain, bucket, key, payload, content_type, access_key_id, access_key_secret):
    """PUT one object with an OSS Authorization header. Transient failures are retried."""
    last = None
    for attempt in range(_COS_PUT_ATTEMPTS):
        try:
            return _put_oss_once(
                domain,
                bucket,
                key,
                payload,
                content_type,
                access_key_id,
                access_key_secret,
            )
        except ImageStoreError as error:
            last = error
            if attempt + 1 >= _COS_PUT_ATTEMPTS or not _cos_put_retryable(error):
                raise
            time.sleep(_COS_PUT_BACKOFF[min(attempt, len(_COS_PUT_BACKOFF) - 1)])
    raise last


def _oss_url_expires():
    _load_dotenv()
    raw = os.environ.get("AIVIDEO_OSS_URL_EXPIRES") or ""
    # Default 4h (design D9): presigned GETs cannot be revoked, so keep the
    # window short and resign at play time. Override via env, clamped 60s..7d.
    try:
        value = int(str(raw).strip() or "14400")
    except ValueError:
        value = 14400
    return max(60, min(value, 7 * 24 * 3600))


def _oss_presign_get(domain, bucket, key, access_key_id, access_key_secret, expires=None):
    if expires is None:
        expires = int(time.time()) + _oss_url_expires()
    expires = int(expires)
    resource = _oss_canonical_resource(bucket, key)
    authorization = _oss_authorization(
        access_key_id,
        access_key_secret,
        "GET",
        "",
        "",
        str(expires),
        None,
        resource,
    )
    signature = authorization.split(":", 1)[1]
    query = urllib.parse.urlencode(
        {
            "OSSAccessKeyId": access_key_id,
            "Expires": str(expires),
            "Signature": signature,
        }
    )
    return "%s?%s" % (_http_object_url(domain, key), query)


def _put_oss_once(domain, bucket, key, payload, content_type, access_key_id, access_key_secret):
    stream = hasattr(payload, "read")
    length = None
    if stream:
        # V1 signing carries Content-MD5, so hash the file in a chunked
        # pre-pass, then stream the same bytes as the body. No whole-file read.
        payload.seek(0)
        md5 = hashlib.md5()
        length = _hash_chunks(payload, md5)
        payload.seek(0)
        digest = base64.b64encode(md5.digest()).decode("ascii")
    else:
        digest = base64.b64encode(hashlib.md5(payload).digest()).decode("ascii")
    date = email.utils.formatdate(usegmt=True)
    resource = _oss_canonical_resource(bucket, key)
    authorization = _oss_authorization(
        access_key_id,
        access_key_secret,
        "PUT",
        digest,
        content_type,
        date,
        None,
        resource,
    )
    target = _http_object_url(domain, key)
    request = urllib.request.Request(target, data=payload, method="PUT")
    if stream:
        request.add_header("Content-Length", str(length))
    request.add_header("Content-Type", content_type)
    request.add_header("Content-MD5", digest)
    request.add_header("Date", date)
    request.add_header("Authorization", authorization)
    status = None
    body = ""
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", 200)
            body = response.read(180).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            body = error.read(180).decode("utf-8", "replace")
        except Exception:
            body = ""
    except Exception as error:
        status = None
        body = str(error)[:180]
    if status is None or status >= 300:
        raise ImageStoreError("OSS 上传失败：HTTP %s %s" % (status, body[:180]))
    return target


def _project_id(value):
    text = str(value or "").strip()
    if not _PROJECT_ID.match(text):
        raise ImageStoreError("项目编号无效")
    return text


def _http_object_url(domain, key):
    """Percent-encode the object key so the HTTP request line stays ASCII."""
    quoted = urllib.parse.quote(str(key).lstrip("/"), safe="/")
    return "%s/%s" % (str(domain).rstrip("/"), quoted)


def _safe_stem(text):
    """One path segment. Live asset ids include 图1 and 图1_3."""
    if not text or len(text) > _MAX_STEM:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return False
    if text != Path(text).name or text in (".", ".."):
        return False
    if not text[0].isalnum():
        return False
    return all(ch.isalnum() or ch in "-_" for ch in text[1:])


def _image_id(value):
    text = str(value or "").strip()
    if not _safe_stem(text):
        raise ImageStoreError("图片编号无效")
    return text


def _filename(value):
    text = str(value or "").strip()
    suffix = Path(text).suffix
    if (
        text != Path(text).name
        or not _IMAGE_SUFFIX.fullmatch(suffix)
        or not _safe_stem(Path(text).stem)
    ):
        raise ImageStoreError("图片文件名无效")
    return text


def _clip_filename(value):
    text = str(value or "").strip()
    suffix = Path(text).suffix
    if (
        text != Path(text).name
        or not _VIDEO_SUFFIX.fullmatch(suffix)
        or not _safe_stem(Path(text).stem)
    ):
        raise ImageStoreError("视频文件名无效")
    return text


def valid_image_filename(value):
    """True when this still name can be published, including 图1.png."""
    try:
        _filename(value)
    except ImageStoreError:
        return False
    return True


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
