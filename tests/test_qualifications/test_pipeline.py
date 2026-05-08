"""skills/qualifications/pipeline.py の DB 状態遷移テスト。

実 Gemini API は呼ばない。``make_ocr_client`` を ``FakeOCRClient`` に差し替え、
DB と staging の動作を一時ディレクトリで再現する。
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from skills.qualifications import pipeline as pipeline_mod
from skills.qualifications import storage as storage_mod
from skills.qualifications.ocr import FakeOCRClient
from skills.qualifications.pipeline import run_ocr_pipeline
from skills.qualifications.schema import (
    Candidate,
    FieldConfidences,
    OCRResponse,
)


# ────────────────────────────────────────────
# フィクスチャ
# ────────────────────────────────────────────

async def _init_temp_db(db_path: Path) -> None:
    """テスト用 DB にスキーマを展開し、admin ユーザー (FK 用) を 1 件入れる。"""
    from web_app.core.database import _SCHEMA_SQL

    db = await aiosqlite.connect(str(db_path))
    try:
        await db.executescript(_SCHEMA_SQL)
        await db.execute(
            "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
            "VALUES ('test-admin', 'admin', 'テスト管理者', 'x', 1)"
        )
        await db.commit()
    finally:
        await db.close()


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """pipeline / storage の DB & staging を tmp_path に切り替える。"""
    db_path = tmp_path / "test.db"
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    # 各モジュール内の参照を上書き
    monkeypatch.setattr(pipeline_mod, "DATABASE_PATH", db_path)
    monkeypatch.setattr(storage_mod, "QUALIFICATIONS_STAGING_ROOT", staging_root)

    asyncio.run(_init_temp_db(db_path))

    return {"db_path": db_path, "staging": staging_root}


def _insert_job(db_path: Path, job_id: str, file_count: int = 1, status: str = "pending") -> None:
    """テスト用ジョブレコードを 1 件入れる。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO q_upload_jobs (job_id, user_id, file_count, status) "
        "VALUES (?, 'test-admin', ?, ?)",
        (job_id, file_count, status),
    )
    conn.commit()
    conn.close()


def _stage_dummy_file(staging_root: Path, job_id: str, name: str = "cert.pdf") -> Path:
    """ステージングに 1 ファイル置く。"""
    job_dir = staging_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    p = job_dir / name
    p.write_bytes(b"%PDF-1.4 dummy")
    return p


def _job_row(db_path: Path, job_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM q_upload_jobs WHERE job_id=?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ────────────────────────────────────────────
# 状態遷移テスト
# ────────────────────────────────────────────

class TestPipelineStateTransitions:
    def test_pending_to_await_review_on_success(self, temp_env, monkeypatch):
        """pending → ocr → await_review (OCR 成功)。classify_json に結果が入る。"""
        job_id = "test_success_001"
        _insert_job(temp_env["db_path"], job_id)
        _stage_dummy_file(temp_env["staging"], job_id, "玉掛け_表.pdf")
        _stage_dummy_file(temp_env["staging"], job_id, "玉掛け_裏.png")

        fixture = OCRResponse(
            candidates=[
                Candidate(
                    page_indices=[0, 1],
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎",
                    issued_on="2024-04-01",
                    expires_on=None,
                    renewal_required=False,
                    field_confidences=FieldConfidences(
                        qualification_name=0.95, worker_name=0.99,
                    ),
                )
            ],
            overall_confidence=0.92,
        )
        monkeypatch.setattr(
            pipeline_mod, "make_ocr_client", lambda: FakeOCRClient(fixture),
        )

        asyncio.run(run_ocr_pipeline(job_id))

        row = _job_row(temp_env["db_path"], job_id)
        assert row["status"] == "await_review"
        assert row["error_message"] is None
        # classify_json はラウンドトリップ可能であること
        restored = OCRResponse.model_validate_json(row["classify_json"])
        assert len(restored.candidates) == 1
        assert restored.candidates[0].worker_name == "山田太郎"
        assert restored.overall_confidence == pytest.approx(0.92)

    def test_pending_to_error_when_ocr_raises(self, temp_env, monkeypatch):
        """OCR 例外 → status='error', error_message に例外名。"""
        job_id = "test_ocr_fail_002"
        _insert_job(temp_env["db_path"], job_id)
        _stage_dummy_file(temp_env["staging"], job_id)

        class _BoomClient:
            def extract(self, files):
                raise RuntimeError("simulated network down")

        monkeypatch.setattr(pipeline_mod, "make_ocr_client", lambda: _BoomClient())

        asyncio.run(run_ocr_pipeline(job_id))

        row = _job_row(temp_env["db_path"], job_id)
        assert row["status"] == "error"
        assert "RuntimeError" in row["error_message"]
        # classify_json は更新されていない
        assert row["classify_json"] is None

    def test_pending_to_error_when_staging_empty(self, temp_env, monkeypatch):
        """staging にファイルが無い → OCR を呼ばずに error に着地。"""
        job_id = "test_no_files_003"
        _insert_job(temp_env["db_path"], job_id)
        # ステージングを意図的に空のまま
        ocr_called = []
        monkeypatch.setattr(
            pipeline_mod, "make_ocr_client",
            lambda: (_ for _ in ()).throw(AssertionError("OCR を呼んではいけない")),
        )

        asyncio.run(run_ocr_pipeline(job_id))

        row = _job_row(temp_env["db_path"], job_id)
        assert row["status"] == "error"
        assert "ファイル" in row["error_message"]
        assert ocr_called == []

    def test_skip_when_already_done(self, temp_env, monkeypatch):
        """status='await_review' のジョブを再呼び出ししても上書きしない (冪等)。"""
        job_id = "test_already_done_004"
        _insert_job(temp_env["db_path"], job_id, status="await_review")
        _stage_dummy_file(temp_env["staging"], job_id)

        ocr_called = []
        monkeypatch.setattr(
            pipeline_mod, "make_ocr_client",
            lambda: (_ for _ in ()).throw(AssertionError("OCR を呼んではいけない")),
        )

        asyncio.run(run_ocr_pipeline(job_id))

        row = _job_row(temp_env["db_path"], job_id)
        assert row["status"] == "await_review"
        assert ocr_called == []

    def test_missing_job_logs_warning(self, temp_env, caplog):
        """存在しない job_id を渡しても例外を投げず警告ログのみ。"""
        import logging

        with caplog.at_level(logging.WARNING, logger="web_app.qualifications.pipeline"):
            asyncio.run(run_ocr_pipeline("nonexistent_job"))
        assert any("見つかりません" in r.message for r in caplog.records)

    def test_status_passes_through_ocr_intermediate(self, temp_env, monkeypatch):
        """pipeline 実行中、一時的に status='ocr' を経由すること。

        OCR が呼ばれた瞬間に DB を覗き、status が ocr になっているかを確認する。
        """
        job_id = "test_intermediate_005"
        _insert_job(temp_env["db_path"], job_id)
        _stage_dummy_file(temp_env["staging"], job_id)

        observed_status: list[str] = []

        class _ObservingClient:
            def extract(self, files):
                # OCR コール時点での status を覗く
                conn = sqlite3.connect(str(temp_env["db_path"]))
                row = conn.execute(
                    "SELECT status FROM q_upload_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                observed_status.append(row[0])
                conn.close()
                return OCRResponse()

        monkeypatch.setattr(pipeline_mod, "make_ocr_client", lambda: _ObservingClient())
        asyncio.run(run_ocr_pipeline(job_id))

        assert observed_status == ["ocr"]
        final = _job_row(temp_env["db_path"], job_id)
        assert final["status"] == "await_review"
