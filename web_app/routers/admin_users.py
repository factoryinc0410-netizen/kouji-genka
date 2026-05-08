"""
管理者画面 — ユーザー管理ルーター

GET  /admin/users          一覧
GET  /admin/users/create   新規発行フォーム
POST /admin/users/create   新規発行（パスワードはランダム生成、must_change=1）
POST /admin/users/{id}/toggle   有効/無効の切替（自分自身は不可）
"""
from __future__ import annotations

import csv
import io
import logging
import secrets
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

import aiosqlite

from web_app.core import auth as auth_core
from web_app.core import flash
from web_app.core.dependencies import (
    SESSION_COOKIE,
    db_dependency,
    require_admin,
    verify_csrf_token,
)
from web_app.core.templates import templates as _templates

logger = logging.getLogger("web_app.admin_users")

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

# 監査ログのページサイズと、UI のプルダウンに出すアクション一覧（value, ラベル）
_AUDIT_PAGE_SIZE = 20
_AUDIT_ACTION_CHOICES: list[tuple[str, str]] = [
    ("CREATE_USER", "ユーザー作成"),
    ("RESET_PASSWORD", "PWリセット"),
    ("TOGGLE_ACTIVE", "有効/無効切替"),
    ("UNLOCK_USER", "ロック解除"),
    ("update_permissions", "権限変更"),
    ("login_success", "ログイン成功"),
    ("login_failure", "ログイン失敗"),
]

# 権限設定 UI で扱う機能の一覧。機能を増やす場合はここに追加すれば
# /admin/users/{id}/permissions の画面に自動で行が増える。
# (key = user_permissions.feature_name と一致, label = 表示名, description = 補足)
_FEATURE_CATALOG: list[tuple[str, str, str]] = [
    (
        "daily_report",
        "工事日報集計",
        "general: 日報入力・集計閲覧 / manager: 現場・作業員マスタや確定・ロールバック",
    ),
    (
        "order_docs",
        "注文書作成",
        "general: ジョブ一覧/状態確認/成果物DL / manager: Excel アップロード・発行（キュー投入）",
    ),
]
_VALID_FEATURE_KEYS: frozenset[str] = frozenset(k for k, _, _ in _FEATURE_CATALOG)

# 権限レベルの表示ラベル（プルダウン表示用）。auth_core.ACCESS_LEVELS と
# キーが一致する想定で、ズレた場合は build 時に AssertionError で検出する。
_LEVEL_LABELS: list[tuple[str, str]] = [
    ("none", "権限なし"),
    ("general", "一般 (general)"),
    ("manager", "管理 (manager)"),
]
assert tuple(k for k, _ in _LEVEL_LABELS) == auth_core.ACCESS_LEVELS, (
    "_LEVEL_LABELS と auth_core.ACCESS_LEVELS の整合が取れていません"
)
_VALID_AUDIT_ACTIONS: frozenset[str] = frozenset(v for v, _ in _AUDIT_ACTION_CHOICES)

# CSV エクスポート時の保険となる上限件数（OOM 回避用、運用上 5 桁あれば十分）
_AUDIT_CSV_MAX_ROWS = 100_000


def _normalize_audit_filters(
    action: str | None, target: str | None
) -> tuple[str | None, str | None]:
    """list_users_page と CSV で同じ絞り込み条件を共有するためのヘルパ。

    - 許可リスト外の action は無視（None に倒す）
    - target は空文字列も None に倒す
    """
    action_filter = action if action in _VALID_AUDIT_ACTIONS else None
    target_filter = target or None
    return action_filter, target_filter


# ── 内部ヘルパ ────────────────────────────────────────────────

# Phase 13: パスワードポリシーに準拠した自動生成器
# 4 クラス（大文字 / 小文字 / 数字 / 記号）から各 1 文字を必ず選び、
# 残りを 4 クラス和集合からランダムに埋めてシャッフルする。
# 記号セットは口頭・チャットでの共有事故を避けるため、見間違い・エスケープ
# の罠になる文字（' " \ `）を除外している。
_PW_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"     # 紛らわしい I, O は除外
_PW_LOWER = "abcdefghijkmnopqrstuvwxyz"     # 紛らわしい l は除外
_PW_DIGIT = "23456789"                       # 紛らわしい 0, 1 は除外
_PW_SYMBOL = "!@#$%^&*()-_=+[]{}<>?,.:;/~"   # ' " \ ` を除外

_PW_DEFAULT_LENGTH = 16


def _generate_initial_password(length: int = _PW_DEFAULT_LENGTH) -> str:
    """ポリシー準拠の初期パスワードを生成する（最小 4 文字、推奨 16）。

    - 4 クラス（大/小/数/記号）から各1文字以上を保証
    - 残りは 4 クラス和集合から secrets.choice
    - 暗号学的乱数器でシャッフル

    口頭・チャットでの共有を想定し、紛らわしい文字（I/l/O/0/1）と
    エスケープ関連文字（' " \\ `）を最初から除外している。
    """
    if length < 4:
        raise ValueError("password length must be >= 4 to satisfy 4 character classes")

    rng = secrets.SystemRandom()
    pool = _PW_UPPER + _PW_LOWER + _PW_DIGIT + _PW_SYMBOL
    chars = [
        secrets.choice(_PW_UPPER),
        secrets.choice(_PW_LOWER),
        secrets.choice(_PW_DIGIT),
        secrets.choice(_PW_SYMBOL),
    ]
    chars += [secrets.choice(pool) for _ in range(length - 4)]
    rng.shuffle(chars)
    return "".join(chars)


def _generate_compliant_password(username: str, length: int = _PW_DEFAULT_LENGTH) -> str:
    """ポリシー準拠のパスワードを生成し、validate_password_policy で
    事後検証する。万一違反した場合は最大 3 回までリトライし、それでも
    駄目なら HTTP 500 を投げる（生成器のバグの保険）。

    ※ 4 クラス保証は生成器の構造上必ず満たされるが、希に username を
       含む文字列が引き当てられる可能性があるためリトライ機構が要る。
    """
    for _ in range(3):
        candidate = _generate_initial_password(length)
        violations = auth_core.validate_password_policy(candidate, username)
        if not violations:
            return candidate
    logger.error(
        "パスワード生成リトライ上限到達: username=%s", username
    )
    raise HTTPException(
        status_code=500, detail="パスワード生成に失敗しました"
    )


def _form_bool(value: str | None) -> bool:
    """HTML checkbox の値を bool に変換する（未送信時は False）。"""
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "on", "yes")


def _client_ip(request: Request) -> str | None:
    """送信元 IP を返す。リバースプロキシ越しでも request.client.host は
    常にプロキシのアドレスになるため、ここでは X-Forwarded-For を尊重しない。
    社内 LAN／systemd 直叩き運用前提で、生の peer アドレスを記録する。
    """
    if request.client is None:
        return None
    return request.client.host


async def _record_audit(
    db: aiosqlite.Connection,
    operator_id: str,
    target_user_id: str | None,
    action: str,
    ip_address: str | None,
) -> None:
    """監査ログ書き込みのラッパ。

    主操作はすでに DB コミット済みの段階で呼ばれることを想定し、
    ここでの例外は握りつぶしてアプリ全体の動作を止めない。
    記録が失敗した場合はサーバーログ側に残す。
    """
    try:
        await auth_core.log_admin_action(
            db, operator_id, target_user_id, action, ip_address
        )
    except Exception:
        logger.exception(
            "監査ログの記録に失敗 (operator=%s target=%s action=%s)",
            operator_id, target_user_id, action,
        )


# ── 一覧 ──────────────────────────────────────────────────────

def _csrf(user: dict) -> str:
    """テンプレートに渡す csrf_token（不在時は空文字列で安全側）。"""
    return user.get("csrf_token", "") or ""


@router.get("", response_class=HTMLResponse)
async def list_users_page(
    request: Request,
    page: int = 1,
    action: str | None = None,
    target: str | None = None,
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """ユーザー一覧 + 監査ログ（ページネーション + フィルタ）。

    クエリパラメータ:
      page   : 1-indexed のページ番号（既定 1）
      action : アクション名で絞り込み（許可された値以外は無視）
      target : users.id で対象ユーザー絞り込み
    """
    users = await auth_core.list_users(db)

    # 入力値の正規化（不正値は無視してフィルタ非適用にフォールバック）
    action_filter, target_filter = _normalize_audit_filters(action, target)

    total = await auth_core.count_audit_logs(
        db, action=action_filter, target_user_id=target_filter
    )
    total_pages = max(1, (total + _AUDIT_PAGE_SIZE - 1) // _AUDIT_PAGE_SIZE)
    page_norm = max(1, min(page, total_pages))
    offset = (page_norm - 1) * _AUDIT_PAGE_SIZE

    audit_logs = await auth_core.list_audit_logs(
        db,
        limit=_AUDIT_PAGE_SIZE,
        offset=offset,
        action=action_filter,
        target_user_id=target_filter,
    )

    token = request.cookies.get(SESSION_COOKIE) or ""
    flashes = await flash.pop(token)

    return _templates.TemplateResponse(
        request,
        "admin/user_list.html",
        {
            "user": user,
            "users": users,
            "flashes": flashes,
            "audit_logs": audit_logs,
            "audit_total": total,
            "audit_page": page_norm,
            "audit_total_pages": total_pages,
            "audit_page_size": _AUDIT_PAGE_SIZE,
            "audit_action": action_filter or "",
            "audit_target": target_filter or "",
            "audit_action_choices": _AUDIT_ACTION_CHOICES,
            "csrf_token": _csrf(user),
        },
    )


# ── 監査ログ CSV エクスポート ─────────────────────────────────
# 注意: パスは "/audit.csv" の固定文字列なので、後段の "/{user_id}/..."
# 可変ルートより前に登録する必要がある（ここで先に定義）。

@router.get("/audit.csv")
async def export_audit_logs_csv(
    request: Request,
    action: str | None = None,
    target: str | None = None,
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """フィルタ適用済みの監査ログ全件を CSV(UTF-8 BOM 付き) で返す。

    - 画面と同じ正規化ロジック（不正な action は無視）を使うため、
      画面の「該当 N 件」表示と CSV の行数が必ず一致する。
    - StreamingResponse で逐次送信するためメモリは行単位だけ保持する。
    - 万一の暴走に備えて _AUDIT_CSV_MAX_ROWS で上限を設けている。
    """
    action_filter, target_filter = _normalize_audit_filters(action, target)

    rows = await auth_core.list_audit_logs(
        db,
        limit=_AUDIT_CSV_MAX_ROWS,
        offset=0,
        action=action_filter,
        target_user_id=target_filter,
    )

    headers = [
        "id", "timestamp", "action",
        "operator_id", "operator_username",
        "target_user_id", "target_username",
        "ip_address",
    ]

    def _stream():
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        # Excel 等が UTF-8 として正しく開けるよう BOM を先頭に付与
        buf.write("\ufeff")
        writer.writerow(headers)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)

        for r in rows:
            writer.writerow([
                r["id"],
                r["timestamp"],
                r["action"],
                r["operator_id"] or "",
                r["operator_username"] or "",
                r["target_user_id"] or "",
                r["target_username"] or "",
                r["ip_address"] or "",
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    filename = f"audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    logger.info(
        "管理者 %s が監査ログCSVを取得 (action=%s target=%s rows=%d)",
        user["username"], action_filter, target_filter, len(rows),
    )
    return StreamingResponse(
        _stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ── 新規作成 ──────────────────────────────────────────────────

@router.get("/create", response_class=HTMLResponse)
async def create_user_page(
    request: Request,
    user: dict = Depends(require_admin),
):
    """新規発行フォーム。"""
    token = request.cookies.get(SESSION_COOKIE) or ""
    flashes = await flash.pop(token)
    return _templates.TemplateResponse(
        request,
        "admin/user_create.html",
        {
            "user": user,
            "flashes": flashes,
            "form": {"username": "", "display_name": "", "is_admin": False},
            "error": None,
            "csrf_token": _csrf(user),
        },
    )


@router.post("/create", dependencies=[Depends(verify_csrf_token)])
async def create_user_action(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    is_admin: str | None = Form(default=None),
    csrf_token: str = Form(default=""),  # 受領のみ（検証は dependencies で完了）
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """新規ユーザーを発行する。

    - パスワードはサーバー側でランダム生成し、`must_change_password=1` で保存
    - 成功時は一覧へ 303 リダイレクトし、フラッシュで初期パスワードを 1 回だけ表示
    """
    username_clean = username.strip()
    display_name_clean = (display_name or "").strip() or username_clean
    is_admin_flag = _form_bool(is_admin)

    # 入力検証 — フォーム再表示
    if not username_clean:
        return _templates.TemplateResponse(
            request,
            "admin/user_create.html",
            {
                "user": user,
                "flashes": [],
                "form": {
                    "username": username_clean,
                    "display_name": display_name_clean,
                    "is_admin": is_admin_flag,
                },
                "error": "ユーザー名は必須です。",
                "csrf_token": _csrf(user),
            },
            status_code=400,
        )

    initial_password = _generate_compliant_password(username_clean)

    try:
        new_user_id = await auth_core.create_user(
            db,
            username=username_clean,
            display_name=display_name_clean,
            password=initial_password,
            is_admin=is_admin_flag,
        )
    except sqlite3.IntegrityError as e:
        msg = str(e).upper()
        error = (
            f"ユーザー名 '{username_clean}' は既に使われています。"
            if "UNIQUE" in msg
            else f"DBエラー: {e}"
        )
        return _templates.TemplateResponse(
            request,
            "admin/user_create.html",
            {
                "user": user,
                "flashes": [],
                "form": {
                    "username": username_clean,
                    "display_name": display_name_clean,
                    "is_admin": is_admin_flag,
                },
                "error": error,
                "csrf_token": _csrf(user),
            },
            status_code=400,
        )

    # 初回ログインでパスワード変更必須にする
    await auth_core.update_password(
        db, new_user_id, initial_password, must_change=True
    )

    logger.info(
        "管理者 %s が新規ユーザー %s (admin=%s) を発行しました",
        user["username"],
        username_clean,
        is_admin_flag,
    )
    await _record_audit(
        db, user["id"], new_user_id, "CREATE_USER", _client_ip(request)
    )

    # フラッシュ経由で初期パスワードを 1 回だけ表示
    token = request.cookies.get(SESSION_COOKIE) or ""
    await flash.push(
        token,
        "success",
        f"ユーザー <strong>{username_clean}</strong> を発行しました。"
        "下記の初期パスワードを本人へ安全な経路で通知してください。",
        initial_password=initial_password,
        username=username_clean,
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ── 有効/無効トグル ───────────────────────────────────────────

@router.post("/{user_id}/toggle", dependencies=[Depends(verify_csrf_token)])
async def toggle_active(
    user_id: str,
    request: Request,
    csrf_token: str = Form(default=""),
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """is_active を反転させる。自分自身に対しては拒否（誤操作防止）。"""
    target = await auth_core.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    token = request.cookies.get(SESSION_COOKIE) or ""

    if target["id"] == user["id"]:
        await flash.push(
            token, "danger", "自分自身を無効化することはできません。"
        )
        return RedirectResponse(url="/admin/users", status_code=303)

    new_active = not target["is_active"]
    await auth_core.set_user_active(db, user_id, new_active)

    verb = "有効化" if new_active else "無効化"
    logger.info(
        "管理者 %s がユーザー %s を %s しました",
        user["username"],
        target["username"],
        verb,
    )
    await _record_audit(
        db, user["id"], target["id"], "TOGGLE_ACTIVE", _client_ip(request)
    )
    await flash.push(
        token,
        "success" if new_active else "warning",
        f"ユーザー <strong>{target['username']}</strong> を{verb}しました。",
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ── パスワードリセット ────────────────────────────────────────

@router.post("/{user_id}/reset-password", dependencies=[Depends(verify_csrf_token)])
async def reset_password(
    user_id: str,
    request: Request,
    csrf_token: str = Form(default=""),
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """対象ユーザーのパスワードをランダム生成して上書きし、`must_change_password=1` を立てる。

    自分自身のリセットは禁止する（自分のパスワード変更は /auth/change-password を使う）。
    """
    target = await auth_core.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    token = request.cookies.get(SESSION_COOKIE) or ""

    if target["id"] == user["id"]:
        await flash.push(
            token,
            "danger",
            "自分自身のパスワードはリセットできません。"
            "<a href=\"/auth/change-password\" class=\"alert-link\">パスワード変更画面</a>を使ってください。",
        )
        return RedirectResponse(url="/admin/users", status_code=303)

    new_password = _generate_compliant_password(target["username"])
    await auth_core.update_password(
        db, user_id, new_password, must_change=True
    )

    logger.info(
        "管理者 %s がユーザー %s のパスワードをリセットしました",
        user["username"],
        target["username"],
    )
    await _record_audit(
        db, user["id"], target["id"], "RESET_PASSWORD", _client_ip(request)
    )
    await flash.push(
        token,
        "success",
        f"ユーザー <strong>{target['username']}</strong> のパスワードをリセットしました。"
        "下記の新しい初期パスワードを本人へ安全な経路で通知してください。",
        initial_password=new_password,
        username=target["username"],
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ── アカウントロック解除 ─────────────────────────────────────
# ブルートフォース対策で locked_until が立ったユーザーを管理者が手動で解除する。
# 失敗カウンタも 0 にリセットし、即座に再ログイン可能な状態に戻す。

@router.post("/{user_id}/unlock", dependencies=[Depends(verify_csrf_token)])
async def unlock_user_action(
    user_id: str,
    request: Request,
    csrf_token: str = Form(default=""),
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """対象ユーザーの failed_login_attempts と locked_until をリセットする。

    既にロックされていなくても呼び出し可能（冪等）だが、UI 側ではロック中の
    ユーザーにのみボタンを表示する。
    """
    target = await auth_core.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    token = request.cookies.get(SESSION_COOKIE) or ""

    await auth_core.unlock_user(db, user_id)

    logger.info(
        "管理者 %s がユーザー %s のアカウントロックを解除しました",
        user["username"],
        target["username"],
    )
    await _record_audit(
        db, user["id"], target["id"], "UNLOCK_USER", _client_ip(request)
    )
    await flash.push(
        token,
        "success",
        f"ユーザー <strong>{target['username']}</strong> のアカウントロックを解除しました。",
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ── 階層的アクセス制御（機能ごとの権限設定） ─────────────────
# user_permissions テーブルに対する upsert UI。GET でフォーム描画、
# POST で機能単位に access_level を更新する。
# 自分自身の権限編集はロックアウト事故防止のため禁止する（is_admin の自己剥奪対策と同じ思想）。

def _features_with_levels(
    current: dict[str, str]
) -> list[dict[str, str | bool]]:
    """テンプレート描画用に、機能カタログと現在値を結合した辞書リストを返す。

    レコード未登録の機能は 'none' を選択済みとして表示する。
    """
    result: list[dict[str, str | bool]] = []
    for key, label, description in _FEATURE_CATALOG:
        result.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "current_level": current.get(key, "none"),
            }
        )
    return result


@router.get("/{user_id}/permissions", response_class=HTMLResponse)
async def edit_permissions_page(
    user_id: str,
    request: Request,
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """対象ユーザーの権限設定画面を表示する。"""
    target = await auth_core.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    if target["id"] == user["id"]:
        # 自分自身の権限編集は誤操作で締め出しに繋がるため拒否
        token = request.cookies.get(SESSION_COOKIE) or ""
        await flash.push(
            token, "danger", "自分自身の権限は編集できません。"
        )
        return RedirectResponse(url="/admin/users", status_code=303)

    current = await auth_core.list_user_permissions(db, user_id)

    token = request.cookies.get(SESSION_COOKIE) or ""
    flashes = await flash.pop(token)

    return _templates.TemplateResponse(
        request,
        "admin/user_permissions.html",
        {
            "user": user,
            "target": target,
            "flashes": flashes,
            "features": _features_with_levels(current),
            "level_labels": _LEVEL_LABELS,
            "csrf_token": _csrf(user),
        },
    )


@router.post(
    "/{user_id}/permissions",
    dependencies=[Depends(verify_csrf_token)],
)
async def update_permissions_action(
    user_id: str,
    request: Request,
    csrf_token: str = Form(default=""),  # 受領のみ（検証は dependencies で完了）
    user: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """フォーム送信された各機能の access_level を反映する。

    - フォームのフィールド名は ``feature__<feature_key>`` 形式。
    - 既存値と一致するものはスキップし、変更があった機能だけ
      set_user_permission で upsert する。
    - 1 件でも変更があれば監査ログ（action="update_permissions"）を1行記録する。
      （現行の user_audit_logs に詳細列が無いため、機能ごとに分けず1リクエスト=1行）
    """
    target = await auth_core.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    token = request.cookies.get(SESSION_COOKIE) or ""

    if target["id"] == user["id"]:
        await flash.push(
            token, "danger", "自分自身の権限は編集できません。"
        )
        return RedirectResponse(url="/admin/users", status_code=303)

    form = await request.form()
    current = await auth_core.list_user_permissions(db, user_id)

    changed: list[tuple[str, str, str]] = []  # (feature_key, old_level, new_level)
    invalid_features: list[str] = []
    for key, _label, _desc in _FEATURE_CATALOG:
        submitted = form.get(f"feature__{key}")
        if not isinstance(submitted, str):
            continue  # 未送信 → 変更なし扱い
        if submitted not in auth_core.ACCESS_LEVELS:
            invalid_features.append(key)
            continue
        old_level = current.get(key, "none")
        if submitted != old_level:
            changed.append((key, old_level, submitted))

    if invalid_features:
        # 改ざんされたフォーム値 → 全体を拒否（部分適用しない）
        raise HTTPException(
            status_code=400,
            detail=f"不正な権限値が含まれています: {', '.join(invalid_features)}",
        )

    if not changed:
        await flash.push(
            token, "info",
            f"ユーザー <strong>{target['username']}</strong> の権限に変更はありませんでした。",
        )
        return RedirectResponse(url="/admin/users", status_code=303)

    for key, _old, new_level in changed:
        await auth_core.set_user_permission(db, user_id, key, new_level)

    summary = "、".join(f"{k}: {old}→{new}" for k, old, new in changed)
    logger.info(
        "管理者 %s がユーザー %s の権限を更新: %s",
        user["username"], target["username"], summary,
    )
    await _record_audit(
        db, user["id"], target["id"],
        "update_permissions", _client_ip(request),
    )
    await flash.push(
        token, "success",
        f"ユーザー <strong>{target['username']}</strong> の権限を更新しました（{len(changed)} 件）。",
    )
    return RedirectResponse(url="/admin/users", status_code=303)
