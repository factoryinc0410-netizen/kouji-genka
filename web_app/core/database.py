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
        # users テーブル: 管理者画面向けの拡張カラム
        await _migrate_add_column(db, "users", "is_active", "INTEGER NOT NULL DEFAULT 1")
        await _migrate_add_column(db, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
        await _migrate_add_column(db, "users", "updated_at", "TEXT")
        # users テーブル: ブルートフォース対策（アカウントロックアウト）用カラム
        await _migrate_add_column(db, "users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0")
        await _migrate_add_column(db, "users", "locked_until", "TEXT")
        # users テーブル: RBAC ロール割当（既存 DB も追従できるよう ALTER で追加）。
        # SQLite の制約: ALTER TABLE ADD COLUMN で REFERENCES を付ける場合、
        # default は NULL でなければならない。NULLABLE = ロール未割当を許容。
        await _migrate_add_column(
            db, "users", "role_id",
            "INTEGER REFERENCES roles(id) ON DELETE SET NULL",
        )
        # sessions テーブル: CSRF（Synchronizer Token Pattern）用カラム
        await _migrate_add_column(db, "sessions", "csrf_token", "TEXT")
        # user_audit_logs.operator_id: ログイン失敗（操作者不明）も記録できるよう NULL 許容化
        await _migrate_relax_audit_operator_nullable(db)
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


async def _migrate_relax_audit_operator_nullable(db) -> None:
    """user_audit_logs.operator_id の NOT NULL 制約を外す（NULL 許容化）。

    SQLite は `ALTER COLUMN` で NOT NULL を解除できないため、テーブルを
    再構築してデータをコピーする。すでに NULL 許容なら何もしない。
    """
    import logging
    _log = logging.getLogger("web_app.database")

    cursor = await db.execute("PRAGMA table_info(user_audit_logs)")
    cols = await cursor.fetchall()
    op_col = next((row for row in cols if row[1] == "operator_id"), None)
    # 行構造: (cid, name, type, notnull, dflt_value, pk)
    if op_col is None or op_col[3] == 0:
        return  # テーブル未作成 もしくは すでに NULL 許容

    _log.info("マイグレーション: user_audit_logs.operator_id を NULL 許容化（テーブル再構築）")

    # 外部キーなし・トリガーなしの単純テーブルなので、退避→DROP→再作成→コピーバック
    await db.executescript(
        """
        CREATE TABLE user_audit_logs__new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id     TEXT,
            target_user_id  TEXT,
            action          TEXT NOT NULL,
            timestamp       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            ip_address      TEXT
        );
        INSERT INTO user_audit_logs__new (id, operator_id, target_user_id, action, timestamp, ip_address)
            SELECT id, operator_id, target_user_id, action, timestamp, ip_address
            FROM user_audit_logs;
        DROP TABLE user_audit_logs;
        ALTER TABLE user_audit_logs__new RENAME TO user_audit_logs;
        CREATE INDEX IF NOT EXISTS idx_user_audit_logs_timestamp ON user_audit_logs(timestamp);
        CREATE INDEX IF NOT EXISTS idx_user_audit_logs_operator ON user_audit_logs(operator_id);
        """
    )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    -- RBAC: 0 ないし 1 ロールを割当（FK は roles 側 DROP 時に SET NULL）。
    -- forward reference: roles テーブルは下で定義されるが SQLite は許容する。
    role_id     INTEGER REFERENCES roles(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT,
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
-- 管理者操作の監査ログ（独立した監査資産として users への FK は貼らない）
-- ユーザー削除後も履歴を残せるよう、operator_id / target_user_id は
-- 単なる TEXT として保持し、表示時に LEFT JOIN で username を補完する。
-- ========================================
CREATE TABLE IF NOT EXISTS user_audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     TEXT,
    target_user_id  TEXT,
    action          TEXT NOT NULL,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ip_address      TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_audit_logs_timestamp ON user_audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_user_audit_logs_operator ON user_audit_logs(operator_id);

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

-- ========================================
-- 機能ごとの階層的アクセス制御
-- access_level: 'none' / 'general' / 'manager'
-- 強さの順序: manager > general > none
-- 1ユーザー × 1機能 で 1行（UNIQUE）。レコードが無い場合は 'none' 相当と解釈する。
-- ========================================
CREATE TABLE IF NOT EXISTS user_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature_name TEXT    NOT NULL,
    access_level TEXT    NOT NULL DEFAULT 'none'
                         CHECK (access_level IN ('none','general','manager')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT,
    UNIQUE(user_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_permissions_feature ON user_permissions(feature_name);

-- ========================================
-- RBAC: ロール定義
-- 1ロール 1名（UNIQUE）。論理削除は is_active=0 で表現する。
-- 削除時の挙動は users.role_id 側で SET NULL（CASCADE しない）。
-- ========================================
CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT
);

-- ========================================
-- RBAC: ロールに紐づく機能別権限
-- 認可判定は「user_permissions の値 OR この role_permissions の値」の論理和。
-- 後者は users.role_id 経由で参照。同階層 (none/general/manager) を共有する
-- ことで、has_permission の比較ロジックを 1 系統にまとめられる。
-- ========================================
CREATE TABLE IF NOT EXISTS role_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    feature_name TEXT    NOT NULL,
    access_level TEXT    NOT NULL DEFAULT 'none'
                         CHECK (access_level IN ('none','general','manager')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT,
    UNIQUE(role_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions(role_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_feature ON role_permissions(feature_name);
"""
