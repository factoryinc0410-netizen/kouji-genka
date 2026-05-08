# Factoryskills — 業務自動化プラットフォーム

建設会社の社内業務を自動化する、3 スキル統合の Web アプリケーション
（FastAPI + Jinja2 + SQLite）。社員向けの業務効率化を起点として、将来は
顧客提供レベルの品質を目指して継続改修中。

## 1. 搭載スキル

| スキル | URL prefix | 概要 |
|--------|-----------|------|
| **注文書自動作成** (order_docs) | `/orders/` | Excel 依頼書から注文書・注文請書 PDF を一括生成。Playwright + 座標スタンプ方式。 |
| **工事日報集計** (construction_cost) | `/construction-cost/` | 工事日報から現場別原価管理表・個人別集計表を作成。予算と累計を管理。 |
| **資格者証管理** (qualifications) | `/qualifications/` | 作業員の資格者証を一元管理。AI OCR + 期限アラート + マスタ管理。 |
| **資格マスタ管理** (master) | `/master/qualifications` | 資格マスタの編集・新規追加・無効化（管理者専用）。 |

## 2. 資格者証管理スキル — 主要機能 (Phase 1〜3 実装済み)

| 機能 | エンドポイント | 説明 |
|------|----------------|------|
| 一覧 + 期限サマリ | `GET /qualifications/` | 4 段階の期限ステータス（>180/180-61/60-31/30-1/期限切れ）を可視化。キーワード/状態/カテゴリでフィルタ可能。 |
| 検索・絞り込み | 上記同 | クエリ `q`, `status`, `category`, `include_archived`。 |
| **AI OCR アップロード** | `GET/POST /qualifications/upload` | PDF/JPEG/PNG（最大 5 枚 / 1 枚 20 MB）を投入し、Gemini 2.5-flash で構造化抽出。 |
| OCR 結果確認・修正 | `GET/POST /qualifications/classify/{job_id}` | OCR 抽出結果を確認・修正して `status='confirmed'` で確定登録。 |
| 編集・アーカイブ | `GET/POST /qualifications/edit/{cert_id}` / `POST /delete/{cert_id}` | 物理削除はせず `status='archived'` で論理削除。 |
| **アーカイブ復元** | `POST /qualifications/{cert_id}/restore` | 削除した cert を `confirmed` に戻す（重複復元は 400 で防ぐ）。 |
| **手動追加 (OCR スキップ)** | `GET/POST /qualifications/manual-add` | OCR を経由せず即時 `confirmed` で 1 件登録。画像添付は任意。 |
| **作業員別ビュー** | `GET /qualifications/workers/{worker_id}` | 1 人分の保有資格・サマリ・履歴をまとめた個票。 |
| **CSV エクスポート** | `GET /qualifications/export` | UTF-8 BOM 付き、Excel 直接読込 OK。フィルタ条件を URL に保持。 |
| **PDF エクスポート (印刷用)** | `GET /qualifications/export/pdf` | A4 横の印刷向けレイアウト。`?preview=1` で HTML プレビュー。 |
| **資格マスタ管理** | `GET /master/qualifications` ほか | 資格名・カテゴリ・更新年数・表示順・有効/無効を Bootstrap モーダルで編集。 |
| **ポータル期限アラート** | `GET /` | 期限切れ・期限近接の cert があるとポータルにバッジ表示。 |
| **期限アラートメール** | cron + `scripts/send_expiration_alerts.py` | 毎日 SMTP で関係者に通知（30 日以内 / 期限切れ）。 |

## 3. セットアップ

### 3.1 必要環境

- Python 3.12+
- SQLite (Python 同梱)
- Playwright Chromium (PDF 生成・order_docs 用)
- (任意) Gemini API キー — qualifications の OCR 機能を使う場合
- (任意) SMTP サーバ — 期限アラートメールを使う場合

### 3.2 初回セットアップ

```bash
# 1. クローン後、venv を作成
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Playwright Chromium を取得
.venv/bin/python -m playwright install chromium

# 3. 環境変数テンプレートをコピーして編集
cp .env.example .env
# → SECRET_KEY を必ず変更すること

# 4. 起動
.venv/bin/python -m uvicorn web_app.main:app --host 127.0.0.1 --port 8000
```

初回起動時、デフォルト管理者 `admin / admin` が自動作成されます。
ログイン後すぐに `/auth/change-password` でパスワードを変更してください。
追加ユーザーは `scripts/create_user.py` で発行できます。

詳細な venv 構築・再構築手順は [`docs/setup.md`](docs/setup.md) を参照。

## 4. 環境変数 (`.env`)

`.env.example` をテンプレートとしてコピーしてください。主要項目を抜粋します。

### 4.1 セキュリティ・サーバ

```bash
SECRET_KEY=__必ず変更__              # python -c "import secrets; print(secrets.token_urlsafe(48))"
HOST=127.0.0.1                       # 0.0.0.0 で社内公開 (SECRET_KEY 必須)
PORT=8000
SESSION_MAX_AGE=28800                # 8 時間
SESSION_COOKIE_SECURE=false          # HTTPS 環境では true
```

`HOST=0.0.0.0` で起動するには SECRET_KEY を強固な値にし、
`admin/admin` のデフォルトパスワードを変更しないと起動がブロックされます。

### 4.2 資格者証管理 — Gemini API (AI OCR)

```bash
GEMINI_API_KEY=                      # 未設定なら OCR 自動無効化 → 手動入力フローのみ
QUALIFICATIONS_OCR_ENABLED=true
QUALIFICATIONS_MAX_FILE_MB=20
QUALIFICATIONS_MAX_FILES_PER_UPLOAD=5
```

[Google AI Studio](https://aistudio.google.com/apikey) で API キーを発行。
未設定でもアプリ自体は起動し、手動追加 (`/qualifications/manual-add`) で登録できます。

### 4.3 期限アラートメール (SMTP)

```bash
SMTP_SERVER=smtp.gmail.com           # 空なら通知 skip (cron は走るが送信しない)
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=__app_password__       # Gmail はアプリパスワード必須 (2 段階認証)
SMTP_USE_TLS=true                    # 587 STARTTLS / 25 plain なら false

ALERT_EMAIL_FROM=alerts@example.com
ALERT_EMAIL_TO=admin@example.com,safety@example.com
```

サーバ内のローカル MTA (postfix 等) を使う場合の例:

```bash
SMTP_SERVER=127.0.0.1
SMTP_PORT=25
SMTP_USE_TLS=false
ALERT_EMAIL_FROM=noreply@your.domain
ALERT_EMAIL_TO=admin@example.com
```

詳細・トラブルシュートは [`docs/email_alerts.md`](docs/email_alerts.md) 参照。

## 5. 定期実行タスク (cron)

期限アラートメールを毎日午前 9 時に送信する設定例:

```cron
0 9 * * * cd /home/ubuntu/dev-app && /home/ubuntu/dev-app/.venv/bin/python scripts/send_expiration_alerts.py >> /var/log/factoryskills/alerts.log 2>&1
```

prod-app 側の DB を対象にする場合は `--db` で明示:

```cron
0 9 * * * cd /home/ubuntu/dev-app && /home/ubuntu/dev-app/.venv/bin/python scripts/send_expiration_alerts.py --db /home/ubuntu/prod-app/web_app/data/app.db >> /var/log/factoryskills/alerts-prod.log 2>&1
```

ログディレクトリは事前に作成 + 権限調整してください:

```bash
sudo mkdir -p /var/log/factoryskills
sudo chown ubuntu:ubuntu /var/log/factoryskills
```

ドライラン (送信せず件数のみ表示):

```bash
.venv/bin/python scripts/send_expiration_alerts.py --dry-run
```

### 5.1 通知対象条件

`q_certificates` のうち以下すべてに合致する行が対象:

- `status = 'confirmed'` (archived / draft 除外)
- `renewal_required = 1` (更新不要は対象外)
- `expires_on IS NOT NULL`
- `expires_on <= 今日 + 30 日`

通知は「期限切れ (残日数 ≤ 0)」と「30 日以内に期限到来 (残 1〜30 日)」の
2 セクションに分けて作業員ごとにグループ化されます。

## 6. 起動・運用 (Linux 本番)

### 6.1 systemd 構成 (本リポジトリの主たる本番)

このマシンの prod-app は `/home/ubuntu/prod-app/` に配置され、
`factory-prod.service` で稼働しています。

```bash
sudo systemctl {start|stop|restart} factory-prod
sudo systemctl status factory-prod
sudo journalctl -u factory-prod -f         # ログ追跡
curl http://127.0.0.1:8000/health          # ヘルスチェック
```

### 6.2 公式グリーンフィールド構成 (新規 Linux サーバ向け)

`deploy_linux/install_linux.sh` を使用:

```bash
sudo bash deploy_linux/install_linux.sh
sudo systemctl {start|stop|restart} factoryskills
```

詳細は [`skills/order_docs/CLAUDE.md`](skills/order_docs/CLAUDE.md) §3 参照。

### 6.3 環境分離の原則

- **dev-app** (`/home/ubuntu/dev-app/`): 開発・テスト用、本リポジトリ
- **prod-app** (`/home/ubuntu/prod-app/`): 本番、別 venv・別 .env・別 DB
- 境界越えの操作は事故源。`scripts/*` は `--db` で対象を明示すること

## 7. テスト

```bash
# 全テスト (slow を除く、380+ 件)
.venv/bin/python -m pytest -m "not slow" -q

# 資格者証管理スキル単独
.venv/bin/python -m pytest tests/test_qualifications/ -q

# Playwright Chromium 必須テストは自動 skip される (未インストール時)
```

## 8. ディレクトリ構成

```
dev-app/
├── README.md                    ← このファイル
├── .env.example                 ← 環境変数テンプレート
├── requirements.txt
├── docs/
│   ├── setup.md                 ← venv 構築・再構築手順
│   ├── email_alerts.md          ← 期限アラートメール運用ガイド
│   └── AUTHORIZATION_GUIDE.md   ← 認可ガイド
├── scripts/
│   ├── create_user.py           ← ユーザー作成 CLI
│   ├── send_expiration_alerts.py ← 期限アラートメール (cron)
│   ├── backup_assets.py         ← G-2 バックアップ
│   └── monitor_health.sh        ← ヘルスチェック監視
├── web_app/
│   ├── main.py                  ← FastAPI エントリポイント
│   ├── core/                    ← config / database / auth / templates
│   ├── routers/                 ← FastAPI ルーター (auth, portal, order_docs,
│   │                              construction_cost, qualifications, master)
│   ├── services/                ← worker / job_queue / cleanup / email_service
│   ├── templates/               ← Jinja2 テンプレート
│   └── static/                  ← CSS / JS / 画像
├── skills/
│   ├── order_docs/              ← 注文書自動作成スキル
│   ├── construction_cost/       ← 工事日報集計スキル
│   └── qualifications/          ← 資格者証管理スキル (schema, ocr, pipeline, storage)
└── tests/
    ├── conftest.py              ← Chromium 検出など共通フィクスチャ
    ├── test_qualifications/     ← 14 ファイル / 233 件
    └── ...
```

## 9. バージョン管理

- セマンティック・バージョニング (`MAJOR.MINOR.PATCH-description`)
- バージョン定数:
  - 基盤: `web_app/core/versions.py` の `CORE_VERSION`
  - 各スキル: `skills/<skill>/config.py` の `*_VERSION`
- ポータル画面下部に基盤 + 全スキルのバージョンを自動表示
- 詳細は [`skills/order_docs/CLAUDE.md`](skills/order_docs/CLAUDE.md) §5 参照

## 10. 関連ドキュメント

| ファイル | 内容 |
|----------|------|
| [`docs/setup.md`](docs/setup.md) | venv 構築・再構築・B-1 再発防止 |
| [`docs/email_alerts.md`](docs/email_alerts.md) | 期限アラートメール cron 設定・SMTP 例・トラブルシュート |
| [`docs/AUTHORIZATION_GUIDE.md`](docs/AUTHORIZATION_GUIDE.md) | 認可・権限モデル |
| [`skills/order_docs/CLAUDE.md`](skills/order_docs/CLAUDE.md) | 注文書スキル詳細・本番運用コマンド・バージョン管理ポリシー |
| [`skills/order_docs/SYSTEM_SPEC.md`](skills/order_docs/SYSTEM_SPEC.md) | 抽出パイプライン仕様 |

## 11. 既知のスコープ外 / 今後の検討

- qualifications スキルのバージョン定数 (`web_app/core/versions.py` への登録)
- 資格者証の作業員別 PDF (現状は一覧のみ)
- 期限アラートのリッチ HTML メールテンプレート差し替え
- 多言語対応 (現状 日本語固定)
