#!/usr/bin/env python3
"""JSON-RPC process for models, skills, and provider calls."""

import json
import os
import time
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


_SKIP_FRAMES = {
    "_reject",
    "_explain_failure",
    "_raise_attempts",
    "explain_parse_failure",
}


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
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        started = getattr(self, "_started", None)
        method = getattr(self, "_method", "")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError) as error:
            # region agent log
            try:
                elapsed = int((time.time() - started) * 1000) if started else None
                payload = {
                    "sessionId": "ee5fa7",
                    "runId": "post-fix",
                    "hypothesisId": "H-pipe",
                    "location": "AI/server.py:_send",
                    "message": "client closed before the response",
                    "data": {
                        "method": method,
                        "elapsedMs": elapsed,
                        "bytes": len(data),
                        "error": type(error).__name__,
                    },
                    "timestamp": int(time.time() * 1000),
                }
                with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                pass
            # endregion
            self.close_connection = True
            return
        # region agent log
        if method == "write":
            try:
                elapsed = int((time.time() - started) * 1000) if started else None
                payload = {
                    "sessionId": "ee5fa7",
                    "runId": "post-fix",
                    "hypothesisId": "H-pipe",
                    "location": "AI/server.py:_send",
                    "message": "write response sent",
                    "data": {"method": method, "elapsedMs": elapsed, "bytes": len(data)},
                    "timestamp": int(time.time() * 1000),
                }
                with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                pass
        # endregion

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
        self._method = method
        self._started = time.time()
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    from AI.qwen_api.client import describe_qwen_target
    from AI.qwen_api.graph import prompt_debug_enabled

    print("AiVideo AI http://%s:%s/rpc" % (HOST, PORT))
    print(describe_qwen_target())
    if prompt_debug_enabled():
        print("PROMPT_DEBUG=on")
    server.serve_forever()


if __name__ == "__main__":
    main()
