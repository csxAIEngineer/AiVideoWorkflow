"""SQLite store for AiVideo web projects. Artifacts stay on disk under data/web_projects/<id>/."""

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "web_projects"
DB_PATH = ROOT / "data" / "aivideo.db"

_LOCK = threading.RLock()
_CONN = None
# Tombstones for deleted projects. A worker that is still winding down must not
# re-INSERT a deleted row (write_project is an upsert) nor recreate its folder.
_DELETED = set()

JSON_FIELDS = ("events", "tasks", "progress")
COLUMNS = (
    "id",
    "brief",
    "messages_json",
    "title",
    "status",
    "stage",
    "script",
    "storyboard",
    "image_prompts",
    "error",
    "events_json",
    "tasks_json",
    "progress_json",
    "cloud_log",
    "prompt_id",
    "video_file",
    "model",
    "template",
    "created_at",
    "started_at",
    "updated_at",
    "finished_at",
    "content_revision",
)

# History menu only. Full rows still go through COLUMNS / list_projects.
LIST_SUMMARY_COLUMNS = (
    "id",
    "brief",
    "title",
    "status",
    "stage",
    "error",
    "progress_json",
    "created_at",
    "started_at",
    "updated_at",
    "finished_at",
)
LIST_SUMMARY_SQL = "SELECT %s FROM projects ORDER BY created_at DESC" % ", ".join(
    LIST_SUMMARY_COLUMNS
)


def project_dir(project_id):
    return DATA / project_id


def _pg():
    """PostgreSQL backend when AIVIDEO_PG_DSN is configured; else None (SQLite)."""
    from end import pg_store

    return pg_store if pg_store.enabled() else None


def connect(db_path=None):
    global _CONN
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _CONN is not None and not db_path:
            return _CONN
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(conn)
        if db_path is None:
            _CONN = conn
            # PG mode: legacy JSON auto-import must not write into the new store.
            if _pg() is None:
                migrate_json_projects(conn)
        return conn


def _init_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            brief TEXT NOT NULL,
            messages_json TEXT NOT NULL DEFAULT '[]',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '',
            script TEXT NOT NULL DEFAULT '',
            storyboard TEXT NOT NULL DEFAULT '',
            image_prompts TEXT NOT NULL DEFAULT '',
            error TEXT,
            events_json TEXT NOT NULL DEFAULT '[]',
            tasks_json TEXT NOT NULL DEFAULT '[]',
            progress_json TEXT NOT NULL DEFAULT '{}',
            cloud_log TEXT NOT NULL DEFAULT '',
            prompt_id TEXT NOT NULL DEFAULT '',
            video_file TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT 'qwen3.8-flash',
            template TEXT NOT NULL DEFAULT 'short-drama-director',
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            finished_at TEXT,
            content_revision TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)"
    )
    columns = [row[1] for row in conn.execute("PRAGMA table_info(projects)")]
    if "messages_json" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN messages_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "model" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN model TEXT NOT NULL DEFAULT 'qwen3.8-flash'"
        )
    if "template" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN template TEXT NOT NULL DEFAULT 'short-drama-director'"
        )
    if "content_revision" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN content_revision TEXT NOT NULL DEFAULT ''"
        )
    conn.commit()


def _dumps(value, fallback):
    if value is None:
        return fallback
    return json.dumps(value, ensure_ascii=False)


def _loads(text, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return fallback


def row_to_project(row):
    if row is None:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "brief": data.get("brief") or "",
        "messages": _loads(data.get("messages_json"), []),
        "title": data.get("title") or "",
        "status": data.get("status") or "",
        "stage": data.get("stage") or "",
        "script": data.get("script") or "",
        "storyboard": data.get("storyboard") or "",
        "image_prompts": data.get("image_prompts") or "",
        "error": data.get("error"),
        "events": _loads(data.get("events_json"), []),
        "tasks": _loads(data.get("tasks_json"), []),
        "progress": _loads(data.get("progress_json"), {}),
        "cloud_log": data.get("cloud_log") or "",
        "prompt_id": data.get("prompt_id") or "",
        "video_file": data.get("video_file") or "",
        "model": data.get("model") or "qwen3.8-flash",
        "template": data.get("template") or "short-drama-director",
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at") or data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "finished_at": data.get("finished_at"),
        "content_revision": data.get("content_revision") or "",
    }


def _row_values(project):
    return (
        project["id"],
        project.get("brief") or "",
        _dumps(project.get("messages") or [], "[]"),
        project.get("title") or "",
        project.get("status") or "",
        project.get("stage") or "",
        project.get("script") or "",
        project.get("storyboard") or "",
        project.get("image_prompts") or "",
        project.get("error"),
        _dumps(project.get("events") or [], "[]"),
        _dumps(project.get("tasks") or [], "[]"),
        _dumps(project.get("progress") or {}, "{}"),
        project.get("cloud_log") or "",
        project.get("prompt_id") or "",
        project.get("video_file") or "",
        project.get("model") or "qwen3.8-flash",
        project.get("template") or "short-drama-director",
        project.get("created_at") or "",
        project.get("started_at"),
        project.get("updated_at") or "",
        project.get("finished_at"),
        project.get("content_revision") or "",
    )


def read_project(project_id, conn=None):
    pg = _pg()
    if pg is not None:
        return pg.read_project(project_id)
    db = conn or connect()
    with _LOCK:
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_to_project(row)


def content_revision_from_project(project):
    """Stable token for script, storyboard, messages, and film name. Not media files."""
    project = project or {}
    payload = json.dumps(
        [
            project.get("script") or "",
            project.get("storyboard") or "",
            project.get("image_prompts") or "",
            project.get("messages") or [],
            project.get("video_file") or "",
            project.get("prompt_id") or "",
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mark_deleted(project_id):
    """Tombstone a project so in-flight workers cannot resurrect it."""
    pg = _pg()
    if pg is not None:
        pg.mark_deleted(project_id)
        return
    with _LOCK:
        _DELETED.add(str(project_id))


def clear_deleted(project_id):
    """Drop a tombstone. Only a brand-new project id should ever need this."""
    pg = _pg()
    if pg is not None:
        pg.clear_deleted(project_id)
        return
    with _LOCK:
        _DELETED.discard(str(project_id))


def is_deleted(project_id):
    pg = _pg()
    if pg is not None:
        return pg.is_deleted(project_id)
    with _LOCK:
        return str(project_id) in _DELETED


def write_project(project, conn=None, user_id=None):
    pg = _pg()
    if pg is not None:
        return pg.write_project(project, user_id=user_id)
    if not project or not project.get("id"):
        raise ValueError("project.id 不能为空")
    db = conn or connect()
    project["content_revision"] = content_revision_from_project(project)
    values = _row_values(project)
    placeholders = ", ".join("?" for _ in COLUMNS)
    assignments = ", ".join("%s = excluded.%s" % (name, name) for name in COLUMNS if name != "id")
    sql = (
        "INSERT INTO projects (%s) VALUES (%s) "
        "ON CONFLICT(id) DO UPDATE SET %s"
        % (", ".join(COLUMNS), placeholders, assignments)
    )
    # Tombstone re-check, folder creation and upsert share one critical section
    # with mark_deleted/DELETE, so a winding-down worker cannot resurrect the
    # row or the folder between the check and the write.
    with _LOCK:
        if str(project["id"]) in _DELETED:
            return project
        folder = project_dir(project["id"])
        folder.mkdir(parents=True, exist_ok=True)
        db.execute(sql, values)
        db.commit()
    return project


def row_to_summary(row):
    """Build a menu row from LIST_SUMMARY_COLUMNS. Do not use row_to_project."""
    if row is None:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "brief": data.get("brief") or "",
        "title": data.get("title") or "",
        "status": data.get("status") or "",
        "stage": data.get("stage") or "",
        "error": data.get("error"),
        "progress": _loads(data.get("progress_json"), {}),
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at") or data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "finished_at": data.get("finished_at"),
    }


def list_project_summaries(conn=None, user_id=None):
    """History menu rows. Does not load scripts, messages, or other heavy columns."""
    pg = _pg()
    if pg is not None:
        return pg.list_project_summaries(user_id=user_id)
    db = conn or connect()
    with _LOCK:
        rows = db.execute(LIST_SUMMARY_SQL).fetchall()
    return [row_to_summary(row) for row in rows]


TICK_COLUMNS = (
    "id",
    "brief",
    "title",
    "status",
    "stage",
    "error",
    "events_json",
    "tasks_json",
    "progress_json",
    "prompt_id",
    "video_file",
    "model",
    "template",
    "created_at",
    "started_at",
    "updated_at",
    "finished_at",
    "content_revision",
)
TICK_SQL = "SELECT %s FROM projects WHERE id = ?" % ", ".join(TICK_COLUMNS)


def row_to_tick(row):
    """Lightweight project row for progress polling. Does not load script or messages."""
    if row is None:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "brief": data.get("brief") or "",
        "title": data.get("title") or "",
        "status": data.get("status") or "",
        "stage": data.get("stage") or "",
        "error": data.get("error"),
        "events": _loads(data.get("events_json"), []),
        "tasks": _loads(data.get("tasks_json"), []),
        "progress": _loads(data.get("progress_json"), {}),
        "prompt_id": data.get("prompt_id") or "",
        "video_file": data.get("video_file") or "",
        "model": data.get("model") or "qwen3.8-flash",
        "template": data.get("template") or "short-drama-director",
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at") or data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "finished_at": data.get("finished_at"),
        "content_revision": data.get("content_revision") or "",
    }


def read_project_tick(project_id, conn=None):
    """Progress tick row. Does not SELECT script, storyboard, or messages."""
    pg = _pg()
    if pg is not None:
        return pg.read_project_tick(project_id)
    db = conn or connect()
    with _LOCK:
        row = db.execute(TICK_SQL, (project_id,)).fetchone()
    return row_to_tick(row)


def list_projects(conn=None, user_id=None):
    """Full project rows for resume and other in-process work."""
    pg = _pg()
    if pg is not None:
        return pg.list_projects(user_id=user_id)
    db = conn or connect()
    with _LOCK:
        rows = db.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [row_to_project(row) for row in rows]


def delete_project(project_id, conn=None):
    pg = _pg()
    if pg is not None:
        return pg.delete_project(project_id)
    # Tombstone first: a worker still winding down must not upsert the row back.
    mark_deleted(project_id)
    db = conn or connect()
    folder = project_dir(project_id)
    with _LOCK:
        cur = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()
        deleted_row = cur.rowcount > 0
    deleted_files = False
    if folder.is_dir():
        root = DATA.resolve()
        target = folder.resolve()
        if root in target.parents:
            import shutil

            shutil.rmtree(target)
            deleted_files = True
    return deleted_row or deleted_files


def migrate_json_projects(conn=None):
    """One-time import of legacy data/web_projects/<id>/project.json files."""
    db = conn or connect()
    if not DATA.is_dir():
        return 0
    imported = 0
    for folder in DATA.iterdir():
        if not folder.is_dir():
            continue
        path = folder / "project.json"
        if not path.is_file():
            continue
        try:
            project = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        project_id = str(project.get("id") or folder.name)
        if not project_id:
            continue
        project["id"] = project_id
        with _LOCK:
            exists = db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
        if exists:
            continue
        write_project(project, conn=db)
        imported += 1
    return imported
