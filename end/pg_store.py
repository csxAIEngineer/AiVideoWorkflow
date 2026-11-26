"""PostgreSQL store for the account system (phase 1).

Enabled by AIVIDEO_PG_DSN. end/db.py stays the public facade: when a DSN is
configured it delegates project reads/writes here; otherwise the legacy SQLite
path is untouched. Schema and migrations live only in this repo (end/migrations).

Media bytes, spec.json/run_state.json and OSS object keys do NOT move; rows
only carry metadata. Timestamps are timestamptz here and ISO strings at the
API boundary, so existing row mappings keep working.
"""

import os
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

_POOL = None
_POOL_LOCK = threading.Lock()
# Fast-path tombstones for ids marked before their row exists (same semantics
# as the SQLite layer's in-memory set, but backed by projects.deleted_at).
_TOMBSTONES = set()


def _load_dotenv():
    # Only the DSN key: injecting the whole .env into os.environ here would
    # leak unrelated config into later callers (and tests) in this process.
    if "AIVIDEO_PG_DSN" in os.environ:
        return
    path = ROOT / ".env"
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "AIVIDEO_PG_DSN":
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
                return
    except OSError:
        pass


def dsn():
    _load_dotenv()
    return str(os.environ.get("AIVIDEO_PG_DSN") or "").strip()


def enabled():
    return bool(dsn())


def pool():
    """Lazy connection pool; runs migrations on first use."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            target = dsn()
            if not target:
                raise RuntimeError("账户系统需要 PostgreSQL：请配置 AIVIDEO_PG_DSN")
            _POOL = ConnectionPool(
                target,
                min_size=1,
                max_size=8,
                kwargs={"row_factory": dict_row, "autocommit": True},
                open=True,
            )
            with _POOL.connection() as conn:
                migrate(conn)
        return _POOL


def reset_pool():
    """Close the pool (tests / DSN change)."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.close()
            except Exception:
                pass
            _POOL = None


def migrate(conn):
    """Apply numbered SQL migrations once, in order, each in its own transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in cur.fetchall()}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = path.name
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))


def _ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


_TIME_FIELDS = ("created_at", "started_at", "updated_at", "finished_at")


def _row_out(row):
    if row is None:
        return None
    data = dict(row)
    for field in _TIME_FIELDS:
        if field in data:
            data[field] = _iso(data[field])
    return data


def _project_values(project):
    from end.db import COLUMNS, _dumps, _row_values  # pure helpers

    values = list(_row_values(project))
    # _row_values follows COLUMNS order; timestamps sit at known positions.
    index = {name: i for i, name in enumerate(COLUMNS)}
    for field in _TIME_FIELDS:
        values[index[field]] = _ts(values[index[field]])
    return values


def read_project(project_id):
    from end.db import row_to_project

    with pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = %s AND deleted_at IS NULL", (project_id,)
        ).fetchone()
    return row_to_project(_row_out(row)) if row else None


def read_project_tick(project_id):
    from end.db import TICK_COLUMNS, row_to_tick

    with pool().connection() as conn:
        row = conn.execute(
            "SELECT %s FROM projects WHERE id = %%s AND deleted_at IS NULL" % ", ".join(TICK_COLUMNS),
            (project_id,),
        ).fetchone()
    return row_to_tick(_row_out(row)) if row else None


def list_projects(user_id=None):
    from end.db import row_to_project

    sql = "SELECT * FROM projects WHERE deleted_at IS NULL"
    params = []
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC"
    with pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_project(_row_out(row)) for row in rows]


def list_project_summaries(user_id=None):
    from end.db import LIST_SUMMARY_COLUMNS, row_to_summary

    sql = "SELECT %s FROM projects WHERE deleted_at IS NULL" % ", ".join(LIST_SUMMARY_COLUMNS)
    params = []
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC"
    with pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_summary(_row_out(row)) for row in rows]


def mark_deleted(project_id):
    _TOMBSTONES.add(str(project_id))
    with pool().connection() as conn:
        conn.execute(
            "UPDATE projects SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
            (project_id,),
        )


def clear_deleted(project_id):
    _TOMBSTONES.discard(str(project_id))
    with pool().connection() as conn:
        conn.execute("UPDATE projects SET deleted_at = NULL WHERE id = %s", (project_id,))


def is_deleted(project_id):
    if str(project_id) in _TOMBSTONES:
        return True
    with pool().connection() as conn:
        row = conn.execute(
            "SELECT deleted_at FROM projects WHERE id = %s", (project_id,)
        ).fetchone()
    return bool(row and row["deleted_at"] is not None)


def write_project(project, user_id=None):
    """Upsert with the tombstone re-check inside the same transaction.

    user_id=None keeps the existing owner (workers upsert without a session);
    an explicit user_id sets it (create path).
    """
    from end.db import COLUMNS, content_revision_from_project, project_dir

    if not project or not project.get("id"):
        raise ValueError("project.id 不能为空")
    project["content_revision"] = content_revision_from_project(project)
    values = _project_values(project)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    assignments = ", ".join("%s = EXCLUDED.%s" % (name, name) for name in COLUMNS if name != "id")
    sql = (
        "INSERT INTO projects (%s, user_id) VALUES (%s, %%s) "
        "ON CONFLICT(id) DO UPDATE SET %s, user_id = COALESCE(EXCLUDED.user_id, projects.user_id)"
        % (", ".join(COLUMNS), placeholders, assignments)
    )
    with pool().connection() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT deleted_at FROM projects WHERE id = %s FOR UPDATE", (project["id"],)
            ).fetchone()
            if str(project["id"]) in _TOMBSTONES or (row and row["deleted_at"] is not None):
                return project
            conn.execute(sql, values + [user_id])
    folder = project_dir(project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    return project


def delete_project(project_id):
    """Soft delete: keep the row as a persistent tombstone; files are removed
    by the caller only after workers have exited (same protocol as SQLite)."""
    from end.db import project_dir

    mark_deleted(project_id)
    with pool().connection() as conn:
        row = conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,)).fetchone()
    folder = project_dir(project_id)
    deleted_files = False
    if folder.is_dir():
        import shutil

        root = project_dir("").resolve()
        target = folder.resolve()
        if root in target.parents:
            shutil.rmtree(target)
            deleted_files = True
    return bool(row) or deleted_files


def project_owner(project_id):
    with pool().connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM projects WHERE id = %s AND deleted_at IS NULL", (project_id,)
        ).fetchone()
    return row["user_id"] if row else None