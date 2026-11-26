"""SQLite → PG 迁移脚本测试（需要可达 PG，否则跳过）。"""

import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from tests.test_account_pg import PG_OK, SKIP, TEST_DSN


@SKIP
class MigrateTest(unittest.TestCase):
    def setUp(self):
        from end import pg_store

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._env = patch.dict(os.environ, {"AIVIDEO_PG_DSN": TEST_DSN})
        self._env.start()
        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()
        pg_store.pool()
        self.pids = []

    def tearDown(self):
        from end import pg_store

        try:
            with pg_store.pool().connection() as conn:
                for pid in self.pids:
                    conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
        except Exception:
            pass
        pg_store.reset_pool()
        self._env.stop()
        self.tmp.cleanup()

    def _sqlite(self, rows):
        import end.db as db

        path = self.root / "legacy.db"
        conn = sqlite3.connect(str(path))
        db._init_schema(conn)
        for row in rows:
            values = db._row_values(row)
            placeholders = ", ".join("?" for _ in db.COLUMNS)
            conn.execute(
                "INSERT INTO projects (%s) VALUES (%s)" % (", ".join(db.COLUMNS), placeholders),
                values,
            )
        conn.commit()
        conn.close()
        return path

    def test_migration_preserves_rows_and_revision(self):
        from end import db, migrate_sqlite_to_pg, pg_store

        pid = uuid.uuid4().hex[:12]
        self.pids.append(pid)
        created = "2026-11-20T10:00:00+08:00"
        project = {
            "id": pid,
            "brief": "迁移测试",
            "title": "旧项目",
            "status": "done",
            "messages": [{"role": "user", "content": "你好"}],
            "events": [],
            "tasks": [],
            "progress": {"percent": 100},
            "created_at": created,
            "started_at": created,
            "updated_at": created,
            "finished_at": created,
            "content_revision": "legacy-revision",
        }
        path = self._sqlite([project])
        count = migrate_sqlite_to_pg.migrate(path)
        self.assertEqual(count, 1)
        loaded = pg_store.read_project(pid)
        self.assertEqual(loaded["title"], "旧项目")
        self.assertEqual(loaded["messages"], [{"role": "user", "content": "你好"}])
        self.assertEqual(loaded["content_revision"], "legacy-revision")
        self.assertIsNone(pg_store.project_owner(pid))
        # timestamptz preserves the instant; the offset representation is
        # normalized to the session timezone.
        from datetime import datetime

        self.assertEqual(
            datetime.fromisoformat(loaded["created_at"]),
            datetime.fromisoformat(created),
        )

    def test_dry_run_writes_nothing(self):
        from end import migrate_sqlite_to_pg, pg_store

        pid = uuid.uuid4().hex[:12]
        created = "2026-11-20T10:00:00+08:00"
        project = {
            "id": pid,
            "brief": "b",
            "title": "t",
            "status": "writing",
            "created_at": created,
            "started_at": created,
            "updated_at": created,
        }
        path = self._sqlite([project])
        count = migrate_sqlite_to_pg.migrate(path, dry_run=True)
        self.assertEqual(count, 1)
        self.assertIsNone(pg_store.read_project(pid))


if __name__ == "__main__":
    unittest.main()