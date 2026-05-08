"""
認証ルーター — ログイン / ログアウト / パスワード変更
"""
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import aiosqlite

from web_app.core.csrf import (
    CSRF_LOGIN_COOKIE,
    generate_csrf_token,
    tokens_match,
)
from web_app.core.dependencies import (
    SESSION_COOKIE,
    db_dependency,
    get_current_user,
    verify_csrf_token,
)
from web_app.core.auth import (
    authenticate,
    create_session,
    delete_session,
    log_admin_action,
    update_password,
    validate_password_policy,
)
from web_app.core.config import SESSION_MAX_AGE, SESSION_COOKIE_SECURE
from web_app.core.templates import templates as _templates

logger = logging.getLogger("web_app.auth")

router = APIRouter(tags=["auth"])

# パスワードポリシーは validate_password_policy（auth.py）に集約。
# ここでは古い _MIN_PASSWORD_LENGTH を保持しない。

# ログイン CSRF Cookie の有効期間（短命）— ログイン画面を開いてから送信するまで
_CSRF_LOGIN_COOKIE_MAX_AGE = 60 * 30  # 30 分


def _set_login_csrf_cookie(response, token: str) -> None:
    """ログインフォーム用の Double-Submit Cookie を発行する。

    SameSite=Lax + HttpOnly + Secure(本番のみ) を明示。HttpOnly でも
    Double-Submit パターンは成立する（同一サーバーがフォームに hidden 値を埋めるため）。
    """
    response.set_cookie(
        key=CSRF_LOGIN_COOKIE,
        value=token,
        max_age=_CSRF_LOGIN_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """ログイン画面を表示し、Double-Submit 用の CSRF Cookie / hidden 値を発行する。

    既に Cookie がある場合はそれを再利用するが、無ければ新規発行する。
    新規発行のたびに古い hidden 値とずれる事故を避けるため、テンプレート側へは
    Cookie に乗せた値そのものを渡す。
    """
    cookie_token = request.cookies.get(CSRF_LOGIN_COOKIE)
    if not cookie_token:
        cookie_token = generate_csrf_token()

    response = _templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "csrf_token": cookie_token},
    )
    _set_login_csrf_cookie(response, cookie_token)
    return response


async def _safe_audit(
    db: aiosqlite.Connection,
    operator_id: str | None,
    target_user_id: str | None,
    action: str,
    ip_address: str | None,
) -> None:
    """ログイン経路の監査ログ書き込み。失敗してもユーザー操作は止めない。"""
    try:
        await log_admin_action(db, operator_id, target_user_id, action, ip_address)
    except Exception:
        logger.exception(
            "ログイン監査ログの記録に失敗 (action=%s target=%s)",
            action, target_user_id,
        )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _lookup_user_id_by_username(
    db: aiosqlite.Connection, username: str
) -> str | None:
    """ログイン失敗時の target_user_id 補完用。is_active を問わずに引く。"""
    cursor = await db.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(default=""),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """ログイン処理。Double-Submit Cookie で CSRF を検証し、成功・失敗のいずれも
    user_audit_logs に記録する。"""
    ip = _client_ip(request)

    # ── CSRF（Double-Submit Cookie）検証 ─────────────────────
    cookie_token = request.cookies.get(CSRF_LOGIN_COOKIE)
    if not tokens_match(csrf_token, cookie_token):
        # 監査ログを残しつつ 403 で拒否（401 と区別したいので別ステータス）
        await _safe_audit(db, None, None, "login_failure", ip)
        logger.warning(
            "ログイン CSRF 検証失敗: ip=%s username=%s", ip, username,
        )
        raise HTTPException(
            status_code=403, detail="CSRF token validation failed"
        )

    result = await authenticate(db, username, password)
    status = result["status"]

    if status == "locked":
        # ロック中アカウントへのログイン試行 — 解除時刻は画面に出さない
        target_id = await _lookup_user_id_by_username(db, username)
        await _safe_audit(db, None, target_id, "login_failure", ip)
        logger.warning(
            "ログイン失敗（ロック中）: username=%s target_id=%s ip=%s "
            "locked_until=%s failed_attempts=%s",
            username, target_id, ip,
            result.get("locked_until"), result.get("failed_attempts"),
        )
        return _templates.TemplateResponse(request, "login.html", {
            "error": "アカウントは一時的にロックされています。"
                     "しばらく経ってから再度お試しください。",
            "csrf_token": cookie_token,
        }, status_code=403)

    if status == "invalid":
        target_id = await _lookup_user_id_by_username(db, username)
        await _safe_audit(db, None, target_id, "login_failure", ip)
        logger.info(
            "ログイン失敗: username=%s target_id=%s ip=%s failed_attempts=%s",
            username, target_id, ip, result.get("failed_attempts"),
        )
        # 再表示時も同じ Cookie 値の hidden を埋める（Cookie はそのまま）
        return _templates.TemplateResponse(request, "login.html", {
            "error": "ユーザー名またはパスワードが正しくありません。",
            "csrf_token": cookie_token,
        }, status_code=401)

    user = result["user"]

    # Session Fixation 対策: 既存の Cookie 由来セッションがあれば破棄してから新規発行
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        await delete_session(db, old_token)

    token = await create_session(db, user["id"])

    # 成功時: operator=本人 / target=本人 として記録
    await _safe_audit(db, user["id"], user["id"], "login_success", ip)
    logger.info("ログイン成功: username=%s ip=%s", user["username"], ip)

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
    )
    # ログイン用 Double-Submit Cookie は役目を終えたので破棄
    response.delete_cookie(
        CSRF_LOGIN_COOKIE,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
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
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
    )
    return response


# ── パスワード変更 ────────────────────────────────────────────
# get_current_user の免除リスト（_PWD_CHANGE_EXEMPT_PATHS）にこのパスを
# 追加してあるため、must_change_password=1 のユーザーでも本画面のみアクセス可。

@router.get("/auth/change-password", response_class=HTMLResponse)
async def change_password_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """パスワード変更フォーム。"""
    return _templates.TemplateResponse(
        request,
        "auth/change_password.html",
        {
            "user": user,
            "error": None,
            "forced": bool(user.get("must_change_password")),
            "csrf_token": user.get("csrf_token", ""),
        },
    )


@router.post("/auth/change-password")
async def change_password_action(
    request: Request,
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    csrf_token: str = Form(default=""),  # 受け取りのみ（検証は依存で済んでいる）
    user: dict = Depends(verify_csrf_token),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """新パスワードを保存し、`must_change_password=0` に倒す。

    成功後はポータル（/）へ 303 リダイレクト。免除リストの仕様により、
    ガードはここを通過すれば自然に解除される。
    """
    forced = bool(user.get("must_change_password"))

    def _err(messages: str | list[str], status_code: int = 400):
        """エラー再表示。messages は単一文字列でもリストでもよい。
        テンプレート側はリストを <ul> で列挙し、文字列は単独表示する。
        """
        return _templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {
                "user": user,
                "error": messages,
                "forced": forced,
                "csrf_token": user.get("csrf_token", ""),
            },
            status_code=status_code,
        )

    if new_password != new_password_confirm:
        return _err("入力された 2 つのパスワードが一致しません。")

    violations = validate_password_policy(new_password, user["username"])
    if violations:
        return _err(violations)

    await update_password(db, user["id"], new_password, must_change=False)
    logger.info("ユーザー %s が自身のパスワードを変更しました", user["username"])
    return RedirectResponse(url="/", status_code=303)
