"""代価表リンク設定 — ルーター。

Excel をアップロード → 内部ハイパーリンクを付与 → ダウンロード提供。
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from web_app.core.config import MAX_UPLOAD_SIZE_MB
from web_app.core.dependencies import RequirePermission
from web_app.core.safe_files import safe_file_response
from web_app.core.templates import templates as _templates
from skills.daika_link_setting import process as run_processor

logger = logging.getLogger("web_app.daika_link")

router = APIRouter(prefix="/daika-link", tags=["daika_link"])

_FEATURE = "daika_link"
_RequireGeneral = RequirePermission(_FEATURE, "general")
_RequireManager = RequirePermission(_FEATURE, "manager")

# 出力ディレクトリ — web_app/outputs_daika_link/ 配下に保存。
# OUTPUT_DIR (web_app/outputs/) の "外側" に置いているのは意図的：
# services/cleanup.py の _cleanup_orphan_dirs は OUTPUT_DIR 直下の任意
# サブディレクトリを「jobs テーブルにない孤児」とみなして毎回削除するため、
# OUTPUT_DIR 配下に置くとアップロード直後でも削除されてしまう（実害発生済）。
# construction_cost も同じ理由で web_app/outputs_cc/ を使っており、それに揃える。
_OUT_BASE: Path = Path(__file__).resolve().parent.parent / "outputs_daika_link"

_MAX_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
_FORBIDDEN_FILENAME_CHARS = re.compile(r'[\\/:\*\?"<>\|\x00-\x1f]')


def _safe_stem(original: str) -> str:
    """OS パス区切り・制御文字を取り除き、長さを 80 文字に制限する。"""
    name = _FORBIDDEN_FILENAME_CHARS.sub("_", original).strip()
    stem = Path(name).stem or "input"
    return stem[:80] or "input"


def _render_index(request: Request, user: dict, *, result: dict | None = None,
                  msg: str = "", cat: str = "") -> HTMLResponse:
    return _templates.TemplateResponse(request, "daika_link/index.html", {
        "user": user,
        "result": result,
        "msg": msg,
        "cat": cat,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
    })


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: dict = Depends(_RequireGeneral)):
    """アップロード画面（結果は POST 後にレンダリングされる）。"""
    return _render_index(request, user)


@router.post("/process", response_class=HTMLResponse)
async def process_upload(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(_RequireManager),
):
    """Excel を受け取り、内部ハイパーリンクを付与して保存する。"""
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".xlsx"):
        return _render_index(
            request, user,
            msg=".xlsx ファイルのみ対応しています。",
            cat="danger",
        )

    data = await file.read()
    if not data:
        return _render_index(
            request, user,
            msg="ファイルが空です。", cat="danger",
        )
    if len(data) > _MAX_BYTES:
        return _render_index(
            request, user,
            msg=f"ファイルサイズが上限 {MAX_UPLOAD_SIZE_MB}MB を超えています。",
            cat="danger",
        )

    stem = _safe_stem(filename)
    job_id = uuid.uuid4().hex[:8]
    out_path = _OUT_BASE / f"{stem}_linked_{job_id}.xlsx"
    # 起動後にディレクトリが消えていた / そもそも作られていなかったケースに備え、
    # 保存処理の直前で毎回確実に作成する（既存なら no-op）。
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        stats = run_processor(data, out_path)
    except Exception as e:
        logger.exception("代価表リンク処理に失敗 (file=%s)", filename)
        return _render_index(
            request, user,
            msg=f"処理中にエラーが発生しました: {e}",
            cat="danger",
        )

    result = {
        "filename": out_path.name,
        "original_filename": filename,
        "sheets_scanned": stats.sheets_scanned,
        "targets_indexed": stats.targets_indexed,
        "links_created": stats.links_created,
        "basis_columns": stats.basis_columns,
        "keywords_unmatched": stats.keywords_unmatched[:20],
        "keywords_unmatched_total": len(stats.keywords_unmatched),
        "duplicate_targets": stats.duplicate_targets[:20],
    }
    return _render_index(
        request, user,
        result=result,
        msg=f"リンクを {stats.links_created} 件付与しました。",
        cat="success",
    )


@router.get("/download/{filename}")
async def download(filename: str, user: dict = Depends(_RequireGeneral)):
    """出力ファイルを安全にダウンロード提供する。"""
    path = _OUT_BASE / filename
    return safe_file_response(
        path,
        _OUT_BASE,
        filename=filename,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
