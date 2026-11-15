#!/usr/bin/env python3
"""JSON-RPC process End and the studio call.

Same envelope as ``AI.server``: HTTP POST ``/rpc``, JSON-RPC 2.0, host and
port from ``AI_HOST`` / ``AI_PORT`` (default ``127.0.0.1:8791``). Method
errors stay HTTP 200 so the existing clients can read ``error.message``.
"""

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from Agents.api import METHODS

HOST = os.environ.get("AI_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_PORT", "8791"))
MAX_BYTES = 8 * 1024 * 1024

_SKIP_FRAMES = {
    "_reject",
    "_explain_failure",
    "_raise_attempts",
    "explain_parse_failure",
}


class RpcError(Exception):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _error_message(error):
    message = str(error).strip() or "AI 调用失败"
    origin = error
    seen = 0
    while getattr(origin, "__cause__", None) is not None and seen < 6:
        origin = origin.__cause__
        seen += 1
    frames = traceback.extract_tb(getattr(origin, "__traceback__", None)) or traceback.extract_tb(
        getattr(error, "__traceback__", None)
    )
    chosen = None
    for frame in frames:
        if frame.name not in _SKIP_FRAMES:
            chosen = frame
    if chosen is None and frames:
        chosen = frames[-1]
    if chosen is not None:
        message += "\n代码：%s:%s %s" % (os.path.basename(chosen.filename), chosen.lineno, chosen.name)
    return message


def _body(request_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        data = {"code": error.code, "message": error.message}
        if error.status is not None:
            data["data"] = {"status": error.status}
        payload["error"] = data
    else:
        payload["result"] = result
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get("AGENTS_RPC_QUIET"):
            return
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_POST(self):
        if self.path.rstrip("/") != "/rpc":
            self._send(404, {"error": "没有这个接口"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BYTES:
            self._send(413, _body(None, error=RpcError(-32600, "请求太大")))
            return
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send(400, _body(None, error=RpcError(-32700, "不是 JSON")))
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        handler = METHODS.get(method)
        if handler is None:
            self._send(200, _body(request_id, error=RpcError(-32601, "没有这个接口")))
            return
        try:
            result = handler(params)
        except Exception as error:
            traceback.print_exc()
            status = getattr(error, "status", None)
            self._send(200, _body(request_id, error=RpcError(-32000, _error_message(error), status=status)))
            return
        self._send(200, _body(request_id, result=result))


def main():
    from AI.qwen_api.client import describe_qwen_target

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AiVideo Agents http://%s:%s/rpc" % (HOST, PORT))
    print(describe_qwen_target())
    server.serve_forever()


if __name__ == "__main__":
    main()
