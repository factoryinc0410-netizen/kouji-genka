# 注文書システム (order_docs) プロジェクト規約

## 1. プロジェクトの基本方針（重要）
- **アーキテクチャ**: 「完全データ抽出 ＋ 事前PDFへの座標スタンプ方式」を採用。
- **禁止事項**: サーバーサイドでのExcelからPDFへの直接変換（COMやLibreOffice）は**絶対に行わない**こと。
- **主要技術**: Python (FastAPI), openpyxl (データ抽出), PyMuPDF (PDFスタンプ処理)。

## 2. コーディングルール
- バグ修正や機能追加を行う際は、必ず既存の `config.py` にある `PDF_STAMP_MAP` の仕組みを壊さないように実装すること。
- Excelの抽出処理では、空行を詰めず `row` インデックスを厳密に保持すること。

## 2.5. 抽出パイプラインのモジュール構成（Phase E + F 完了, 2026-05-05）

Excel 抽出ロジックは責務別に 8 モジュールに分割済み。`extractor.py` は **オーケストレータ専用** で、re-export ハブの役割は持たない。
新しい関数を追加するときや既存関数を修正するときは、下記のテーブルに従って **正しいモジュール** に置くこと。

| モジュール | 役割 | 主な公開 API |
|---|---|---|
| `extractor.py` | メインオーケストレータ（`extract_data` のみ） | `extract_data` |
| `extractor_utils.py` | 純粋ユーティリティ + Cell ラッパー + `_banner` | `_normalize` / `_clean_amount` / `_format_wareki` / `_parse_*` / `_cell_*` / `_safe_*` |
| `irai_scan_utils.py` | 依頼書スキャン + 金額抽出 | `_detect_vendor_base_cols` / `_scan_keyword_rows` / `_find_sub_keyword_row` / `_extract_kingaku_direct` |
| `nairaku_text_utils.py` | 内訳書テキスト判定 + シート種別分類 | `_classify_sheet_type` / `_is_subtotal_text` / `_is_footer_terminator` / `_count_indent` / `_NAIRAKU_*_KEYWORDS` |
| `nairaku_extraction.py` | 内訳書抽出本体 + 補助ヘルパー | `extract_nairaku_data` / `apply_nairaku_page_padding` / `_build_merged_cells_cache` / `_resolve_col_spans` |
| `sheet_assignment_utils.py` | 業者×シートのマッチング | `build_sheet_assignment` / `_match_score` / `_extract_from_first_joken` |
| `terms_extraction.py` | 契約条件書構造化抽出 + 契約変更回数スキャン | `extract_terms_data` / `scan_contract_change_count` / `_is_checked` |
| `vml_utils.py` | 契約条件書テキスト + VML チェックボックス | `extract_joken_text_data` / `_extract_checkboxes_from_vml` |

**import 規則（厳守）**:
- 外部呼び出し元（`generate_order_docs.py`, `web_app/`, `tests/`）は **所有モジュールから直接 import** する。
  ```python
  from skills.order_docs.extractor import extract_data
  from skills.order_docs.nairaku_extraction import extract_nairaku_data
  from skills.order_docs.terms_extraction import extract_terms_data
  from skills.order_docs.vml_utils import extract_joken_text_data
  ```
- **禁止**: `from skills.order_docs.extractor import _normalize` のような旧 re-export 経由 import は Phase F で機能しなくなった。コミット前に必ず動作確認すること。
- 詳細仕様は `SYSTEM_SPEC.md` §5.2「抽出パイプライン」を参照。

## 3. 主要コマンド

### 3.1 Windows
- サーバー起動: `start_system.bat` （エラー確認時は最後に `pause` を入れて実行）
- サーバー停止: `stop_system.bat`
- サーバー再起動: `restart_system.bat`

### 3.2 Linux (さくらVPS / Ubuntu / Debian)

**注**: 本番環境のデプロイには 2 つの系統が存在する。サーバごとに使い分けること。

#### 3.2.a 公式グリーンフィールド構成（新規 Linux サーバ向け）
`deploy_linux/install_linux.sh` が想定する標準構成。`/opt/factoryskills/` 配下に専用ユーザー `factoryskills` で配置する。
- 初回セットアップ: `sudo bash deploy_linux/install_linux.sh`
- サーバー起動 / 停止 / 再起動: `sudo systemctl {start|stop|restart} factoryskills`
- 状態確認: `sudo systemctl status factoryskills`
- ログ追跡: `sudo journalctl -u factoryskills -f`

#### 3.2.b 既存運用サーバ（このリポジトリの主たる本番環境）
このマシンの prod-app は `/home/ubuntu/prod-app/` に配置され、`factory-prod.service`（user=ubuntu、venv=`~/prod-app/.venv`）で稼働中。
- サーバー起動 / 停止 / 再起動: `sudo systemctl {start|stop|restart} factory-prod`
- 状態確認: `sudo systemctl status factory-prod`
- ログ追跡: `sudo journalctl -u factory-prod -f`

#### 3.2.c 共通
- ヘルスチェック: `curl http://127.0.0.1:8000/health`

### 3.3 OS 共通の注意
- Linux サーバでは Excel COM が使えないため、`.xls` 入力は事前に `.xlsx` へ変換する運用とする。
- `start_local.py` は Windows ローカル向け Tkinter ランチャ。Linux サーバ運用では使用しない。

## 4. 詳細仕様の確認先
システムの詳細な仕様や過去の決定事項については、プロジェクト内にある `SYSTEM_SPEC.md` （または仕様書ファイル）を優先して読み込むこと。むやみに全ファイルを検索しないこと。

開発環境（venv）の構築・再構築手順、および venv 汚染（B-1）の再発防止ルールは
プロジェクトルートの `docs/setup.md` を参照すること。**venv は絶対に `cp -r` で
コピーしない**（各プロジェクト独自に `python3 -m venv .venv` で作る）。

## 5. バージョン管理ルール（必須）

### 5.1 タスク完了時のバージョン更新（義務）
- **各タスク（機能追加・修正）の完了時には、必ず該当するスキルのバージョンを更新すること。**
- 更新先（2026-05-05 時点で全スキルが登録済み）:
  - 基盤 (Factoryskills 本体) の変更 → `web_app/core/versions.py` の `CORE_VERSION`
  - 注文書作成スキルの変更 → `skills/order_docs/config.py` の `ORDER_DOCS_VERSION`
  - 工事日報集計スキルの変更 → `skills/construction_cost/config.py` の `CONSTRUCTION_COST_VERSION`
  - ファクトリーチャットの変更 → `chat/version.py` の `CHAT_VERSION`
  - 新スキル追加時は、スキル配下にバージョン定数を定義し（FastAPI app 等の重い
    モジュールとは別ファイルに置くこと）、`web_app/core/versions.py` の
    `SKILL_VERSIONS` と `SKILL_DISPLAY_NAMES` の両方に登録する。
- バージョン一覧はポータル画面下部に自動表示され、右下バッジには現在のスキルのバージョンが表示される。
- `compound_cache_key()` が全バージョンを連結するため、どれか 1 つでも bump すれば
  ブラウザキャッシュが自動破棄される。

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
