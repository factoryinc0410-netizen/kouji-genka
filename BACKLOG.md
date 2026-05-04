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

## B-3. extractor.py のモジュール分割と構造整理

**現状** (2026-05-04 時点):
- `skills/order_docs/extractor.py` が **3,126 行** に肥大化しており、可読性・テスト容易性・差分レビューに支障が出始めている。
- C-1 Phase D としてリファクタを検討したが、コアロジック（PDF生成・抽出・合冊処理）に直接触れるリスクが高いため別タスクとして切り出した。

**やること**（提案、要再評価）:
1. まず実際の責務単位で関数群をマッピングする。想定セクション:
   - Excel/PDF 入力ローダ群
   - 行・セルパース系のユーティリティ
   - 帳票テンプレート別の抽出ロジック（種類ごとに分岐している関数）
   - 合冊（ページ結合）パイプライン
   - 出力フォーマッタ
2. 上記を順に `skills/order_docs/extractor/` パッケージへ移動し、`__init__.py` で外部公開 API を維持する。
3. 各分割モジュールに対する**最小限のスナップショットテスト**を `tests/order_docs/` に追加してから本格的な分割に着手する（後方互換性検証用）。
4. 1 PR で全部やらず、ファイル分割 → 内部リファクタ の順で**コミットを細かく**切る。

**注意点**:
- 既存の絶対 import パス（`from skills.order_docs.extractor import xxx`）が外部から参照されているか grep で要確認。崩すと chat/ や web_app/ 側が壊れる。
- 合冊処理周りは PyMuPDF + Playwright のリソース管理が絡むので触る順序に注意。

---

## C-2. pypdf 6 系への移行に伴う非推奨警告の解消

**現状** (2026-05-04 時点):
- `skills/order_docs/pdf_merger.py:62` の `writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)` 呼び出しが、pypdf 6 系で 2 件の DeprecationWarning を出している。
  - `remove_identicals` → `remove_duplicates` に改名（pypdf 7.0.0 で削除予定）
  - `remove_orphans` → `remove_unreferenced` に改名（pypdf 7.0.0 で削除予定）
- 現状はテスト 4 件で警告として観測されるのみで動作には影響なし（`test_required_documents_succeeded` の合冊実行時に発生）。

**やること**:
1. `pdf_merger.py:62` のキーワード引数を新名 (`remove_duplicates` / `remove_unreferenced`) に置換する。
2. インストール済み pypdf のバージョンを確認し、新引数名がサポートされる最低バージョンを `requirements.txt` の `pypdf>=` に反映する（現状: `pypdf>=4.0.0` → 新名対応版へ引き上げ）。
3. `tests/test_integration_merge.py` を再実行し、警告が消えることを確認する。
4. CLAUDE.md ルールに従い `ORDER_DOCS_VERSION` を bump する（例: `2.3.20-pypdf-deprecation-fix`）。

**注意点**:
- pypdf 7 はまだ未リリース（2026-05 時点）だが、APIサーフェスは固まっている。早めの移行で 7.0 リリース後の破壊的変更を回避できる。
- `compress_identical_objects` 自体は維持される。引数名のみの変更。

---

## Done

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
