"""Gzip, ETag, content-hash cache, and RPC gzip for public responses."""

import gzip
import hashlib
import http.client
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import end.db as db
import end.server as server
from end.db import project_dir, write_project
from end.server import (
    Handler,
    _CONTENT_HASH_LENGTH,
    _GzipBodyCache,
    _STATIC_GZIP_CACHE,
    _accepts_gzip,
    content_hash_for_bytes,
)
from http.server import ThreadingHTTPServer


def tiny_png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def repeated(text, minimum=800):
    body = text
    while len(body.encode("utf-8")) < minimum:
        body += text
    return body


class AcceptGzipTest(unittest.TestCase):
    def test_missing_and_empty_are_identity(self):
        self.assertFalse(_accepts_gzip(None))
        self.assertFalse(_accepts_gzip(""))
        self.assertFalse(_accepts_gzip("   "))
        self.assertFalse(_accepts_gzip("identity"))
        self.assertFalse(_accepts_gzip("deflate, br"))

    def test_q_zero_and_case_and_wildcard(self):
        self.assertTrue(_accepts_gzip("gzip"))
        self.assertTrue(_accepts_gzip("GZIP"))
        self.assertTrue(_accepts_gzip("deflate, Gzip;q=1.0"))
        self.assertTrue(_accepts_gzip("gzip;q=0.1"))
        self.assertTrue(_accepts_gzip("*"))
        self.assertTrue(_accepts_gzip("*;q=0.5"))
        self.assertTrue(_accepts_gzip("gzip;q=0, *;q=0, gzip"))
        self.assertTrue(_accepts_gzip("*;q=0, gzip;q=0.8"))
        self.assertFalse(_accepts_gzip("gzip;q=0"))
        self.assertFalse(_accepts_gzip("gzip;q=0.0"))
        self.assertFalse(_accepts_gzip("GZIP;q=0"))
        self.assertFalse(_accepts_gzip("identity, gzip;q=0"))
        self.assertFalse(_accepts_gzip("*;q=0"))
        self.assertFalse(_accepts_gzip("gzip;q=0, *;q=1"))
        self.assertFalse(_accepts_gzip("*;q=1, gzip;q=0"))
        self.assertFalse(_accepts_gzip("gzip ; q = 0"))


class StaticCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.static = self.root / "front"
        self.static.mkdir()
        self.index = repeated("<html><body>首页静态页 " + "文案" * 40 + "</body></html>\n")
        self.script = repeated("function hello() { return 'app-script'; }\n", 1200)
        self.css = repeated("body { color: #222; background: #fff; }\n", 600)
        (self.static / "index.html").write_text(self.index, encoding="utf-8")
        (self.static / "app.js").write_text(self.script, encoding="utf-8")
        (self.static / "styles.css").write_text(self.css, encoding="utf-8")
        (self.static / "pixel.png").write_bytes(tiny_png())
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "static.db"
        db._CONN = None
        db.DATA.mkdir()
        self._env = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = "http://203.0.113.10:8787"
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        _STATIC_GZIP_CACHE.clear()
        self._static_patch = patch.object(server, "STATIC", self.static)
        self._static_patch.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)
        self._static_patch.stop()
        _STATIC_GZIP_CACHE.clear()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        if headers is None and isinstance(body, dict):
            headers, body = body, None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            header_map = {key.lower(): value for key, value in response.getheaders()}
            return response.status, header_map, payload
        finally:
            connection.close()

    def rpc(self, method, params=None, headers=None):
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1},
            ensure_ascii=False,
        ).encode("utf-8")
        merged = {"Content-Type": "application/json"}
        if headers:
            merged.update(headers)
        return self.request("POST", "/rpc", payload, merged)

    def test_gzip_roundtrip_and_length_and_vary(self):
        raw = (self.static / "app.js").read_bytes()
        status, headers, body = self.request(
            "GET", "/app.js?v=2", {"Accept-Encoding": "gzip, deflate"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-encoding"), "gzip")
        self.assertEqual(headers.get("vary"), "Accept-Encoding")
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertEqual(gzip.decompress(body), raw)
        self.assertLess(len(body), len(raw))
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(headers.get("etag"), '"%s-gzip"' % digest)

    def test_identity_and_q_zero_stay_uncompressed(self):
        raw = (self.static / "app.js").read_bytes()
        cases = [
            {},
            {"Accept-Encoding": "identity"},
            {"Accept-Encoding": "gzip;q=0"},
            {"Accept-Encoding": "gzip;q=0, *;q=1"},
            {"Accept-Encoding": "*;q=0"},
        ]
        for headers in cases:
            status, response_headers, body = self.request("GET", "/app.js", headers)
            self.assertEqual(status, 200, headers)
            self.assertIsNone(response_headers.get("content-encoding"), headers)
            self.assertEqual(body, raw)
            self.assertEqual(int(response_headers["content-length"]), len(raw))
            self.assertEqual(response_headers.get("vary"), "Accept-Encoding")
            self.assertEqual(response_headers.get("cache-control"), "no-cache")
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(response_headers.get("etag"), '"%s"' % digest)

    def test_etag_304_has_no_body_and_changes_with_file(self):
        status, headers, body = self.request("GET", "/app.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        etag = headers["etag"]
        status, again, empty = self.request(
            "GET",
            "/app.js?v=2",
            {"Accept-Encoding": "gzip", "If-None-Match": etag},
        )
        self.assertEqual(status, 304)
        self.assertEqual(empty, b"")
        self.assertNotIn("content-length", again)
        self.assertEqual(again.get("etag"), etag)
        self.assertEqual(again.get("cache-control"), "no-cache")
        self.assertEqual(again.get("content-encoding"), "gzip")
        self.assertEqual(again.get("vary"), "Accept-Encoding")
        (self.static / "app.js").write_text(self.script + "\n// changed\n", encoding="utf-8")
        status, changed, new_body = self.request(
            "GET",
            "/app.js?v=2",
            {"Accept-Encoding": "gzip", "If-None-Match": etag},
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(changed.get("etag"), etag)
        self.assertEqual(gzip.decompress(new_body), (self.static / "app.js").read_bytes())

    def test_gzip_and_identity_use_distinct_etags(self):
        status, gzip_headers, _ = self.request("GET", "/styles.css", {"Accept-Encoding": "GZIP"})
        status_raw, raw_headers, _ = self.request("GET", "/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(status_raw, 200)
        self.assertNotEqual(gzip_headers.get("etag"), raw_headers.get("etag"))
        status, mismatched, body = self.request(
            "GET",
            "/styles.css",
            {"Accept-Encoding": "gzip", "If-None-Match": raw_headers["etag"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(gzip.decompress(body), (self.static / "styles.css").read_bytes())
        status, still_raw, raw_body = self.request(
            "GET",
            "/styles.css",
            {"If-None-Match": gzip_headers["etag"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(raw_body, (self.static / "styles.css").read_bytes())

    def test_homepage_uses_conservative_cache(self):
        status, headers, body = self.request("GET", "/", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertNotIn("immutable", (headers.get("cache-control") or "").lower())
        self.assertNotIn("max-age=31536000", (headers.get("cache-control") or "").lower())
        self.assertEqual(headers.get("vary"), "Accept-Encoding")
        self.assertEqual(gzip.decompress(body).decode("utf-8"), self.index)
        status, versioned, _ = self.request("GET", "/app.js?v=94", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(versioned.get("cache-control"), "no-cache")
        self.assertNotIn("immutable", (versioned.get("cache-control") or "").lower())

    def test_static_image_is_not_gzipped(self):
        png = (self.static / "pixel.png").read_bytes()
        status, headers, body = self.request("GET", "/pixel.png", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(body, png)
        self.assertIsNone(headers.get("content-encoding"))
        self.assertIsNone(headers.get("vary"))
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertTrue(headers.get("etag"))

    def test_media_and_downloads_are_not_compressed_or_cached(self):
        project_id = "a11c0de00001"
        write_project(
            {
                "id": project_id,
                "brief": "雨夜",
                "title": "雨夜",
                "status": "done",
                "stage": "完成",
                "script": "剧本",
                "messages": [],
                "progress": {},
                "video_file": "film.mp4",
                "created_at": "2026-01-01T00:00:00+08:00",
            }
        )
        folder = project_dir(project_id)
        png = tiny_png()
        image = folder / "outputs" / "images" / "A1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(png)
        video = folder / "outputs" / "film.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"fake-mp4-bytes-not-gzip")
        (folder / "spec.json").write_text('{"title":"雨夜"}', encoding="utf-8")
        status, image_headers, image_body = self.request(
            "GET",
            "/media/projects/%s/images/A1.png" % project_id,
            {"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(image_body, png)
        self.assertIsNone(image_headers.get("content-encoding"))
        self.assertTrue(image_headers.get("etag"))
        self.assertEqual(image_headers.get("accept-ranges"), "bytes")
        self.assertIsNone(image_headers.get("cache-control"))
        self.assertIsNone(image_headers.get("vary"))
        status, video_headers, video_body = self.request(
            "GET",
            "/media/projects/%s/video" % project_id,
            {"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(video_body, b"fake-mp4-bytes-not-gzip")
        self.assertIsNone(video_headers.get("content-encoding"))
        self.assertTrue(video_headers.get("etag"))
        self.assertEqual(video_headers.get("accept-ranges"), "bytes")
        self.assertIsNone(video_headers.get("cache-control"))
        status, spec_headers, _ = self.request(
            "GET",
            "/media/projects/%s/spec" % project_id,
            {"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(spec_headers.get("content-encoding"))
        self.assertIsNone(spec_headers.get("cache-control"))
        self.assertIsNone(spec_headers.get("etag"))
        # RPC gzip is covered separately; media/downloads stay uncompressed.

    def test_gzip_cache_is_keyed_by_content_and_bounded(self):
        raw = (self.static / "app.js").read_bytes()
        self.request("GET", "/app.js?v=1", {"Accept-Encoding": "gzip"})
        self.request("GET", "/app.js?v=2", {"Accept-Encoding": "gzip"})
        self.assertEqual(len(_STATIC_GZIP_CACHE), 1)
        self.assertEqual(_STATIC_GZIP_CACHE.get(hashlib.sha256(raw).hexdigest()), gzip.compress(raw, compresslevel=6))
        limited = _GzipBodyCache(max_entries=2, max_bytes=64 * 1024)
        original = server._STATIC_GZIP_CACHE
        server._STATIC_GZIP_CACHE = limited
        try:
            for index in range(4):
                name = "chunk%s.js" % index
                (self.static / name).write_text(repeated("var n = %s; /* pad */\n" % index, 900), encoding="utf-8")
                status, headers, body = self.request("GET", "/" + name, {"Accept-Encoding": "gzip"})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("content-encoding"), "gzip")
                self.assertEqual(gzip.decompress(body), (self.static / name).read_bytes())
            self.assertLessEqual(len(limited), 2)
            self.assertLessEqual(limited.nbytes, 64 * 1024)
        finally:
            server._STATIC_GZIP_CACHE = original


    def test_rpc_json_is_gzipped_when_accepted_and_smaller(self):
        project_id = "a11c0de00001"
        write_project(
            {
                "id": project_id,
                "brief": "雨夜" * 40,
                "title": "雨夜长标题" * 10,
                "status": "done",
                "stage": "完成",
                "script": "剧本" * 80,
                "messages": [],
                "progress": {"label": "已完成"},
                "created_at": "2026-01-01T00:00:00+08:00",
            }
        )
        status, headers, body = self.rpc(
            "projects.list",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type", "").split(";")[0].strip(), "application/json")
        self.assertEqual(headers.get("vary"), "Accept-Encoding")
        self.assertIsNone(headers.get("etag"))
        self.assertIsNone(headers.get("cache-control"))
        self.assertEqual(headers.get("content-encoding"), "gzip")
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        self.assertIsNone(payload.get("error"))
        self.assertIn("result", payload)
        self.assertEqual(len(payload["result"]), 1)
        self.assertLess(len(body), int(headers["content-length"]) + 1)
        self.assertEqual(int(headers["content-length"]), len(body))

        status, identity, raw = self.rpc("projects.list", headers={"Accept-Encoding": "identity"})
        self.assertEqual(status, 200)
        self.assertIsNone(identity.get("content-encoding"))
        self.assertEqual(identity.get("vary"), "Accept-Encoding")
        self.assertEqual(json.loads(raw.decode("utf-8")), payload)
        self.assertGreater(len(raw), len(body))

        status, qzero, raw_q = self.rpc("projects.list", headers={"Accept-Encoding": "gzip;q=0"})
        self.assertEqual(status, 200)
        self.assertIsNone(qzero.get("content-encoding"))
        self.assertEqual(json.loads(raw_q.decode("utf-8")), payload)

        status, cfg, cfg_body = self.rpc("config.get", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(cfg.get("vary"), "Accept-Encoding")
        if cfg.get("content-encoding") == "gzip":
            config_payload = json.loads(gzip.decompress(cfg_body).decode("utf-8"))
        else:
            config_payload = json.loads(cfg_body.decode("utf-8"))
        self.assertIsNone(config_payload.get("error"))
        self.assertIn("result", config_payload)

    def test_tiny_rpc_stays_uncompressed_if_gzip_is_not_smaller(self):
        # A short parse-error body is under the useful gzip window or does not shrink.
        payload = b"{"
        status, headers, body = self.request(
            "POST",
            "/rpc",
            payload,
            {"Content-Type": "application/json", "Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(headers.get("vary"), "Accept-Encoding")
        parsed = json.loads(body.decode("utf-8"))
        self.assertIn("error", parsed)

    def test_html_rewrites_local_assets_to_content_hash_and_stays_revalidating(self):
        (self.static / "index.html").write_text(
            '<link rel="stylesheet" href="/styles.css?v=59">\n'
            '<script src="/app.js?v=96"></script>\n'
            '<script src="https://cdn.example/app.js?v=1"></script>\n',
            encoding="utf-8",
        )
        app_hash = content_hash_for_bytes((self.static / "app.js").read_bytes())
        css_hash = content_hash_for_bytes((self.static / "styles.css").read_bytes())
        status, headers, body = self.request("GET", "/", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertNotIn("immutable", (headers.get("cache-control") or "").lower())
        html = gzip.decompress(body).decode("utf-8")
        self.assertIn('/styles.css?h=%s' % css_hash, html)
        self.assertIn('/app.js?h=%s' % app_hash, html)
        self.assertNotIn("/app.js?v=96", html)
        self.assertIn("https://cdn.example/app.js?v=1", html)
        # HTML ETag follows the rewritten bytes, so a new script hash busts the document.
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        self.assertEqual(headers.get("etag"), '"%s-gzip"' % digest)

    def test_matching_content_hash_is_immutable_and_304_keeps_that_policy(self):
        raw = (self.static / "app.js").read_bytes()
        token = content_hash_for_bytes(raw)
        self.assertEqual(len(token), _CONTENT_HASH_LENGTH)
        path = "/app.js?h=%s" % token
        status, headers, body = self.request("GET", path, {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "public, max-age=31536000, immutable")
        self.assertEqual(headers.get("content-encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), raw)
        etag = headers["etag"]
        status, again, empty = self.request(
            "GET",
            path,
            {"Accept-Encoding": "gzip", "If-None-Match": etag},
        )
        self.assertEqual(status, 304)
        self.assertEqual(empty, b"")
        self.assertEqual(again.get("etag"), etag)
        self.assertEqual(again.get("cache-control"), "public, max-age=31536000, immutable")
        self.assertEqual(again.get("content-encoding"), "gzip")
        self.assertEqual(again.get("vary"), "Accept-Encoding")

    def test_missing_or_mismatched_hash_is_not_immutable_but_still_serves_the_file(self):
        raw = (self.static / "app.js").read_bytes()
        cases = ["/app.js", "/app.js?v=96", "/app.js?h=deadbeefdeadbeef"]
        for path in cases:
            status, headers, body = self.request("GET", path, {"Accept-Encoding": "gzip"})
            self.assertEqual(status, 200, path)
            self.assertEqual(headers.get("cache-control"), "no-cache", path)
            self.assertNotIn("immutable", (headers.get("cache-control") or "").lower(), path)
            self.assertEqual(gzip.decompress(body), raw, path)

    def test_path_traversal_is_still_rejected(self):
        secret = self.root / "secret.js"
        secret.write_text("should-not-read", encoding="utf-8")
        status, headers, body = self.request("GET", "/../secret.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 404)
        self.assertNotIn(b"should-not-read", body)
        self.assertIsNone(headers.get("content-encoding"))


if __name__ == "__main__":
    unittest.main()
