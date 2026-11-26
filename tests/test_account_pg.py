"""PG store + account auth integration tests (phase 1).

Requires a reachable PostgreSQL; defaults to the local dev container's
aivideo_test database. Skipped when unavailable so the suite stays green
on machines without PG.

    AIVIDEO_TEST_PG_DSN=postgres://aivideo:aivideo_dev_local@127.0.0.1:5433/aivideo_test
"""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

TEST_DSN = os.environ.get(
    "AIVIDEO_TEST_PG_DSN",
    "postgres://aivideo:aivideo_dev_local@127.0.0.1:5433/aivideo_test",
)


def _pg_available():
    try:
        import psycopg

        with psycopg.connect(TEST_DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_available()
SKIP = unittest.skipUnless(PG_OK, "需要可达的 PostgreSQL（AIVIDEO_TEST_PG_DSN）")


class _PgCase(unittest.TestCase):
    def setUp(self):
        from end import pg_store
        import end.db as db

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old_data = db.DATA
        db.DATA = self.root / "projects"
        db.DATA.mkdir()
        self._env = patch.dict(os.environ, {"AIVIDEO_PG_DSN": TEST_DSN})
        self._env.start()
        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()
        self._pids = []
        pg_store.pool()  # runs migrations

    def tearDown(self):
        from end import pg_store
        import end.db as db

        try:
            with pg_store.pool().connection() as conn:
                for pid in getattr(self, "_pids", []):
                    conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
        except Exception:
            pass
        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()
        self._env.stop()
        db.DATA = self._old_data
        self.tmp.cleanup()

    def _project(self, pid=None, **extra):
        from end.server import now

        pid = pid or uuid.uuid4().hex[:12]
        self._pids.append(pid)
        created = now()
        project = {
            "id": pid,
            "brief": "b",
            "title": "t",
            "status": "writing",
            "messages": [],
            "events": [],
            "tasks": [],
            "progress": {},
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": None,
        }
        project.update(extra)
        return project


@SKIP
class PgProjectTest(_PgCase):
    def test_write_read_roundtrip_and_summaries(self):
        from end import pg_store

        project = self._project(title="第一轮")
        pg_store.write_project(project)
        loaded = pg_store.read_project(project["id"])
        self.assertEqual(loaded["title"], "第一轮")
        self.assertEqual(loaded["status"], "writing")
        self.assertTrue(loaded["created_at"])
        summaries = pg_store.list_project_summaries()
        self.assertIn(project["id"], [row["id"] for row in summaries])
        tick = pg_store.read_project_tick(project["id"])
        self.assertEqual(tick["title"], "第一轮")

    def test_upsert_keeps_owner_and_tombstone_blocks_resurrection(self):
        from end import auth, pg_store

        owner = auth.create_user("own_%s" % uuid.uuid4().hex[:8], "password-123")
        self.addCleanup(self._drop_user, owner["username"])
        project = self._project()
        pg_store.write_project(project, user_id=owner["id"])
        pg_store.write_project(dict(project, title="worker 更新"))
        self.assertEqual(pg_store.project_owner(project["id"]), owner["id"])
        self.assertEqual(pg_store.read_project(project["id"])["title"], "worker 更新")

        pg_store.mark_deleted(project["id"])
        self.assertTrue(pg_store.is_deleted(project["id"]))
        pg_store.write_project(dict(project, title="复活"))
        self.assertIsNone(pg_store.read_project(project["id"]))
        self.assertNotIn(project["id"], [p["id"] for p in pg_store.list_projects()])

    def _drop_user(self, username):
        from end import pg_store

        try:
            with pg_store.pool().connection() as conn:
                row = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
                if row:
                    conn.execute("DELETE FROM projects WHERE user_id = %s", (row["id"],))
                    conn.execute("DELETE FROM users WHERE id = %s", (row["id"],))
        except Exception:
            pass

    def test_tombstone_survives_pool_restart(self):
        from end import pg_store

        project = self._project()
        pg_store.write_project(project)
        pg_store.mark_deleted(project["id"])
        pg_store._TOMBSTONES.clear()  # simulate process restart
        pg_store.reset_pool()
        self.assertTrue(pg_store.is_deleted(project["id"]))
        pg_store.write_project(dict(project, title="复活"))
        self.assertIsNone(pg_store.read_project(project["id"]))

    def test_delete_removes_files_but_keeps_tombstone_row(self):
        from end import pg_store
        import end.db as db

        project = self._project()
        pg_store.write_project(project)
        folder = db.project_dir(project["id"])
        self.assertTrue(folder.is_dir())
        self.assertTrue(pg_store.delete_project(project["id"]))
        self.assertFalse(folder.exists())
        self.assertTrue(pg_store.is_deleted(project["id"]))

    def test_user_filter(self):
        from end import auth, pg_store

        alice = auth.create_user("a_%s" % uuid.uuid4().hex[:8], "password-123")
        bob = auth.create_user("b_%s" % uuid.uuid4().hex[:8], "password-123")
        self.addCleanup(self._drop_user, alice["username"])
        self.addCleanup(self._drop_user, bob["username"])
        mine = self._project()
        other = self._project()
        pg_store.write_project(mine, user_id=alice["id"])
        pg_store.write_project(other, user_id=bob["id"])
        rows = pg_store.list_project_summaries(user_id=alice["id"])
        self.assertEqual([row["id"] for row in rows], [mine["id"]])


@SKIP
class AuthTest(_PgCase):
    def setUp(self):
        super().setUp()
        self.name = "t_%s" % uuid.uuid4().hex[:10]

    def tearDown(self):
        from end import pg_store

        try:
            with pg_store.pool().connection() as conn:
                conn.execute("DELETE FROM login_events WHERE username = %s", (self.name,))
                conn.execute("DELETE FROM users WHERE username = %s", (self.name,))
        except Exception:
            pass
        super().tearDown()

    def test_create_login_session_audience(self):
        from end import auth

        user = auth.create_user(self.name, "password-123", role="user")
        self.assertTrue(user["must_change_password"])
        token, logged = auth.login(self.name, "password-123", audience="user")
        self.assertEqual(logged["id"], user["id"])
        self.assertIsNotNone(auth.session_user(token, "user"))
        # user session must not resolve as admin, and vice versa.
        self.assertIsNone(auth.session_user(token, "admin"))
        with self.assertRaises(auth.AuthError):
            auth.login(self.name, "password-123", audience="admin")

    def test_wrong_password_and_rate_limit(self):
        from end import auth

        auth.create_user(self.name, "password-123")
        for _ in range(auth.RATE_LIMIT_MAX):
            with self.assertRaises(auth.AuthError) as caught:
                auth.login(self.name, "wrong-password", ip="1.2.3.4")
            self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(auth.AuthError) as caught:
            auth.login(self.name, "password-123", ip="1.2.3.4")
        self.assertEqual(caught.exception.code, 429)

    def test_change_password_revokes_sessions(self):
        from end import auth

        user = auth.create_user(self.name, "password-123")
        token, _ = auth.login(self.name, "password-123")
        auth.change_password(user["id"], "password-123", "new-password-456")
        self.assertIsNone(auth.session_user(token, "user"))
        with self.assertRaises(auth.AuthError):
            auth.login(self.name, "password-123")
        token2, logged = auth.login(self.name, "new-password-456")
        self.assertFalse(logged["must_change_password"])
        self.assertTrue(token2)

    def test_admin_reset_forces_change_and_disable_blocks(self):
        from end import auth

        user = auth.create_user(self.name, "password-123")
        token, _ = auth.login(self.name, "password-123")
        auth.reset_password(user["id"], "reset-password-789")
        self.assertIsNone(auth.session_user(token, "user"))
        _, logged = auth.login(self.name, "reset-password-789")
        self.assertTrue(logged["must_change_password"])
        auth.set_disabled(user["id"], True)
        with self.assertRaises(auth.AuthError):
            auth.login(self.name, "reset-password-789")

    def test_login_events_and_last_login(self):
        from end import auth, pg_store

        user = auth.create_user(self.name, "password-123")
        auth.login(self.name, "password-123", ip="9.9.9.9", user_agent="pytest")
        auth.login(self.name, "password-123")
        fresh = auth.get_user(user["id"])
        self.assertIsNotNone(fresh["last_login_at"])
        self.assertIsNotNone(fresh["prev_login_at"])
        with pg_store.pool().connection() as conn:
            events = conn.execute(
                "SELECT ok, ip FROM login_events WHERE username = %s ORDER BY id", (self.name,)
            ).fetchall()
        self.assertEqual([e["ok"] for e in events], [True, True])
        self.assertEqual(events[0]["ip"], "9.9.9.9")

    def test_short_password_and_bad_username_rejected(self):
        from end import auth

        with self.assertRaises(auth.AuthError):
            auth.create_user(self.name, "short")
        with self.assertRaises(auth.AuthError):
            auth.create_user("Bad Name!", "password-123")


if __name__ == "__main__":
    unittest.main()