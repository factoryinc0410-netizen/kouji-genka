"""期限アラートメール通知 (web_app.services.email_service) のテスト。

実際のネットワーク通信を一切行わないよう ``smtplib.SMTP`` を ``unittest.mock``
で差し替える。テストは大きく 3 系統:

  1. collect_expiring_certs   — DB クエリの抽出ロジック
  2. build_alert_email        — 件名 / 本文の組立
  3. send_alert_email + run_alert_job — SMTP モックを通した送信フロー
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _seed_master(db_path: Path, *, qual_id: int, name: str | None = None) -> None:
    """テスト用に q_qualifications を 1 件投入する。

    name を未指定なら ``EM-MASTER-<qual_id>`` を使い、テスト間で UNIQUE 制約に
    引っかからないようにする (これを怠ると INSERT OR IGNORE が silent no-op に
    なり、後続の cert が dangling FK となって JOIN から消える)。
    """
    if name is None:
        name = f"EM-MASTER-{qual_id}"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO q_qualifications "
        "(qual_id, name, category, renewal_required) "
        "VALUES (?, ?, '技能講習', 1)",
        (qual_id, name),
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
) -> None:
    if cert_no is None:
        cert_no = f"EM-{cert_id:04d}"
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, '○○協会',
                '2024-04-01', ?, ?, ?, '[]', 'admin-id',
                datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, worker_id, qual_id, cert_no,
         expires_on, renewal_required, status),
    )
    conn.commit()
    conn.close()


def _clear_certs(db_path: Path) -> None:
    """テストごとに cert を真っ白にしてから始める (他テストとの干渉防止)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates")
    conn.commit()
    conn.close()


FIXED_TODAY = date(2026, 5, 8)


def _today_offset(days: int) -> str:
    return (FIXED_TODAY + timedelta(days=days)).isoformat()


# ────────────────────────────────────────────
# 1. collect_expiring_certs — 抽出ロジック
# ────────────────────────────────────────────

class TestCollectExpiringCerts:
    def test_picks_up_expired_and_urgent(self, app_env):
        """期限切れ + 30日以内に期限到来する cert を 2 セクションで返す。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=700)
        _insert_cert(
            app_env["db_path"], cert_id=7001, worker_id=1, qual_id=700,
            expires_on=_today_offset(-5),  # 5 日経過 → expired
        )
        _insert_cert(
            app_env["db_path"], cert_id=7002, worker_id=2, qual_id=700,
            expires_on=_today_offset(15),  # 残 15 日 → urgent
        )

        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert len(result["expired"]) == 1
        assert len(result["urgent"]) == 1
        assert result["expired"][0]["cert_id"] == 7001
        assert result["urgent"][0]["cert_id"] == 7002
        # days_remaining が付与されている
        assert result["expired"][0]["days_remaining"] == -5
        assert result["urgent"][0]["days_remaining"] == 15

    def test_excludes_far_future(self, app_env):
        """30 日より先に期限が来る cert は対象外。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=701)
        _insert_cert(
            app_env["db_path"], cert_id=7010, worker_id=1, qual_id=701,
            expires_on=_today_offset(60),  # 残 60 日 → 対象外
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []
        assert result["urgent"] == []

    def test_excludes_archived(self, app_env):
        """archived 状態の cert は対象外。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=702)
        _insert_cert(
            app_env["db_path"], cert_id=7020, worker_id=1, qual_id=702,
            expires_on=_today_offset(-5), status="archived",
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []
        assert result["urgent"] == []

    def test_excludes_draft(self, app_env):
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=703)
        _insert_cert(
            app_env["db_path"], cert_id=7030, worker_id=1, qual_id=703,
            expires_on=_today_offset(-5), status="draft",
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []

    def test_excludes_no_renewal(self, app_env):
        """renewal_required=0 (更新不要) の cert は対象外。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=704)
        _insert_cert(
            app_env["db_path"], cert_id=7040, worker_id=1, qual_id=704,
            expires_on=_today_offset(15), renewal_required=0,
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []
        assert result["urgent"] == []

    def test_excludes_null_expires_on(self, app_env):
        """expires_on が NULL の cert は対象外 (期限不明)。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=705)
        _insert_cert(
            app_env["db_path"], cert_id=7050, worker_id=1, qual_id=705,
            expires_on=None,
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []
        assert result["urgent"] == []

    def test_boundary_today_is_expired(self, app_env):
        """期限がちょうど今日の cert は expired セクションに入る (残日数 0)。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=706)
        _insert_cert(
            app_env["db_path"], cert_id=7060, worker_id=1, qual_id=706,
            expires_on=_today_offset(0),
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert len(result["expired"]) == 1
        assert result["urgent"] == []

    def test_boundary_30_days_is_urgent(self, app_env):
        """残 30 日ちょうどは urgent に入る (境界)。"""
        from web_app.services.email_service import collect_expiring_certs
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=707)
        _insert_cert(
            app_env["db_path"], cert_id=7070, worker_id=1, qual_id=707,
            expires_on=_today_offset(30),
        )
        result = collect_expiring_certs(app_env["db_path"], today=FIXED_TODAY)
        assert result["expired"] == []
        assert len(result["urgent"]) == 1


# ────────────────────────────────────────────
# 2. build_alert_email — 本文組立
# ────────────────────────────────────────────

class TestBuildAlertEmail:
    def _sample_alerts(self) -> dict:
        return {
            "expired": [{
                "cert_id": 1, "worker_id": 1,
                "worker_name": "山田太郎", "group_name": "A班",
                "qual_name": "玉掛け技能講習", "qual_category": "技能講習",
                "certificate_no": "第A001号",
                "expires_on": "2026-05-01", "days_remaining": -7,
            }],
            "urgent": [{
                "cert_id": 2, "worker_id": 2,
                "worker_name": "佐藤花子", "group_name": "B班",
                "qual_name": "フォークリフト運転技能講習", "qual_category": "技能講習",
                "certificate_no": "第B002号",
                "expires_on": "2026-05-25", "days_remaining": 17,
            }],
        }

    def test_subject_includes_total_count(self):
        from web_app.services.email_service import build_alert_email
        subject, _, _ = build_alert_email(
            self._sample_alerts(), today=FIXED_TODAY,
        )
        assert "2 件" in subject
        assert "2026-05-08" in subject
        assert "FactorySkills" in subject

    def test_text_body_groups_by_worker(self):
        from web_app.services.email_service import build_alert_email
        _, text, _ = build_alert_email(
            self._sample_alerts(), today=FIXED_TODAY,
        )
        # 両セクションのヘッダ
        assert "■ 期限切れ" in text
        assert "■ 30日以内に期限到来" in text
        # 作業員名 / 所属 / 資格名 / 交付番号 / 期限
        assert "山田太郎" in text
        assert "A班" in text
        assert "玉掛け技能講習" in text
        assert "第A001号" in text
        assert "2026-05-01" in text
        assert "7 日経過" in text  # 残日数 -7
        # urgent 側
        assert "佐藤花子" in text
        assert "残 17 日" in text

    def test_html_body_contains_styling(self):
        """HTML 本文には色 (期限切れ赤系・近接黄系) が含まれる。"""
        from web_app.services.email_service import build_alert_email
        _, _, html = build_alert_email(
            self._sample_alerts(), today=FIXED_TODAY,
        )
        assert "<html" in html.lower()
        assert "山田太郎" in html
        assert "佐藤花子" in html
        # 期限切れ用の赤系カラー (#dc3545)
        assert "#dc3545" in html
        # 近接用の黄系カラー (#b88600)
        assert "#b88600" in html

    def test_only_expired_section(self):
        """urgent が空なら 30日以内セクションは出さない。"""
        from web_app.services.email_service import build_alert_email
        alerts = self._sample_alerts()
        alerts["urgent"] = []
        _, text, _ = build_alert_email(alerts, today=FIXED_TODAY)
        assert "■ 期限切れ" in text
        assert "■ 30日以内に期限到来" not in text

    def test_handles_empty_optional_fields(self):
        """certificate_no や group_name が None でも本文が壊れない。"""
        from web_app.services.email_service import build_alert_email
        alerts = {
            "expired": [{
                "cert_id": 9, "worker_id": 9,
                "worker_name": "山田", "group_name": None,
                "qual_name": "X", "qual_category": None,
                "certificate_no": None,
                "expires_on": "2026-05-01", "days_remaining": -3,
            }],
            "urgent": [],
        }
        _, text, html = build_alert_email(alerts, today=FIXED_TODAY)
        assert "所属なし" in text
        assert "(交付番号: -)" in text
        assert "山田" in html


# ────────────────────────────────────────────
# 3. send_alert_email — SMTP モックを通した送信
# ────────────────────────────────────────────

class TestSendAlertEmail:
    def _smtp_config(self, **overrides) -> dict:
        base = {
            "server":   "smtp.example.com",
            "port":     587,
            "user":     "alerts@example.com",
            "password": "p@ss",
            "use_tls":  True,
            "from":     "alerts@example.com",
            "to_list":  ["admin@example.com", "safety@example.com"],
        }
        base.update(overrides)
        return base

    def test_smtp_invoked_with_starttls_and_login(self):
        """use_tls=True / user 設定 → starttls() + login() + send_message()。"""
        from web_app.services import email_service

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            email_service.send_alert_email(
                "subj", "text", "<p>html</p>", self._smtp_config(),
            )

        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("alerts@example.com", "p@ss")
        mock_smtp.send_message.assert_called_once()

        # 送信メッセージのヘッダを確認
        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "subj"
        assert sent_msg["From"] == "alerts@example.com"
        assert sent_msg["To"] == "admin@example.com, safety@example.com"

    def test_no_starttls_when_disabled(self):
        """use_tls=False のとき starttls は呼ばれない。"""
        from web_app.services import email_service

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            email_service.send_alert_email(
                "s", "t", "<p>h</p>",
                self._smtp_config(use_tls=False),
            )
        mock_smtp.starttls.assert_not_called()

    def test_no_login_when_user_empty(self):
        """SMTP_USER が空のローカル MTA 構成では login を呼ばない。"""
        from web_app.services import email_service

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            email_service.send_alert_email(
                "s", "t", "<p>h</p>",
                self._smtp_config(user="", password="", use_tls=False),
            )
        mock_smtp.login.assert_not_called()
        mock_smtp.send_message.assert_called_once()

    def test_raises_when_server_missing(self):
        """SMTP_SERVER 未設定で呼ぶと ValueError。"""
        from web_app.services import email_service
        import pytest as _pt
        with _pt.raises(ValueError, match="SMTP server"):
            email_service.send_alert_email(
                "s", "t", "<p>h</p>",
                self._smtp_config(server=""),
            )

    def test_raises_when_to_list_empty(self):
        from web_app.services import email_service
        import pytest as _pt
        with _pt.raises(ValueError, match="ALERT_EMAIL_TO"):
            email_service.send_alert_email(
                "s", "t", "<p>h</p>",
                self._smtp_config(to_list=[]),
            )


# ────────────────────────────────────────────
# 4. run_alert_job — 統合 (mock SMTP 経由)
# ────────────────────────────────────────────

class TestRunAlertJob:
    def _full_smtp(self) -> dict:
        return {
            "server":   "smtp.example.com",
            "port":     587,
            "user":     "u",
            "password": "p",
            "use_tls":  True,
            "from":     "from@example.com",
            "to_list":  ["to@example.com"],
        }

    def test_skips_when_no_alerts(self, app_env):
        """対象 0 件のときは SMTP に接続せず skipped を返す。"""
        from web_app.services import email_service
        _clear_certs(app_env["db_path"])

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            result = email_service.run_alert_job(
                app_env["db_path"], self._full_smtp(),
                today=FIXED_TODAY,
            )
        assert result["sent"] is False
        assert result["skipped_reason"] == "no_alerts"
        assert result["alerts_count"] == 0
        mock_smtp_cls.assert_not_called()

    def test_skips_when_smtp_not_configured(self, app_env):
        """対象がある状態で SMTP 設定が不完全だと skip され例外も投げない。"""
        from web_app.services import email_service
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=720)
        _insert_cert(
            app_env["db_path"], cert_id=7201, worker_id=1, qual_id=720,
            expires_on=_today_offset(-5),
        )

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            result = email_service.run_alert_job(
                app_env["db_path"],
                {**self._full_smtp(), "server": ""},  # server 欠如
                today=FIXED_TODAY,
            )
        assert result["sent"] is False
        assert result["skipped_reason"] == "smtp_not_configured"
        assert result["alerts_count"] == 1
        mock_smtp_cls.assert_not_called()

    def test_full_flow_sends_email(self, app_env):
        """対象あり + SMTP 設定 OK → SMTP モックの send_message が呼ばれる。"""
        from web_app.services import email_service
        _clear_certs(app_env["db_path"])
        _seed_master(app_env["db_path"], qual_id=721)
        _insert_cert(
            app_env["db_path"], cert_id=7211, worker_id=1, qual_id=721,
            expires_on=_today_offset(-3), cert_no="EM-RUN-EXP",
        )
        _insert_cert(
            app_env["db_path"], cert_id=7212, worker_id=2, qual_id=721,
            expires_on=_today_offset(20), cert_no="EM-RUN-URG",
        )

        with patch.object(email_service.smtplib, "SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            result = email_service.run_alert_job(
                app_env["db_path"], self._full_smtp(),
                today=FIXED_TODAY,
            )

        assert result["sent"] is True
        assert result["skipped_reason"] is None
        assert result["expired_count"] == 1
        assert result["urgent_count"] == 1
        assert result["alerts_count"] == 2

        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_smtp.starttls.assert_called_once()
        mock_smtp.send_message.assert_called_once()

        # 件名 + 本文に対象 cert_no が含まれていることを確認
        sent_msg = mock_smtp.send_message.call_args[0][0]
        # multipart の中身を平文に取り出して検査
        body_text = "".join(
            part.get_payload(decode=True).decode("utf-8")
            for part in sent_msg.walk()
            if part.get_content_type() == "text/plain"
        )
        assert "EM-RUN-EXP" in body_text
        assert "EM-RUN-URG" in body_text


# ────────────────────────────────────────────
# 5. smtp_config_from_env — config.py 連携
# ────────────────────────────────────────────

class TestSmtpConfigFromEnv:
    def test_reads_from_config_module(self):
        """ALERT_EMAIL_TO のカンマ区切りが list[str] に分解される。"""
        from web_app.services import email_service
        from web_app.core import config as cfg

        with patch.object(cfg, "SMTP_SERVER", "smtp.example.com"), \
             patch.object(cfg, "SMTP_PORT", 587), \
             patch.object(cfg, "SMTP_USER", "u"), \
             patch.object(cfg, "SMTP_PASSWORD", "p"), \
             patch.object(cfg, "SMTP_USE_TLS", True), \
             patch.object(cfg, "ALERT_EMAIL_FROM", "from@example.com"), \
             patch.object(cfg, "ALERT_EMAIL_TO", "a@example.com, b@example.com"):
            sc = email_service.smtp_config_from_env()

        assert sc["server"]   == "smtp.example.com"
        assert sc["port"]     == 587
        assert sc["use_tls"]  is True
        assert sc["from"]     == "from@example.com"
        assert sc["to_list"]  == ["a@example.com", "b@example.com"]

    def test_empty_to_returns_empty_list(self):
        from web_app.services import email_service
        from web_app.core import config as cfg
        with patch.object(cfg, "ALERT_EMAIL_TO", ""):
            sc = email_service.smtp_config_from_env()
        assert sc["to_list"] == []
