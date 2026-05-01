"""
SQLite データベース管理 — 接続プール・マイグレーション
"""
import aiosqlite
from web_app.core.config import DATABASE_PATH

_DB_PATH = str(DATABASE_PATH)


async def get_db() -> aiosqlite.Connection:
    """リクエストごとの DB 接続を取得する。"""
    db = await aiosqlite.connect(_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    """テーブル作成（初回起動時のマイグレーション）。"""
    db = await aiosqlite.connect(_DB_PATH)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(_SCHEMA_SQL)
        await db.commit()
        # マイグレーション: 既存DB対応
        await _migrate_add_column(db, "cc_workers", "group_name", "TEXT")
        await _migrate_add_column(db, "cc_sites", "initial_cumulative_cost", "REAL DEFAULT 0")
        await _migrate_add_column(db, "cc_sites", "is_active", "INTEGER NOT NULL DEFAULT 1")
        await _migrate_add_column(db, "cc_workers", "is_active", "INTEGER NOT NULL DEFAULT 1")
        await _migrate_seed_groups(db)
        await _migrate_trim_group_names(db)
        await db.commit()
    finally:
        await db.close()


async def _migrate_seed_groups(db) -> None:
    """既存の cc_workers.group_name を cc_groups に自動登録する。"""
    import logging
    _log = logging.getLogger("web_app.database")
    try:
        cursor = await db.execute(
            "SELECT DISTINCT group_name FROM cc_workers WHERE group_name IS NOT NULL AND group_name != ''"
        )
        existing_names = [row[0] for row in await cursor.fetchall()]
        for name in existing_names:
            await db.execute(
                "INSERT OR IGNORE INTO cc_groups (group_name) VALUES (?)", (name,)
            )
        if existing_names:
            _log.info("マイグレーション: %d 件のグループ名を cc_groups に登録しました", len(existing_names))
    except Exception:
        pass  # cc_workers がまだ無い場合など


async def _migrate_trim_group_names(db) -> None:
    """group_name の前後空白（全角含む）を除去し、表記揺れを防止する。"""
    import logging
    _log = logging.getLogger("web_app.database")
    try:
        # TRIM で半角スペース除去 + REPLACE で全角スペース除去
        for table in ("cc_workers", "cc_groups", "cc_site_group_budgets"):
            await db.execute(f"""
                UPDATE {table}
                SET group_name = TRIM(REPLACE(group_name, X'E38080', ''))
                WHERE group_name IS NOT NULL
                  AND group_name != TRIM(REPLACE(group_name, X'E38080', ''))
            """)
        _log.info("マイグレーション: group_name のTRIM処理を実行しました")
    except Exception:
        pass


async def _migrate_add_column(db, table: str, column: str, col_type: str) -> None:
    """既存テーブルにカラムがなければ追加する。"""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cursor.fetchall()]
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        import logging
        logging.getLogger("web_app.database").info(
            "マイグレーション: %s.%s カラムを追加しました", table, column
        )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    file_hash       TEXT,
    upload_path     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    total_vendors   INTEGER,
    success_count   INTEGER,
    result_zip      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ========================================
-- 工事日報集計: 現場マスタ
-- ========================================
CREATE TABLE IF NOT EXISTS cc_sites (
    site_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    site_code              TEXT    UNIQUE NOT NULL,
    site_name              TEXT    NOT NULL,
    budget                 REAL    NOT NULL DEFAULT 0,
    cumulative             REAL    NOT NULL DEFAULT 0,
    initial_cumulative_cost REAL   NOT NULL DEFAULT 0,
    status                 TEXT    NOT NULL DEFAULT '進行中',
    is_active              INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at             TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ========================================
-- 工事日報集計: グループマスタ
-- ========================================
CREATE TABLE IF NOT EXISTS cc_groups (
    group_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name    TEXT    UNIQUE NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ========================================
-- 工事日報集計: 現場別グループ予算
-- ========================================
CREATE TABLE IF NOT EXISTS cc_site_group_budgets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id       INTEGER NOT NULL REFERENCES cc_sites(site_id) ON DELETE CASCADE,
    group_name    TEXT    NOT NULL,
    budget        REAL    NOT NULL DEFAULT 0,
    initial_cumulative_cost REAL NOT NULL DEFAULT 0,
    UNIQUE(site_id, group_name)
);

-- ========================================
-- 工事日報集計: 作業員単価マスタ
-- ========================================
CREATE TABLE IF NOT EXISTS cc_workers (
    worker_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_name   TEXT    NOT NULL,
    role          TEXT,
    group_name    TEXT,
    daily_rate    REAL    NOT NULL DEFAULT 0,
    overtime_rate REAL    NOT NULL DEFAULT 0,
    night_rate    REAL    NOT NULL DEFAULT 0,
    transport     REAL    NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ========================================
-- 工事日報集計: 機材・経費単価マスタ
-- ========================================
CREATE TABLE IF NOT EXISTS cc_equipment (
    equip_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    equip_name    TEXT    NOT NULL,
    category      TEXT    NOT NULL DEFAULT '機械',
    unit_price    REAL    NOT NULL DEFAULT 0,
    unit          TEXT    NOT NULL DEFAULT '日',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ========================================
-- 工事日報集計: 累計更新履歴
-- ========================================
CREATE TABLE IF NOT EXISTS cc_cumulative_history (
    history_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id           INTEGER NOT NULL REFERENCES cc_sites(site_id),
    target_month      TEXT    NOT NULL,
    monthly_cost      REAL    NOT NULL,
    cumulative_before REAL    NOT NULL,
    cumulative_after  REAL    NOT NULL,
    confirmed_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(site_id, target_month)
);

-- ========================================
-- 工事日報集計: 処理ログ
-- ========================================
CREATE TABLE IF NOT EXISTS cc_process_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_month    TEXT    NOT NULL,
    file_name       TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    aggregated_json TEXT,
    output_site     TEXT,
    output_worker   TEXT,
    processed_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    confirmed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_cc_cumhist_site ON cc_cumulative_history(site_id);
CREATE INDEX IF NOT EXISTS idx_cc_cumhist_month ON cc_cumulative_history(target_month);
CREATE INDEX IF NOT EXISTS idx_cc_proclog_month ON cc_process_log(target_month);
"""
