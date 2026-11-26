"""SQLite → PostgreSQL 一次性迁移（设计 §8 / 实施方案 §7 runbook 的工具）。

短暂停切换：先停全部出片并确认队列空闲，再运行本脚本；媒体目录与 OSS 对象不搬。
历史行 user_id 留空（仅运营后台可见）。SQLite 文件保留作回滚，不删除。

用法：
    AIVIDEO_PG_DSN=postgres://... python -m end.migrate_sqlite_to_pg [--dry-run]

迁移前检查：打印实库表结构与行数（确认没有代码之外的手改表）。
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from end import db, pg_store


def inspect_sqlite(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    report = []
    for table in tables:
        count = conn.execute("SELECT COUNT(*) AS n FROM %s" % table["name"]).fetchone()["n"]
        columns = [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table["name"])]
        report.append({"table": table["name"], "rows": count, "columns": columns})
    conn.close()
    return report


def migrate(sqlite_path, dry_run=False):
    if not pg_store.enabled():
        raise RuntimeError("未配置 AIVIDEO_PG_DSN")
    report = inspect_sqlite(sqlite_path)
    print("实库结构：")
    for item in report:
        print("  %s：%d 行，列 %s" % (item["table"], item["rows"], ", ".join(item["columns"])))
    names = {item["table"] for item in report}
    if names != {"projects"}:
        print("警告：存在 projects 之外的表，按实库结构人工核对后再迁。", file=sys.stderr)

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
    projects = [db.row_to_project(row) for row in rows]
    conn.close()

    pg_store.pool()  # ensure schema migrated
    with pg_store.pool().connection() as pg:
        existing = pg.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    if existing and not dry_run:
        raise RuntimeError("PG projects 已有 %d 行；拒绝重复导入（确认可覆盖时人工清理后再跑）" % existing)

    if dry_run:
        print("dry-run：将迁移 %d 行，user_id 全部留空。" % len(projects))
        return len(projects)

    from end.db import COLUMNS

    time_fields = ("created_at", "started_at", "updated_at", "finished_at")
    index = {name: i for i, name in enumerate(COLUMNS)}
    migrated = 0
    with pg_store.pool().connection() as pg:
        with pg.transaction():
            for project in projects:
                values = list(db._row_values(project))
                for field in time_fields:
                    values[index[field]] = pg_store._ts(values[index[field]])
                # 保留原 content_revision，避免迁移本身改变前端缓存指纹。
                values[index["content_revision"]] = project.get("content_revision") or ""
                placeholders = ", ".join(["%s"] * len(COLUMNS))
                pg.execute(
                    "INSERT INTO projects (%s) VALUES (%s) ON CONFLICT(id) DO NOTHING"
                    % (", ".join(COLUMNS), placeholders),
                    values,
                )
                migrated += 1
    with pg_store.pool().connection() as pg:
        after = pg.execute("SELECT COUNT(*) AS n FROM projects WHERE deleted_at IS NULL").fetchone()["n"]
    print("迁移完成：SQLite %d 行 → PG %d 行。" % (len(projects), after))
    if after != len(projects):
        raise RuntimeError("行数不一致，请核对后再切换")
    return migrated


def main():
    parser = argparse.ArgumentParser(description="SQLite projects → PostgreSQL（短暂停窗口内运行）")
    parser.add_argument("--sqlite", default=str(db.DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.sqlite)
    if not path.is_file():
        print("找不到 SQLite 库：%s" % path, file=sys.stderr)
        return 1
    migrate(path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())