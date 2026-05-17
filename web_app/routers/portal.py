"""
ポータルルーター — ダッシュボード（部署別ツール一覧）
"""
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web_app.core.database import get_db
from web_app.core.dependencies import get_current_user
from web_app.core.templates import templates as _templates

logger = logging.getLogger("web_app.portal")

router = APIRouter(tags=["portal"])

# kouji-genka (KGK) アドオンの遷移先 URL。
# Phase 2a (ADR-003) で SSO 起点 (/sso/kgk/start) 経由が既定になった。
# 認証済みユーザが「工事原価管理」をクリック → Factoryskills が KGK チケットを Redis
# に発行 → KGK 側 callback でセッション確立 → KGK ダッシュボードへ自動到達する。
# 環境変数で override 可: 例えば KGK の login 画面に直接飛ばしたい場合は
# KGK_PORTAL_URL=http://localhost:3000/login を指定する (テスト目的等)。
_KGK_PORTAL_URL = os.getenv("KGK_PORTAL_URL", "/sso/kgk/start")

# ── 部署別ツール一覧 ────────────────────────────────────────
# 新しいツールを追加する際は、該当部署の tools リストにエントリを追加するだけで OK。
DEPARTMENTS = [
    {
        "id": "management",
        "name": "管理部",
        "icon": "bi-building",
        "tools": [
            {
                "id": "order_docs",
                # feature : user_permissions.feature_name とつき合わせる権限キー。
                # tool.id とは独立に管理する（id は表示用 ID、feature は権限用）。
                "feature": "order_docs",
                "name": "注文書自動作成",
                "description": "Excel依頼書から注文書・注文請書PDFを一括生成します。",
                "icon": "bi-file-earmark-pdf",
                "url": "/orders/",
                "color": "primary",
            },
            {
                "id": "qualifications",
                "feature": "qualifications",
                "name": "資格者証管理",
                "description": "作業員の資格者証を一元管理し、有効期限の近接 (180/60/30 日) を可視化します。",
                "icon": "bi-patch-check",
                "url": "/qualifications/",
                "color": "primary",
            },
        ],
    },
    {
        "id": "civil_engineering",
        "name": "土木部",
        "icon": "bi-cone-striped",
        "tools": [
            {
                "id": "construction_cost",
                "feature": "daily_report",
                "name": "工事日報集計",
                "description": "工事日報から現場別原価管理表・個人別集計表を作成し、予算と累計を管理します。",
                "icon": "bi-calculator",
                "url": "/construction-cost/",
                "color": "success",
            },
            {
                "id": "daika_link",
                "feature": "daika_link",
                "name": "代価表リンク設定",
                "description": "代価表 Excel の基準列に、同名の代価明細行（単/施/P/明 N 号）への内部ハイパーリンクを自動付与します。",
                "icon": "bi-link-45deg",
                "url": "/daika-link/",
                "color": "success",
            },
            # kouji-genka (KGK) アドオン — 別プロセスで稼働する独立 Web アプリへの導線。
            # 認証/データは Factoryskills とは別建て (Phase 1)、入口だけ統合する方式。
            # 詳細: skills/kouji-genka/docs/adr/ADR-001-addon-integration-strategy.md
            {
                "id": "kouji_genka",
                "feature": "kouji_genka",
                "name": "工事原価管理",
                "description": "実行予算の編成・改定・承認ワークフロー、予算消化率ダッシュボードを提供します。",
                "icon": "bi-graph-up-arrow",
                "url": _KGK_PORTAL_URL,
                "color": "success",
            },
        ],
    },
    {
        "id": "architecture",
        "name": "建築部",
        "icon": "bi-house-door",
        "tools": [],
    },
]


async def _qualification_alerts() -> dict[str, int]:
    """qualifications スキルの期限アラート件数を取得する。

    qualifications テーブルがまだ無い旧 DB / 起動直後でも壊れないよう、
    例外時はゼロで握りつぶしてバナーを単に表示しない。
    """
    try:
        from web_app.routers.qualifications import count_alerts
    except ImportError:
        return {"warning": 0, "expired": 0}

    db = await get_db()
    try:
        return await count_alerts(db)
    except Exception:
        # マイグレーション直後など q_certificates 未作成のケースを想定
        logger.exception("ポータルでの qualifications アラート集計に失敗")
        return {"warning": 0, "expired": 0}
    finally:
        await db.close()


@router.get("/", response_class=HTMLResponse)
async def portal_page(request: Request, user: dict = Depends(get_current_user)):
    """ポータル画面（部署別ツール一覧ダッシュボード）。"""
    qualification_alerts = await _qualification_alerts()
    return _templates.TemplateResponse(request, "portal.html", {
        "user": user,
        "departments": DEPARTMENTS,
        "qualification_alerts": qualification_alerts,
    })
