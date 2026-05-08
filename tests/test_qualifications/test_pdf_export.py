"""GET /qualifications/export/pdf のテスト (印刷向け一覧)。

2 系統:
  1. ``?preview=1`` (HTML プレビュー) — 軽量、常時実行する
  2. PDF 生成本体 — Playwright Chromium が必要。requires_chromium マーカーで
     CI 等の chromium 不在環境では自動 skip される。

HTML プレビューでテンプレ描画 / フィルタ適用 / 認可 / 空状態 をすべてカバーするので、
PDF 本体テストは「最小限の通過判定 (PDF マジックバイト)」だけで十分。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_certificates(db_path: Path) -> None:
    """既存テストと同じ 6 件のテストデータを投入 (test_export.py と同じ構造)。"""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        DELETE FROM q_certificates;
        DELETE FROM q_qualifications;
        INSERT INTO q_qualifications (qual_id, name, category, renewal_required) VALUES
            (10, 'PDF玉掛け技能講習',           '技能講習', 0),
            (11, 'PDFフォークリフト運転技能講習', '技能講習', 0),
            (12, 'PDF酸欠特別教育',              '特別教育', 0),
            (13, 'PDF危険物取扱者乙種第4類',     '免許',     1);
        """
    )
    rows = [
        (101, 1, 10, "PDF-A001", "2024-04-01", _today_offset(2000), 1),  # safe
        (102, 1, 11, "PDF-A002", "2022-10-01", _today_offset(120),  1),  # warning
        (103, 2, 10, "PDF-A003", "2020-04-01", _today_offset(-30),  1),  # expired
        (104, 2, 12, "PDF-A004", "2024-01-01", None,                0),  # no_renewal
        (105, 3, 13, "PDF-A005", "2024-06-01", _today_offset(15),   1),  # urgent
        (106, 3, 12, "PDF-A006", "2024-02-01", _today_offset(2000), 1),  # safe
    ]
    for cert_id, w_id, q_id, cno, issued, expires, renewal in rows:
        conn.execute(
            """
            INSERT INTO q_certificates
                (cert_id, worker_id, qual_id, certificate_no, issuer,
                 issued_on, expires_on, renewal_required, status,
                 original_files_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, '○○協会',
                    ?, ?, ?, 'confirmed', '[]', 'admin-id',
                    datetime('now','localtime'), datetime('now','localtime'))
            """,
            (cert_id, w_id, q_id, cno, issued, expires, renewal),
        )
    conn.commit()
    conn.close()


# ────────────────────────────────────────────
# HTML プレビューモード (?preview=1)
# ────────────────────────────────────────────

class TestPdfPreviewBasic:
    def test_returns_html_content_type(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf?preview=1")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")

    def test_includes_certificates(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf?preview=1")
        # 全 6 件分の certificate_no が含まれる
        for n in ("PDF-A001", "PDF-A002", "PDF-A003",
                  "PDF-A004", "PDF-A005", "PDF-A006"):
            assert n in r.text

    def test_no_navigation_in_print_template(self, app_env):
        """印刷専用テンプレなので base.html のナビゲーションは含まれない。"""
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf?preview=1")
        # base.html にあるサイドナビ要素が無いこと (大まかな指標)
        assert "navbar" not in r.text
        # _nav.html のタブも無い
        assert 'href="/qualifications/upload"' not in r.text

    def test_includes_print_styles(self, app_env):
        """@page 設定がテンプレ内に含まれている (PDF 化時にレイアウト制御される)。"""
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf?preview=1")
        assert "@page" in r.text
        assert "A4" in r.text
        assert "landscape" in r.text


# ────────────────────────────────────────────
# フィルタ適用
# ────────────────────────────────────────────

class TestPdfPreviewFilters:
    def test_filter_label_shown_when_no_filter(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf?preview=1")
        # filter_label のデフォルトは「全件」
        assert "全件" in r.text
        # 全 6 件
        assert "6 件" in r.text

    def test_keyword_filter_applied(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&q=山田",
        )
        # filter_label にキーワードが現れる
        assert "山田" in r.text
        # 山田太郎 (worker 1) の cert のみ表示
        assert "PDF-A001" in r.text
        assert "PDF-A002" in r.text
        # 他作業員の cert は出ない
        assert "PDF-A003" not in r.text

    def test_status_filter_expired(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&status=expired",
        )
        # 期限切れの 1 件のみ
        assert "PDF-A003" in r.text
        assert "PDF-A001" not in r.text
        # ヘッダの filter_label に状態が出る
        assert "期限切れ" in r.text

    def test_category_filter(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&category=免許",
        )
        # 免許カテゴリは PDF-A005 のみ
        assert "PDF-A005" in r.text
        assert "PDF-A001" not in r.text

    def test_include_archived_in_filter_label(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&include_archived=1",
        )
        # ヘッダに「アーカイブ含む」の表記が出る
        assert "アーカイブ含む" in r.text


# ────────────────────────────────────────────
# 空状態 / アーカイブ
# ────────────────────────────────────────────

class TestPdfPreviewSpecial:
    def test_empty_state_when_no_match(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&q=該当なしxyz",
        )
        assert r.status_code == 200
        assert "該当する資格者証がありません" in r.text

    def test_archived_visible_with_include_archived(self, app_env):
        _seed_certificates(app_env["db_path"])
        # 1 件 archive
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "UPDATE q_certificates SET status='archived' WHERE certificate_no='PDF-A001'"
        )
        conn.commit()
        conn.close()
        # default: archived 非表示
        r1 = app_env["client"].get("/qualifications/export/pdf?preview=1")
        assert "PDF-A001" not in r1.text
        # include_archived=1: archived も表示
        r2 = app_env["client"].get(
            "/qualifications/export/pdf?preview=1&include_archived=1",
        )
        assert "PDF-A001" in r2.text
        # 「アーカイブ」ラベルが付く
        assert "アーカイブ" in r2.text


# ────────────────────────────────────────────
# PDF 本体生成 (Playwright Chromium 必須)
# ────────────────────────────────────────────

@pytest.mark.requires_chromium
class TestPdfBinaryOutput:
    def test_returns_pdf_bytes(self, app_env):
        """PDF マジックバイト ('%PDF') で始まるレスポンスを返す。"""
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export/pdf")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        # Content-Disposition: attachment; filename="qualifications_*.pdf"
        cd = r.headers["content-disposition"]
        assert "attachment" in cd
        assert "qualifications_" in cd
        assert ".pdf" in cd
        # PDF マジックバイト
        assert r.content[:4] == b"%PDF"

    def test_pdf_with_filter_succeeds(self, app_env):
        """フィルタ適用しても PDF 生成が完走する。"""
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export/pdf?status=expired",
        )
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"
