"""
バックグラウンドワーカー — 単一スレッドでジョブを直列処理
COM 操作はこのスレッドからのみ実行されるため、排他制御が保証される。
"""
import asyncio
import logging
import shutil
import traceback
import zipfile
from pathlib import Path
from threading import Thread

import aiosqlite

from web_app.core.config import DATABASE_PATH, OUTPUT_DIR, COM_TEMP_DIR
from web_app.services.job_queue import job_queue
from web_app.services.excel_guard import (
    track_excel_processes,
    check_orphan_excel_processes,
)

logger = logging.getLogger("web_app.worker")

# ── ジョブ処理タイムアウト（秒） ──────────────────────────────
JOB_TIMEOUT_SECONDS = 600  # 10分

_worker_thread: Thread | None = None
_event_loop: asyncio.AbstractEventLoop | None = None


def start_worker(loop: asyncio.AbstractEventLoop) -> None:
    """ワーカースレッドを起動する。lifespan の startup から呼ばれる。"""
    global _worker_thread, _event_loop
    _event_loop = loop
    _worker_thread = Thread(target=_worker_main, daemon=True, name="job-worker")
    _worker_thread.start()
    logger.info("ワーカースレッド起動")


def stop_worker() -> None:
    """ワーカースレッドにシャットダウン信号を送る。"""
    job_queue.put(None)  # センチネル
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=30)
    logger.info("ワーカースレッド停止")


# ── ワーカー本体 ──────────────────────────────────────────────

def _worker_main() -> None:
    """キューから job_id を取り出し、1 件ずつ処理するループ。"""
    # 起動時に孤児 Excel プロセスをチェック
    orphans = check_orphan_excel_processes()
    if orphans:
        logger.warning("起動時に孤児 EXCEL.EXE を %d 件検出 — 手動確認を推奨", len(orphans))

    logger.info("ワーカースレッド開始 — ジョブ待機中")
    while True:
        job_id = job_queue.get()
        if job_id is None:
            logger.info("シャットダウン信号受信")
            break
        try:
            _process_job(job_id)
        except Exception:
            # ワーカースレッド自体がクラッシュしないよう最外殻で捕捉
            logger.exception("ジョブ処理で予期せぬ致命的エラー: %s", job_id[:8])
            try:
                _run_async(_update_status(
                    job_id, "error",
                    error_message="致命的な内部エラーが発生しました。管理者に連絡してください。",
                ))
            except Exception:
                logger.exception("エラーステータス更新にも失敗: %s", job_id[:8])


def _process_job(job_id: str) -> None:
    """1 件のジョブを処理する（同期関数）。"""
    job = _run_async(_fetch_job(job_id))
    if job is None:
        logger.warning("ジョブ %s が DB に見つかりません", job_id[:8])
        return

    logger.info("ジョブ処理開始: %s (%s)", job_id[:8], job["filename"])
    _run_async(_update_status(job_id, "processing"))

    upload_path = Path(job["upload_path"])
    job_output_dir = OUTPUT_DIR / job_id
    job_com_tmp = COM_TEMP_DIR / job_id

    try:
        job_com_tmp.mkdir(parents=True, exist_ok=True)

        from skills.order_docs.generate_order_docs import generate_from_excel

        # ── 確認済みデータがあれば読み込む ──
        confirmed_vendors = None
        confirmed_path = upload_path.parent / "confirmed_vendors.json"
        if confirmed_path.exists():
            import json
            try:
                confirmed_vendors = json.loads(
                    confirmed_path.read_text(encoding="utf-8")
                )
                logger.info("確認済みデータを使用: %s (%d 社)", job_id[:8], len(confirmed_vendors))
            except Exception:
                logger.warning("confirmed_vendors.json の読み込み失敗 — 通常抽出にフォールバック", exc_info=True)

        # ── PID トラッキング付きで COM 操作を実行 ──
        # ジョブ専用の一時作業フォルダ（他ジョブと絶対に衝突しない）
        job_work_tmp = job_com_tmp / "work"
        with track_excel_processes() as guard:
            result = generate_from_excel(
                excel_path=upload_path,
                output_dir=job_output_dir,
                confirmed_vendors=confirmed_vendors,
                work_tmp_base=job_work_tmp,
            )
        # ← track_excel_processes の finally で残存 Excel が自動クリーンアップされる

        if result.error:
            error_summary = _format_error_for_user(result.error)
            _run_async(_update_status(
                job_id, "error",
                total_vendors=result.total_vendors,
                success_count=result.success_count,
                error_message=error_summary,
            ))
            logger.error("ジョブ失敗: %s — %s", job_id[:8], result.error[:200])
            return

        # ZIP 作成
        zip_path = _create_result_zip(job_output_dir, job["filename"])

        if zip_path is None:
            error_msg = (
                "PDF が 1 件も生成されませんでした。"
                "Excel の内容と各業者の処理結果を確認してください。"
            )
            _run_async(_update_status(
                job_id, "error",
                total_vendors=result.total_vendors,
                success_count=result.success_count,
                error_message=error_msg,
            ))
            logger.error(
                "ジョブ失敗（PDFゼロ）: %s (0/%d 社成功)",
                job_id[:8], result.total_vendors,
            )
            return

        _run_async(_update_status(
            job_id, "completed",
            total_vendors=result.total_vendors,
            success_count=result.success_count,
            result_zip=str(zip_path),
            error_message=None,
        ))
        logger.info(
            "ジョブ完了: %s (%d/%d 社成功)",
            job_id[:8], result.success_count, result.total_vendors,
        )

    except Exception:
        err = traceback.format_exc()
        error_summary = _format_error_for_user(err)
        _run_async(_update_status(job_id, "error", error_message=error_summary))
        logger.exception("ジョブ処理エラー: %s", job_id[:8])

    finally:
        # COM 一時ディレクトリのクリーンアップ
        if job_com_tmp.exists():
            shutil.rmtree(str(job_com_tmp), ignore_errors=True)

        # ジョブ完了後に孤児チェック（警告ログのみ）
        check_orphan_excel_processes()


# ── エラーメッセージ整形 ──────────────────────────────────────

def _format_error_for_user(raw_error: str) -> str:
    """スタックトレースからユーザー向けのエラーメッセージを生成する。

    - 末尾の例外行（最も重要な情報）を抽出
    - 全体は2000文字以内に収める
    """
    lines = raw_error.strip().splitlines()

    # 末尾の例外メッセージを抽出
    error_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            error_lines.insert(0, stripped)
            # 例外クラス行（"XxxError:" を含む行）まで遡る
            if "Error" in stripped or "Exception" in stripped:
                break
        if len(error_lines) >= 3:
            break

    summary = "\n".join(error_lines) if error_lines else raw_error[-500:]

    # 全体も保持（デバッグ用）
    full = raw_error[:1500]
    result = f"{summary}\n\n--- 詳細 ---\n{full}"
    return result[:2000]


# ── ZIP 作成 ──────────────────────────────────────────────────

def _create_result_zip(output_dir: Path, original_filename: str) -> Path | None:
    """出力ディレクトリ内の全 PDF を ZIP にまとめる。"""
    pdf_files = list(output_dir.glob("*.pdf"))
    if not pdf_files:
        return None

    zip_name = Path(original_filename).stem + "_注文書一式.zip"
    zip_path = output_dir / zip_name

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf in pdf_files:
            zf.write(pdf, pdf.name)

    logger.info("ZIP 作成完了: %s (%d ファイル)", zip_path.name, len(pdf_files))
    return zip_path


# ── asyncio ブリッジ ──────────────────────────────────────────

def _run_async(coro):
    """ワーカースレッドから asyncio コルーチンを実行する。"""
    if _event_loop is None:
        raise RuntimeError("Event loop not set")
    future = asyncio.run_coroutine_threadsafe(coro, _event_loop)
    return future.result(timeout=30)


async def _fetch_job(job_id: str) -> dict | None:
    """DB からジョブ情報を取得する。"""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    try:
        cursor = await db.execute(
            "SELECT id, user_id, filename, upload_path, status FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def _update_status(
    job_id: str,
    status: str,
    *,
    total_vendors: int | None = None,
    success_count: int | None = None,
    result_zip: str | None = None,
    error_message: str | None = None,
) -> None:
    """DB のジョブステータスを更新する。"""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    try:
        from web_app.services.job_queue import update_job_status
        await update_job_status(
            db, job_id, status,
            total_vendors=total_vendors,
            success_count=success_count,
            result_zip=result_zip,
            error_message=error_message,
        )
    finally:
        await db.close()
