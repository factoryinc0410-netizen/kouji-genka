"""ポータル画面 (GET /) の資格者証アラート表示テスト。

警告/期限切れ件数に応じた表示有無を確認する。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _clear_certs(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates")
    conn.commit()
    conn.close()


def _insert_cert(
    db_path: Path,
    *,
    cert_id: int,
    expires_on: str | None,
    renewal_required: int,
    status: str = "confirmed",
) -> None:
    """1 件投入。q_qualifications はすでに 1 件 (id=10) を共有して使う。"""
    conn = sqlite3.connect(str(db_path))
    # 資格マスタ (qual_id=10) は冪等に確保
    conn.execute(
        "INSERT OR IGNORE INTO q_qualifications "
        "(qual_id, name, category, renewal_required) "
        "VALUES (10, '玉掛け技能講習', '技能講習', 0)"
    )
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, 1, 10, ?, '○○協会',
                '2024-04-01', ?, ?, ?, '[]', 'admin-id',
                datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, f"PORTAL-{cert_id}", expires_on, renewal_required, status),
    )
    conn.commit()
    conn.close()


# ────────────────────────────────────────────
# アラート表示有無
# ────────────────────────────────────────────

class TestPortalAlertVisibility:
    def test_no_alert_when_no_certificates(self, app_env):
        _clear_certs(app_env["db_path"])
        r = app_env["client"].get("/")
        assert r.status_code == 200
        assert "資格者証の確認が必要です" not in r.text

    def test_no_alert_when_only_safe(self, app_env):
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2001,
            expires_on=_today_offset(2000), renewal_required=1,
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" not in r.text

    def test_no_alert_when_only_no_renewal(self, app_env):
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2002,
            expires_on=None, renewal_required=0,
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" not in r.text

    def test_alert_shows_when_warning(self, app_env):
        """期限近接 (180日以内) が 1 件あれば表示。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2003,
            expires_on=_today_offset(60), renewal_required=1,
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" in r.text
        assert "180日以内に期限到来 1 件" in r.text
        # expired バッジは出ない
        assert "期限切れ" not in r.text

    def test_alert_shows_when_expired(self, app_env):
        """期限切れが 1 件あれば表示。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2004,
            expires_on=_today_offset(-5), renewal_required=1,
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" in r.text
        assert "期限切れ 1 件" in r.text

    def test_alert_shows_both_kinds(self, app_env):
        """warning + expired 両方ある場合、両バッジが出る。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2005,
            expires_on=_today_offset(60), renewal_required=1,
        )
        _insert_cert(
            app_env["db_path"], cert_id=2006,
            expires_on=_today_offset(-5), renewal_required=1,
        )
        _insert_cert(
            app_env["db_path"], cert_id=2007,
            expires_on=_today_offset(-100), renewal_required=1,
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" in r.text
        assert "180日以内に期限到来 1 件" in r.text
        assert "期限切れ 2 件" in r.text


class TestPortalAlertScope:
    def test_archived_not_counted(self, app_env):
        """archived の cert は集計に含まれない。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2010,
            expires_on=_today_offset(-5), renewal_required=1,
            status="archived",
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" not in r.text

    def test_draft_not_counted(self, app_env):
        """draft の cert も含めない (未確定なので)。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2011,
            expires_on=_today_offset(-5), renewal_required=1,
            status="draft",
        )
        r = app_env["client"].get("/")
        assert "資格者証の確認が必要です" not in r.text


class TestPortalAlertLink:
    def test_link_to_warning_when_only_warning(self, app_env):
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2020,
            expires_on=_today_offset(60), renewal_required=1,
        )
        r = app_env["client"].get("/")
        # warning のみ → リンクは status=warning
        assert "/qualifications/?status=warning" in r.text

    def test_link_to_expired_when_expired_present(self, app_env):
        """expired がある場合は status=expired を優先表示する。"""
        _clear_certs(app_env["db_path"])
        _insert_cert(
            app_env["db_path"], cert_id=2021,
            expires_on=_today_offset(-5), renewal_required=1,
        )
        _insert_cert(
            app_env["db_path"], cert_id=2022,
            expires_on=_today_offset(60), renewal_required=1,
        )
        r = app_env["client"].get("/")
        # expired 優先
        assert "/qualifications/?status=expired" in r.text
