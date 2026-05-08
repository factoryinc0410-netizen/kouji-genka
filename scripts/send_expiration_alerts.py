#!/usr/bin/env python3
"""scripts/send_expiration_alerts.py — 期限アラートメールの定期送信。

毎日 cron から呼び出すことを想定したワンショット CLI。
``web_app.services.email_service`` の ``run_alert_job`` を呼び出すだけ
の薄いラッパで、SMTP 設定は ``.env`` 経由で読み込む。

Usage:
    # 通常実行 (cron 推奨)
    .venv/bin/python scripts/send_expiration_alerts.py

    # prod-app の DB に対して走らせる
    .venv/bin/python scripts/send_expiration_alerts.py \\
        --db /home/ubuntu/prod-app/web_app/data/app.db

    # ドライラン: 抽出だけして送信しない (件数を stdout に出すだけ)
    .venv/bin/python scripts/send_expiration_alerts.py --dry-run

cron 設定例:
    0 9 * * * cd /home/ubuntu/dev-app && \\
        .venv/bin/python scripts/send_expiration_alerts.py \\
        >> /var/log/factoryskills/alerts.log 2>&1

詳細なセットアップは docs/email_alerts.md を参照。

終了コード:
    0  正常 (送信 or skip いずれも 0)
    1  異常 (DB アクセス失敗 / SMTP 通信失敗 など)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web_app.core import config as cfg  # noqa: E402
from web_app.services.email_service import (  # noqa: E402
    collect_expiring_certs,
    run_alert_job,
    smtp_config_from_env,
)

logger = logging.getLogger("send_expiration_alerts")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="資格者証の期限アラートメールを送信する (cron 用)",
    )
    p.add_argument(
        "--db", default=str(cfg.DATABASE_PATH),
        help=f"対象 SQLite DB のパス (デフォルト: {cfg.DATABASE_PATH})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="抽出だけ行い、メールは送信しない (件数のみ stdout に出力)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("DB ファイルが見つかりません: %s", db_path)
        return 1

    if args.dry_run:
        alerts = collect_expiring_certs(db_path)
        e = len(alerts["expired"])
        u = len(alerts["urgent"])
        print(f"[dry-run] 期限切れ: {e} 件 / 30日以内: {u} 件")
        return 0

    smtp_config = smtp_config_from_env()
    try:
        result = run_alert_job(db_path, smtp_config)
    except Exception:
        logger.exception("期限アラートメール送信中に例外発生")
        return 1

    if result["sent"]:
        print(
            f"sent: 期限切れ {result['expired_count']} 件 + "
            f"30日以内 {result['urgent_count']} 件"
        )
    else:
        print(f"skipped: {result['skipped_reason']} ({result['alerts_count']} 件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
