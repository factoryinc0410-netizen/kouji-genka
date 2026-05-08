"""アップロードジョブ → OCR → ``await_review`` の一気通貫オーケストレーション。

呼ばれ方:
- ``services/worker.py`` のディスパッチが ``run_ocr_pipeline(job_id)`` を呼ぶ
- ジョブの状態を ``q_upload_jobs.status`` で進める

状態遷移:
    pending → ocr → await_review     (Gemini が応答した)
    pending → ocr → error            (ステージング欠落 / OCR 失敗)
    既に done/await_review/error なら no-op で抜ける (冪等性)

OCR 呼び出しは同期 SDK (google-genai) なので ``asyncio.to_thread`` で
別スレッドにオフロードし、本ループの event loop をブロックしない。
"""
from __future__ import annotations

import asyncio
import logging

import aiosqlite

from skills.qualifications.ocr import make_ocr_client
from skills.qualifications.schema import OCRResponse
from skills.qualifications.storage import list_staged_files
from web_app.core.config import DATABASE_PATH

logger = logging.getLogger("web_app.qualifications.pipeline")

# pipeline.run_ocr_pipeline が処理対象とする初期ステータス。
# ``ocr`` を含めるのは「以前クラッシュして ocr のまま残っているジョブ」を再投入したケースに対応するため
# (worker 側の restore_pending_jobs が pending に戻す前提だが、念のため)。
_PROCESSABLE_STATUS = frozenset({"pending", "ocr"})


async def _update_status(
    db: aiosqlite.Connection,
    job_id: str,
    status: str,
    *,
    classify_json: str | None = None,
    error_message: str | None = None,
) -> None:
    """``q_upload_jobs.status`` を更新する。

    既存の値は ``COALESCE`` で温存。``updated_at`` は呼ぶたび更新する。
    """
    await db.execute(
        """
        UPDATE q_upload_jobs
           SET status         = ?,
               classify_json  = COALESCE(?, classify_json),
               error_message  = COALESCE(?, error_message),
               updated_at     = datetime('now','localtime')
         WHERE job_id = ?
        """,
        (status, classify_json, error_message, job_id),
    )
    await db.commit()


async def run_ocr_pipeline(job_id: str) -> None:
    """1 ジョブの OCR ステージを実行する。

    例外を投げない設計: 想定外エラーも DB 上の status='error' に着地させる。
    呼び出し側 (worker) は戻り値を見ない。
    """
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    try:
        # ── ジョブ取得 ──
        cur = await db.execute(
            "SELECT job_id, status FROM q_upload_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.warning("qualifications job %s が DB に見つかりません", job_id[:8])
            return
        current_status = row["status"]
        if current_status not in _PROCESSABLE_STATUS:
            logger.info(
                "qualifications job %s は既に %s — 処理をスキップ",
                job_id[:8], current_status,
            )
            return

        # ── ステージング確認 ──
        files = list_staged_files(job_id)
        if not files:
            await _update_status(
                db, job_id, "error",
                error_message="ステージングディレクトリにファイルがありません。",
            )
            logger.error("qualifications job %s: staging 空", job_id[:8])
            return

        # ── status='ocr' に進める ──
        await _update_status(db, job_id, "ocr")
        logger.info(
            "qualifications OCR 開始 job=%s files=%d",
            job_id[:8], len(files),
        )

        # ── OCR 実行 (同期 SDK を別スレッドへオフロード) ──
        client = make_ocr_client()
        try:
            response: OCRResponse = await asyncio.to_thread(client.extract, files)
        except Exception as e:
            # API キー不備・ネットワーク不通・schema 違反などをまとめて捕捉
            logger.exception(
                "qualifications OCR 失敗 job=%s: %s",
                job_id[:8], e.__class__.__name__,
            )
            await _update_status(
                db, job_id, "error",
                error_message=f"OCR エラー ({e.__class__.__name__})",
            )
            return

        # ── 結果保存 + status='await_review' ──
        await _update_status(
            db, job_id, "await_review",
            classify_json=response.model_dump_json(),
        )
        logger.info(
            "qualifications OCR 完了 job=%s candidates=%d overall=%.2f",
            job_id[:8], len(response.candidates), response.overall_confidence,
        )

    finally:
        await db.close()


__all__ = ["run_ocr_pipeline"]
