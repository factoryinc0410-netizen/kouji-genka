# 期限アラートメール通知 — 運用ガイド

`scripts/send_expiration_alerts.py` を cron から定期実行することで、資格者証の
期限切れ・期限近接 (30 日以内) を毎日自動で関係者にメール通知します。

## 1. 通知対象

`q_certificates` テーブルから以下の条件すべてに合致する行が対象になります:

- `status = 'confirmed'` (archived / draft は除外)
- `renewal_required = 1` (更新不要なものはアラート対象外)
- `expires_on IS NOT NULL`
- `expires_on <= 今日 + 30 日`

抽出結果は **「期限切れ (残日数 ≤ 0)」** と **「30 日以内に期限到来 (残 1〜30 日)」**
の 2 セクションに分けてメール本文に展開されます。各セクション内は作業員ごとに
グループ化されます。

## 2. SMTP 設定

`.env` に以下を追記してください:

```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_account@gmail.com
SMTP_PASSWORD=__app_password__   # 必ずアプリパスワードを使用 (2 段階認証必須)
SMTP_USE_TLS=true

ALERT_EMAIL_FROM=your_account@gmail.com
ALERT_EMAIL_TO=admin@example.com,safety@example.com
```

サーバ内のローカル MTA (例: postfix) を使う場合:

```bash
SMTP_SERVER=127.0.0.1
SMTP_PORT=25
SMTP_USE_TLS=false
ALERT_EMAIL_FROM=noreply@your.domain
ALERT_EMAIL_TO=admin@example.com
```

`SMTP_SERVER` / `ALERT_EMAIL_FROM` / `ALERT_EMAIL_TO` のいずれかが空のままだと
スクリプトは抽出だけ行い `skipped: smtp_not_configured` を返して終了します
(cron の異常停止にはなりません)。

## 3. 動作確認

```bash
# ドライラン (送信せず件数のみ表示)
.venv/bin/python scripts/send_expiration_alerts.py --dry-run

# 実送信
.venv/bin/python scripts/send_expiration_alerts.py
```

prod-app の DB を対象にする場合は `--db` で明示:

```bash
.venv/bin/python scripts/send_expiration_alerts.py \
    --db /home/ubuntu/prod-app/web_app/data/app.db
```

## 4. cron 登録例

毎日午前 9 時に dev-app の DB を対象として実行:

```cron
0 9 * * * cd /home/ubuntu/dev-app && /home/ubuntu/dev-app/.venv/bin/python scripts/send_expiration_alerts.py >> /var/log/factoryskills/alerts.log 2>&1
```

prod-app 側を運用する場合は `--db` を付けて別行で登録:

```cron
0 9 * * * cd /home/ubuntu/dev-app && /home/ubuntu/dev-app/.venv/bin/python scripts/send_expiration_alerts.py --db /home/ubuntu/prod-app/web_app/data/app.db >> /var/log/factoryskills/alerts-prod.log 2>&1
```

ログディレクトリは事前に作成 + 権限調整してください:

```bash
sudo mkdir -p /var/log/factoryskills
sudo chown ubuntu:ubuntu /var/log/factoryskills
```

## 5. トラブルシュート

| 症状 | 原因 / 対処 |
|------|-------------|
| `skipped: no_alerts (0 件)` | 通知対象がなく正常終了。 |
| `skipped: smtp_not_configured (N 件)` | `SMTP_SERVER` / `ALERT_EMAIL_FROM` / `ALERT_EMAIL_TO` のいずれかが空。`.env` を確認。 |
| `smtplib.SMTPAuthenticationError` | Gmail でアプリパスワード未使用、または `SMTP_USER`/`SMTP_PASSWORD` が誤り。 |
| `[Errno 111] Connection refused` | ローカル MTA 未稼働。`systemctl status postfix` 等で確認。 |
| 件数は出るのにメールが届かない | 受信側のスパム判定。 `From` の SPF/DKIM、または `ALERT_EMAIL_FROM` を実在ドメインに合わせる。 |

## 6. 関連実装

- 抽出 + 本文 + 送信のロジック: `web_app/services/email_service.py`
- CLI エントリ: `scripts/send_expiration_alerts.py`
- テスト: `tests/test_qualifications/test_email_alerts.py` (`smtplib.SMTP` を mock)
- 設定読込: `web_app/core/config.py` の SMTP_* / ALERT_EMAIL_* 定数
