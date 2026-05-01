"""
FastAPI 共通依存関係 — DB 接続・認証済みユーザー取得
"""
from fastapi import Request

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
