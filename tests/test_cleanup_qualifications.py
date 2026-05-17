"""``web_app.services.cleanup`` のクリーンアップロジックのテスト。

最重要シナリオは「qualifications 配下のファイルが孤児ディレクトリ削除に
巻き込まれない」こと。過去に ``_cleanup_orphan_dirs`` が
``UPLOAD_DIR / qualifications`` を ``jobs`` テーブルに無いから、と判定して
丸ごと ``rmtree`` する事故が起きており、再発防止のリグレッションテストを
重点的に置く。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import aiosqlite
import pytest

from web_app.services import cleanup as cleanup_module


# ────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    upload_path TEXT,
    result_zip  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS q_upload_jobs (
    job_id      TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS q_certificates (
    cert_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id            INTEGER,
    qual_id              INTEGER,
    status               TEXT NOT NULL DEFAULT 'confirmed',
    original_files_json  TEXT
);
"""


@pytest.fixture
def tmp_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """テスト用の UPLOAD_DIR を一時ディレクトリに差し替える。"""
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(cleanup_module, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(cleanup_module, "OUTPUT_DIR", outputs)
    return uploads


async def _open_db(db_path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    return db


def _set_dir_old(p: Path, days_old: int) -> None:
    """ディレクトリ (および親) の mtime を ``days_old`` 日前に巻き戻す。"""
    target_ts = time.time() - days_old * 86400
    os.utime(str(p), (target_ts, target_ts))


# ════════════════════════════════════════════
# _cleanup_orphan_dirs: qualifications 防御
# ════════════════════════════════════════════

class TestOrphanDirsRespectsQualificationsReservation:
    def test_qualifications_dir_is_not_deleted_even_if_jobs_table_empty(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """``UPLOAD_DIR/qualifications/`` は jobs テーブルが空でも保護される。

        過去に発生した「全資格者証ファイルが毎時消える」事故の直接の
        リグレッションテスト。
        """
        qualifications_root = tmp_uploads / "qualifications"
        qualifications_root.mkdir()
        (qualifications_root / "job-A" / "cert.pdf").parent.mkdir(parents=True)
        (qualifications_root / "job-A" / "cert.pdf").write_bytes(b"%PDF-1.4")

        # 比較対象: 注文書ジョブの孤児ディレクトリ (jobs テーブルに無い)
        order_orphan = tmp_uploads / "abandoned-order-job-id"
        order_orphan.mkdir()
        (order_orphan / "input.xlsx").write_bytes(b"x")

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                # jobs テーブルは空 → 通常ロジックなら全部削除されるが、
                # qualifications は予約名としてスキップされる必要がある。
                deleted = await cleanup_module._cleanup_orphan_dirs(db)
            finally:
                await db.close()
            return deleted

        deleted = asyncio.run(run())

        # 注文書孤児は削除される (1 件)
        assert not order_orphan.exists()
        # qualifications はもちろん残る
        assert qualifications_root.exists()
        assert (qualifications_root / "job-A" / "cert.pdf").exists()
        assert deleted == 1

    def test_outputs_dir_is_unaffected_by_qualifications_reservation(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """OUTPUT_DIR 直下に ``qualifications`` という名前があっても、
        そちらは予約対象ではないので通常通り扱う。

        予約は UPLOAD_DIR 直下にだけ効くこと (副作用の最小化) を担保する。
        """
        outputs = tmp_uploads.parent / "outputs"
        spurious = outputs / "qualifications"
        spurious.mkdir()
        (spurious / "trash.txt").write_bytes(b"x")

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                return await cleanup_module._cleanup_orphan_dirs(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        # OUTPUT_DIR 側の "qualifications" は保護されないため削除される
        assert not spurious.exists()
        assert deleted == 1


# ════════════════════════════════════════════
# _cleanup_qualifications_orphans: 専用孤児検出
# ════════════════════════════════════════════

class TestQualificationsOrphansCleanup:
    def test_referenced_by_q_upload_jobs_is_kept(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """``q_upload_jobs`` に行があるジョブのディレクトリは絶対に消さない
        (進行中ジョブ保護)。"""
        job_id = "active-job-1"
        d = tmp_uploads / "qualifications" / job_id
        d.mkdir(parents=True)
        (d / "scan.pdf").write_bytes(b"%PDF-1.4")
        _set_dir_old(d, days_old=60)  # 60 日経過していても保護される

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                await db.execute(
                    "INSERT INTO q_upload_jobs(job_id, user_id, status) "
                    "VALUES (?, 'u1', 'await_review')",
                    (job_id,),
                )
                await db.commit()
                return await cleanup_module._cleanup_qualifications_orphans(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        assert deleted == 0
        assert d.exists()

    def test_referenced_by_q_certificates_is_kept(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """``q_certificates.original_files_json`` から参照される job_id の
        ディレクトリは保護される (確定済み資格者証の原本保護)。"""
        job_id = "confirmed-job-1"
        d = tmp_uploads / "qualifications" / job_id
        d.mkdir(parents=True)
        (d / "cert.pdf").write_bytes(b"%PDF-1.4")
        _set_dir_old(d, days_old=120)  # 4 か月経過していても保護される

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                await db.execute(
                    "INSERT INTO q_certificates(worker_id, qual_id, status, "
                    "original_files_json) VALUES (1, 1, 'confirmed', ?)",
                    (json.dumps([f"qualifications/{job_id}/cert.pdf"]),),
                )
                await db.commit()
                return await cleanup_module._cleanup_qualifications_orphans(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        assert deleted == 0
        assert d.exists()

    def test_fresh_orphan_is_kept(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """30 日未満の孤児は保持期間内のため保護される。

        DB トランザクション中断などで発生した可能性があるが、まだ生きた
        ジョブの完成途中の可能性もあるためすぐには消さない。
        """
        job_id = "fresh-orphan"
        d = tmp_uploads / "qualifications" / job_id
        d.mkdir(parents=True)
        (d / "x.pdf").write_bytes(b"%PDF")
        # 5 日前 — 保持期間内
        _set_dir_old(d, days_old=5)

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                return await cleanup_module._cleanup_qualifications_orphans(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        assert deleted == 0
        assert d.exists()

    def test_old_unreferenced_orphan_is_removed(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """30 日超 + 参照ゼロの場合だけ削除される。"""
        job_id = "stale-orphan"
        d = tmp_uploads / "qualifications" / job_id
        d.mkdir(parents=True)
        (d / "x.pdf").write_bytes(b"%PDF")
        # 35 日前 — 保持期間 (30 日) を超過
        _set_dir_old(d, days_old=35)

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                return await cleanup_module._cleanup_qualifications_orphans(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        assert deleted == 1
        assert not d.exists()

    def test_handles_malformed_original_files_json_gracefully(
        self, tmp_path: Path, tmp_uploads: Path,
    ):
        """``original_files_json`` が壊れた JSON でも例外にせず、警告ログを
        出して続行する (本番 DB が腐っても crash させないフェイルセーフ)。"""
        d = tmp_uploads / "qualifications" / "stale-job"
        d.mkdir(parents=True)
        (d / "x.pdf").write_bytes(b"%PDF")
        _set_dir_old(d, days_old=40)

        async def run():
            db = await _open_db(tmp_path / "test.db")
            try:
                await db.execute(
                    "INSERT INTO q_certificates(worker_id, qual_id, status, "
                    "original_files_json) VALUES (1, 1, 'confirmed', ?)",
                    ("not-a-json-string",),
                )
                await db.commit()
                return await cleanup_module._cleanup_qualifications_orphans(db)
            finally:
                await db.close()

        deleted = asyncio.run(run())
        # 壊れた JSON でも例外を投げず、参照集合に何も追加されないだけ。
        # 30 日超の orphan として削除される。
        assert deleted == 1
