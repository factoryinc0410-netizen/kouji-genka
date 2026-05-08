"""
ポータルルーター — ダッシュボード（部署別ツール一覧）
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web_app.core.dependencies import get_current_user
from web_app.core.templates import templates as _templates

router = APIRouter(tags=["portal"])

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
                "name": "注文書自動作成",
                "description": "Excel依頼書から注文書・注文請書PDFを一括生成します。",
                "icon": "bi-file-earmark-pdf",
                "url": "/orders/",
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
                "name": "工事日報集計",
                "description": "工事日報から現場別原価管理表・個人別集計表を作成し、予算と累計を管理します。",
                "icon": "bi-calculator",
                "url": "/construction-cost/",
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
    {
        "id": "safety",
        "name": "安全衛生",
        "icon": "bi-shield-check",
        "tools": [
            {
                "id": "qualifications",
                "name": "資格者証管理",
                "description": "作業員の資格者証を一元管理し、有効期限の近接 (180/60/30 日) を可視化します。",
                "icon": "bi-patch-check",
                "url": "/qualifications/",
                "color": "primary",
            },
        ],
    },
]


@router.get("/", response_class=HTMLResponse)
async def portal_page(request: Request, user: dict = Depends(get_current_user)):
    """ポータル画面（部署別ツール一覧ダッシュボード）。"""
    return _templates.TemplateResponse(request, "portal.html", {
        "user": user,
        "departments": DEPARTMENTS,
    })
