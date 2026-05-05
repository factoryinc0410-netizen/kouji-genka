# BACKLOG — 技術負債・改善タスク

このファイルは、緊急性は低いが将来的に解消すべき技術負債・改善タスクを記録するものです。
新しい課題は末尾に追記し、解消したものは `## Done` セクションに移動するか、コミット SHA を併記して残してください。

---

## Done

### B-1. venv シバン行が prod-app を参照する問題の根本修正 — 完了 (2026-05-05)

**根本原因の特定**:
- `dev-app/.venv/pyvenv.cfg` を確認したところ、`command = /usr/bin/python3 -m venv /home/ubuntu/prod-app/.venv` となっており、**dev-app の venv は prod-app の venv をそのままコピーしたもの**であることが判明（仮説の `cp -r` ルートが正解）。
- shebang はすでに `sed` 修正で dev-app を指していたが、pyvenv.cfg のメタデータは汚染されたまま放置されていた。
- パッケージ構成は dev-app と prod-app で完全一致 (44 個) しており、機能的な実害はゼロだったが、再発リスクが残る状態だった。

**実施した修正**:
1. 既存の `.venv` を `.venv.bak.B-1` にリネームしてバックアップ確保。
2. `/usr/bin/python3 -m venv .venv` でクリーンに作り直し。pyvenv.cfg の `command` が `... /home/ubuntu/dev-app/.venv` になることを確認。
3. `pip install --upgrade pip` で pip 24.0 → 26.1.1 に更新。
4. `pip install -r requirements.txt -r requirements-dev.txt` で全依存を再インストール。
5. パッケージ数が 44 個（旧 / prod-app と完全一致）であること、`pip list --format=freeze` の diff が空であることを確認。
6. `pytest -m "not slow"` → **165 passed**。バックアップを削除して完了。
7. Playwright Chromium は `~/.cache/ms-playwright/` に配置されており venv 外なので再ダウンロード不要だった。

**再発防止策**:
- プロジェクト直下に `docs/setup.md` を新設し、「**venv は絶対に `cp -r` でコピーしない**、`python3 -m venv .venv` で各プロジェクト独自に作成する」ルールを明記。
- セットアップ確認用のワンライナー（pyvenv.cfg の `command` 行が現在のプロジェクトを指していること）を同 docs に記載。
- 関連コミット: `<次のコミット SHA>`。


### B-2. システム全体のバージョン定数の整備 — 完了 (2026-05-05)

**完了サマリ**:
- `skills/construction_cost/config.py` に `CONSTRUCTION_COST_VERSION = "1.0.0-initial-version"` を追加。
- ファクトリーチャット用に `chat/version.py` を新規作成し、`CHAT_VERSION = "1.0.0-initial-version"` を定義（FastAPI app 初期化を巻き込まないよう独立モジュール化）。`chat/backend/main.py` からは re-export 目的で import。
- `web_app/core/versions.py` の `SKILL_VERSIONS` / `SKILL_DISPLAY_NAMES` に両エントリを登録。`compound_cache_key()` が 4 バージョン (CORE + 3 スキル) を連結する形に拡張。
- `skills/order_docs/CLAUDE.md` §5.1 に bump 対象として `CONSTRUCTION_COST_VERSION` / `CHAT_VERSION` を追記し、新スキル追加時の手順 (FastAPI app 等の重いモジュールとは別ファイルに置くこと) も明記。
- スモークテスト: `compound_cache_key()` →
  `1.1.0-linux-portability-2.4.0-extractor-modularization-1.0.0-initial-version-1.0.0-initial-version`。
- これで「いつ何が変わったか」が UI バッジ・診断ログから常に追跡可能になる。

### D-1. 本番環境 (prod-app) への安全なデプロイ — 完了 (2026-05-05)

**完了サマリ**:
- 事前準備として dev-app 側で `ORDER_DOCS_VERSION` を `2.4.0-extractor-modularization` に bump、サービス名の食い違い (`factoryskills` vs 実体 `factory-prod`) を BACKLOG.md / `skills/order_docs/CLAUDE.md` で訂正 (コミット `d7f6b2d`)。
- 段階的デプロイを Phase 0 〜 9 で実行 (A 案：ユーザーが `!` プレフィックス経由で sudo コマンドを直接実行する形):
  - Phase 0: dev-app pytest **165 passed** 確認、HEAD = `d7f6b2d`
  - Phase 2: prod-app の現状コミット `94009e8` をロールバック点として記録
  - Phase 3: `sudo systemctl stop factory-prod` → `git fetch devapp && git merge --ff-only devapp/main` で `94009e8..d7f6b2d` の 11 コミットを fast-forward 取り込み (17 ファイル変更、+3,522 / -3,114 行)
  - Phase 4: `find /home/ubuntu/prod-app -name __pycache__ -type d -exec rm -rf {} +` で旧 .pyc を一掃
  - Phase 8: `sudo systemctl start factory-prod` → ヘルスチェック `OK` で復帰
  - Phase 9: end-to-end 動作確認に成功、エラーなく安定稼働
- ロールバックは未発動 (移行は一発成功)。
- 関連コミット: `d7f6b2d` (dev-app 側)、prod-app 側は `94009e8 → d7f6b2d` への fast-forward マージ。


### B-3. extractor.py のモジュール分割と構造整理 — 完了 (2026-05-05)

**完了サマリ**:
Phase E（Step 1 〜 8b、計 8 コミット）と Phase F（1 コミット）で完遂。
- `skills/order_docs/extractor.py` を **3,126 行 → 413 行**（−87%）まで縮小。
- 抽出ロジックを責務別に 7 つの新モジュールに分散:
  - `extractor_utils.py` (421行): 純粋ユーティリティ + Cell ラッパー + `_banner`
  - `irai_scan_utils.py` (240行): 依頼書スキャン + 金額抽出
  - `nairaku_text_utils.py` (223行): 内訳書テキスト判定 + シート種別分類
  - `nairaku_extraction.py` (1,010行): 内訳書抽出本体 + 補助ヘルパー
  - `sheet_assignment_utils.py` (374行): 業者×シートのマッチング
  - `terms_extraction.py` (313行): 契約条件書抽出 + 契約変更回数スキャン
  - `vml_utils.py` (416行): 契約条件書テキスト + VML チェックボックス
- Phase F で **re-export ハブを完全廃止**し、外部呼び出し元 7 ファイル（`generate_order_docs.py`, `web_app/routers/order_docs.py`, テスト 5 ファイル）を所有モジュールへの **直接 import** に切り替え。
- 全期間を通じて `pytest -m "not slow"` → **165 passed**（回帰ゼロ）。
- 提案時の懸念事項（外部参照の崩壊、PyMuPDF/Playwright のリソース管理）は **抽出系のみを対象とし**、PDF 生成・合冊系には一切触れずに完遂。
- `SYSTEM_SPEC.md` §4 ディレクトリ構成 / §5.2 モジュール構成も同時に更新。
- 関連コミット: `a2119ec` 〜 `a72af21`（10 コミット）。

### C-2. pypdf 6 系への移行に伴う非推奨警告の解消 — 完了 (2026-05-04)

**完了サマリ**:
- `skills/order_docs/pdf_merger.py:62` のキーワード引数を新名 (`remove_duplicates` / `remove_unreferenced`) に置換。
- `requirements.txt` の `pypdf>=4.0.0` → `pypdf>=6.0.0` に引き上げ（インストール済み: 6.10.2）。
- `ORDER_DOCS_VERSION` を `2.3.20-pypdf-deprecation-fix` に bump。
- `pytest -m "not slow"` で **165 passed / 0 warnings** を確認（修正前は 4 warnings）。

### B-4. tests/ ディレクトリを pytest 形式へ移行 — 完了 (2026-05-04)

**完了サマリ**:
- 旧手動スクリプト 3 本 (`test_breakdown_html.py` / `test_condition_html.py` / `test_integration_merge.py`) を pytest 関数群に書換。
- 純粋関数ユニットテスト 2 本を新規追加 (`test_construction_cost_reader.py` / `test_order_docs_helpers.py`、計 134 件)。
- 既存出力 PDF を「正」として固定するスナップショットテスト (`test_pdf_snapshots.py`、13 件) を追加。
- Excel 抽出回帰テスト (`test_excel_extraction.py`、18 件) を追加し、`extracted_vendors.json` を真値として全 11 フィールド × 5 業者を照合。
- マーカー設計: `slow` / `requires_sample` / `requires_chromium` を `pyproject.toml` に登録、Chromium 不在時は自動 skip。
- `tests/conftest.py` に共通 fixture (`sample_excel`, `pdf_html_dir`, `pdf_integration_dir` 等) を集約。
- **最終結果: 181 passed / 0 failed (76 秒)**。`-m "not slow"` で軽量 165 件を 31 秒で実行可能。
- コアロジック (`extractor.py` 等) は **1 行も変更せず**、現状を保存するテストの作成のみで完遂。
