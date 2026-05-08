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

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from web_app.core.config import (
    QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
    QUALIFICATIONS_MAX_FILE_MB,
)
from web_app.core.database import get_db
from web_app.core.dependencies import get_current_user, require_admin
from web_app.core.safe_files import safe_file_response
from web_app.core.templates import templates as _templates
from web_app.services.job_queue import job_queue
from skills.qualifications.schema import OCRResponse
from skills.qualifications.storage import (
    QUALIFICATIONS_STAGING_ROOT,
    is_allowed_extension,
    list_staged_files,
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
            "next_url": f"/qualifications/classify/{job_id}",
        }
    )


# ────────────────────────────────────────────
# Classify (OCR 結果の確認・修正・確定) — Phase 2.5
# ────────────────────────────────────────────

# ファイルプレビュー (左ペイン) で参照される拡張子。
# safe_file_response が path 検証を行うので拡張子チェックは UI 補助のみ。
_PREVIEW_MIME_HINT: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


async def _fetch_workers(db) -> list[dict]:
    """cc_workers から在職中の作業員を読み込む (作業員ドロップダウン用)。"""
    cur = await db.execute(
        """
        SELECT worker_id, worker_name, group_name
          FROM cc_workers
         WHERE is_active = 1
         ORDER BY group_name, worker_name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


async def _fetch_qualifications_master(db) -> list[dict]:
    """q_qualifications マスタを取得 (資格名のオートコンプリート用)。"""
    cur = await db.execute(
        """
        SELECT qual_id, name, category, default_valid_years
          FROM q_qualifications
         WHERE is_active = 1
         ORDER BY display_order, name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


def _shape_candidates_for_form(response: OCRResponse) -> list[dict]:
    """OCRResponse → form 表示用の dict リストに整形する。"""
    out: list[dict] = []
    for c in response.candidates:
        fcs = [v for v in c.field_confidences.model_dump().values() if v is not None]
        avg = sum(fcs) / len(fcs) if fcs else 0.0
        out.append({
            "qualification_name": c.qualification_name or "",
            "category":           c.category or "",
            "worker_name":        c.worker_name or "",
            "certificate_no":     c.certificate_no or "",
            "issuer":             c.issuer or "",
            "issued_on":          c.issued_on or "",
            "expires_on":         c.expires_on or "",
            "renewal_required":   bool(c.renewal_required),
            "page_indices":       c.page_indices,
            "confidence":         avg,
        })
    return out


@router.get("/classify/{job_id}", response_class=HTMLResponse)
async def classify_page(
    request: Request,
    job_id: str,
    user: dict = Depends(require_admin),
):
    """OCR 結果の確認・修正画面を返す。admin のみ。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM q_upload_jobs WHERE job_id = ?", (job_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        job = dict(row)

        if job["status"] not in ("await_review", "ocr"):
            # ocr 中は OCR 完了待ちなので少しだけ許容、それ以外は pending に戻す
            return RedirectResponse(
                url="/qualifications/pending", status_code=303,
            )

        # OCR 結果をパース (壊れた JSON でも落ちないように)
        candidates: list[dict] = []
        overall_confidence = 0.0
        if job["classify_json"]:
            try:
                response = OCRResponse.model_validate_json(job["classify_json"])
                candidates = _shape_candidates_for_form(response)
                overall_confidence = response.overall_confidence
            except Exception:
                logger.exception("classify_json パース失敗 job=%s", job_id[:8])

        # ファイルプレビュー用: ステージングのファイル一覧
        files = []
        for p in list_staged_files(job_id):
            files.append({
                "name": p.name,
                "url":  f"/qualifications/files/{job_id}/{p.name}",
                "is_image": p.suffix.lower() in (".png", ".jpg", ".jpeg"),
                "is_pdf":   p.suffix.lower() == ".pdf",
            })

        workers       = await _fetch_workers(db)
        quals_master  = await _fetch_qualifications_master(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/classify.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "pending",
            "job": job,
            "files": files,
            "candidates": candidates,
            "overall_confidence": overall_confidence,
            "workers": workers,
            "qualifications_master": quals_master,
        },
    )


@router.get("/files/{job_id}/{filename}")
async def serve_staged_file(
    job_id: str,
    filename: str,
    user: dict = Depends(require_admin),
):
    """staging ディレクトリのファイルを安全に配信する (左ペインプレビュー用)。

    safe_file_response が path 解決を行うため、``..`` 等のトラバーサル試行は 404 になる。
    """
    base = QUALIFICATIONS_STAGING_ROOT / job_id
    target = base / filename
    suffix = target.suffix.lower()
    media_type = _PREVIEW_MIME_HINT.get(suffix)
    return safe_file_response(
        target, base_dir=base,
        filename=filename, media_type=media_type,
    )


async def _ensure_qualification(
    db, name: str, category: str | None = None,
) -> int:
    """資格マスタに ``name`` が無ければ追加し qual_id を返す。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="資格名は必須です")
    cur = await db.execute(
        "SELECT qual_id FROM q_qualifications WHERE name = ?", (name,),
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    # 新規追加
    cur = await db.execute(
        """
        INSERT INTO q_qualifications (name, category, renewal_required)
        VALUES (?, ?, 1)
        """,
        (name, (category or "").strip() or ""),
    )
    qual_id = cur.lastrowid
    logger.info("資格マスタに新規追加: %s (qual_id=%d)", name, qual_id)
    return int(qual_id)


@router.post("/classify/{job_id}")
async def classify_submit(
    request: Request,
    job_id: str,
    user: dict = Depends(require_admin),
):
    """フォーム送信を受け、各候補を q_certificates(status='confirmed') に確定登録する。"""
    form = await request.form()
    try:
        n_candidates = int(form.get("n_candidates", "0"))
    except ValueError:
        n_candidates = 0
    if n_candidates < 1:
        raise HTTPException(status_code=400, detail="登録する候補がありません")

    db = await get_db()
    created: list[int] = []
    try:
        # ジョブ存在 + 状態確認
        cur = await db.execute(
            "SELECT status, classify_json FROM q_upload_jobs WHERE job_id = ?",
            (job_id,),
        )
        job_row = await cur.fetchone()
        if job_row is None:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        if job_row["status"] not in ("await_review", "ocr"):
            raise HTTPException(
                status_code=400,
                detail=f"このジョブは確定済みです (status={job_row['status']})",
            )

        # staging のファイル群を original_files_json に記録
        staged = list_staged_files(job_id)
        original_files_json = _json_dumps(
            [str(p.relative_to(QUALIFICATIONS_STAGING_ROOT.parent)) for p in staged]
        )

        for i in range(n_candidates):
            qual_name = (form.get(f"qualification_name_{i}", "") or "").strip()
            category  = (form.get(f"category_{i}", "") or "").strip()
            try:
                worker_id = int(form.get(f"worker_id_{i}", "") or "0")
            except ValueError:
                worker_id = 0
            certificate_no = (form.get(f"certificate_no_{i}", "") or "").strip() or None
            issuer         = (form.get(f"issuer_{i}", "") or "").strip() or None
            issued_on      = (form.get(f"issued_on_{i}", "") or "").strip() or None
            expires_on     = (form.get(f"expires_on_{i}", "") or "").strip() or None
            renewal_required = (form.get(f"renewal_required_{i}", "") == "1")

            if worker_id <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"候補 {i + 1}: 作業員を選択してください",
                )

            qual_id = await _ensure_qualification(db, qual_name, category)
            cur = await db.execute(
                """
                INSERT INTO q_certificates (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, renewal_required,
                    status, original_files_json,
                    ocr_raw_json, ocr_confidence, ocr_model,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?,
                        ?, NULL, NULL, ?, datetime('now','localtime'), datetime('now','localtime'))
                """,
                (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, 1 if renewal_required else 0,
                    original_files_json,
                    job_row["classify_json"], user["id"],
                ),
            )
            created.append(int(cur.lastrowid))

        # ジョブを done に
        await db.execute(
            "UPDATE q_upload_jobs SET status='done', updated_at=datetime('now','localtime') "
            "WHERE job_id = ?",
            (job_id,),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications classify 確定: job=%s certs=%d user=%s",
        job_id[:8], len(created), user.get("username"),
    )
    # 一覧 (確定済み) にリダイレクト
    return RedirectResponse(url="/qualifications/", status_code=303)


def _json_dumps(value) -> str:
    """JSON dumps (順序保持・日本語そのまま)。Python 標準で十分。"""
    import json as _json
    return _json.dumps(value, ensure_ascii=False)
