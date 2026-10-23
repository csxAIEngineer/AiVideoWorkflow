"""SQLite store for AiVideo web projects. Artifacts stay on disk under data/web_projects/<id>/."""

import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "web_projects"
DB_PATH = ROOT / "data" / "aivideo.db"

_LOCK = threading.RLock()
_CONN = None

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
)


def project_dir(project_id):
    return DATA / project_id


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
            model TEXT NOT NULL DEFAULT 'deepseek-v4-pro',
            template TEXT NOT NULL DEFAULT 'short-drama-director',
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            finished_at TEXT
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
            "ALTER TABLE projects ADD COLUMN model TEXT NOT NULL DEFAULT 'deepseek-v4-pro'"
        )
    if "template" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN template TEXT NOT NULL DEFAULT 'short-drama-director'"
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
        "model": data.get("model") or "deepseek-v4-pro",
        "template": data.get("template") or "short-drama-director",
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at") or data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "finished_at": data.get("finished_at"),
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
        project.get("model") or "deepseek-v4-pro",
        project.get("template") or "short-drama-director",
        project.get("created_at") or "",
        project.get("started_at"),
        project.get("updated_at") or "",
        project.get("finished_at"),
    )


def read_project(project_id, conn=None):
    db = conn or connect()
    with _LOCK:
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_to_project(row)


def write_project(project, conn=None):
    if not project or not project.get("id"):
        raise ValueError("project.id 不能为空")
    db = conn or connect()
    folder = project_dir(project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    values = _row_values(project)
    placeholders = ", ".join("?" for _ in COLUMNS)
    assignments = ", ".join("%s = excluded.%s" % (name, name) for name in COLUMNS if name != "id")
    sql = (
        "INSERT INTO projects (%s) VALUES (%s) "
        "ON CONFLICT(id) DO UPDATE SET %s"
        % (", ".join(COLUMNS), placeholders, assignments)
    )
    with _LOCK:
        db.execute(sql, values)
        db.commit()
    return project


def list_projects(conn=None):
    db = conn or connect()
    with _LOCK:
        rows = db.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [row_to_project(row) for row in rows]


def delete_project(project_id, conn=None):
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
