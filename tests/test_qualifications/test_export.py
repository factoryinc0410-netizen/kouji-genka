"""GET /qualifications/export (CSV ダウンロード) のテスト。

レスポンスヘッダ、BOM、フィルタ通過、件数を確認する。
"""
from __future__ import annotations

import io
import sqlite3
from csv import reader as csv_reader
from datetime import date, timedelta
from pathlib import Path


def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_certificates(db_path: Path) -> None:
    """test_search.py と同じ 6 件のテストデータを投入する。"""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        DELETE FROM q_certificates;
        DELETE FROM q_qualifications;
        INSERT INTO q_qualifications (qual_id, name, category, renewal_required) VALUES
            (10, '玉掛け技能講習',         '技能講習', 0),
            (11, 'フォークリフト運転技能講習', '技能講習', 0),
            (12, '酸欠特別教育',            '特別教育', 0),
            (13, '危険物取扱者乙種第4類',   '免許',     1);
        """
    )
    rows = [
        (101, 1, 10, "第A001号", "2024-04-01", _today_offset(2000),  1),  # safe
        (102, 1, 11, "第A002号", "2022-10-01", _today_offset(120),   1),  # warning (far)
        (103, 2, 10, "第A003号", "2020-04-01", _today_offset(-30),   1),  # expired
        (104, 2, 12, "第A004号", "2024-01-01", None,                 0),  # no_renewal
        (105, 3, 13, "第A005号", "2024-06-01", _today_offset(15),    1),  # warning (urgent)
        (106, 3, 12, "第A006号", "2024-02-01", _today_offset(2000),  1),  # safe
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


def _read_csv(content: bytes) -> tuple[list[str], list[list[str]]]:
    """BOM 付き UTF-8 の CSV をパースして (header, rows) を返す。"""
    # BOM を除去
    text = content.decode("utf-8-sig")
    parsed = list(csv_reader(io.StringIO(text)))
    return parsed[0], parsed[1:]


# ────────────────────────────────────────────
# レスポンスヘッダ・基本構造
# ────────────────────────────────────────────

class TestExportResponse:
    def test_returns_csv_content_type(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "charset=utf-8" in r.headers["content-type"]

    def test_returns_attachment_disposition(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        cd = r.headers["content-disposition"]
        assert "attachment" in cd
        assert "qualifications_" in cd
        assert ".csv" in cd

    def test_starts_with_utf8_bom(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        # BOM = EF BB BF
        assert r.content.startswith(b"\xef\xbb\xbf")

    def test_has_expected_header_row(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        header, _ = _read_csv(r.content)
        assert header == [
            "作業員", "所属", "資格名", "カテゴリ",
            "交付番号", "交付機関", "交付日", "有効期限",
            "残日数", "状態",
        ]


# ────────────────────────────────────────────
# 中身の正しさ
# ────────────────────────────────────────────

class TestExportContent:
    def test_unfiltered_includes_all_certificates(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        _, rows = _read_csv(r.content)
        assert len(rows) == 6
        cert_nos = {row[4] for row in rows}
        assert cert_nos == {
            "第A001号", "第A002号", "第A003号", "第A004号", "第A005号", "第A006号",
        }

    def test_row_has_japanese_status_label(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        _, rows = _read_csv(r.content)
        statuses = {row[4]: row[9] for row in rows}
        assert statuses["第A001号"] == "有効"
        assert statuses["第A003号"] == "期限切れ"
        assert statuses["第A004号"] == "更新不要"
        # 第A005号 は urgent (残30日以内)
        assert "30日以内" in statuses["第A005号"]

    def test_no_renewal_row_has_blank_days(self, app_env):
        """更新不要 (renewal_required=0) の行は残日数が空になる。"""
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        _, rows = _read_csv(r.content)
        # 第A004号 (renewal=0, expires=NULL) は残日数が空
        for row in rows:
            if row[4] == "第A004号":
                assert row[8] == ""
                assert row[7] == ""  # 有効期限も空
                break
        else:
            assert False, "第A004号 が見つからない"

    def test_renewal_row_has_numeric_days(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export")
        _, rows = _read_csv(r.content)
        # 第A002号 (renewal=1, expires=today+120) は残日数 ≈ 120
        for row in rows:
            if row[4] == "第A002号":
                days = int(row[8])
                assert 119 <= days <= 121  # 実行タイミングで ±1 のブレ許容
                break
        else:
            assert False, "第A002号 が見つからない"


# ────────────────────────────────────────────
# フィルタ通過
# ────────────────────────────────────────────

class TestExportFilters:
    def test_keyword_filter(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export?q=山田")
        _, rows = _read_csv(r.content)
        assert len(rows) == 2
        assert all(row[0] == "山田太郎" for row in rows)

    def test_status_filter_warning(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export?status=warning")
        _, rows = _read_csv(r.content)
        assert len(rows) == 2
        cert_nos = {row[4] for row in rows}
        assert cert_nos == {"第A002号", "第A005号"}

    def test_status_filter_expired(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export?status=expired")
        _, rows = _read_csv(r.content)
        assert len(rows) == 1
        assert rows[0][4] == "第A003号"

    def test_category_filter(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export?category=技能講習")
        _, rows = _read_csv(r.content)
        # 技能講習 = 玉掛け 2件 + フォーク 1件
        assert len(rows) == 3

    def test_combined_filters(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get(
            "/qualifications/export?q=山田&status=warning"
        )
        _, rows = _read_csv(r.content)
        assert len(rows) == 1
        assert rows[0][4] == "第A002号"

    def test_zero_match_returns_only_header(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/export?q=nomatch_xxx")
        assert r.status_code == 200
        header, rows = _read_csv(r.content)
        # ヘッダーは常に存在
        assert header[0] == "作業員"
        assert len(rows) == 0


# ────────────────────────────────────────────
# 認可
# ────────────────────────────────────────────

class TestExportAccess:
    def test_excludes_archived(self, app_env):
        """archived の資格は CSV に含まれない。"""
        _seed_certificates(app_env["db_path"])
        # 1 件 archive
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "UPDATE q_certificates SET status='archived' WHERE cert_id = 101"
        )
        conn.commit()
        conn.close()
        r = app_env["client"].get("/qualifications/export")
        _, rows = _read_csv(r.content)
        cert_nos = {row[4] for row in rows}
        assert "第A001号" not in cert_nos
        assert len(rows) == 5
