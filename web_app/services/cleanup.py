"""
定期クリーンアップ — 古いジョブファイルと DB レコードの自動削除

完了またはエラーから一定時間経過したジョブの:
  - uploads/{job_id}/ ディレクトリ
  - outputs/{job_id}/ ディレクトリ
  - DB の jobs レコード
  - DB の期限切れ sessions レコード
を削除する。

qualifications (資格者証管理) のファイルは ``uploads/qualifications/<job_id>/``
配下に保存されるが、これは ``jobs`` テーブルとは別系統 (``q_upload_jobs`` /
``q_certificates``) で管理されるため、注文書系のクリーンアップ対象から
完全に除外する。専用の孤児検出は ``_cleanup_qualifications_orphans`` が担う。
"""
import asyncio
import json
import logging
import shutil
import time
from threading import Thread

import aiosqlite

from web_app.core.config import (
    DATABASE_PATH,
    UPLOAD_DIR,
    OUTPUT_DIR,
    CLEANUP_AGE_HOURS,
    CLEANUP_INTERVAL,
    MAX_JOBS_PER_USER,
)

# ``UPLOAD_DIR`` 直下にあって注文書ジョブの ID ではない予約名。
# 孤児検出のループで child.name がここに含まれていたら絶対に削除しない。
# 資格者証管理 (qualifications) のファイル基幹ディレクトリを誤削除しないための
# 防御で、これを外すと過去に発生した「ファイル一括欠損」事故が再発する。
_RESERVED_UPLOAD_SUBDIRS: frozenset[str] = frozenset({"qualifications"})

# qualifications 専用孤児検出の保持期間 (秒)。
# 30 日経過し、かつ DB のどこからも参照されていない job_id ディレクトリのみ
# 削除する。ユーザーが明示的に削除/確定するまでは原則保持する方針。
_QUALIFICATIONS_ORPHAN_RETENTION_SECONDS: int = 30 * 24 * 60 * 60

logger = logging.getLogger("web_app.cleanup")

_cleanup_thread: Thread | None = None
_stop_event: asyncio.Event | None = None


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

        # ── 4) 孤児ディレクトリの検出・削除 (注文書系) ──
        cleaned_orphans = await _cleanup_orphan_dirs(db)

        # ── 5) qualifications 専用の孤児クリーンアップ ──
        # q_upload_jobs / q_certificates のいずれからも参照されていない
        # uploads/qualifications/<job_id>/ を 30 日経過後にだけ削除する。
        cleaned_qualifications_orphans = await _cleanup_qualifications_orphans(db)

        total_jobs = deleted_excess + deleted_jobs
        if deleted_drafts:
            logger.info("古い draft 集計データ %d 件を削除しました", deleted_drafts)
        any_action = (
            total_jobs or deleted_sessions or cleaned_orphans
            or deleted_drafts or cleaned_qualifications_orphans
        )
        if any_action:
            logger.info(
                "クリーンアップ完了: ジョブ %d 件削除（超過 %d + 期限切れ %d）, "
                "セッション %d 件削除, 孤児ディレクトリ %d 件削除, "
                "資格者証孤児 %d 件削除",
                total_jobs, deleted_excess, deleted_jobs,
                deleted_sessions, cleaned_orphans,
                cleaned_qualifications_orphans,
            )
        else:
            logger.debug("クリーンアップ: 削除対象なし")

    finally:
        await db.close()


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
    """注文書系の孤児ディレクトリを削除する。

    ``jobs`` テーブルに存在しない ``UPLOAD_DIR/`` または ``OUTPUT_DIR/`` 直下の
    サブディレクトリを削除する。**ただし** ``_RESERVED_UPLOAD_SUBDIRS`` に
    含まれるサブディレクトリ (現状: ``qualifications``) は別系統の DB
    (``q_upload_jobs`` / ``q_certificates``) で管理されており、ここで処理すると
    全件を孤児と誤判定して丸ごと削除してしまうため、明示的にスキップする。
    """
    count = 0

    for base_dir in (UPLOAD_DIR, OUTPUT_DIR):
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            job_id = child.name
            # 予約サブディレクトリ (qualifications など) は別系統の管理対象。
            # ここで rmtree すると過去の「資格者証ファイル一括欠損」事故が
            # 再発するため、必ずスキップする。
            if base_dir == UPLOAD_DIR and job_id in _RESERVED_UPLOAD_SUBDIRS:
                continue
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            if row["cnt"] == 0:
                shutil.rmtree(str(child), ignore_errors=True)
                logger.info("孤児ディレクトリ削除: %s", child)
                count += 1

    return count


async def _cleanup_qualifications_orphons_collect_referenced_jobs(
    db: aiosqlite.Connection,
) -> set[str]:
    """``q_upload_jobs`` と ``q_certificates`` から参照中の job_id 集合を作る。

    - ``q_upload_jobs.job_id`` は staging 中 (= 確定前) の参照
    - ``q_certificates.original_files_json`` は確定済み資格者証から参照される
      物理ファイルパス (例: ``qualifications/<job_id>/<file>.pdf``)。ここから
      job_id 部を抽出して保持参照リストに加える
    """
    referenced: set[str] = set()

    cursor = await db.execute("SELECT job_id FROM q_upload_jobs")
    for row in await cursor.fetchall():
        if row["job_id"]:
            referenced.add(row["job_id"])

    cursor = await db.execute(
        "SELECT original_files_json FROM q_certificates "
        "WHERE original_files_json IS NOT NULL AND original_files_json <> ''"
    )
    for row in await cursor.fetchall():
        raw = row["original_files_json"]
        try:
            paths = json.loads(raw) or []
        except (TypeError, ValueError):
            logger.warning(
                "q_certificates.original_files_json が JSON として解釈できません: %r",
                raw[:120] if isinstance(raw, str) else raw,
            )
            continue
        for path_str in paths:
            # 形式は ``qualifications/<job_id>/<filename>``。
            # 想定外の形でも壊れずに拾うため split で頑健にハンドルする。
            if not isinstance(path_str, str):
                continue
            parts = path_str.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[0] == "qualifications":
                referenced.add(parts[1])

    return referenced


async def _cleanup_qualifications_orphans(db: aiosqlite.Connection) -> int:
    """資格者証専用の孤児クリーンアップ。

    削除対象の条件 (すべて満たした場合のみ):
      1. ``uploads/qualifications/<job_id>/`` が物理ディレクトリとして存在
      2. ``q_upload_jobs`` および ``q_certificates`` のどちらからも参照されない
      3. ディレクトリの mtime が ``_QUALIFICATIONS_ORPHAN_RETENTION_SECONDS``
         (デフォルト 30 日) より前

    一定時間で勝手に消える従来挙動を是正し、ユーザーが明示的に確定/削除
    するまで原則保持する方針に揃える。30 日のグレース期間は本当に到達不能
    な孤児 (DB ロールバック中断などで発生) のみを対象とするための保険。
    """
    qualifications_root = UPLOAD_DIR / "qualifications"
    if not qualifications_root.exists():
        return 0

    referenced = await _cleanup_qualifications_orphons_collect_referenced_jobs(db)

    now = time.time()
    cutoff = now - _QUALIFICATIONS_ORPHAN_RETENTION_SECONDS
    count = 0

    for child in qualifications_root.iterdir():
        if not child.is_dir():
            continue
        job_id = child.name
        if job_id in referenced:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError as exc:
            logger.warning(
                "qualifications 孤児候補の stat 失敗 path=%s err=%s", child, exc,
            )
            continue
        if mtime > cutoff:
            # まだ 30 日経っていない。確定途中・差し替え予定など、生きた
            # ジョブの可能性があるので保持する。
            continue
        age_days = (now - mtime) / 86400
        try:
            shutil.rmtree(str(child), ignore_errors=False)
        except OSError as exc:
            logger.error(
                "qualifications 孤児削除失敗 job_id=%s path=%s err=%s",
                job_id, child, exc,
            )
            continue
        logger.info(
            "qualifications 孤児ディレクトリ削除: job_id=%s path=%s age_days=%.1f",
            job_id, child, age_days,
        )
        count += 1

    return count
