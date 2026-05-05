# BACKLOG — 技術負債・改善タスク

このファイルは、緊急性は低いが将来的に解消すべき技術負債・改善タスクを記録するものです。
新しい課題は末尾に追記し、解消したものは `## Done` セクションに移動するか、コミット SHA を併記して残してください。

---

## B-1. venv シバン行が prod-app を参照する問題の根本修正

**発覚経緯** (2026-05-04):
- C-1 Phase B 着手時、`/home/ubuntu/dev-app/.venv/bin/pip install ruff` を実行したところ、出力に `Requirement already satisfied: ruff in /home/ubuntu/prod-app/.venv/...` と表示され、dev-app の venv ではなく prod-app の venv にインストールされようとしていることが判明した。
- 原因は `dev-app/.venv/bin/pip` ほか複数のスクリプトの shebang が `#!/home/ubuntu/prod-app/.venv/bin/python3` を指していたため。
- 暫定対処として `sed` で `dev-app/.venv/bin/` 配下を一括書き換えして復旧済み。

**残課題**:
1. なぜ dev-app の venv が prod-app を指すシバンを持っていたのか、構築手順を遡って根本原因を特定する。
   - 仮説: prod-app から `cp -r` で複製された / 初期 `python -m venv` 実行時のインタプリタが prod-app 側だった、等。
2. 再発防止策の確立:
   - venv 再構築手順を README または `docs/setup.md` に明記する（`python3 -m venv .venv --clear` を使う等）。
   - CI またはセットアップスクリプトに「シバンが現在のプロジェクトを指しているか」の検証ステップを追加。
3. `dev-app/.venv/lib/python*/site-packages` の中に prod-app から流れ込んだ不整合パッケージがないかを `pip list` で突き合わせる。

**運用上のワークアラウンド**（恒久対応までの間）:
- venv 内のスクリプトを直接呼ばず、必ず `python -m pip` のようにモジュール経由で実行する（C-1 Phase B 以降のルール）。

---

## B-2. システム全体のバージョン定数の整備

**現状** (2026-05-04 時点):
- `skills/order_docs/config.py` には `ORDER_DOCS_VERSION` が定義され、CLAUDE.md ルール（変更時に必ず bump）にも組み込まれている。例: `2.3.19-unused-import-cleanup`
- `web_app/core/versions.py` には `CORE_VERSION = "1.1.0-linux-portability"` が存在する。
- 一方、`chat/`、`skills/construction_cost/` には対応するバージョン定数が**存在しない**。
- そのため、これらモジュールの変更は SKILL_VERSIONS に反映されず、運用ログ・診断レポートからは「いつ何が変わったか」が追跡できない。

**やること**:
1. `chat/` 配下に `chat/version.py` または既存 `chat/backend/main.py` 内に `CHAT_VERSION = "1.0.0"` を導入。
2. `skills/construction_cost/` 配下に `skills/construction_cost/config.py` の冒頭などに `CONSTRUCTION_COST_VERSION = "1.0.0"` を導入。
3. 横断的なバージョンレジストリ（例: `web_app/core/versions.py` 内の `SKILL_VERSIONS = {...}`）に上記を登録し、UI の About 画面・診断 API でまとめて参照できるようにする。
4. CLAUDE.md にこれら新規バージョンも「変更時 bump 必須」のルール対象として追記する。

**バージョニング規則** (既存 ORDER_DOCS_VERSION 流儀):
`MAJOR.MINOR.PATCH-<short-suffix>` 形式。`<short-suffix>` には変更内容を象徴するキーワード（例: `unused-import-cleanup`, `linux-portability`）を ASCII で短く記載。

---

## D-1. 本番環境（prod-app）への安全なデプロイ

**背景** (2026-05-05 時点):
- Phase E + F のリファクタリングで `dev-app/skills/order_docs/extractor.py` が **3,126 → 413 行**まで縮小し、抽出ロジックは 8 モジュールに分散した。
- ただし変更は **dev-app（`/home/ubuntu/dev-app/`）にのみ反映**されており、`prod-app`（さくら VPS の `/opt/factoryskills/` または `/home/ubuntu/prod-app/`）には未デプロイ。
- ファイル数が大きく増えた（+7 モジュール）ため、安易な単純コピーや `git pull` 一発では移行漏れ・古いキャッシュ参照が起きやすい。

**やること**:
1. **デプロイ前検証（dev-app 側）**:
   - `pytest -m "not slow"` 全件 (165 passed) を再確認。
   - 1 件以上の実 Excel フィクスチャで `generate_from_excel` を end-to-end 走らせ、生成 PDF の目視確認 or スナップショットテストの再実行。
   - `.venv` 配下の依存パッケージに dev/prod 差がないか `pip freeze | diff` で確認。
2. **prod-app の現状把握**:
   - 現在 prod に乗っているコミット SHA を `git -C /opt/factoryskills log -1` などで控える（ロールバック用）。
   - `systemctl status factoryskills` でサービス稼働状況・直近エラーを確認。
3. **段階的デプロイ手順**:
   - メンテ告知（必要なら）→ `systemctl stop factoryskills`
   - prod-app の `.git` で `git fetch && git checkout <main の最新>`（force push を含まない通常 fast-forward）
   - 新規ファイル 7 つ（`extractor_utils.py`, `irai_scan_utils.py`, `nairaku_text_utils.py`, `nairaku_extraction.py`, `sheet_assignment_utils.py`, `terms_extraction.py`, `vml_utils.py`）の存在確認: `ls /opt/factoryskills/skills/order_docs/*_utils.py *_extraction.py vml_utils.py`
   - `__pycache__` を全削除（古い re-export を残した .pyc を踏まないため）: `find /opt/factoryskills -name __pycache__ -type d -exec rm -rf {} +`
   - `pip install -r requirements.txt` で依存ズレを是正
   - `systemctl start factoryskills` → `journalctl -u factoryskills -f` でログ追跡
   - `curl http://localhost:8000/health` の戻りを確認
4. **本番動作確認**:
   - 実依頼書 1 件で end-to-end 生成し、注文書/注文請書/内訳書/契約条件書/新旧対照表/約款の 6 書類すべてが PDF として生成されることを確認。
   - 内訳書の **動的疑似結合**（A/B/C 列の長文自動結合）が PDF に正しく反映されているか確認。
   - チェックボックス（VML 解析）が正しくスタンプされているか確認。
5. **ロールバック準備**:
   - 失敗時は `git checkout <旧 SHA>` → `__pycache__` 削除 → `systemctl restart factoryskills` で即時復旧できることを事前にリハーサル。

**リスク要因**:
- 旧バージョンの `.pyc` が `__pycache__` に残ったまま新コードと混在すると `ImportError` ではなく **無音のロード失敗**を起こす可能性がある（特に re-export が消えたシンボル）。事前削除は必須。
- prod-app 側で B-1 (venv シバン問題) が再燃していないか確認すること。`#!/usr/bin/env python3` 経由で実行するか、フルパスで `/opt/factoryskills/.venv/bin/python` を呼ぶこと。
- `ORDER_DOCS_VERSION` を Phase E + F 用に bump しておく（例: `2.4.0-extractor-modularization`）。バッジで本番環境のバージョンが切り替わったことを目視確認できるようにする。

---

## Done

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
