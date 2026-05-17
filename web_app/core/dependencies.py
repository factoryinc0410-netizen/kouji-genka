"""
FastAPI 共通依存関係 — DB 接続・認証済みユーザー取得・CSRF 検証
"""
from fastapi import Depends, HTTPException, Request, status

import aiosqlite

from web_app.core.csrf import CSRF_FORM_FIELD, tokens_match
from web_app.core.database import get_db
from web_app.core.auth import (
    get_role_permissions,
    get_session_user,
    has_permission,
    list_user_permissions,
)

SESSION_COOKIE = "session_token"

# パスワード変更ガードを免除するパス
# - 変更画面そのもの（無限リダイレクト防止）
# - ログアウト（強制変更を放棄してログアウトする選択肢を残す）
# - /static/ は依存関数を通らないが念のため
_PWD_CHANGE_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/auth/change-password",
    "/logout",
})


class RequiresLoginException(Exception):
    """未認証時にログイン画面へリダイレクトさせるための例外。"""
    pass


class RequiresPasswordChangeException(Exception):
    """`must_change_password=1` ユーザーがガード対象パスを叩いた際に送出。"""
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

    - 未認証 → RequiresLoginException（/login へ誘導）
    - 認証済みかつ must_change_password=1、ただし免除パス以外 → RequiresPasswordChangeException
      （/auth/change-password へ誘導）
    免除パスは _PWD_CHANGE_EXEMPT_PATHS 参照。
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise RequiresLoginException()

    db = await get_db()
    try:
        user = await get_session_user(db, token)
        if user is None:
            raise RequiresLoginException()
        # 認可判定を同期で完結させるため、権限を user dict に pre-load する。
        # - permissions      : user_permissions テーブル由来（個別権限）
        # - role_permissions : role_permissions テーブル由来（ロール権限、role_id があれば）
        # has_permission / has_perm はこの 2 つを論理和（max）で評価する。
        # セッションキャッシュは挟まない → 管理者の権限変更は次リクエストから即時反映（ホットスワップ）。
        user["permissions"] = await list_user_permissions(db, user["id"])
        user["role_permissions"] = await get_role_permissions(db, user.get("role_id"))
    finally:
        await db.close()

    if user.get("must_change_password") and request.url.path not in _PWD_CHANGE_EXEMPT_PATHS:
        raise RequiresPasswordChangeException()

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


# ── CSRF 検証（Synchronizer Token Pattern） ───────────────────
# 認証済み POST/PUT/DELETE で利用する。フォームの hidden 値（csrf_token）
# とログイン中ユーザーのセッションに保存された CSRF トークンを比較する。
# get_current_user に依存しているため、未ログイン時は先にリダイレクト/401 が
# 返り、ここまで届かない。
#
# - フォームに csrf_token フィールドが無い、もしくは値不一致 → 403
# - 比較は secrets.compare_digest（タイミング攻撃耐性）

# ── 機能別の階層的アクセス制御ガード ──────────────────────────
# RequirePermission(feature_name, required_level) は呼び出し可能オブジェクトで、
# Depends() に渡すとリクエスト毎に評価される。
#
#   @router.get("/daily_report/master")
#   async def master_view(
#       user: dict = Depends(RequirePermission("daily_report", "manager")),
#   ):
#       ...
#
# - is_admin=True のユーザーは has_permission 内部で常に許可されるため、
#   既存 require_admin 系エンドポイントとの重複ガードにならない。
# - 権限不足は 403 を返す（未ログインは get_current_user 経由で /login にリダイレクト）。

class RequirePermission:
    """機能ごとに必要な access_level をチェックする FastAPI 依存関数。

    認可は user_permissions（個別）と role_permissions（ロール）の **論理和**。
    どちらか一方でも required_level 以上ならアクセスを許可する（manager > general > none）。
    両権限は get_current_user が user dict に pre-load しているため、
    本クラスでは追加 DB クエリ不要。
    """

    __slots__ = ("feature_name", "required_level")

    def __init__(self, feature_name: str, required_level: str = "general") -> None:
        self.feature_name = feature_name
        self.required_level = required_level

    async def __call__(
        self, user: dict = Depends(get_current_user)
    ) -> dict:
        if not has_permission(user, self.feature_name, self.required_level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"この操作には機能『{self.feature_name}』の "
                    f"『{self.required_level}』以上の権限が必要です。"
                ),
            )
        return user


class RequireAnyPermission:
    """複数 (feature_name, required_level) の **いずれか** が満たされれば許可する OR ガード。

    用途例: 同じ画面/エンドポイントが複数の機能から共有される場合
    (例: スタッフマスタは ``daily_report.manager`` でも ``qualifications.manager`` でも
    操作できるべき) に、エンドポイント側でガードを 1 個書くだけで済むように。

    - 1 つでも ``has_permission`` が True を返せばアクセス許可。
    - 全て False なら 403 を返す (詳細メッセージにはチェックした全ペアを列挙)。
    - is_admin=True のユーザーは ``has_permission`` のショートカットにより常に許可。
    """

    __slots__ = ("checks",)

    def __init__(self, checks: list[tuple[str, str]]) -> None:
        if not checks:
            raise ValueError("RequireAnyPermission には少なくとも 1 つの (feature, level) が必要です")
        self.checks = tuple(checks)

    async def __call__(
        self, user: dict = Depends(get_current_user)
    ) -> dict:
        for feature_name, required_level in self.checks:
            if has_permission(user, feature_name, required_level):
                return user
        labels = " または ".join(
            f"『{f}』の『{lvl}』以上" for f, lvl in self.checks
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"この操作には {labels} の権限が必要です。",
        )


async def verify_csrf_token(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """csrf_token をセッションのトークンと突き合わせる。

    受理するソース (この順で評価し、最初に見つかった非空値を採用):
      1. ``X-CSRF-Token`` ヘッダー  (AJAX / fetch リクエスト用)
      2. フォームの hidden input ``csrf_token``  (HTML form submit 用)

    どちらにも入っていない / 値不一致なら 403。成功時はそのまま user dict を
    返すので、エンドポイント側は ``Depends(verify_csrf_token)`` で認証＋CSRF を
    1 回で要求できる。
    """
    expected = user.get("csrf_token")

    # 1) AJAX 経路: X-CSRF-Token ヘッダー (大小区別なしに 1 つだけ拾う)
    submitted: str | None = request.headers.get("x-csrf-token")

    # 2) form / multipart 経路 (ヘッダーが空のときのみ)
    if not submitted:
        try:
            form = await request.form()
        except Exception:
            form = {}
        raw = form.get(CSRF_FORM_FIELD) if form else None
        submitted = raw if isinstance(raw, str) else None

    if not tokens_match(submitted, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed",
        )
    return user
