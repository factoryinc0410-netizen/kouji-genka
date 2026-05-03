"""
FastAPI 共通依存関係 — DB 接続・認証済みユーザー取得
"""
from fastapi import Depends, HTTPException, Request, status

import aiosqlite

from web_app.core.database import get_db
from web_app.core.auth import get_session_user

SESSION_COOKIE = "session_token"


class RequiresLoginException(Exception):
    """未認証時にログイン画面へリダイレクトさせるための例外。"""
    pass


async def db_dependency() -> aiosqlite.Connection:
    """リクエストスコープの DB 接続。"""
    db = await get_db()
    try:
        yield db
    finally:
        await db.close()


async def get_current_user(request: Request) -> dict:
    """Cookie からセッショントークンを取り出し、認証済みユーザーを返す。
    未認証の場合は RequiresLoginException を送出。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise RequiresLoginException()

    db = await get_db()
    try:
        user = await get_session_user(db, token)
    finally:
        await db.close()

    if user is None:
        raise RequiresLoginException()
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """管理者権限を要求する依存関数。

    未ログイン時は get_current_user 経由で RequiresLoginException が送出され、
    ログイン済みでも is_admin が False の場合は 403 を返す。
    """
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理者権限が必要です",
        )
    return user
