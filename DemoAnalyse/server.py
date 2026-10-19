"""Upload a video and report shot count plus narration.

    python3 DemoAnalyse/server.py

Then open http://127.0.0.1:8765
"""

import cgi
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyse import AnalyseError, analyse_video, envelope
from narration import public_error

RUNS = ROOT / "runs"
SAMPLE = ROOT / "test.mp4"
WEB = ROOT / "web" / "index.html"
HOST = "127.0.0.1"
PORT = int(os.environ.get("DEMO_ANALYSE_PORT", "8765"))
MAX_UPLOAD = 200 * 1024 * 1024
ALLOWED = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


class AnalyseServer(ThreadingHTTPServer):
    allow_reuse_address = True


class AnalyseHandler(BaseHTTPRequestHandler):
    server_version = "DemoAnalyse/1.0"

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_bytes(200, "text/html; charset=utf-8", WEB.read_bytes())
            return
        if path == "/favicon.ico":
            self._send_bytes(204, "image/x-icon", b"")
            return
        poster = _safe_run_file(path)
        if poster is not None:
            self._send_bytes(200, "image/jpeg", poster.read_bytes())
            return
        self._send_json(404, envelope(error="没有这个页面"))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/analyse":
            self._send_json(404, envelope(error="没有这个接口"))
            return
        try:
            video_path, job_id = self._receive_video()
            result = analyse_video(video_path, RUNS / job_id)
            self._send_json(200, envelope(_public_result(result, job_id)))
        except AnalyseError as error:
            self._send_json(400, envelope(error=public_error(error)))
        except Exception as error:
            traceback.print_exc()
            self._send_json(500, envelope(error=public_error(error)))

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _receive_video(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            raise AnalyseError("请求是空的。")
        if length > MAX_UPLOAD:
            raise AnalyseError("视频超过 200MB。")
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("application/json"):
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                raise AnalyseError("请求不是有效的 JSON。")
            if isinstance(payload, dict) and payload.get("sample") is True:
                if not SAMPLE.is_file():
                    raise AnalyseError("找不到测试视频 test.mp4。")
                return SAMPLE, uuid4().hex[:12]
            raise AnalyseError("请上传视频，或使用测试视频。")
        if "multipart/form-data" not in content_type:
            raise AnalyseError("请用表单上传视频。")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(length),
            },
        )
        if "video" not in form:
            raise AnalyseError("没有收到视频文件。")
        item = form["video"]
        if isinstance(item, list):
            item = item[0]
        filename = Path(item.filename or "").name
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED:
            raise AnalyseError("请上传 mp4、mov、m4v、webm 或 mkv。")
        data = item.file.read()
        if not data:
            raise AnalyseError("视频文件是空的。")
        if len(data) > MAX_UPLOAD:
            raise AnalyseError("视频超过 200MB。")
        job_id = uuid4().hex[:12]
        folder = RUNS / job_id
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / ("source" + suffix)
        dest.write_bytes(data)
        return dest, job_id

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _public_result(result, job_id):
    shots = []
    for shot in result["shots"]:
        poster = shot.get("poster")
        url = "/runs/%s/%s" % (job_id, poster) if poster else None
        shots.append(dict(shot, poster=url))
    return dict(result, shots=shots)


def _safe_run_file(url_path):
    parts = [part for part in url_path.split("/") if part]
    if len(parts) != 3 or parts[0] != "runs":
        return None
    job_id, name = parts[1], parts[2]
    if not job_id.isalnum() or not name.startswith("shot-") or not name.endswith(".jpg"):
        return None
    path = (RUNS / job_id / name).resolve()
    if RUNS.resolve() not in path.parents or not path.is_file():
        return None
    return path


def main():
    RUNS.mkdir(parents=True, exist_ok=True)
    if not WEB.is_file():
        raise SystemExit("找不到页面：%s" % WEB)
    server = AnalyseServer((HOST, PORT), AnalyseHandler)
    print("DemoAnalyse http://%s:%s" % (HOST, PORT), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
