"""
認証ロジック — パスワードハッシュ・セッション管理
"""
import secrets
import uuid
from datetime import datetime, timedelta

import aiosqlite
import bcrypt

from web_app.core.config import (
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_FAILURES,
    SESSION_MAX_AGE,
)
from web_app.core.csrf import generate_csrf_token

# SQLite に保存する日時の書式（DEFAULT (datetime('now','localtime')) と一致）
_DT_FMT = "%Y-%m-%d %H:%M:%S"


# ── パスワード ────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """平文パスワードを bcrypt ハッシュ化する。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """平文パスワードとハッシュを照合する。"""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── パスワードポリシー検証 ──────────────────────────────────
# Phase 13: 強度の最低ラインを定数で持ち、UI 注記とコードの整合を取る。
PASSWORD_MIN_LENGTH: int = 12
# username 部分一致の判定に使う最小長（短すぎる username は誤検出を生むので除外）
_USERNAME_SUBSTRING_MIN_LEN: int = 3


def validate_password_policy(
    password: str, username: str | None = None
) -> list[str]:
    """パスワードがポリシーを満たしているか検証する。

    違反内容を日本語メッセージのリストで返す。空リストなら合格。

    ポリシー:
        - PASSWORD_MIN_LENGTH (12) 文字以上
        - 大文字 / 小文字 / 数字 / 記号 をそれぞれ1文字以上含む
          （記号 = 英数字でも空白でもない可視文字）
        - username が与えられている場合、完全一致は不可。
          username が3文字以上なら、部分文字列として含むのも不可（大小無視）。

    UI 側はこのリストを <ul> でそのまま列挙する想定。
    """
    errors: list[str] = []

    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"{PASSWORD_MIN_LENGTH}文字以上で入力してください。")

    if not any(c.isupper() for c in password):
        errors.append("大文字（A-Z）を1文字以上含めてください。")
    if not any(c.islower() for c in password):
        errors.append("小文字（a-z）を1文字以上含めてください。")
    if not any(c.isdigit() for c in password):
        errors.append("数字（0-9）を1文字以上含めてください。")
    if not any((not c.isalnum()) and (not c.isspace()) for c in password):
        errors.append("記号（!@#$ など）を1文字以上含めてください。")

    if username:
        u_lower = username.lower()
        p_lower = password.lower()
        if p_lower == u_lower:
            errors.append("ユーザー名と同一のパスワードは使用できません。")
        elif (
            len(username) >= _USERNAME_SUBSTRING_MIN_LEN
            and u_lower in p_lower
        ):
            errors.append("パスワードにユーザー名を含めることはできません。")

    return errors


# ── ユーザー操作 ──────────────────────────────────────────────

async def create_user(
    db: aiosqlite.Connection,
    username: str,
    display_name: str,
    password: str,
    is_admin: bool = False,
) -> str:
    """新規ユーザーを作成し、user_id を返す。"""
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(password)
    await db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, username, display_name, pw_hash, int(is_admin)),
    )
    await db.commit()
    return user_id


async def authenticate(
    db: aiosqlite.Connection, username: str, password: str
) -> dict:
    """ユーザー名とパスワードで認証する。

    戻り値は dict 固定で、status により呼び出し元が分岐する:
        - "ok"      : 認証成功。"user" にユーザー辞書を含む
        - "invalid" : ユーザー不在 / is_active=0 / パスワード不一致
        - "locked"  : アカウントが現在ロック中、または本リクエストでロックが
                      新規発動した場合
    "user" / "locked_until" は該当ない場合 None。
    failed_attempts はロック判定後の現在値（呼び出し元のログ用、UI表示はしない）。

    副作用:
        - 失敗時: failed_login_attempts をインクリメントし、
          LOGIN_MAX_FAILURES 回到達で locked_until を設定する
        - 成功時: failed_login_attempts=0 / locked_until=NULL にリセット

    is_active=0 のユーザーは "invalid" として扱い、ロック判定や失敗カウンタの
    更新は行わない（無効化されたユーザーが攻撃で巻き込まれるのを避ける）。
    """
    cursor = await db.execute(
        "SELECT id, username, display_name, password_hash, is_admin, "
        "is_active, must_change_password, failed_login_attempts, locked_until "
        "FROM users WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()

    # ユーザー不在 / 無効化済み: 失敗カウンタは更新しない
    if row is None or not bool(row["is_active"]):
        return {"status": "invalid", "user": None,
                "locked_until": None, "failed_attempts": 0}

    user_id = row["id"]
    locked_until_raw = row["locked_until"]
    now = datetime.now()

    # 既にロック中ならパスワード検証せず即返す
    if locked_until_raw:
        try:
            locked_until_dt = datetime.strptime(locked_until_raw, _DT_FMT)
        except ValueError:
            locked_until_dt = None
        if locked_until_dt and now < locked_until_dt:
            return {
                "status": "locked", "user": None,
                "locked_until": locked_until_raw,
                "failed_attempts": int(row["failed_login_attempts"] or 0),
            }

    # 通常のパスワード照合
    if not verify_password(password, row["password_hash"]):
        new_attempts = int(row["failed_login_attempts"] or 0) + 1
        if new_attempts >= LOGIN_MAX_FAILURES:
            # 閾値到達 → ロック発動
            locked_until = (
                now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            ).strftime(_DT_FMT)
            await db.execute(
                "UPDATE users SET failed_login_attempts = ?, locked_until = ?, "
                "updated_at = ? WHERE id = ?",
                (new_attempts, locked_until, _now_str(), user_id),
            )
            await db.commit()
            return {"status": "locked", "user": None,
                    "locked_until": locked_until,
                    "failed_attempts": new_attempts}
        await db.execute(
            "UPDATE users SET failed_login_attempts = ?, updated_at = ? "
            "WHERE id = ?",
            (new_attempts, _now_str(), user_id),
        )
        await db.commit()
        return {"status": "invalid", "user": None,
                "locked_until": None, "failed_attempts": new_attempts}

    # 認証成功 → 失敗カウンタとロック時刻をクリア
    await db.execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
        "updated_at = ? WHERE id = ?",
        (_now_str(), user_id),
    )
    await db.commit()
    return {
        "status": "ok",
        "user": {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "is_admin": bool(row["is_admin"]),
            "is_active": bool(row["is_active"]),
            "must_change_password": bool(row["must_change_password"]),
        },
        "locked_until": None,
        "failed_attempts": 0,
    }


# ── セッション管理 ────────────────────────────────────────────

async def create_session(db: aiosqlite.Connection, user_id: str) -> str:
    """セッショントークンと CSRF トークンを発行・保存し、セッショントークンを返す。

    CSRF トークンは Synchronizer Token Pattern 用で、sessions.csrf_token に
    保存される。テンプレートからは get_session_user 経由で参照する。
    """
    token = secrets.token_urlsafe(48)
    csrf = generate_csrf_token()
    expires = datetime.now() + timedelta(seconds=SESSION_MAX_AGE)
    await db.execute(
        "INSERT INTO sessions (token, user_id, csrf_token, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (token, user_id, csrf, expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    await db.commit()
    return token


async def get_session_user(db: aiosqlite.Connection, token: str) -> dict | None:
    """トークンからユーザー情報を取得する。期限切れまたは無効化済みなら None。

    is_active=0 のユーザーは即座にセッション無効として扱い、対応するセッション
    レコードも削除する（管理者が無効化した瞬間からアクセス不可）。

    sessions.csrf_token は Synchronizer Token Pattern のサーバー側保管。
    過去の CSRF 未対応バージョンで作られたセッションでは NULL のことがあるため、
    その場合はここで lazy 発行して書き戻す（既存ユーザーを切らないため）。
    """
    cursor = await db.execute(
        "SELECT u.id, u.username, u.display_name, u.is_admin, "
        "u.is_active, u.must_change_password, u.role_id, "
        "s.expires_at, s.csrf_token "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ?",
        (token,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if not bool(row["is_active"]):
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()
        return None
    expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expires:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()
        return None

    csrf = row["csrf_token"]
    if not csrf:
        # 過去のセッションには csrf_token が無い → ここで一度だけ発行して保存
        csrf = generate_csrf_token()
        await db.execute(
            "UPDATE sessions SET csrf_token = ? WHERE token = ?", (csrf, token)
        )
        await db.commit()

    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "role_id": row["role_id"],  # NULL 可。ロール未割当ユーザーは None
        "csrf_token": csrf,
    }


async def delete_session(db: aiosqlite.Connection, token: str) -> None:
    """セッションを削除（ログアウト）。"""
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    await db.commit()


async def cleanup_expired_sessions(db: aiosqlite.Connection) -> int:
    """期限切れセッションを一括削除し、削除件数を返す。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = await db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    await db.commit()
    return cursor.rowcount


# ── 管理者画面向け: ユーザー CRUD ヘルパ ─────────────────────
# 既存の create_user / authenticate と同じ aiosqlite 接続を前提とし、
# 取得系・更新系をここに集約する。物理削除はジョブ履歴を CASCADE で
# 失うため、原則 set_user_active(..., False) による無効化を推奨。

def _now_str() -> str:
    """SQLite の datetime('now','localtime') と同じ書式で現在時刻を返す。"""
    return datetime.now().strftime(_DT_FMT)


def _compute_is_locked(locked_until_raw: str | None) -> bool:
    """locked_until 文字列を現在時刻と比較してロック中かを返す。"""
    if not locked_until_raw:
        return False
    try:
        return datetime.now() < datetime.strptime(locked_until_raw, _DT_FMT)
    except ValueError:
        return False


def _user_row_to_dict(row) -> dict:
    """list_users / get_user_by_id 共通の行 → 辞書変換。

    is_locked は locked_until の比較済みフラグで、テンプレート側から
    そのまま {% if u.is_locked %} と書ける。
    """
    locked_until = row["locked_until"] if "locked_until" in row.keys() else None
    failed_attempts = (
        int(row["failed_login_attempts"]) if "failed_login_attempts" in row.keys() else 0
    )
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "failed_login_attempts": failed_attempts,
        "locked_until": locked_until,
        "is_locked": _compute_is_locked(locked_until),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_users(db: aiosqlite.Connection) -> list[dict]:
    """全ユーザーを作成日時の昇順で返す（管理者画面の一覧用）。"""
    cursor = await db.execute(
        "SELECT id, username, display_name, is_admin, is_active, "
        "must_change_password, failed_login_attempts, locked_until, "
        "created_at, updated_at "
        "FROM users ORDER BY created_at ASC"
    )
    rows = await cursor.fetchall()
    return [_user_row_to_dict(r) for r in rows]


async def get_user_by_id(db: aiosqlite.Connection, user_id: str) -> dict | None:
    """ユーザーIDから1件取得する。存在しなければ None。"""
    cursor = await db.execute(
        "SELECT id, username, display_name, is_admin, is_active, "
        "must_change_password, failed_login_attempts, locked_until, "
        "created_at, updated_at "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _user_row_to_dict(row)


async def unlock_user(db: aiosqlite.Connection, user_id: str) -> bool:
    """アカウントロックを手動で解除する（failed_login_attempts=0, locked_until=NULL）。

    既にロックされていないユーザーに対しても安全に呼べる（冪等）。
    存在しないユーザーIDなら False を返す。
    """
    cursor = await db.execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
        "updated_at = ? WHERE id = ?",
        (_now_str(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_password(
    db: aiosqlite.Connection,
    user_id: str,
    new_password: str,
    *,
    must_change: bool = False,
) -> bool:
    """ユーザーのパスワードを差し替える。

    - `must_change=True` を渡すと「次回ログイン時にパスワード変更必須」フラグを立てる
      （管理者によるリセット直後を想定）。
    - `must_change=False` の場合は本人による変更とみなしてフラグを下ろす。
    存在しないユーザーIDなら False を返す。
    """
    if not new_password:
        raise ValueError("new_password must not be empty")
    pw_hash = hash_password(new_password)
    cursor = await db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = ?, "
        "updated_at = ? WHERE id = ?",
        (pw_hash, int(bool(must_change)), _now_str(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def set_user_active(
    db: aiosqlite.Connection, user_id: str, active: bool
) -> bool:
    """有効化/無効化を切り替える。

    無効化（active=False）した場合、当該ユーザーの全セッションも破棄して
    即座にログアウト状態にする。存在しないユーザーIDなら False。
    """
    cursor = await db.execute(
        "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
        (int(bool(active)), _now_str(), user_id),
    )
    if cursor.rowcount == 0:
        await db.commit()
        return False
    if not active:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    return True


async def set_user_admin(
    db: aiosqlite.Connection, user_id: str, admin: bool
) -> bool:
    """管理者権限を付与/剥奪する。存在しないユーザーIDなら False。"""
    cursor = await db.execute(
        "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
        (int(bool(admin)), _now_str(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_user(db: aiosqlite.Connection, user_id: str) -> bool:
    """ユーザーを物理削除する。

    sessions / jobs は ON DELETE CASCADE で連鎖削除されるため、過去ジョブ履歴も
    失う点に注意。原則 set_user_active による無効化を優先すること。
    """
    cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount > 0


# ── 監査ログ ──────────────────────────────────────────────────
# user_audit_logs テーブルへの追記。呼び出し元では try/except で囲い、
# ログ記録の失敗が主操作の成功を打ち消さないようにすること。

async def log_admin_action(
    db: aiosqlite.Connection,
    operator_id: str | None,
    target_user_id: str | None,
    action: str,
    ip_address: str | None = None,
) -> int:
    """監査ログ（user_audit_logs）に 1 件追加し、自動採番された id を返す。

    - operator_id : 操作した者の users.id。ログイン失敗のように操作者が
                    特定できない場合は None を渡してよい。
    - target_user_id : 操作対象の users.id（対象不在の場合は None）
    - action : "CREATE_USER" / "RESET_PASSWORD" / "TOGGLE_ACTIVE" /
               "login_success" / "login_failure" など
    - ip_address : request.client.host から取得した送信元 IP
    """
    cursor = await db.execute(
        "INSERT INTO user_audit_logs (operator_id, target_user_id, action, ip_address) "
        "VALUES (?, ?, ?, ?)",
        (operator_id, target_user_id, action, ip_address),
    )
    await db.commit()
    return cursor.lastrowid


def _audit_where_clause(
    action: str | None, target_user_id: str | None
) -> tuple[str, list]:
    """list_audit_logs / count_audit_logs 共通の WHERE 句を組み立てる。"""
    conds: list[str] = []
    params: list = []
    if action:
        conds.append("a.action = ?")
        params.append(action)
    if target_user_id:
        conds.append("a.target_user_id = ?")
        params.append(target_user_id)
    where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where_sql, params


async def list_audit_logs(
    db: aiosqlite.Connection,
    limit: int = 20,
    offset: int = 0,
    action: str | None = None,
    target_user_id: str | None = None,
) -> list[dict]:
    """監査ログを新しい順でページ単位に返す。username は LEFT JOIN で補完する。

    - limit / offset でページネーション
    - action 文字列で完全一致フィルタ（例: "login_failure"）
    - target_user_id で対象ユーザーフィルタ（users.id）
    """
    where_sql, params = _audit_where_clause(action, target_user_id)
    sql = (
        "SELECT a.id, a.operator_id, a.target_user_id, a.action, "
        "       a.timestamp, a.ip_address, "
        "       op.username AS operator_username, "
        "       tg.username AS target_username "
        "FROM user_audit_logs a "
        "LEFT JOIN users op ON a.operator_id = op.id "
        "LEFT JOIN users tg ON a.target_user_id = tg.id "
        f"{where_sql} "
        "ORDER BY a.id DESC "
        "LIMIT ? OFFSET ?"
    )
    cursor = await db.execute(sql, (*params, limit, offset))
    rows = await cursor.fetchall()
    return [
        {
            "id": r["id"],
            "operator_id": r["operator_id"],
            "operator_username": r["operator_username"],
            "target_user_id": r["target_user_id"],
            "target_username": r["target_username"],
            "action": r["action"],
            "timestamp": r["timestamp"],
            "ip_address": r["ip_address"],
        }
        for r in rows
    ]


async def count_audit_logs(
    db: aiosqlite.Connection,
    action: str | None = None,
    target_user_id: str | None = None,
) -> int:
    """list_audit_logs と同じフィルタ条件で総件数を返す（ページ数計算用）。"""
    where_sql, params = _audit_where_clause(action, target_user_id)
    cursor = await db.execute(
        f"SELECT COUNT(*) AS cnt FROM user_audit_logs a {where_sql}",
        params,
    )
    row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


# ── 機能ごとの階層的アクセス制御 ─────────────────────────────
# user_permissions テーブルに保存された access_level を読み出し、
# 要求レベルを満たすかを判定する。
#
# レベル階層:
#     manager (2) > general (1) > none (0)
# レコード未登録 = 'none' と等価。is_admin=True のユーザーは常に許可される。

ACCESS_LEVELS: tuple[str, ...] = ("none", "general", "manager")
_LEVEL_RANK: dict[str, int] = {name: idx for idx, name in enumerate(ACCESS_LEVELS)}


def _rank(level: str | None) -> int:
    """access_level 文字列 → 強さの数値。未知文字列や None は 0 ('none') 扱い。"""
    if level is None:
        return 0
    return _LEVEL_RANK.get(level, 0)


def is_level_at_least(actual: str | None, required: str) -> bool:
    """actual >= required を比較する純関数（DBアクセスなし）。"""
    return _rank(actual) >= _rank(required)


async def get_user_access_level(
    db: aiosqlite.Connection, user_id: str, feature_name: str
) -> str:
    """指定ユーザー × 機能の access_level を返す。レコード無しなら 'none'。"""
    cursor = await db.execute(
        "SELECT access_level FROM user_permissions "
        "WHERE user_id = ? AND feature_name = ?",
        (user_id, feature_name),
    )
    row = await cursor.fetchone()
    if row is None:
        return "none"
    level = row["access_level"]
    return level if level in _LEVEL_RANK else "none"


def effective_access_level(
    user_level: str | None, role_level: str | None
) -> str:
    """個別権限とロール権限の論理和（高い方を採用）。

    - 両方 None / 'none' / 未知文字列 なら 'none' を返す
    - 純関数で DB アクセスなし。pre-loaded な user dict から呼ぶ用途を想定
    """
    ul = user_level if user_level in _LEVEL_RANK else "none"
    rl = role_level if role_level in _LEVEL_RANK else "none"
    return ul if _rank(ul) >= _rank(rl) else rl


def has_permission(
    user: dict,
    feature_name: str,
    required_level: str = "general",
) -> bool:
    """user が feature_name に対し required_level 以上の権限を持つか判定する（同期版）。

    認可判定の単一窓口。ユーザー個別権限（user["permissions"]）とロール権限
    （user["role_permissions"]）の **論理和（max）** を取って required と比較する。

    - is_admin=True のユーザーはすべての機能・レベルで常に True（バックドア兼運用上の保険）。
    - required_level が未知の文字列なら ValueError。
    - 'none' を required_level に渡すケースは「ログイン済みなら誰でも可」と等価。
    - user dict 不在 / permissions 未ロード時も安全側で False を返す（is_admin だけは尊重）。

    呼び出し前提:
        get_current_user (dependencies.py) が user["permissions"] と
        user["role_permissions"] を埋めていること。テンプレート用 has_perm からも
        本関数を呼ぶことで認可ロジックを 1 系統に統一する。
    """
    if required_level not in _LEVEL_RANK:
        raise ValueError(f"unknown access_level: {required_level!r}")

    if not user:
        return False
    if user.get("is_admin"):
        return True

    user_perms: dict[str, str] = user.get("permissions") or {}
    role_perms: dict[str, str] = user.get("role_permissions") or {}
    effective = effective_access_level(
        user_perms.get(feature_name),
        role_perms.get(feature_name),
    )
    return is_level_at_least(effective, required_level)


async def set_user_permission(
    db: aiosqlite.Connection,
    user_id: str,
    feature_name: str,
    access_level: str,
) -> None:
    """ユーザー × 機能の access_level を upsert する（管理画面から呼ばれる想定）。

    'none' を渡した場合は行を削除し、レコード無し = 'none' という規約に揃える。
    """
    if access_level not in _LEVEL_RANK:
        raise ValueError(f"unknown access_level: {access_level!r}")

    if access_level == "none":
        await db.execute(
            "DELETE FROM user_permissions WHERE user_id = ? AND feature_name = ?",
            (user_id, feature_name),
        )
        await db.commit()
        return

    await db.execute(
        "INSERT INTO user_permissions (user_id, feature_name, access_level, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, feature_name) DO UPDATE SET "
        "    access_level = excluded.access_level, "
        "    updated_at   = excluded.updated_at",
        (user_id, feature_name, access_level, _now_str()),
    )
    await db.commit()


async def list_user_permissions(
    db: aiosqlite.Connection, user_id: str
) -> dict[str, str]:
    """指定ユーザーの全権限を {feature_name: access_level} で返す。"""
    cursor = await db.execute(
        "SELECT feature_name, access_level FROM user_permissions WHERE user_id = ?",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return {r["feature_name"]: r["access_level"] for r in rows}


# ── RBAC: ロール権限 ────────────────────────────────────────────
# user_permissions と同じインターフェース（{feature_name: access_level}）で
# ロールに紐づく権限を返す。dependencies.py の get_current_user が
# user_permissions と並べて pre-load し、has_permission が論理和で評価する。

async def get_role_permissions(
    db: aiosqlite.Connection, role_id: int | None
) -> dict[str, str]:
    """role_id に紐づく全権限を {feature_name: access_level} で返す。

    - role_id=None なら空辞書（ロール未割当ユーザー想定）
    - 不正な access_level（CHECK 制約は守られているはずだが念のため）はスキップ
    """
    if role_id is None:
        return {}
    cursor = await db.execute(
        "SELECT feature_name, access_level FROM role_permissions WHERE role_id = ?",
        (role_id,),
    )
    rows = await cursor.fetchall()
    result: dict[str, str] = {}
    for r in rows:
        level = r["access_level"]
        if level in _LEVEL_RANK:
            result[r["feature_name"]] = level
    return result
