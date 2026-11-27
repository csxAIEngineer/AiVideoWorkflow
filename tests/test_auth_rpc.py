"""账户系统 RPC/媒体鉴权集成测试（HTTP 级，需要 PG，否则跳过）。"""

import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.test_account_pg import PG_OK, SKIP, TEST_DSN


@SKIP
class AuthRpcTest(unittest.TestCase):
    def setUp(self):
        from end import auth, pg_store
        import end.db as db
        import end.server as server

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old_data = db.DATA
        db.DATA = self.root / "projects"
        db.DATA.mkdir()
        self._env = patch.dict(os.environ, {
            "AIVIDEO_PG_DSN": TEST_DSN,
            "AIVIDEO_REQUIRE_AUTH": "require",
        })
        self._env.start()
        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()
        pg_store.pool()
        self.server_mod = server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        # No background workers: drill/render submissions are stubbed.
        self._submit = patch.object(server, "submit_agent_task", lambda task: True)
        self._submit.start()
        self.names = []
        self.admin = auth.create_user(self._name(), "admin-pass-123", role="admin")
        self.alice = auth.create_user(self._name(), "alice-pass-123")
        self.bob = auth.create_user(self._name(), "bob-pass-123")

    def tearDown(self):
        from end import pg_store
        import end.db as db

        self._submit.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        try:
            with pg_store.pool().connection() as conn:
                for name in self.names:
                    row = conn.execute("SELECT id FROM users WHERE username = %s", (name,)).fetchone()
                    if row:
                        conn.execute("DELETE FROM projects WHERE user_id = %s", (row["id"],))
                        conn.execute("DELETE FROM users WHERE id = %s", (row["id"],))
        except Exception:
            pass
        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()
        self._env.stop()
        db.DATA = self._old_data
        self.tmp.cleanup()

    def _name(self):
        name = "t_%s" % uuid.uuid4().hex[:10]
        self.names.append(name)
        return name

    def _call(self, method, params=None, cookie="", bearer="", raw_headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if bearer:
            headers["Authorization"] = "Bearer %s" % bearer
        headers.update(raw_headers or {})
        try:
            connection.request(
                "POST", "/rpc",
                body=json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}),
                headers=headers,
            )
            response = connection.getresponse()
            cookies = response.getheader("Set-Cookie") or ""
            payload = json.loads(response.read().decode("utf-8"))
            return payload, cookies
        finally:
            connection.close()

    def _login(self, user, password, method="auth.login"):
        payload, cookies = self._call(method, {"username": user["username"], "password": password})
        self.assertNotIn("error", payload, payload)
        token = cookies.split("aivideo_")[1].split("=")[1].split(";")[0]
        name = "aivideo_admin" if method == "auth.admin_login" else "aivideo_user"
        return "%s=%s" % (name, token)

    def _create(self, cookie, brief="测试项目"):
        payload, _ = self._call("projects.create", {"brief": brief}, cookie=cookie)
        self.assertNotIn("error", payload, payload)
        return payload["result"]["id"]

    def test_anonymous_is_blocked_and_me_reports_required(self):
        payload, _ = self._call("auth.me")
        self.assertIsNone(payload["result"]["user"])
        self.assertTrue(payload["result"]["required"])
        payload, _ = self._call("projects.list")
        self.assertEqual(payload["error"]["code"], 401)

    def test_login_cookie_and_ownership(self):
        cookie = self._login(self.alice, "alice-pass-123")
        payload, _ = self._call("auth.me", cookie=cookie)
        self.assertEqual(payload["result"]["user"]["username"], self.alice["username"])
        pid = self._create(cookie)
        payload, _ = self._call("projects.list", cookie=cookie)
        self.assertEqual([row["id"] for row in payload["result"]], [pid])
        payload, _ = self._call("projects.get", {"id": pid}, cookie=cookie)
        self.assertNotIn("error", payload)
        # Bob cannot see Alice's project anywhere.
        bob = self._login(self.bob, "bob-pass-123")
        for method, params in (
            ("projects.get", {"id": pid}),
            ("projects.tick", {"id": pid}),
            ("projects.delete", {"id": pid}),
            ("projects.media_urls", {"id": pid}),
        ):
            payload, _ = self._call(method, params, cookie=bob)
            self.assertEqual(payload["error"]["code"], 404, method)
        payload, _ = self._call("projects.list", cookie=bob)
        self.assertEqual(payload["result"], [])

    def test_wrong_password_and_admin_audience(self):
        payload, _ = self._call("auth.login", {"username": self.alice["username"], "password": "wrong-pass-123"})
        self.assertEqual(payload["error"]["code"], 401)
        # A user account cannot mint an admin session.
        payload, _ = self._call("auth.admin_login", {"username": self.alice["username"], "password": "alice-pass-123"})
        self.assertEqual(payload["error"]["code"], 401)
        # A user cookie cannot call admin.*.
        cookie = self._login(self.alice, "alice-pass-123")
        payload, _ = self._call("admin.users.list", cookie=cookie)
        self.assertEqual(payload["error"]["code"], 401)

    def test_admin_bearer_and_project_visibility(self):
        cookie = self._login(self.alice, "alice-pass-123")
        pid = self._create(cookie)
        admin_cookie = self._login(self.admin, "admin-pass-123", method="auth.admin_login")
        token = admin_cookie.split("=", 1)[1]
        payload, _ = self._call("admin.users.list", bearer=token)
        self.assertNotIn("error", payload)
        self.assertIn(self.alice["username"], [u["username"] for u in payload["result"]])
        payload, _ = self._call("admin.projects.list", bearer=token)
        self.assertIn(pid, [row["id"] for row in payload["result"]])
        # Admin can read the user's project detail via the user surface? No:
        # projects.* requires a user session; admin uses admin.projects.list.
        payload, _ = self._call("projects.get", {"id": pid}, cookie=admin_cookie)
        self.assertEqual(payload["error"]["code"], 401)
        # Admin logout revokes the admin session.
        payload, cookies = self._call("auth.admin_logout", cookie=admin_cookie)
        self.assertNotIn("error", payload)
        self.assertIn("Max-Age=0", cookies)
        payload, _ = self._call("admin.users.list", cookie=admin_cookie)
        self.assertEqual(payload["error"]["code"], 401)

    def test_config_sanitized_for_user(self):
        cookie = self._login(self.alice, "alice-pass-123")
        payload, _ = self._call("config.get", cookie=cookie)
        self.assertNotIn("minimax_base_url", payload["result"])
        self.assertNotIn("qwen_env", payload["result"])
        self.assertIn("models", payload["result"])
        admin_cookie = self._login(self.admin, "admin-pass-123", method="auth.admin_login")
        payload, _ = self._call("config.get", cookie=admin_cookie)
        self.assertIn("minimax_base_url", payload["result"])

    def test_media_guard(self):
        cookie = self._login(self.alice, "alice-pass-123")
        pid = self._create(cookie)
        folder = self.server_mod.project_dir(pid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text("{}", encoding="utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET", "/media/projects/%s/spec" % pid)
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET", "/media/projects/%s/spec" % pid, headers={"Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()
        bob = self._login(self.bob, "bob-pass-123")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET", "/media/projects/%s/spec" % pid, headers={"Cookie": bob})
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
        finally:
            connection.close()

    def test_logout_and_change_password(self):
        cookie = self._login(self.alice, "alice-pass-123")
        payload, _ = self._call("auth.change_password", {
            "old_password": "alice-pass-123", "new_password": "alice-new-456",
        }, cookie=cookie)
        self.assertNotIn("error", payload)
        payload, _ = self._call("auth.me", cookie=cookie)
        self.assertIsNone(payload["result"]["user"])
        cookie2 = self._login(self.alice, "alice-new-456")
        payload, _ = self._call("auth.logout", cookie=cookie2)
        self.assertNotIn("error", payload)
        payload, _ = self._call("auth.me", cookie=cookie2)
        self.assertIsNone(payload["result"]["user"])

    def test_off_mode_keeps_legacy_access(self):
        with patch.dict(os.environ, {"AIVIDEO_REQUIRE_AUTH": "off"}):
            payload, _ = self._call("projects.list")
            self.assertNotIn("error", payload)
            payload, _ = self._call("auth.me")
            self.assertFalse(payload["result"]["required"])


if __name__ == "__main__":
    unittest.main()