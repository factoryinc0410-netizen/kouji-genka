"""資格者証管理ルーター — Phase 1 骨組み

このルーターは段階的に育てる:

Phase 1 (本コミット):
  - GET /qualifications/         → 確定済み一覧 + 期限サマリ
  - GET /qualifications/pending  → 未確定 (draft) 一覧

Phase 2:
  - GET/POST /qualifications/upload                   (require_admin)
  - GET/POST /qualifications/jobs/<job_id>/classify   (require_admin)
  - GET/POST /qualifications/<cert_id>/review         (require_admin)
  - POST     /qualifications/<cert_id>/delete         (require_admin)

権限モデル:
  - 全員閲覧 (general 相当): ``get_current_user`` でログイン必須
  - 登録/編集/削除 (manager 相当): ``require_admin`` で admin に限定

期限ステータス分類 (180/60/30 日):
  - >180 日           → 'safe'
  - 180〜61 日       → 'far'
  - 60〜31 日        → 'soon'
  - 30〜1 日         → 'urgent'
  - ≤0 日            → 'expired'
  - renewal_required=0 か expires_on=NULL → 'no_renewal'
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web_app.core.database import get_db
from web_app.core.dependencies import get_current_user
from web_app.core.templates import templates as _templates

logger = logging.getLogger("web_app.qualifications")

router = APIRouter(prefix="/qualifications", tags=["qualifications"])

_SKILL_KEY = "qualifications"


# ────────────────────────────────────────────
# 期限ステータス判定
# ────────────────────────────────────────────

def _expiry_bucket(expires_on: str | None, renewal_required: int) -> str:
    """有効期限から表示用ステータスを決める。

    expires_on は ISO 8601 (YYYY-MM-DD) もしくは NULL。
    renewal_required=0 のものは更新不要扱い。
    """
    if not renewal_required or not expires_on:
        return "no_renewal"
    try:
        exp = date.fromisoformat(expires_on)
    except ValueError:
        # 不正フォーマットは「不明」扱い。Phase 2 で validator が直すので一旦 expired 寄せ。
        return "expired"
    days = (exp - date.today()).days
    if days <= 0:
        return "expired"
    if days <= 30:
        return "urgent"
    if days <= 60:
        return "soon"
    if days <= 180:
        return "far"
    return "safe"


# ────────────────────────────────────────────
# データ取得ヘルパ
# ────────────────────────────────────────────

async def _fetch_confirmed(db) -> list[dict]:
    """確定済み資格者証を作業員・資格マスタと JOIN して取得する。"""
    cur = await db.execute(
        """
        SELECT  c.cert_id, c.certificate_no, c.issuer,
                c.issued_on, c.expires_on, c.renewal_required,
                c.notes, c.status, c.ocr_confidence,
                w.worker_id, w.worker_name, w.group_name,
                q.qual_id, q.name AS qual_name, q.category AS qual_category
          FROM  q_certificates c
          JOIN  cc_workers       w ON w.worker_id = c.worker_id
          JOIN  q_qualifications q ON q.qual_id   = c.qual_id
         WHERE  c.status = 'confirmed'
         ORDER BY c.expires_on IS NULL, c.expires_on, w.worker_name
        """
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["bucket"] = _expiry_bucket(r["expires_on"], r["renewal_required"])
    return rows


async def _fetch_pending(db) -> list[dict]:
    cur = await db.execute(
        """
        SELECT  c.cert_id, c.certificate_no, c.issued_on, c.expires_on,
                c.ocr_confidence, c.created_at,
                w.worker_id, w.worker_name, w.group_name,
                q.qual_id, q.name AS qual_name
          FROM  q_certificates c
          JOIN  cc_workers       w ON w.worker_id = c.worker_id
          JOIN  q_qualifications q ON q.qual_id   = c.qual_id
         WHERE  c.status = 'draft'
         ORDER BY c.created_at DESC
        """
    )
    return [dict(r) for r in await cur.fetchall()]


def _summarize(rows: list[dict]) -> dict[str, int]:
    """期限サマリ用の件数集計（confirmed のみカウント）。"""
    summary = {"total": len(rows), "safe": 0, "warning": 0, "expired": 0, "no_renewal": 0}
    for r in rows:
        b = r["bucket"]
        if b == "safe":
            summary["safe"] += 1
        elif b in ("far", "soon", "urgent"):
            summary["warning"] += 1
        elif b == "expired":
            summary["expired"] += 1
        elif b == "no_renewal":
            summary["no_renewal"] += 1
    return summary


# ────────────────────────────────────────────
# ルート
# ────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: dict = Depends(get_current_user)):
    """確定済み一覧 + 期限サマリ。general（ログイン済み）に開放。"""
    db = await get_db()
    try:
        certs = await _fetch_confirmed(db)
        pending_count_cur = await db.execute(
            "SELECT COUNT(*) FROM q_certificates WHERE status='draft'"
        )
        pending_count = (await pending_count_cur.fetchone())[0]
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/index.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "index",
            "certificates": certs,
            "summary": _summarize(certs),
            "pending_count": pending_count,
        },
    )


@router.get("/pending", response_class=HTMLResponse)
async def pending(request: Request, user: dict = Depends(get_current_user)):
    """未確定 (draft) 一覧。general（ログイン済み）に開放。"""
    db = await get_db()
    try:
        rows = await _fetch_pending(db)
        pending_count = len(rows)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/pending.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "pending",
            "drafts": rows,
            "pending_count": pending_count,
        },
    )
