"""
FastAPI アプリケーション — エントリーポイント
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web_app.core.config import SECRET_KEY, HOST, PORT
from web_app.core.database import init_db
from web_app.core.auth import create_user, verify_password
from web_app.core.database import get_db
from web_app.core.dependencies import RequiresLoginException
from web_app.services.worker import start_worker, stop_worker
from web_app.services.job_queue import restore_pending_jobs
from web_app.services.cleanup import start_cleanup_scheduler
from web_app.routers import auth, portal, order_docs, construction_cost

logger = logging.getLogger("web_app")

# ── アプリ起動時刻（ヘルスチェックの uptime 計算用） ─────────
_STARTED_AT: datetime | None = None

# ── SECRET_KEY の安全性チェック用キーワード ──────────────────
_INSECURE_KEY_MARKERS = ("change-me", "default", "secret", "changeme", "password")


def _check_secret_key() -> None:
    """SECRET_KEY が安全でない場合にログ警告を出す。

    本番環境（HOST=0.0.0.0）で危険なキーが検出された場合は起動をブロックする。
    """
    key_lower = SECRET_KEY.lower()
    is_insecure = any(marker in key_lower for marker in _INSECURE_KEY_MARKERS)
    is_short = len(SECRET_KEY) < 32
    is_public = HOST == "0.0.0.0"

    if is_insecure or is_short:
        msg = (
            "SECRET_KEY が安全ではありません！"
            f"（長さ={len(SECRET_KEY)}, 危険なキーワード含む={is_insecure}）"
        )
        if is_public:
            # 社内公開設定（0.0.0.0）で危険なキー → 起動ブロック
            logger.critical(
                "%s HOST=0.0.0.0 のため起動を中止します。"
                ".env の SECRET_KEY をランダムな文字列に変更してください。",
                msg,
            )
            raise SystemExit(
                "FATAL: SECRET_KEY is insecure and HOST=0.0.0.0. "
                "Set a strong SECRET_KEY in .env before exposing to the network."
            )
        else:
            # ローカル専用 → 警告のみ
            logger.warning(
                "%s .env の SECRET_KEY を変更することを強く推奨します。",
                msg,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーション起動・終了時の処理。"""
    global _STARTED_AT

    # ── 起動時 ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # SECRET_KEY 安全性チェック（危険な状態なら起動ブロック）
    _check_secret_key()

    logger.info("データベースを初期化しています...")
    await init_db()
    await _ensure_default_admin()
    await _check_default_admin_credentials()

    # ワーカースレッド起動
    loop = asyncio.get_running_loop()
    start_worker(loop)

    # クリーンアップスケジューラ起動
    start_cleanup_scheduler(loop)

    # 前回未完了のジョブをキューに復元
    await restore_pending_jobs()

    _STARTED_AT = datetime.now(timezone.utc)
    logger.info("アプリケーション起動完了 (HOST=%s, PORT=%s)", HOST, PORT)

    yield

    # ── 終了時 ──
    stop_worker()
    logger.info("アプリケーションを終了します")


async def _ensure_default_admin():
    """初回起動時にデフォルト管理者ユーザーを作成する。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        if row["cnt"] == 0:
            await create_user(
                db,
                username="admin",
                display_name="管理者",
                password="admin",
                is_admin=True,
            )
            _log_default_admin_banner(
                "デフォルト管理者ユーザーを新規作成しました (admin / admin)。"
                "ログイン後ただちにパスワードを変更してください。"
            )
    finally:
        await db.close()


def _log_default_admin_banner(message: str) -> None:
    """デフォルト管理者に関する警告を視認性の高いバナー形式で出力する。"""
    border = "!" * 72
    logger.critical("\n%s\n!! SECURITY WARNING\n!! %s\n%s", border, message, border)


async def _check_default_admin_credentials() -> None:
    """デフォルト admin/admin が残存している場合の対応。

    - HOST=0.0.0.0（公開モード）かつ admin/admin のままなら **起動を中止**。
    - ローカル運用（127.0.0.1 等）では大きな警告バナーのみ出してそのまま起動する。
      既存の動作を壊さないため、ローカル開発フローはブロックしない。
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT password_hash FROM users WHERE username = 'admin'"
        )
        row = await cursor.fetchone()
        if row is None:
            return  # admin ユーザー自体がない（既に削除済み等）

        if not verify_password("admin", row["password_hash"]):
            return  # 既に変更済み — OK

        # admin/admin のまま残っている
        is_public = HOST == "0.0.0.0"
        if is_public:
            _log_default_admin_banner(
                "admin ユーザーのパスワードがデフォルトの 'admin' のままです。"
                f"HOST={HOST} で公開しようとしているため、起動を中止します。"
                "ローカル (HOST=127.0.0.1) で起動してパスワードを変更してから再度公開してください。"
            )
            raise SystemExit(
                "FATAL: default admin/admin password is in use while HOST=0.0.0.0. "
                "Change the admin password before exposing the service to the network."
            )
        else:
            _log_default_admin_banner(
                "admin ユーザーのパスワードがデフォルトの 'admin' のままです。"
                f"現在は HOST={HOST} のためローカル運用とみなして起動を継続しますが、"
                "公開前に必ずパスワードを変更してください。"
            )
    finally:
        await db.close()


# ── FastAPI アプリケーション生成 ──────────────────────────────
app = FastAPI(
    title="業務自動化プラットフォーム",
    version="1.0.0",
    lifespan=lifespan,
)

# ── 静的ファイル（JS/CSS キャッシュ制御付き） ────────────────────
app.mount("/static", StaticFiles(directory="web_app/static"), name="static")


@app.middleware("http")
async def no_cache_static_js(request: Request, call_next):
    """JS/CSS の静的ファイルにキャッシュ無効化ヘッダーを付与する。"""
    response = await call_next(request)
    if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """全レスポンスに最低限のセキュリティヘッダーを付与する。

    - X-Content-Type-Options: MIME スニッフィング防止
    - X-Frame-Options: クリックジャッキング防止（iframe 埋め込み禁止）
    - Referrer-Policy: 外部サイトに完全な URL を漏らさない
    既存ヘッダーは setdefault で尊重し、上書きしない。
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ── ルーター登録 ──────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(portal.router)
app.include_router(order_docs.router)
app.include_router(construction_cost.router)


# ── ヘルスチェック ────────────────────────────────────────────
# /health : 監視スクリプト・ロードバランサ等から死活確認用（認証不要）
# HEAD /  : プレビューツール等のリクエスト対応

@app.get("/health", response_class=PlainTextResponse)
async def health_check():
    """サーバー死活確認用エンドポイント（認証不要）。"""
    return "OK"


@app.head("/")
async def head_root():
    """HEAD / リクエストに 200 を返す（プレビューツール互換）。"""
    return JSONResponse(content={"status": "ok"})


# ── 未認証時のリダイレクトハンドラ ────────────────────────────
@app.exception_handler(RequiresLoginException)
async def redirect_to_login(request: Request, exc: RequiresLoginException):
    return RedirectResponse(url="/login", status_code=303)
