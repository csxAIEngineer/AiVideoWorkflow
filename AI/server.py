#!/usr/bin/env python3
"""JSON-RPC process for models, skills, and provider calls."""

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from AI.api import METHODS

HOST = os.environ.get("AI_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_PORT", "8791"))
MAX_BYTES = 8 * 1024 * 1024


class RpcError(Exception):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            self._send(200, _body(request_id, error=RpcError(-32000, str(error), status=status)))
            return
        self._send(200, _body(request_id, result=result))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AiVideo AI http://%s:%s/rpc" % (HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
