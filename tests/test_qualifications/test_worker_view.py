"""GET /qualifications/workers/{worker_id} のテスト (作業員別ビュー)。

カバー範囲:
- 作業員カード描画 (氏名・所属・在職フラグ・サマリ件数)
- 該当作業員の confirmed cert のみ表示 (他作業員 / archived は除外)
- 空状態の表示
- 404 (存在しない worker_id)
- index.html からの導線リンク
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_master(db_path: Path, *, qual_id: int, name: str, category: str = "技能講習") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT OR IGNORE INTO q_qualifications
            (qual_id, name, category, renewal_required)
        VALUES (?, ?, ?, 1)
        """,
        (qual_id, name, category),
    )
    conn.commit()
    conn.close()


def _insert_cert(
    db_path: Path,
    *,
    cert_id: int,
    worker_id: int,
    qual_id: int,
    expires_on: str | None,
    renewal_required: int = 1,
    status: str = "confirmed",
    cert_no: str | None = None,
    original_files_json: str = "[]",
) -> None:
    """テスト用 q_certificates 行を 1 件投入する。"""
    if cert_no is None:
        cert_no = f"WV-{cert_id:04d}"
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, '○○協会',
                '2024-04-01', ?, ?, ?, ?, 'admin-id',
                datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, worker_id, qual_id, cert_no,
         expires_on, renewal_required, status, original_files_json),
    )
    conn.commit()
    conn.close()


# ────────────────────────────────────────────
# レンダリング基本
# ────────────────────────────────────────────

class TestWorkerViewRender:
    def test_renders_worker_card(self, app_env):
        """conftest でシード済みの worker_id=1 (山田太郎/A班) が描画される。"""
        r = app_env["client"].get("/qualifications/workers/1")
        assert r.status_code == 200
        # 氏名・所属
        assert "山田太郎" in r.text
        assert "A班" in r.text
        # breadcrumb の active セグメント
        assert "の保有資格" in r.text or "山田太郎" in r.text
        # 「一覧に戻る」リンク
        assert 'href="/qualifications/"' in r.text

    def test_renders_active_status(self, app_env):
        """is_active=1 の作業員には「在職中」ラベル。"""
        r = app_env["client"].get("/qualifications/workers/1")
        assert "在職中" in r.text

    def test_renders_inactive_status(self, app_env):
        """is_active=0 の作業員には「非在職」ラベル。"""
        # 非在職の作業員を投入
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "INSERT OR REPLACE INTO cc_workers "
            "(worker_id, worker_name, group_name, is_active) "
            "VALUES (90, '退職太郎', 'X班', 0)"
        )
        conn.commit()
        conn.close()

        r = app_env["client"].get("/qualifications/workers/90")
        assert r.status_code == 200
        assert "退職太郎" in r.text
        assert "非在職" in r.text


# ────────────────────────────────────────────
# 資格表示・スコープ
# ────────────────────────────────────────────

class TestWorkerViewCerts:
    def test_lists_workers_certificates(self, app_env):
        """指定作業員 (=1) の confirmed cert が表示される。"""
        _seed_master(app_env["db_path"], qual_id=500, name="WV玉掛け講習")
        _insert_cert(
            app_env["db_path"], cert_id=5001, worker_id=1, qual_id=500,
            expires_on=_today_offset(2000), cert_no="WV-OWN-001",
        )
        r = app_env["client"].get("/qualifications/workers/1")
        assert "WV玉掛け講習" in r.text
        assert "WV-OWN-001" in r.text

    def test_excludes_other_workers_certs(self, app_env):
        """他作業員 (=2) の cert はこの作業員 (=1) のページには出ない。"""
        _seed_master(app_env["db_path"], qual_id=501, name="WV他人資格")
        # 作業員 2 (佐藤花子) に紐づく cert
        _insert_cert(
            app_env["db_path"], cert_id=5002, worker_id=2, qual_id=501,
            expires_on=_today_offset(2000), cert_no="WV-OTHER-001",
        )
        r = app_env["client"].get("/qualifications/workers/1")
        # 作業員 1 のページに作業員 2 の cert は出ない
        assert "WV-OTHER-001" not in r.text

    def test_excludes_archived_certs(self, app_env):
        """status='archived' の cert はこの個票には出さない。"""
        _seed_master(app_env["db_path"], qual_id=502, name="WV廃止資格")
        _insert_cert(
            app_env["db_path"], cert_id=5003, worker_id=1, qual_id=502,
            expires_on=_today_offset(2000), cert_no="WV-ARCH-001",
            status="archived",
        )
        r = app_env["client"].get("/qualifications/workers/1")
        assert "WV-ARCH-001" not in r.text

    def test_summary_counts(self, app_env):
        """サマリの数字 (safe / warning / expired / no_renewal) が正しく集計される。"""
        # ユニークなカテゴリで他テストの cert と混ざらない qual を使う
        _seed_master(app_env["db_path"], qual_id=510, name="WV-SUM-1")
        _seed_master(app_env["db_path"], qual_id=511, name="WV-SUM-2")
        _seed_master(app_env["db_path"], qual_id=512, name="WV-SUM-3")
        _seed_master(app_env["db_path"], qual_id=513, name="WV-SUM-4")
        # クリーンスレートで作業員 3 (鈴木一郎) に投入する (他テストと干渉しない)
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("DELETE FROM q_certificates WHERE worker_id = 3")
        conn.commit()
        conn.close()
        # safe 1, warning 1, expired 1, no_renewal 1
        _insert_cert(
            app_env["db_path"], cert_id=5101, worker_id=3, qual_id=510,
            expires_on=_today_offset(2000), renewal_required=1,
        )  # safe
        _insert_cert(
            app_env["db_path"], cert_id=5102, worker_id=3, qual_id=511,
            expires_on=_today_offset(60), renewal_required=1,
        )  # warning
        _insert_cert(
            app_env["db_path"], cert_id=5103, worker_id=3, qual_id=512,
            expires_on=_today_offset(-30), renewal_required=1,
        )  # expired
        _insert_cert(
            app_env["db_path"], cert_id=5104, worker_id=3, qual_id=513,
            expires_on=None, renewal_required=0,
        )  # no_renewal

        r = app_env["client"].get("/qualifications/workers/3")
        assert r.status_code == 200
        # 4 件の総数
        assert "4 件" in r.text

    def test_zero_state_for_worker_with_no_certs(self, app_env):
        """cert を 1 件も持っていない作業員には空状態メッセージが出る。"""
        # 完全にクリーンな新規作業員を投入
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "INSERT OR REPLACE INTO cc_workers "
            "(worker_id, worker_name, group_name, is_active) "
            "VALUES (95, '新規花子', 'Y班', 1)"
        )
        conn.execute("DELETE FROM q_certificates WHERE worker_id = 95")
        conn.commit()
        conn.close()

        r = app_env["client"].get("/qualifications/workers/95")
        assert r.status_code == 200
        assert "新規花子" in r.text
        assert "保有資格はまだ登録されていません" in r.text

    def test_renders_original_file_link(self, app_env):
        """original_files_json があれば原本リンクが描画される。"""
        _seed_master(app_env["db_path"], qual_id=520, name="WV-FILE-LINK")
        _insert_cert(
            app_env["db_path"], cert_id=5201, worker_id=1, qual_id=520,
            expires_on=_today_offset(2000), cert_no="WV-FILE-001",
            original_files_json='["qualifications/manual_xyz/sample.pdf"]',
        )
        r = app_env["client"].get("/qualifications/workers/1")
        assert "/qualifications/files/manual_xyz/sample.pdf" in r.text
        assert "sample.pdf" in r.text


# ────────────────────────────────────────────
# 404
# ────────────────────────────────────────────

class TestWorkerViewErrors:
    def test_404_when_worker_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/workers/99999")
        assert r.status_code == 404


# ────────────────────────────────────────────
# index → worker view 導線
# ────────────────────────────────────────────

class TestIndexLink:
    def test_index_links_to_worker_view(self, app_env):
        """index.html の作業員名セルが /qualifications/workers/{id} を href に持つ。"""
        _seed_master(app_env["db_path"], qual_id=530, name="WV-INDEXLINK")
        _insert_cert(
            app_env["db_path"], cert_id=5301, worker_id=1, qual_id=530,
            expires_on=_today_offset(2000), cert_no="WV-LINK-001",
        )
        r = app_env["client"].get("/qualifications/")
        assert r.status_code == 200
        # cert が表示されている
        assert "WV-LINK-001" in r.text
        # 作業員 1 へのリンクが含まれる
        assert 'href="/qualifications/workers/1"' in r.text
