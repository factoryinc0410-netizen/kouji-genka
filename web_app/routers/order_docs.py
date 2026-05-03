"""
注文書自動作成ルーター — /orders 配下

2 段階フロー:
  Step 1  POST /orders/upload   → Excel 保存 & データ抽出 → 確認用 JSON 返却
  Step 2  POST /orders/{id}/confirm → 変更回数を反映してジョブ投入
"""
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse

import aiosqlite

from web_app.core.config import UPLOAD_DIR, OUTPUT_DIR, MAX_UPLOAD_SIZE_MB
from web_app.core.dependencies import db_dependency, get_current_user
from web_app.core.safe_files import safe_file_response
from web_app.core.templates import templates as _templates
from web_app.services.job_queue import (
    check_duplicate, compute_file_hash, get_job, get_user_jobs,
    job_queue,
)
from web_app.services.validator import validate_excel

logger = logging.getLogger("web_app.order_docs")

router = APIRouter(prefix="/orders", tags=["order_docs"])

_ALLOWED_EXTENSIONS = {".xlsx", ".xls"}


@router.get("/", response_class=HTMLResponse)
async def order_docs_page(
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """注文書作成ツールのメイン画面。"""
    jobs = await get_user_jobs(db, user["id"])
    return _templates.TemplateResponse(request, "order_docs/upload.html", {
        "user": user,
        "jobs": jobs,
        # base.html のバッジを「注文書作成 v{ORDER_DOCS_VERSION}」にする
        "skill_key": "order_docs",
    })


@router.post("/upload")
async def upload_excel(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """
    Step 1: Excel をアップロードし、データを抽出して確認用に返す。

    ジョブは 'draft' 状態で DB に登録されるが、キューには投入しない。
    フロントエンドで確認画面を表示し、confirm エンドポイントで確定する。
    """
    # ── 拡張子チェック ──
    filename = file.filename or "unknown.xlsx"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return JSONResponse(
            {"ok": False, "error": f"対応していないファイル形式です: {ext}"},
            status_code=400,
        )

    # ── ファイル読み込み＆サイズチェック ──
    content = await file.read()
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        return JSONResponse(
            {"ok": False, "error": f"ファイルサイズが上限（{MAX_UPLOAD_SIZE_MB}MB）を超えています。"},
            status_code=400,
        )

    # ── ジョブID生成＆保存先ディレクトリ作成 ──
    job_id = uuid.uuid4().hex
    upload_dir = UPLOAD_DIR / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / "original.xlsx"
    upload_path.write_bytes(content)

    # ── ハッシュ計算＆重複チェック ──
    file_hash = compute_file_hash(upload_path)
    dup = await check_duplicate(db, user["id"], file_hash)

    # ── Excel バリデーション ──
    valid, error_msg = validate_excel(upload_path)
    if not valid:
        shutil.rmtree(str(upload_dir), ignore_errors=True)
        return JSONResponse(
            {"ok": False, "error": f"Excelファイルの検証に失敗しました: {error_msg}"},
            status_code=400,
        )

    # ── データ抽出（確認画面用） ──
    vendors_for_preview = None
    try:
        from skills.order_docs.extractor import extract_data
        vendor_list = extract_data(upload_path)
        if vendor_list:
            # 抽出結果をファイルに保存（confirm 時に再利用）
            extracted_path = upload_dir / "extracted_vendors.json"
            extracted_path.write_text(
                json.dumps(vendor_list, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # フロントエンド表示用に簡略化
            vendors_for_preview = []
            for v in vendor_list:
                vendors_for_preview.append({
                    "vendor_company": v.get("vendor_company") or "",
                    "contract_date": _format_contract_date(v),
                    "kingaku_ukeoi": v.get("kingaku_ukeoi") or "",
                    "kingaku_koji": v.get("kingaku_koji") or "",
                    "kingaku_zei": v.get("kingaku_zei") or "",
                    "kouki_start": _format_kouki(v, "kouki_start"),
                    "kouki_end": _format_kouki(v, "kouki_end"),
                })
    except Exception:
        logger.warning("データ抽出に失敗（確認画面スキップ）: %s", filename, exc_info=True)

    # ── ジョブ登録（draft 状態 — まだキューには入れない） ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO jobs (id, user_id, filename, file_hash, upload_path, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
        (job_id, user["id"], filename, file_hash, str(upload_path), now, now),
    )
    await db.commit()

    return JSONResponse({
        "ok": True,
        "job_id": job_id,
        "filename": filename,
        "vendors": vendors_for_preview,
        "duplicate_warning": (
            f"同じファイルが {dup['created_at']} に投入済みです（ステータス: {dup['status']}）"
            if dup else None
        ),
    })


@router.post("/{job_id}/confirm")
async def confirm_and_start(
    job_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """
    Step 2: 確認画面で入力された変更回数を反映し、ジョブをキューに投入する。

    Request Body (JSON):
        { "vendors": [ {"index": 0, "henkou_kaisuu": 2}, ... ] }
    """
    job = await get_job(db, job_id)
    if job is None or job["user_id"] != user["id"]:
        return JSONResponse({"ok": False, "error": "ジョブが見つかりません"}, status_code=404)

    if job["status"] != "draft":
        return JSONResponse({"ok": False, "error": "このジョブは既に処理済みです"}, status_code=400)

    # ── リクエストボディから変更回数を取得 ──
    body = await request.json()
    henkou_list = body.get("vendors", [])

    # ── 抽出済みデータに変更回数を反映 ──
    upload_dir = Path(job["upload_path"]).parent
    extracted_path = upload_dir / "extracted_vendors.json"

    if extracted_path.exists():
        vendor_list = json.loads(extracted_path.read_text(encoding="utf-8"))

        for item in henkou_list:
            idx = item.get("index", -1)
            kaisuu = int(item.get("henkou_kaisuu", 0))
            if 0 <= idx < len(vendor_list):
                vendor_list[idx]["henkou_kaisuu"] = str(kaisuu)
                if kaisuu >= 1:
                    vendor_list[idx]["henkou_flag"] = f"第{kaisuu}回変更"

        confirmed_path = upload_dir / "confirmed_vendors.json"
        confirmed_path.write_text(
            json.dumps(vendor_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── ステータスを pending に変更してキュー投入 ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "UPDATE jobs SET status='pending', updated_at=? WHERE id=?",
        (now, job_id),
    )
    await db.commit()
    job_queue.put(job_id)
    logger.info("ジョブ確定・キュー投入: %s (変更件数: %d)", job_id[:8], len(henkou_list))

    return JSONResponse({"ok": True, "job_id": job_id})


def _format_contract_date(vendor: dict) -> str:
    """契約日の年月日を結合して表示用文字列にする。"""
    y = vendor.get("contract_year") or ""
    m = vendor.get("contract_month") or ""
    d = vendor.get("contract_day") or ""
    if y or m or d:
        return f"R{y}.{m}.{d}"
    return ""


def _format_kouki(vendor: dict, prefix: str) -> str:
    """工期の年月日を結合して表示用文字列にする。"""
    y = vendor.get(f"{prefix}_year") or ""
    m = vendor.get(f"{prefix}_month") or ""
    d = vendor.get(f"{prefix}_day") or ""
    if y or m or d:
        return f"R{y}.{m}.{d}"
    return ""


@router.get("/{job_id}/status")
async def job_status(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """ジョブの現在のステータスを返す（ポーリング用）。"""
    job = await get_job(db, job_id)
    if job is None or job["user_id"] != user["id"]:
        return JSONResponse({"error": "ジョブが見つかりません"}, status_code=404)

    return JSONResponse({
        "job_id": job["id"],
        "status": job["status"],
        "filename": job["filename"],
        "total_vendors": job["total_vendors"],
        "success_count": job["success_count"],
        "error_message": job["error_message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "has_zip": job["result_zip"] is not None,
    })


@router.get("/{job_id}/download")
async def download_zip(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(db_dependency),
):
    """完成した ZIP ファイルをダウンロードする。"""
    job = await get_job(db, job_id)
    if job is None or job["user_id"] != user["id"]:
        return JSONResponse({"error": "ジョブが見つかりません"}, status_code=404)

    if job["status"] == "error":
        err = job.get("error_message") or "生成中にエラーが発生しました"
        return JSONResponse({"error": err}, status_code=400)

    if job["status"] != "completed" or not job["result_zip"]:
        return JSONResponse({"error": "ダウンロード可能なファイルがありません"}, status_code=400)

    zip_path = Path(job["result_zip"])
    # safe_file_response が OUTPUT_DIR 配下に収まっているか・実体が存在するかを
    # まとめて検証し、範囲外（DB 改竄等）も非存在も 404 を返す
    return safe_file_response(
        zip_path,
        OUTPUT_DIR,
        filename=zip_path.name,
        media_type="application/zip",
    )
