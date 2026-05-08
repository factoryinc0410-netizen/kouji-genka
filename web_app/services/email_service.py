"""期限アラートメール通知サービス。

責務:
  - q_certificates から期限切れ + 30日以内に期限到来する cert を抽出
  - 作業員ごとにグループ化したテキスト/HTML 本文を組み立て
  - smtplib で設定された宛先にメール送信

設計判断:
  - **同期 sqlite3** で実装。既存の async DB API ではなく cron バッチに合わせた
    シンプルな構成 (scripts/create_user.py と同じ方針)。テストもイベントループ
    なしで書けるようにする。
  - SMTP 設定 (server / from / to) のいずれかが欠けていれば送信は skip され、
    "smtp_not_configured" として記録される。設定不備で cron が落ちないため。

公開 API:
  - collect_expiring_certs(db_path, today=None) -> dict
  - build_alert_email(alerts, today=None) -> (subject, text, html)
  - send_alert_email(subject, text, html, smtp_config) -> None
  - run_alert_job(db_path, smtp_config, today=None) -> dict (実行結果サマリ)
  - smtp_config_from_env() -> dict (config.py からの読み込みヘルパ)

テスト:
  - tests/test_qualifications/test_email_alerts.py で smtplib.SMTP をモック化。
"""
from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger("web_app.email_service")


# ────────────────────────────────────────────
# データ抽出
# ────────────────────────────────────────────

def collect_expiring_certs(
    db_path: str | Path,
    *,
    today: date | None = None,
    threshold_days: int = 30,
) -> dict[str, list[dict]]:
    """期限切れ + ``threshold_days`` 日以内に期限到来する cert を分類して返す。

    対象条件:
      - ``status = 'confirmed'``        (archived / draft は除外)
      - ``renewal_required = 1``        (更新不要なものはアラート対象外)
      - ``expires_on IS NOT NULL``      (期限不明は除外)
      - ``expires_on <= today + threshold_days``

    戻り値:
        ``{"expired": [...], "urgent": [...]}``
        各要素は cert + worker + qual を JOIN した dict に
        ``days_remaining`` (期限切れの場合は負数) を付与したもの。
    """
    today = today or date.today()
    threshold_iso = (today + timedelta(days=threshold_days)).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT  c.cert_id, c.certificate_no, c.expires_on, c.issued_on,
                w.worker_id, w.worker_name, w.group_name,
                ql.name AS qual_name, ql.category AS qual_category
          FROM  q_certificates c
          JOIN  cc_workers w        ON  w.worker_id = c.worker_id
          JOIN  q_qualifications ql ON  ql.qual_id  = c.qual_id
         WHERE  c.status            = 'confirmed'
           AND  c.renewal_required  = 1
           AND  c.expires_on IS NOT NULL
           AND  c.expires_on        <= ?
         ORDER BY c.expires_on, w.worker_name
        """,
        (threshold_iso,),
    ).fetchall()
    conn.close()

    expired: list[dict] = []
    urgent: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            exp = date.fromisoformat(d["expires_on"])
        except (TypeError, ValueError):
            continue
        days = (exp - today).days
        d["days_remaining"] = days
        if days <= 0:
            expired.append(d)
        elif days <= threshold_days:
            urgent.append(d)
    return {"expired": expired, "urgent": urgent}


# ────────────────────────────────────────────
# メール本文組立
# ────────────────────────────────────────────

def _group_by_worker(items: list[dict]) -> dict[tuple, list[dict]]:
    """(worker_id, worker_name, group_name) でグループ化。順序を保持する。"""
    grouped: dict[tuple, list[dict]] = {}
    for a in items:
        key = (a["worker_id"], a["worker_name"], a["group_name"])
        grouped.setdefault(key, []).append(a)
    return grouped


def _format_text_section(
    title: str, items: list[dict], *, expired_section: bool,
) -> list[str]:
    """テキスト本文の 1 セクションを組み立てて行のリストを返す。"""
    lines = [f"■ {title} ({len(items)} 件)", "-" * 50]
    for (_, name, group), bucket_items in _group_by_worker(items).items():
        lines.append(f"\n[{name} ({group or '所属なし'})]")
        for a in bucket_items:
            no = a["certificate_no"] or "-"
            days = a["days_remaining"]
            if expired_section:
                tail = f"({-days} 日経過)"
            else:
                tail = f"(残 {days} 日)"
            lines.append(
                f"  - {a['qual_name']} (交付番号: {no})  期限: {a['expires_on']} {tail}"
            )
    lines.append("")
    return lines


def _format_html_section(
    title: str, items: list[dict], *, expired_section: bool, color: str,
) -> str:
    """HTML 本文の 1 セクションを返す。"""
    rows_html = []
    for (_, name, group), bucket_items in _group_by_worker(items).items():
        rows_html.append(
            f'<tr><td colspan="4" style="padding:8px 4px;background:#f8f9fa;'
            f'font-weight:bold;">{name}'
            f' <span style="color:#6c757d;font-weight:normal;">'
            f'({group or "所属なし"})</span></td></tr>'
        )
        for a in bucket_items:
            no = a["certificate_no"] or "-"
            days = a["days_remaining"]
            tail = f"{-days} 日経過" if expired_section else f"残 {days} 日"
            rows_html.append(
                f'<tr>'
                f'<td style="padding:4px 8px;">　{a["qual_name"]}</td>'
                f'<td style="padding:4px 8px;color:#6c757d;">{no}</td>'
                f'<td style="padding:4px 8px;">{a["expires_on"]}</td>'
                f'<td style="padding:4px 8px;color:{color};font-weight:bold;">{tail}</td>'
                f'</tr>'
            )
    return (
        f'<h3 style="color:{color};border-bottom:2px solid {color};'
        f'padding-bottom:4px;">{title} ({len(items)} 件)</h3>'
        f'<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        f'{"".join(rows_html)}'
        f'</table>'
    )


def build_alert_email(
    alerts: dict[str, list[dict]],
    *,
    today: date | None = None,
) -> tuple[str, str, str]:
    """``(subject, text_body, html_body)`` を返す。"""
    today = today or date.today()
    expired = alerts.get("expired", [])
    urgent  = alerts.get("urgent", [])
    total   = len(expired) + len(urgent)

    subject = (
        f"【FactorySkills】資格者証の期限アラート "
        f"({today.isoformat()}) — {total} 件"
    )

    # ── テキスト ──
    text_lines = [
        f"資格者証の期限アラート — {today.isoformat()}",
        "=" * 50,
        f"期限切れ: {len(expired)} 件",
        f"30日以内: {len(urgent)} 件",
        "",
    ]
    if expired:
        text_lines.extend(_format_text_section(
            "期限切れ", expired, expired_section=True,
        ))
    if urgent:
        text_lines.extend(_format_text_section(
            "30日以内に期限到来", urgent, expired_section=False,
        ))
    text_lines.append("-" * 50)
    text_lines.append("本メールは自動送信されています (FactorySkills)。")
    text_body = "\n".join(text_lines)

    # ── HTML ──
    html_parts = [
        '<html><body style="font-family:sans-serif;color:#212529;">',
        '<h2 style="margin-bottom:4px;">資格者証の期限アラート</h2>',
        f'<p style="color:#6c757d;margin-top:0;">{today.isoformat()} 自動配信</p>',
        f'<p><strong>期限切れ: {len(expired)} 件</strong>'
        f' / 30日以内: {len(urgent)} 件</p>',
    ]
    if expired:
        html_parts.append(_format_html_section(
            "期限切れ", expired, expired_section=True, color="#dc3545",
        ))
    if urgent:
        html_parts.append(_format_html_section(
            "30日以内に期限到来", urgent, expired_section=False, color="#b88600",
        ))
    html_parts.append(
        '<hr style="margin-top:24px;"/>'
        '<p style="color:#6c757d;font-size:12px;">'
        '本メールは自動送信されています (FactorySkills)。</p>'
        '</body></html>'
    )
    html_body = "".join(html_parts)

    return subject, text_body, html_body


# ────────────────────────────────────────────
# SMTP 送信
# ────────────────────────────────────────────

def send_alert_email(
    subject: str,
    text_body: str,
    html_body: str,
    smtp_config: dict,
) -> None:
    """smtplib で送信する。

    smtp_config:
      - server, port, user, password, use_tls
      - from (str)
      - to_list (list[str])

    認証情報・宛先・送信元のいずれかが欠けていれば ``ValueError`` を送出する。
    呼び出し側は ``run_alert_job`` 経由で呼ぶことを想定 (skip 判定は run 側)。
    """
    if not smtp_config.get("server"):
        raise ValueError("SMTP server が設定されていません")
    if not smtp_config.get("from"):
        raise ValueError("ALERT_EMAIL_FROM が設定されていません")
    if not smtp_config.get("to_list"):
        raise ValueError("ALERT_EMAIL_TO が設定されていません")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config["from"]
    msg["To"] = ", ".join(smtp_config["to_list"])
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_config["server"], smtp_config["port"]) as smtp:
        if smtp_config.get("use_tls"):
            smtp.starttls()
        if smtp_config.get("user"):
            smtp.login(smtp_config["user"], smtp_config["password"])
        smtp.send_message(msg)


# ────────────────────────────────────────────
# 実行サマリ
# ────────────────────────────────────────────

def run_alert_job(
    db_path: str | Path,
    smtp_config: dict,
    *,
    today: date | None = None,
) -> dict:
    """1 回分の収集 → 送信を実行し結果サマリを返す。

    戻り値:
      - ``alerts_count`` (int): 通知対象 cert 数
      - ``expired_count`` (int)
      - ``urgent_count`` (int)
      - ``sent`` (bool): 実際に SMTP 送信が走ったか
      - ``skipped_reason`` (str | None):
          - "no_alerts"          — 対象 0 件
          - "smtp_not_configured" — SMTP 設定不足
          - None                 — 正常送信
    """
    alerts = collect_expiring_certs(db_path, today=today)
    expired = len(alerts["expired"])
    urgent = len(alerts["urgent"])
    total = expired + urgent

    if total == 0:
        logger.info("期限アラート対象なし — メール送信 skip")
        return {
            "alerts_count": 0, "expired_count": 0, "urgent_count": 0,
            "sent": False, "skipped_reason": "no_alerts",
        }

    if (
        not smtp_config.get("server")
        or not smtp_config.get("from")
        or not smtp_config.get("to_list")
    ):
        logger.warning(
            "期限アラート対象 %d 件あるが SMTP 設定が不完全のため送信を skip",
            total,
        )
        return {
            "alerts_count": total,
            "expired_count": expired, "urgent_count": urgent,
            "sent": False, "skipped_reason": "smtp_not_configured",
        }

    subject, text_body, html_body = build_alert_email(alerts, today=today)
    send_alert_email(subject, text_body, html_body, smtp_config)
    logger.info(
        "期限アラートメール送信完了: 期限切れ %d 件 + 30日以内 %d 件 → %s",
        expired, urgent, smtp_config["to_list"],
    )
    return {
        "alerts_count": total,
        "expired_count": expired, "urgent_count": urgent,
        "sent": True, "skipped_reason": None,
    }


def smtp_config_from_env() -> dict:
    """``web_app.core.config`` から SMTP 設定を取り出して dict 化する。

    cron スクリプト側で読み込み・検証する際の単一エントリ。
    """
    from web_app.core import config as cfg

    raw_to = cfg.ALERT_EMAIL_TO or ""
    to_list = [a.strip() for a in raw_to.split(",") if a.strip()]
    return {
        "server":   cfg.SMTP_SERVER,
        "port":     cfg.SMTP_PORT,
        "user":     cfg.SMTP_USER,
        "password": cfg.SMTP_PASSWORD,
        "use_tls":  cfg.SMTP_USE_TLS,
        "from":     cfg.ALERT_EMAIL_FROM,
        "to_list":  to_list,
    }
