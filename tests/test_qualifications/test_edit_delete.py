"""GET/POST /edit/{cert_id} と POST /delete/{cert_id} のテスト。

archived された資格者証は一覧 / 編集画面から消えるが、DB 行は保持されることも検証する。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _cert_no_for(cert_id: int) -> str:
    """cert_id ごとにユニークな cert_no を返す (テスト間で衝突しないように)。"""
    return f"第ED{cert_id:04d}号"


def _seed_one_certificate(db_path: Path, *, cert_id: int = 201) -> None:
    """1 件の確定済み資格者証を投入する。

    cert_no は cert_id 由来でユニーク化することで、複数テストが
    同じ DB に追記しても識別できる。
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO q_qualifications
            (qual_id, name, category, renewal_required)
        VALUES (50, '玉掛け技能講習', '技能講習', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, 1, 50, ?, '○○協会',
                '2024-04-01', '2029-03-31', 0, 'confirmed',
                ?, 'admin-id', datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, _cert_no_for(cert_id),
         '["qualifications/test-job-id/sample.pdf"]'),
    )
    conn.commit()
    conn.close()


def _get_cert(db_path: Path, cert_id: int) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_certificates WHERE cert_id = ?", (cert_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ────────────────────────────────────────────
# GET /edit/{cert_id}
# ────────────────────────────────────────────

class TestEditGet:
    def test_renders_form_with_current_values(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=210)
        r = app_env["client"].get("/qualifications/edit/210")
        assert r.status_code == 200
        # 現在値がフォームにプリフィル
        assert f'value="{_cert_no_for(210)}"' in r.text
        assert 'value="2024-04-01"' in r.text
        assert 'value="2029-03-31"' in r.text
        assert "玉掛け技能講習" in r.text
        # 作業員 1 (山田太郎) が選択済み
        assert 'value="1"' in r.text and "山田太郎" in r.text
        # 原本ファイルへのリンク
        assert "/qualifications/files/test-job-id/sample.pdf" in r.text

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/edit/99999")
        assert r.status_code == 404

    def test_410_when_cert_archived(self, app_env):
        """archived な資格者証は編集できない (410 Gone)。"""
        _seed_one_certificate(app_env["db_path"], cert_id=211)
        # 直接 DB で archived にする
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("UPDATE q_certificates SET status='archived' WHERE cert_id = 211")
        conn.commit()
        conn.close()

        r = app_env["client"].get("/qualifications/edit/211")
        assert r.status_code == 410


# ────────────────────────────────────────────
# POST /edit/{cert_id}
# ────────────────────────────────────────────

class TestEditPost:
    def test_updates_existing_certificate(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=220)
        r = app_env["client"].post(
            "/qualifications/edit/220",
            data={
                "worker_id": "2",                  # 山田 → 佐藤に変更
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": "第ED001号_修正",
                "issuer": "新団体",
                "issued_on": "2024-04-01",
                "expires_on": "2030-04-01",       # 期限を延長
                "renewal_required": "1",          # 更新必要に変更
                "notes": "誤入力を修正",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

        cert = _get_cert(app_env["db_path"], 220)
        assert cert["worker_id"] == 2
        assert cert["certificate_no"] == "第ED001号_修正"
        assert cert["issuer"] == "新団体"
        assert cert["expires_on"] == "2030-04-01"
        assert cert["renewal_required"] == 1
        assert cert["notes"] == "誤入力を修正"
        # status は confirmed のまま
        assert cert["status"] == "confirmed"

    def test_changing_qualification_name_creates_new_master(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=221)
        r = app_env["client"].post(
            "/qualifications/edit/221",
            data={
                "worker_id": "1",
                "qualification_name": "新しい資格名_テスト",   # 新規
                "category": "その他",
                "certificate_no": "x",
                "issuer": "x",
                "issued_on": "2024-04-01",
                "expires_on": "",
                "renewal_required": "1",
                "notes": "",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        # q_qualifications に新規行が作られる
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row
        q = conn.execute(
            "SELECT * FROM q_qualifications WHERE name = '新しい資格名_テスト'"
        ).fetchone()
        assert q is not None
        # cert がその qual_id を指している
        cert = _get_cert(app_env["db_path"], 221)
        assert cert["qual_id"] == q["qual_id"]
        conn.close()

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].post(
            "/qualifications/edit/99998",
            data={"worker_id": "1", "qualification_name": "x", "issued_on": "2024-01-01"},
        )
        assert r.status_code == 404

    @pytest.mark.parametrize("missing_field", [
        "worker_id",
        "qualification_name",
        "issued_on",
    ])
    def test_required_field_missing(self, app_env, missing_field):
        _seed_one_certificate(app_env["db_path"], cert_id=222)
        data = {
            "worker_id": "1",
            "qualification_name": "x",
            "issued_on": "2024-01-01",
        }
        # 該当フィールドだけ空にする
        data[missing_field] = "" if missing_field != "worker_id" else "0"
        r = app_env["client"].post("/qualifications/edit/222", data=data)
        assert r.status_code == 400


# ────────────────────────────────────────────
# POST /delete/{cert_id}
# ────────────────────────────────────────────

class TestDelete:
    def test_archives_certificate(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=230)
        r = app_env["client"].post(
            "/qualifications/delete/230", follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert(app_env["db_path"], 230)
        assert cert is not None  # 物理削除されていない
        assert cert["status"] == "archived"

    def test_archived_disappears_from_index(self, app_env):
        cert_id = 231
        _seed_one_certificate(app_env["db_path"], cert_id=cert_id)
        cert_no = _cert_no_for(cert_id)
        # アーカイブ前は出る
        r1 = app_env["client"].get("/qualifications/")
        assert cert_no in r1.text
        # アーカイブ
        post_resp = app_env["client"].post(
            f"/qualifications/delete/{cert_id}", follow_redirects=False,
        )
        assert post_resp.status_code == 303
        # アーカイブ後は出ない (この cert_no はユニークなので他テスト由来でも混ざらない)
        r2 = app_env["client"].get("/qualifications/")
        assert cert_no not in r2.text

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].post("/qualifications/delete/99997")
        assert r.status_code == 404

    def test_idempotent_on_already_archived(self, app_env):
        """既に archived のものを再度アーカイブしても 303 で正常終了 (冪等)。"""
        _seed_one_certificate(app_env["db_path"], cert_id=232)
        app_env["client"].post(
            "/qualifications/delete/232", follow_redirects=False,
        )
        # 二度目
        r = app_env["client"].post(
            "/qualifications/delete/232", follow_redirects=False,
        )
        assert r.status_code == 303
        cert = _get_cert(app_env["db_path"], 232)
        assert cert["status"] == "archived"
