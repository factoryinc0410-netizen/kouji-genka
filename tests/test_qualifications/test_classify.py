"""classify GET / POST / file 配信の統合テスト。

TestClient + 一時 DB + dependency_overrides で admin 動作を再現する。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from skills.qualifications.schema import (
    Candidate,
    FieldConfidences,
    OCRResponse,
)


# ────────────────────────────────────────────
# テスト全体共通: 一時 DB + UPLOAD_DIR を切り替えてからモジュールロード
# ────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_env():
    """モジュールスコープで temp DB / staging を切り替え、TestClient を返す。

    web_app.* モジュールは他のテストで既にロード済みの可能性があるため、
    env だけでなくモジュール内の定数 (``_DB_PATH`` 等) を直接 monkeypatch する。
    """
    import shutil

    from _pytest.monkeypatch import MonkeyPatch

    tmp_root = Path(tempfile.mkdtemp(prefix="classify_test_"))
    tmp_db = tmp_root / "app.db"
    tmp_upload = tmp_root / "uploads"
    tmp_upload.mkdir()
    os.environ["DATABASE_PATH"] = str(tmp_db)
    os.environ["UPLOAD_DIR"] = str(tmp_upload)

    from fastapi.testclient import TestClient

    from web_app.core import config as cfg
    from web_app.core import database as db
    from web_app.core.dependencies import get_current_user, require_admin
    from web_app.main import app
    from skills.qualifications import pipeline as pipeline_mod
    from skills.qualifications import storage as storage_mod
    from web_app.routers import qualifications as q_router

    mpatch = MonkeyPatch()
    # 既にロード済みのモジュール内に焼き込まれた絶対パスを上書きする
    mpatch.setattr(cfg, "DATABASE_PATH", tmp_db, raising=False)
    mpatch.setattr(cfg, "UPLOAD_DIR", tmp_upload, raising=False)
    mpatch.setattr(db, "_DB_PATH", str(tmp_db), raising=False)
    mpatch.setattr(pipeline_mod, "DATABASE_PATH", tmp_db, raising=False)
    mpatch.setattr(
        storage_mod, "QUALIFICATIONS_STAGING_ROOT",
        tmp_upload / "qualifications", raising=False,
    )
    mpatch.setattr(
        q_router, "QUALIFICATIONS_STAGING_ROOT",
        tmp_upload / "qualifications", raising=False,
    )

    asyncio.run(db.init_db())

    # users + cc_workers のシード (FK 用)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
        "VALUES ('admin-id', 'admin', '管理者', 'x', 1)"
    )
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (1, '山田太郎', 'A班', 1)"
    )
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (2, '佐藤花子', 'B班', 1)"
    )
    conn.commit()
    conn.close()

    admin_user = {
        "id": "admin-id", "username": "admin",
        "display_name": "管理者", "is_admin": 1,
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user

    client = TestClient(app)
    yield {"client": client, "db_path": tmp_db, "staging_root": tmp_upload / "qualifications"}

    # 後片付け: モジュール定数を元の値へ戻し、tmp を削除
    app.dependency_overrides.clear()
    mpatch.undo()
    shutil.rmtree(tmp_root, ignore_errors=True)


def _seed_job(
    db_path: Path,
    staging_root: Path,
    job_id: str,
    *,
    status: str = "await_review",
    candidates_fixture: OCRResponse | None = None,
    file_names: list[str] = ("cert.pdf",),
) -> None:
    """ジョブ + classify_json + staging ファイルを 1 セット用意する。"""
    classify_json = (
        candidates_fixture.model_dump_json() if candidates_fixture else None
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO q_upload_jobs (job_id, user_id, file_count, status, classify_json) "
        "VALUES (?, 'admin-id', ?, ?, ?)",
        (job_id, len(file_names), status, classify_json),
    )
    conn.commit()
    conn.close()

    job_dir = staging_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in file_names:
        (job_dir / name).write_bytes(b"%PDF-1.4 dummy\n%%EOF\n")


# ────────────────────────────────────────────
# GET /classify/{job_id}
# ────────────────────────────────────────────

class TestClassifyGet:
    def test_renders_form_with_candidates(self, app_env):
        """OCR 結果 1 件が candidate 形式でフォームに展開される。"""
        job_id = "test_get_001_aaaa"
        fixture = OCRResponse(
            candidates=[
                Candidate(
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎",
                    issued_on="2024-04-01",
                    renewal_required=False,
                    field_confidences=FieldConfidences(
                        qualification_name=0.95, worker_name=0.99,
                    ),
                )
            ],
            overall_confidence=0.97,
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture, file_names=["cert.pdf"])

        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        # フォーム要素が描画されている
        assert "玉掛け技能講習" in r.text
        assert "2024-04-01" in r.text
        assert 'name="n_candidates" value="1"' in r.text
        # 作業員 select に山田太郎が pre-selected されているはず
        assert 'value="1"' in r.text and "山田太郎" in r.text
        # 左ペインに staging のファイル参照
        assert f"/qualifications/files/{job_id}/cert.pdf" in r.text
        # 信頼度バッジ (高信頼度なので bg-success)
        assert "bg-success" in r.text

    def test_redirect_when_status_not_await_review(self, app_env):
        """pending 状態のジョブはアクセス不可 → /pending にリダイレクト。"""
        job_id = "test_get_pending_002"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].get(
            f"/qualifications/classify/{job_id}", follow_redirects=False
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/pending"

    def test_404_when_job_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/classify/does_not_exist")
        assert r.status_code == 404

    def test_renders_when_no_candidates(self, app_env):
        """候補ゼロのジョブもフォームは表示される (警告 alert 付き)。"""
        job_id = "test_get_empty_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=OCRResponse())
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert "候補を抽出できませんでした" in r.text


# ────────────────────────────────────────────
# GET /files/{job_id}/{filename} — preview 配信
# ────────────────────────────────────────────

class TestFilesServe:
    def test_serves_existing_pdf(self, app_env):
        job_id = "test_file_001"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["sample.pdf"])
        r = app_env["client"].get(f"/qualifications/files/{job_id}/sample.pdf")
        assert r.status_code == 200
        assert b"%PDF" in r.content
        assert r.headers["content-type"].startswith("application/pdf")

    def test_404_for_nonexistent_file(self, app_env):
        job_id = "test_file_002"
        _seed_job(app_env["db_path"], app_env["staging_root"], job_id)
        r = app_env["client"].get(f"/qualifications/files/{job_id}/missing.pdf")
        assert r.status_code == 404

    def test_path_traversal_rejected(self, app_env):
        """``..`` を使った base_dir 外への到達を試みると 404。"""
        job_id = "test_file_003"
        _seed_job(app_env["db_path"], app_env["staging_root"], job_id)
        # FastAPI は URL-encoded `..` を path に許容するのでここで試行
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/..%2F..%2Fapp.db"
        )
        # safe_file_response が 404 で弾く (200 では絶対にいけない)
        assert r.status_code == 404


# ────────────────────────────────────────────
# POST /classify/{job_id} — 確定登録
# ────────────────────────────────────────────

class TestClassifySubmit:
    def test_creates_certificate_and_marks_done(self, app_env):
        """送信成功で q_certificates 行が作成、ジョブが done に。"""
        job_id = "test_post_001_aaaaaaaaaaaaaaaaa"
        fixture = OCRResponse(
            candidates=[
                Candidate(
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎",
                    issued_on="2024-04-01",
                    renewal_required=False,
                )
            ],
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture)

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "玉掛け技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",   # 山田太郎
                "certificate_no_0": "第12345号",
                "issuer_0": "○○技能講習センター",
                "issued_on_0": "2024-04-01",
                "expires_on_0": "",
                "renewal_required_0": "",  # チェックなし → 更新不要
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

        # DB 検証
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row

        # ジョブが done
        job = dict(conn.execute(
            "SELECT * FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone())
        assert job["status"] == "done"

        # q_certificates が 1 件
        certs = [dict(r) for r in conn.execute(
            "SELECT * FROM q_certificates WHERE created_by='admin-id' "
            "ORDER BY cert_id DESC LIMIT 1"
        ).fetchall()]
        assert len(certs) == 1
        cert = certs[0]
        assert cert["worker_id"] == 1
        assert cert["certificate_no"] == "第12345号"
        assert cert["issued_on"] == "2024-04-01"
        assert cert["renewal_required"] == 0  # 更新不要
        assert cert["status"] == "confirmed"
        assert "玉掛け技能講習.pdf" in cert["original_files_json"] or \
               "cert.pdf" in cert["original_files_json"]

        # q_qualifications も自動追加される
        q = conn.execute(
            "SELECT * FROM q_qualifications WHERE name='玉掛け技能講習'"
        ).fetchone()
        assert q is not None
        conn.close()

    def test_rejects_when_worker_not_selected(self, app_env):
        job_id = "test_post_no_worker_002"
        fixture = OCRResponse(
            candidates=[Candidate(qualification_name="フォークリフト運転技能講習")],
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture)

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "フォーク",
                "worker_id_0": "",   # 未選択
                "issued_on_0": "2022-10-15",
            },
        )
        assert r.status_code == 400
        assert "作業員" in r.json()["detail"]

        # DB は変更されていない
        conn = sqlite3.connect(str(app_env["db_path"]))
        job_row = conn.execute(
            "SELECT status FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        assert job_row[0] == "await_review"
        conn.close()

    def test_rejects_when_already_done(self, app_env):
        """二重登録防止: 既に done のジョブには再 POST できない。"""
        job_id = "test_post_done_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="done")
        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={"n_candidates": "1", "qualification_name_0": "x",
                  "worker_id_0": "1", "issued_on_0": "2024-01-01"},
        )
        assert r.status_code == 400
        assert "確定済み" in r.json()["detail"]
