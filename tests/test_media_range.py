"""Local /media streams ranges without reading the whole file."""

import email.utils
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
import end.server as server
from end.db import project_dir, write_project
from end.server import Handler
from http.server import ThreadingHTTPServer


def write_project_media(project_id, film=b"", clip=b"", image=b""):
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
            "video_file": "film.mp4" if film is not None and film != "missing" else "",
            "created_at": "2026-01-01T00:00:00+08:00",
        }
    )
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    if film is not None and film != "missing":
        path = folder / "outputs" / "film.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(film)
        paths["film"] = path
    if clip is not None and clip != "missing":
        path = folder / "outputs" / "clips" / "S01.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(clip)
        paths["clip"] = path
    if image is not None and image != "missing":
        path = folder / "outputs" / "images" / "A1.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        paths["image"] = path
    return paths


class MediaRangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "media.db"
        db._CONN = None
        db.DATA.mkdir()
        self._env = {
            "AIVIDEO_PUBLIC_BASE": os.environ.get("AIVIDEO_PUBLIC_BASE"),
            "AIVIDEO_IMAGE_STORE": os.environ.get("AIVIDEO_IMAGE_STORE"),
        }
        os.environ["AIVIDEO_PUBLIC_BASE"] = "http://203.0.113.10:8787"
        os.environ["AIVIDEO_IMAGE_STORE"] = "local"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.project_id = "b22e0de00001"
        self.payload = bytes(range(256)) * 8  # 2048 bytes
        self.paths = write_project_media(self.project_id, film=self.payload, clip=self.payload, image=b"PNG-demo")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)
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

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            header_map = {key.lower(): value for key, value in response.getheaders()}
            return response.status, header_map, payload
        finally:
            connection.close()

    def count_reads(self, path):
        reads = {"bytes": 0, "opens": 0}
        real_open = server.Path.open

        def counting_open(self_path, *args, **kwargs):
            handle = real_open(self_path, *args, **kwargs)
            if self_path.resolve() != path.resolve():
                return handle
            reads["opens"] += 1
            original = handle.read

            def read(size=-1):
                data = original(size)
                reads["bytes"] += len(data)
                return data

            handle.read = read
            return handle

        return reads, patch.object(server.Path, "open", counting_open)

    def test_range_prefix_suffix_open_and_full_get(self):
        video = "/media/projects/%s/video" % self.project_id
        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("GET", video, {"Range": "bytes=0-15"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[:16])
        self.assertEqual(headers.get("content-range"), "bytes 0-15/%s" % len(self.payload))
        self.assertEqual(headers.get("content-length"), "16")
        self.assertEqual(headers.get("accept-ranges"), "bytes")
        self.assertTrue(headers.get("etag"))
        self.assertTrue(headers.get("last-modified"))
        self.assertEqual(reads["bytes"], 16)
        self.assertEqual(reads["opens"], 1)

        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("GET", video, {"Range": "bytes=-10"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[-10:])
        self.assertEqual(headers.get("content-range"), "bytes %s-%s/%s" % (len(self.payload) - 10, len(self.payload) - 1, len(self.payload)))
        self.assertEqual(reads["bytes"], 10)

        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("GET", video, {"Range": "bytes=2000-"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[2000:])
        self.assertEqual(headers.get("content-range"), "bytes 2000-2047/2048")
        self.assertEqual(reads["bytes"], 48)

        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("GET", video)
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertEqual(headers.get("content-length"), "2048")
        self.assertEqual(headers.get("accept-ranges"), "bytes")
        self.assertIsNone(headers.get("content-range"))
        self.assertEqual(reads["bytes"], 2048)

    def test_unsatisfiable_and_multi_range_and_illegal(self):
        video = "/media/projects/%s/video" % self.project_id
        status, headers, body = self.request("GET", video, {"Range": "bytes=9000-9010"})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("content-range"), "bytes */2048")
        self.assertEqual(body, b"")
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-1,2-3"})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertIsNone(headers.get("content-range"))
        status, headers, body = self.request("GET", video, {"Range": "bytes=abc-def"})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)

    def test_head_has_headers_but_no_body(self):
        video = "/media/projects/%s/video" % self.project_id
        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("HEAD", video)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("content-length"), "2048")
        self.assertEqual(headers.get("accept-ranges"), "bytes")
        self.assertTrue(headers.get("etag"))
        self.assertEqual(reads["bytes"], 0)
        self.assertEqual(reads["opens"], 0)
        reads, opener = self.count_reads(self.paths["film"])
        with opener:
            status, headers, body = self.request("HEAD", video, {"Range": "bytes=0-15"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("content-length"), "16")
        self.assertEqual(headers.get("content-range"), "bytes 0-15/2048")
        self.assertEqual(reads["bytes"], 0)

    def test_if_range_etag_and_date(self):
        video = "/media/projects/%s/video" % self.project_id
        status, headers, _ = self.request("GET", video, {"Range": "bytes=0-15"})
        etag = headers["etag"]
        last_modified = headers["last-modified"]
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-15", "If-Range": etag})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[:16])
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-15", "If-Range": '"nope"'})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-15", "If-Range": "W/" + etag})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-15", "If-Range": last_modified})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[:16])
        earlier = email.utils.formatdate(self.paths["film"].stat().st_mtime - 120, usegmt=True)
        status, headers, body = self.request("GET", video, {"Range": "bytes=0-15", "If-Range": earlier})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)

    def test_download_disposition_and_clip_and_empty_file(self):
        status, headers, body = self.request(
            "GET",
            "/media/projects/%s/video?download=1" % self.project_id,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertIn("attachment", headers.get("content-disposition") or "")
        self.assertIn("film.mp4", headers.get("content-disposition") or "")
        status, headers, body = self.request(
            "GET",
            "/media/projects/%s/clips/S01.mp4" % self.project_id,
            {"Range": "bytes=0-15"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[:16])
        empty_id = "b22e0de00002"
        write_project_media(empty_id, film=b"")
        status, headers, body = self.request("GET", "/media/projects/%s/video" % empty_id)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("content-length"), "0")
        status, headers, body = self.request("GET", "/media/projects/%s/video" % empty_id, {"Range": "bytes=0-15"})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("content-range"), "bytes */0")
        self.assertEqual(body, b"")

    def test_rpc_is_unaffected_and_path_is_still_checked(self):
        status, headers, body = self.request("GET", "/media/projects/not-a-valid-id/video")
        self.assertEqual(status, 404)
        status, headers, body = self.request(
            "GET",
            "/media/projects/%s/clips/../S01.mp4" % self.project_id,
        )
        self.assertEqual(status, 404)
        status, headers, body = self.request(
            "GET",
            "/media/projects/%s/clips/%%2e%%2e%%2fS01.mp4" % self.project_id,
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
