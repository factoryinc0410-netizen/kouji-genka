"""
認証ロジック — パスワードハッシュ・セッション管理
"""
import secrets
import uuid
from datetime import datetime, timedelta

import aiosqlite
import bcrypt

from web_app.core.config import SESSION_MAX_AGE


# ── パスワード ────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """平文パスワードを bcrypt ハッシュ化する。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """平文パスワードとハッシュを照合する。"""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


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
) -> dict | None:
    """ユーザー名とパスワードで認証し、成功時にユーザー辞書を返す。"""
    cursor = await db.execute(
        "SELECT id, username, display_name, password_hash, is_admin FROM users WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
    }


# ── セッション管理 ────────────────────────────────────────────

async def create_session(db: aiosqlite.Connection, user_id: str) -> str:
    """セッショントークンを生成・保存して返す。"""
    token = secrets.token_urlsafe(48)
    expires = datetime.now() + timedelta(seconds=SESSION_MAX_AGE)
    await db.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    await db.commit()
    return token


async def get_session_user(db: aiosqlite.Connection, token: str) -> dict | None:
    """トークンからユーザー情報を取得する。期限切れなら None。"""
    cursor = await db.execute(
        "SELECT u.id, u.username, u.display_name, u.is_admin, s.expires_at "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ?",
        (token,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expires:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
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
