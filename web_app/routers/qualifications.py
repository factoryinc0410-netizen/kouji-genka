"""資格者証管理ルーター

このルーターは段階的に育てる:

Phase 1:
  - GET  /qualifications/                  → 確定済み一覧 + 期限サマリ
  - GET  /qualifications/pending           → 未確定 (draft) 一覧
  - GET  /qualifications/upload            → アップロード画面 (admin)
  - POST /qualifications/upload            → ファイル保存 + ジョブ作成 (admin)

Phase 2:
  - GET/POST /qualifications/jobs/<job_id>/classify   (admin)
  - GET/POST /qualifications/<cert_id>/review         (admin)
  - POST     /qualifications/<cert_id>/delete         (admin)

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
import shutil
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from web_app.core.config import (
    QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
    QUALIFICATIONS_MAX_FILE_MB,
)
from web_app.core.database import get_db
from web_app.core.dependencies import get_current_user, require_admin
from web_app.core.templates import templates as _templates
from web_app.services.job_queue import job_queue
from skills.qualifications.schema import OCRResponse
from skills.qualifications.storage import (
    is_allowed_extension,
    sanitize_filename,
    staging_dir_for,
)

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


async def _fetch_upload_jobs(db) -> list[dict]:
    """進行中・確認待ちの ``q_upload_jobs`` を取得し、OCR 結果をパースして返す。

    戻り値の各 dict は q_upload_jobs の生カラムに加え:
      - ``short_id``           : job_id の先頭 8 文字
      - ``candidates``         : list[dict]  classify_json から展開した候補
      - ``overall_confidence`` : float       OCRResponse.overall_confidence
    を持つ。
    """
    cur = await db.execute(
        """
        SELECT  job_id, user_id, file_count, status,
                classify_json, error_message,
                created_at, updated_at
          FROM  q_upload_jobs
         WHERE  status != 'done'
         ORDER BY created_at DESC
        """
    )
    jobs = [dict(r) for r in await cur.fetchall()]

    for j in jobs:
        j["short_id"] = j["job_id"][:8]
        j["candidates"] = []
        j["overall_confidence"] = 0.0

        if not j["classify_json"]:
            continue
        try:
            response = OCRResponse.model_validate_json(j["classify_json"])
        except Exception:
            # 壊れた JSON を出さないため defensive: ログに残しても画面は破綻させない
            logger.exception(
                "classify_json のパースに失敗 job=%s", j["short_id"],
            )
            continue

        j["overall_confidence"] = response.overall_confidence
        for c in response.candidates:
            # フィールド単位の信頼度を平均して 1 候補の自信度にまとめる
            fcs = [
                v for v in c.field_confidences.model_dump().values() if v is not None
            ]
            avg = sum(fcs) / len(fcs) if fcs else 0.0
            j["candidates"].append({
                "qualification_name": c.qualification_name,
                "category":           c.category,
                "worker_name":        c.worker_name,
                "issued_on":          c.issued_on,
                "expires_on":         c.expires_on,
                "renewal_required":   c.renewal_required,
                "confidence":         avg,
            })
    return jobs


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

async def _count_active_jobs(db) -> int:
    """サブナビバッジ表示用に、未完了のアップロードジョブ数を返す。"""
    cur = await db.execute(
        "SELECT COUNT(*) FROM q_upload_jobs WHERE status != 'done'"
    )
    row = await cur.fetchone()
    return row[0] if row else 0


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: dict = Depends(get_current_user)):
    """確定済み一覧 + 期限サマリ。general（ログイン済み）に開放。"""
    db = await get_db()
    try:
        certs = await _fetch_confirmed(db)
        pending_count = await _count_active_jobs(db)
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
    """未確定一覧 — q_upload_jobs ベース。

    OCR 後の確認待ち / 進行中 / エラーのジョブをカード形式で表示する。
    """
    db = await get_db()
    try:
        jobs = await _fetch_upload_jobs(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/pending.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "pending",
            "jobs": jobs,
            "pending_count": len(jobs),
        },
    )


# ────────────────────────────────────────────
# アップロード (admin)
# ────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, user: dict = Depends(require_admin)):
    """アップロード画面を返す。admin のみ。"""
    return _templates.TemplateResponse(
        request,
        "qualifications/upload.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "upload",
            "max_file_mb": QUALIFICATIONS_MAX_FILE_MB,
            "max_files": QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
        },
    )


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_admin),
):
    """multipart で複数ファイルを受け取り、ステージングへ保存して
    ``q_upload_jobs`` に status='pending' のジョブを作成する。

    後続の OCR/classify ステップは Phase 2 で worker に乗せる。本エンドポイントは
    あくまで「ステージング完了」を返すだけ。失敗時は途中で書き出したファイルを
    削除して整合性を保つ。
    """
    # ── 入力検証 ──
    if not files:
        return JSONResponse({"ok": False, "error": "ファイルが選択されていません。"}, status_code=400)
    if len(files) > QUALIFICATIONS_MAX_FILES_PER_UPLOAD:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    f"一度にアップロードできるのは "
                    f"{QUALIFICATIONS_MAX_FILES_PER_UPLOAD} 枚までです。"
                ),
            },
            status_code=400,
        )

    max_bytes = QUALIFICATIONS_MAX_FILE_MB * 1024 * 1024

    # ── ファイル拡張子・サイズの先行検証（DB 書き込み前にエラーを返す） ──
    contents: list[tuple[str, bytes]] = []
    for f in files:
        original_name = f.filename or "unnamed"
        if not is_allowed_extension(original_name):
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"非対応のファイル形式です: {original_name}（PDF/JPG/PNG のみ）",
                },
                status_code=400,
            )
        data = await f.read()
        if len(data) == 0:
            return JSONResponse(
                {"ok": False, "error": f"空ファイルです: {original_name}"},
                status_code=400,
            )
        if len(data) > max_bytes:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"{original_name} がサイズ上限 "
                        f"{QUALIFICATIONS_MAX_FILE_MB} MB を超えています。"
                    ),
                },
                status_code=400,
            )
        contents.append((original_name, data))

    # ── ジョブ ID とステージング ──
    job_id = uuid.uuid4().hex
    staging = staging_dir_for(job_id)

    saved_paths: list[str] = []
    try:
        # ファイル名衝突を避けるため、必要に応じてサフィックスを付ける
        used_names: set[str] = set()
        for original_name, data in contents:
            base = sanitize_filename(original_name)
            unique = base
            n = 1
            while unique in used_names:
                stem, _, ext = base.rpartition(".")
                unique = f"{stem}_{n}.{ext}" if ext else f"{base}_{n}"
                n += 1
            used_names.add(unique)
            target = staging / unique
            target.write_bytes(data)
            saved_paths.append(str(target))

        # ── DB レコード作成 ──
        db = await get_db()
        try:
            await db.execute(
                """
                INSERT INTO q_upload_jobs (job_id, user_id, file_count, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (job_id, user["id"], len(contents)),
            )
            await db.commit()
        finally:
            await db.close()

        # ── ワーカーキューに投入 ──
        # ワーカーが落ちている (TestClient で lifespan 未起動など) 場合でも
        # キューに残るだけで失敗にはならない。次回起動時に restore_pending_jobs が拾う。
        job_queue.put(("qualifications", job_id))
    except Exception as e:
        # 部分的に書き出したファイルを片付けて再送出
        logger.exception("アップロード処理でエラー発生 job_id=%s", job_id)
        shutil.rmtree(staging, ignore_errors=True)
        return JSONResponse(
            {"ok": False, "error": f"サーバーエラー: {e.__class__.__name__}"},
            status_code=500,
        )

    logger.info(
        "qualifications upload accepted: job_id=%s files=%d user=%s",
        job_id, len(contents), user.get("username"),
    )
    return JSONResponse(
        {
            "ok": True,
            "job_id": job_id,
            "file_count": len(contents),
            "status": "pending",
            "saved_files": [p.rsplit("/", 1)[-1] for p in saved_paths],
            # Phase 2 で classify 画面に飛ばす想定。今は upload 画面に留まる。
            "next_url": None,
        }
    )
