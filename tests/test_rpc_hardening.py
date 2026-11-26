"""Interim RPC hardening: JSON Content-Type, Origin and Host allowlist.

Guards against no-cors text/plain CSRF and DNS rebinding until the account
system's session middleware lands.
"""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from end.server import Handler


class RpcHardeningTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, body, headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/rpc", body=body, headers=headers)
            response = connection.getresponse()
            return json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def _rpc(self, method="no.such.method"):
        return json.dumps({"jsonrpc": "2.0", "method": method, "id": 1})

    def test_text_plain_content_type_is_rejected(self):
        payload = self._post(self._rpc(), {"Content-Type": "text/plain"})
        self.assertEqual(payload["error"]["code"], 415)

    def test_foreign_origin_is_rejected(self):
        payload = self._post(
            self._rpc(),
            {"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        self.assertEqual(payload["error"]["code"], 403)

    def test_rebinding_host_is_rejected(self):
        payload = self._post(
            self._rpc(),
            {"Content-Type": "application/json", "Host": "evil.example"},
        )
        self.assertEqual(payload["error"]["code"], 403)

    def test_json_same_origin_passes_hardening(self):
        payload = self._post(
            self._rpc(),
            {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port},
        )
        # Unknown method: proves the request reached dispatch, not a guard.
        self.assertEqual(payload["error"]["code"], -32601)

    def test_named_host_needs_allowlist(self):
        body = self._rpc()
        headers = {"Content-Type": "application/json", "Host": "dev.internal"}
        payload = self._post(body, headers)
        self.assertEqual(payload["error"]["code"], 403)
        with patch.dict("os.environ", {"AIVIDEO_ALLOWED_HOSTS": "dev.internal"}):
            payload = self._post(body, headers)
        self.assertEqual(payload["error"]["code"], -32601)

    def test_get_with_rebinding_host_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/", headers={"Host": "evil.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()