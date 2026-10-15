"""Local image store writes png files and builds the public media URL."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_store import CosImageStore, ImageStoreError, LocalImageStore, OssImageStore, get_image_store
from image_store import store as store_module


class ImageStoreTest(unittest.TestCase):
    def test_save_writes_png_and_url_only_builds_the_address(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = LocalImageStore(base="http://43.132.225.71:8787/", root=root)
        payload = b"\x89PNG-still"
        url = store.save("fffdd6d6b188", "U1", payload)
        path = root / "fffdd6d6b188" / "outputs" / "images" / "U1.png"
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(url, "http://43.132.225.71:8787/media/projects/fffdd6d6b188/images/U1.png")
        os.utime(path, (1700000000, 1700000000))
        again = store.url("fffdd6d6b188", "U1.png")
        self.assertEqual(again, url)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(path.stat().st_mtime, 1700000000)
        self.assertFalse((path.parent / "U1.png.part").exists())

    def test_missing_base_is_an_error(self):
        store = LocalImageStore(base="", root=Path("."))
        with self.assertRaises(ImageStoreError) as raised:
            store.url("fffdd6d6b188", "U1.png")
        self.assertIn("AIVIDEO_PUBLIC_BASE", str(raised.exception))
        saved = os.environ.pop("AIVIDEO_PUBLIC_BASE", None)
        saved_kind = os.environ.get("AIVIDEO_IMAGE_STORE")
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        try:
            with patch.object(store_module, "_load_dotenv", lambda: None):
                with self.assertRaises(ImageStoreError) as raised:
                    get_image_store().save("fffdd6d6b188", "U1", b"\x89PNG")
        finally:
            if saved is None:
                os.environ.pop("AIVIDEO_PUBLIC_BASE", None)
            else:
                os.environ["AIVIDEO_PUBLIC_BASE"] = saved
            if saved_kind is None:
                os.environ.pop("AIVIDEO_IMAGE_STORE", None)
            else:
                os.environ["AIVIDEO_IMAGE_STORE"] = saved_kind
        self.assertIn("AIVIDEO_PUBLIC_BASE", str(raised.exception))

    def test_base_must_be_http(self):
        store = LocalImageStore(base="ftp://files.example/stills", root=Path("."))
        with self.assertRaises(ImageStoreError) as raised:
            store.url("fffdd6d6b188", "U1.png")
        self.assertIn("HTTP(S)", str(raised.exception))

    def test_oss_is_not_connected(self):
        store = get_image_store("oss")
        self.assertIsInstance(store, OssImageStore)
        with self.assertRaises(ImageStoreError) as raised:
            store.save("fffdd6d6b188", "U1", b"\x89PNG")
        self.assertIn("尚未接入", str(raised.exception))
        with self.assertRaises(ImageStoreError) as raised:
            store.url("fffdd6d6b188", "U1.png")
        self.assertIn("尚未接入", str(raised.exception))

    def test_default_store_is_local(self):
        saved = os.environ.pop("AIVIDEO_IMAGE_STORE", None)
        try:
            with patch.object(store_module, "_load_dotenv", lambda: None):
                store = get_image_store()
        finally:
            if saved is not None:
                os.environ["AIVIDEO_IMAGE_STORE"] = saved
        self.assertIsInstance(store, LocalImageStore)

    def test_cos_put_uses_bucket_domain_and_returns_that_url(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = CosImageStore(
            bucket="demo-1250000000",
            domain="https://demo-1250000000.cos.ap-guangzhou.myqcloud.com/",
            root=root,
        )
        calls = []

        class _Response(object):
            status = 200

            def read(self, _limit=0):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake_open(request, timeout=None):
            calls.append((request, timeout))
            return _Response()

        payload = b"\x89PNG-still"
        with patch("image_store.store.urllib.request.urlopen", fake_open):
            url = store.save("fffdd6d6b188", "U1", payload)
        path = root / "fffdd6d6b188" / "outputs" / "images" / "U1.png"
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(
            url,
            "https://demo-1250000000.cos.ap-guangzhou.myqcloud.com/projects/fffdd6d6b188/images/U1.png",
        )
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.data, payload)
        self.assertEqual(request.get_header("Content-type"), "image/png")
        self.assertTrue(request.get_header("Content-md5"))
        self.assertTrue(request.get_header("Date"))
        self.assertFalse(request.has_header("Authorization"))
        self.assertGreater(timeout, 0)
        with patch("image_store.store.urllib.request.urlopen", fake_open):
            again = store.ensure_remote(
                "fffdd6d6b188",
                "U1.png",
                "http://43.132.225.71:8787/media/projects/fffdd6d6b188/images/U1.png",
                source=path,
            )
        self.assertEqual(again, url)
        self.assertEqual(len(calls), 2)
        kept = store.ensure_remote("fffdd6d6b188", "U1.png", "https://cdn.example/U1.png", source=path)
        self.assertEqual(kept, "https://cdn.example/U1.png")
        self.assertEqual(len(calls), 2)

    def test_cos_requires_bucket_and_domain(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = CosImageStore(bucket="", domain="", root=root)
        with self.assertRaises(ImageStoreError) as raised:
            store.save("fffdd6d6b188", "U1", b"\x89PNG")
        self.assertIn("AIVIDEO_COS_BUCKET", str(raised.exception))
        store = CosImageStore(bucket="demo-1250000000", domain="", root=root)
        with self.assertRaises(ImageStoreError) as raised:
            store.publish("fffdd6d6b188", "U1.png", b"\x89PNG")
        self.assertIn("AIVIDEO_COS_DOMAIN", str(raised.exception))

    def test_cos_http_error_is_reported(self):
        store = CosImageStore(
            bucket="demo-1250000000",
            domain="https://demo-1250000000.cos.ap-guangzhou.myqcloud.com",
            root=Path("."),
        )

        def fake_open(request, timeout=None):
            raise store_module.urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

        with patch("image_store.store.urllib.request.urlopen", fake_open):
            with self.assertRaises(ImageStoreError) as raised:
                store.publish("fffdd6d6b188", "U1.png", b"\x89PNG")
        self.assertIn("403", str(raised.exception))

    def test_rejects_a_path_in_the_id(self):
        store = LocalImageStore(base="http://127.0.0.1:8787", root=Path("."))
        with self.assertRaises(ImageStoreError):
            store.save("../etc", "U1", b"\x89PNG")
        with self.assertRaises(ImageStoreError):
            store.url("fffdd6d6b188", "../U1.png")


if __name__ == "__main__":
    unittest.main()
