-- 0001_accounts.sql — 账户系统第一阶段 schema（唯一事实源：主仓库后端）
-- 设计依据：output/account-system/design-reference.md §5 与 implementation-plan.md §5。
-- 积分只用整数；人民币用分（第三阶段表在后续迁移中加入）。

CREATE TABLE IF NOT EXISTS users (
    id                   BIGSERIAL PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT,
    role                 TEXT NOT NULL DEFAULT 'user',
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    phone                TEXT UNIQUE,
    status               TEXT NOT NULL DEFAULT 'active',
    last_login_at        TIMESTAMPTZ,
    prev_login_at        TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audience     TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, audience);

CREATE TABLE IF NOT EXISTS login_events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    username   TEXT NOT NULL,
    ok         BOOLEAN NOT NULL,
    audience   TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_login_events_username ON login_events(username, created_at DESC);

CREATE TABLE IF NOT EXISTS token_accounts (
    user_id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance    BIGINT NOT NULL DEFAULT 0,
    version    BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- projects：从 SQLite data/aivideo.db 迁入的 23 列 + 归属与软删。
-- JSON 列暂保留 TEXT（与现有 row_to_project/_loads 映射兼容）；
-- 时间戳转 timestamptz，读写层负责 ISO 字符串互转。
CREATE TABLE IF NOT EXISTS projects (
    id               TEXT PRIMARY KEY,
    brief            TEXT NOT NULL,
    messages_json    TEXT NOT NULL DEFAULT '[]',
    title            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,
    stage            TEXT NOT NULL DEFAULT '',
    script           TEXT NOT NULL DEFAULT '',
    storyboard       TEXT NOT NULL DEFAULT '',
    image_prompts    TEXT NOT NULL DEFAULT '',
    error            TEXT,
    events_json      TEXT NOT NULL DEFAULT '[]',
    tasks_json       TEXT NOT NULL DEFAULT '[]',
    progress_json    TEXT NOT NULL DEFAULT '{}',
    cloud_log        TEXT NOT NULL DEFAULT '',
    prompt_id        TEXT NOT NULL DEFAULT '',
    video_file       TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT 'qwen3.8-flash',
    template         TEXT NOT NULL DEFAULT 'short-drama-director',
    created_at       TIMESTAMPTZ NOT NULL,
    started_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    content_revision TEXT NOT NULL DEFAULT '',
    user_id          BIGINT REFERENCES users(id),
    deleted_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_user_created ON projects(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_user_status ON projects(user_id, status);

-- 目录表：一至三阶段随列表需求启用；只记对象键/相对路径，不存预签名地址。
CREATE TABLE IF NOT EXISTS project_assets (
    project_id TEXT NOT NULL,
    asset_key  TEXT NOT NULL,
    object_key TEXT NOT NULL DEFAULT '',
    meta_json  TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, asset_key)
);

CREATE TABLE IF NOT EXISTS project_videos (
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    filename   TEXT NOT NULL,
    object_key TEXT NOT NULL DEFAULT '',
    meta_json  TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, kind, filename)
);