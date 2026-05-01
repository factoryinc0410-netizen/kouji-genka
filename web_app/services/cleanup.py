"""
定期クリーンアップ — 古いジョブファイルと DB レコードの自動削除

完了またはエラーから一定時間経過したジョブの:
  - uploads/{job_id}/ ディレクトリ
  - outputs/{job_id}/ ディレクトリ
  - DB の jobs レコード
  - DB の期限切れ sessions レコード
を削除する。
"""
import asyncio
import logging
import shutil
from pathlib import Path
from threading import Thread

import aiosqlite

from web_app.core.config import DATABASE_PATH, UPLOAD_DIR, OUTPUT_DIR, CLEANUP_AGE_HOURS

logger = logging.getLogger("web_app.cleanup")

_cleanup_thread: Thread | None = None
_stop_event: asyncio.Event | None = None

# クリーンアップ実行間隔（秒）— 1時間ごと
CLEANUP_INTERVAL = 3600


def start_cleanup_scheduler(loop: asyncio.AbstractEventLoop) -> None:
    """クリーンアップスケジューラを起動する。"""
    global _cleanup_thread
    _cleanup_thread = Thread(
        target=_cleanup_loop, args=(loop,),
        daemon=True, name="cleanup-scheduler",
    )
    _cleanup_thread.start()
    logger.info("クリーンアップスケジューラ起動（%d時間経過したジョブを削除）", CLEANUP_AGE_HOURS)


def _cleanup_loop(loop: asyncio.AbstractEventLoop) -> None:
    """定期的にクリーンアップを実行するループ。"""
    import time

    # 初回は起動30秒後に実行（起動直後の負荷を避ける）
    time.sleep(30)

    while True:
        try:
            future = asyncio.run_coroutine_threadsafe(_run_cleanup(), loop)
            future.result(timeout=120)
        except Exception:
            logger.exception("クリーンアップ処理でエラーが発生")
        time.sleep(CLEANUP_INTERVAL)


async def _run_cleanup() -> None:
    """クリーンアップ本体。"""
    logger.info("クリーンアップ処理を開始します")

    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode=WAL")

        # ── 1) ユーザーごとに最新4件を超える古いジョブを削除 ──
        deleted_excess = await _cleanup_excess_jobs(db)

        # ── 2) 古いジョブのファイル削除 + DB レコード削除 ──
        deleted_jobs = await _cleanup_old_jobs(db)

        # ── 3) 期限切れセッションの削除 ──
        deleted_sessions = await _cleanup_expired_sessions(db)

        # ── 3.5) 古い draft 集計データの削除（24時間以上放置されたもの） ──
        deleted_drafts = await _cleanup_stale_drafts(db)

        # ── 4) 孤児ディレクトリの検出・削除 ──
        cleaned_orphans = await _cleanup_orphan_dirs(db)

        total_jobs = deleted_excess + deleted_jobs
        if deleted_drafts:
            logger.info("古い draft 集計データ %d 件を削除しました", deleted_drafts)
        if total_jobs or deleted_sessions or cleaned_orphans or deleted_drafts:
            logger.info(
                "クリーンアップ完了: ジョブ %d 件削除（超過 %d + 期限切れ %d）, セッション %d 件削除, 孤児ディレクトリ %d 件削除",
                total_jobs, deleted_excess, deleted_jobs, deleted_sessions, cleaned_orphans,
            )
        else:
            logger.debug("クリーンアップ: 削除対象なし")

    finally:
        await db.close()


# ユーザーごとに保持する最大ジョブ数
MAX_JOBS_PER_USER = 4


async def _cleanup_excess_jobs(db: aiosqlite.Connection) -> int:
    """ユーザーごとに最新 MAX_JOBS_PER_USER 件を超える完了/エラージョブを削除する。"""
    # 全ユーザーを取得
    cursor = await db.execute("SELECT DISTINCT user_id FROM jobs")
    users = await cursor.fetchall()

    count = 0
    for user_row in users:
        user_id = user_row["user_id"]
        # そのユーザーの完了/エラージョブを新しい順に取得し、5件目以降を特定
        cursor = await db.execute(
            "SELECT id FROM jobs "
            "WHERE user_id = ? AND status IN ('completed', 'error') "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (user_id, MAX_JOBS_PER_USER),
        )
        excess_rows = await cursor.fetchall()

        for row in excess_rows:
            job_id = row["id"]

            upload_dir = UPLOAD_DIR / job_id
            if upload_dir.exists():
                shutil.rmtree(str(upload_dir), ignore_errors=True)

            output_dir = OUTPUT_DIR / job_id
            if output_dir.exists():
                shutil.rmtree(str(output_dir), ignore_errors=True)

            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            count += 1

    if count > 0:
        await db.commit()
        logger.info("ジョブ件数超過: %d 件削除（ユーザーあたり最大 %d 件保持）", count, MAX_JOBS_PER_USER)

    return count


async def _cleanup_old_jobs(db: aiosqlite.Connection) -> int:
    """完了/エラーから CLEANUP_AGE_HOURS 以上経過したジョブを削除する。"""
    cursor = await db.execute(
        "SELECT id, upload_path, result_zip FROM jobs "
        "WHERE status IN ('completed', 'error') "
        "AND updated_at < datetime('now', 'localtime', ?)",
        (f"-{CLEANUP_AGE_HOURS} hours",),
    )
    rows = await cursor.fetchall()

    count = 0
    for row in rows:
        job_id = row["id"]

        # uploads/{job_id}/ を削除
        upload_dir = UPLOAD_DIR / job_id
        if upload_dir.exists():
            shutil.rmtree(str(upload_dir), ignore_errors=True)
            logger.debug("アップロードディレクトリ削除: %s", upload_dir)

        # outputs/{job_id}/ を削除
        output_dir = OUTPUT_DIR / job_id
        if output_dir.exists():
            shutil.rmtree(str(output_dir), ignore_errors=True)
            logger.debug("出力ディレクトリ削除: %s", output_dir)

        # DB レコード削除
        await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        count += 1

    if count > 0:
        await db.commit()
        logger.info("古いジョブ %d 件を削除しました", count)

    return count


async def _cleanup_expired_sessions(db: aiosqlite.Connection) -> int:
    """期限切れセッションを削除する。"""
    cursor = await db.execute(
        "DELETE FROM sessions WHERE expires_at < datetime('now', 'localtime')"
    )
    await db.commit()
    count = cursor.rowcount
    return count


async def _cleanup_stale_drafts(db: aiosqlite.Connection) -> int:
    """24時間以上放置された draft 状態の cc_process_log レコードを削除する。"""
    cursor = await db.execute(
        "DELETE FROM cc_process_log "
        "WHERE status = 'draft' "
        "AND processed_at < datetime('now', 'localtime', '-24 hours')"
    )
    await db.commit()
    return cursor.rowcount


async def _cleanup_orphan_dirs(db: aiosqlite.Connection) -> int:
    """DB にレコードがないが uploads/ や outputs/ にディレクトリが残っている場合に削除する。"""
    count = 0

    for base_dir in (UPLOAD_DIR, OUTPUT_DIR):
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            job_id = child.name
            # DB にこの job_id が存在するか確認
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            if row["cnt"] == 0:
                shutil.rmtree(str(child), ignore_errors=True)
                logger.info("孤児ディレクトリ削除: %s", child)
                count += 1

    return count
