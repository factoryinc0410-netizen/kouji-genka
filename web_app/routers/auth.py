"""
認証ルーター — ログイン / ログアウト
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import aiosqlite

from web_app.core.dependencies import db_dependency, SESSION_COOKIE
from web_app.core.auth import authenticate, create_session, delete_session
from web_app.core.config import SESSION_MAX_AGE
from web_app.core.templates import templates as _templates

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """ログイン画面を表示。"""
    return _templates.TemplateResponse(request, "login.html", {
        "error": None,
    })


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """ログイン処理。"""
    user = await authenticate(db, username, password)
    if user is None:
        return _templates.TemplateResponse(request, "login.html", {
            "error": "ユーザー名またはパスワードが正しくありません。",
        }, status_code=401)

    token = await create_session(db, user["id"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout(
    request: Request,
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """ログアウト処理。"""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db, token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
