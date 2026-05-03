"""
ジョブキュー管理 — SQLite 永続化 + threading.Queue
"""
import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from queue import Queue

import aiosqlite

logger = logging.getLogger("web_app.job_queue")

# ── プロセス内キュー（ワーカースレッドとの連携用） ────────────
job_queue: Queue[str | None] = Queue()  # job_id を投入、None はシャットダウン信号


def compute_file_hash(file_path: Path) -> str:
    """ファイルの SHA-256 ハッシュを計算する。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def create_job(
    db: aiosqlite.Connection,
    user_id: str,
    filename: str,
    upload_path: str,
    file_hash: str,
) -> str:
    """ジョブをDB登録し、キューに投入する。job_id を返す。"""
    job_id = uuid.uuid4().hex
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO jobs (id, user_id, filename, file_hash, upload_path, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (job_id, user_id, filename, file_hash, upload_path, now, now),
    )
    await db.commit()
    job_queue.put(job_id)
    logger.info("ジョブ登録: %s (%s)", job_id[:8], filename)
    return job_id


async def check_duplicate(
    db: aiosqlite.Connection, user_id: str, file_hash: str
) -> dict | None:
    """同一ユーザーが同一ハッシュを直近1時間以内に投入済みか確認する。"""
    cursor = await db.execute(
        "SELECT id, filename, status, created_at FROM jobs "
        "WHERE user_id = ? AND file_hash = ? "
        "AND created_at > datetime('now', 'localtime', '-1 hour') "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, file_hash),
    )
    row = await cursor.fetchone()
    if row:
        return {"id": row["id"], "filename": row["filename"],
                "status": row["status"], "created_at": row["created_at"]}
    return None


async def get_job(db: aiosqlite.Connection, job_id: str) -> dict | None:
    """ジョブ情報を取得する。"""
    cursor = await db.execute(
        "SELECT id, user_id, filename, upload_path, status, total_vendors, success_count, "
        "result_zip, error_message, created_at, updated_at FROM jobs WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def get_user_jobs(db: aiosqlite.Connection, user_id: str, limit: int = 50) -> list[dict]:
    """ユーザーのジョブ一覧を取得する（新しい順）。"""
    cursor = await db.execute(
        "SELECT id, filename, status, total_vendors, success_count, "
        "error_message, created_at, updated_at FROM jobs "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_job_status(
    db: aiosqlite.Connection,
    job_id: str,
    status: str,
    *,
    total_vendors: int | None = None,
    success_count: int | None = None,
    result_zip: str | None = None,
    error_message: str | None = None,
) -> None:
    """ジョブステータスを更新する。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "UPDATE jobs SET status=?, total_vendors=COALESCE(?, total_vendors), "
        "success_count=COALESCE(?, success_count), result_zip=COALESCE(?, result_zip), "
        "error_message=COALESCE(?, error_message), updated_at=? WHERE id=?",
        (status, total_vendors, success_count, result_zip, error_message, now, job_id),
    )
    await db.commit()


async def restore_pending_jobs() -> int:
    """起動時に未完了ジョブをキューに再投入する。"""
    import aiosqlite as _aiosqlite
    from web_app.core.config import DATABASE_PATH

    db = await _aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = _aiosqlite.Row
    try:
        # processing 状態で残っているジョブは前回クラッシュしたものなので pending に戻す
        await db.execute(
            "UPDATE jobs SET status='pending', updated_at=datetime('now','localtime') "
            "WHERE status='processing'"
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT id FROM jobs WHERE status='pending' ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        for row in rows:
            job_queue.put(row["id"])
        count = len(rows)
        if count > 0:
            logger.info("未完了ジョブ %d 件をキューに復元しました", count)
        return count
    finally:
        await db.close()
