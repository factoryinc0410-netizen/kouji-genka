# 注文書システム (order_docs) プロジェクト規約

## 1. プロジェクトの基本方針（重要）
- **アーキテクチャ**: 「完全データ抽出 ＋ 事前PDFへの座標スタンプ方式」を採用。
- **禁止事項**: サーバーサイドでのExcelからPDFへの直接変換（COMやLibreOffice）は**絶対に行わない**こと。
- **主要技術**: Python (FastAPI), openpyxl (データ抽出), PyMuPDF (PDFスタンプ処理)。

## 2. コーディングルール
- バグ修正や機能追加を行う際は、必ず既存の `config.py` にある `PDF_STAMP_MAP` の仕組みを壊さないように実装すること。
- Excelの抽出処理では、空行を詰めず `row` インデックスを厳密に保持すること。

## 3. 主要コマンド

### 3.1 Windows
- サーバー起動: `start_system.bat` （エラー確認時は最後に `pause` を入れて実行）
- サーバー停止: `stop_system.bat`
- サーバー再起動: `restart_system.bat`

### 3.2 Linux (さくらVPS / Ubuntu / Debian)
- 初回セットアップ: `sudo bash deploy_linux/install_linux.sh`
- サーバー起動: `sudo systemctl start factoryskills`
- サーバー停止: `sudo systemctl stop factoryskills`
- サーバー再起動: `sudo systemctl restart factoryskills`
- 状態確認: `sudo systemctl status factoryskills`
- ログ追跡: `sudo journalctl -u factoryskills -f`
- ヘルスチェック: `curl http://localhost:8000/health`

### 3.3 OS 共通の注意
- Linux サーバでは Excel COM が使えないため、`.xls` 入力は事前に `.xlsx` へ変換する運用とする。
- `start_local.py` は Windows ローカル向け Tkinter ランチャ。Linux サーバ運用では使用しない。

## 4. 詳細仕様の確認先
システムの詳細な仕様や過去の決定事項については、プロジェクト内にある `SYSTEM_SPEC.md` （または仕様書ファイル）を優先して読み込むこと。むやみに全ファイルを検索しないこと。

## 5. バージョン管理ルール（必須）

### 5.1 タスク完了時のバージョン更新（義務）
- **各タスク（機能追加・修正）の完了時には、必ず該当するスキルのバージョンを更新すること。**
- 更新先:
  - 基盤 (Factoryskills 本体) の変更 → `web_app/core/versions.py` の `CORE_VERSION`
  - 注文書作成スキルの変更 → `skills/order_docs/config.py` の `ORDER_DOCS_VERSION`
  - 他スキル追加時も同じく、スキル配下の `config.py` にバージョン定数を定義し、
    `web_app/core/versions.py` の `SKILL_VERSIONS` に登録すること。
- バージョン一覧はポータル画面下部に自動表示され、右下バッジには現在のスキルのバージョンが表示される。

### 5.2 バージョン命名規約
- **セマンティック・バージョニング**（`MAJOR.MINOR.PATCH`）に従うこと。
  - `MAJOR` — 互換性を破る大規模変更
  - `MINOR` — 後方互換のある機能追加
  - `PATCH` — 後方互換のあるバグ修正
- **末尾にハイフン繋ぎで変更内容を付記すること**。
  - フォーマット: `MAJOR.MINOR.PATCH-change-description`
  - 例: `"1.3.0-flow-layout"`, `"1.4.1-csv-fix"`, `"2.0.0-multi-tenant"`
- **スペース・括弧は使用禁止**。
  - 禁止例: `"v1.1.0 (Reflected Rows Fix)"`, `"1.3.0 flow layout"`
  - 必ず URL 安全な ASCII 小文字とハイフンのみで構成する（日本語・大文字は避ける）。

### 5.3 バージョン更新の手順
1. 実装・修正を完了する。
2. 対応する `*_VERSION` 定数を更新する（PATCH 上げ or MINOR 上げ or MAJOR 上げ）。
3. 末尾のハイフンサフィックスを今回の変更内容に合わせて書き換える。
4. ポータル画面・右下バッジで最新バージョンが表示されていることを確認する。
5. 必要に応じて `start_system.bat` で再起動し、ブラウザキャッシュが自動破棄されて
   新しい CSS/JS がロードされることを確認する（キャッシュキーは複合バージョンで自動更新）。
