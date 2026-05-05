# 注文書自動作成システム — システム仕様書

> **Version**: 2.1
> **Date**: 2026-04-29
> **Platform**: Windows Server 2022 / Linux (Ubuntu 22.04+ / Debian 12+, さくらVPS 等) / Python 3.10+
> **重要**: v2.0 から MS Office 依存を排除し、完全ヘッドレス動作が可能になった。
> **重要**: v2.3.18 から Linux 対応（OS 非依存化）が完了し、さくらVPS 等の Linux サーバでも運用可能。

---

## 目次

1. [システム概要](#1-システム概要)
2. [アーキテクチャ（v2.0 ハイブリッド方式）](#2-アーキテクチャv20-ハイブリッド方式)
3. [環境構築](#3-環境構築)
4. [ディレクトリ構成](#4-ディレクトリ構成)
5. [モジュール構成と責務](#5-モジュール構成と責務)
6. [外部呼び出しインターフェース](#6-外部呼び出しインターフェース)
7. [設定の集約 — `config.py` ガイド](#7-設定の集約--configpy-ガイド)
8. [エラーハンドリング仕様](#8-エラーハンドリング仕様)
9. [ビジネスロジック・特殊仕様](#9-ビジネスロジック特殊仕様)
10. [拡張ガイド — 未来の開発者（Claude 自身を含む）向け](#10-拡張ガイド--未来の開発者向け)
11. [履歴と廃止機能](#11-履歴と廃止機能)

---

## 1. システム概要

### 1.1 解決する課題

建設工事の下請契約書類（注文書・注文請書・内訳書・契約条件書・新旧対照表・約款）を、Excel 依頼書 1 ファイルから**業者数 × 書類種類**の組み合わせで PDF セットとして一括生成する。従来は手作業で数十時間を要していた転記・印字・合冊を自動化する。

### 1.2 v1.0 → v2.0 の主な変更点

| 項目 | v1.0（旧） | v2.0（現行） |
|---|---|---|
| 内訳書 PDF 化 | LibreOffice Headless 経由 Excel→PDF | **ReportLab Platypus による動的生成** |
| MS Office | COM 呼び出し必須（Word/Excel） | **不要**（xls→xlsx 変換でのみ任意利用） |
| 内訳書ページ制御 | 不可（LibreOffice 任せ） | `repeatRows` による自動ページ送り |
| チェックボックス表示 | PDF 化で消失（既知バグ） | PyMuPDF スタンプで確実に描画 |
| 設定の集約 | 各モジュール散在 | **`config.py` に一元化** |
| マジックナンバー | 多数 | 全て `config` 参照化 |
| エラー検出 | 業者ループ途中で発覚 | **Pre-flight 検証で即座に返却** |

---

## 2. アーキテクチャ（v2.0 ハイブリッド方式）

### 2.1 処理フロー

```
Excel 依頼書 (.xlsx)
    │
    ▼ (0) Pre-flight 検証
    │    openpyxl.load_workbook(read_only=True) で破損／保護を検知
    │
    ▼ (1) データ抽出 (extractor.extract_data)
    │    キーワード検索でベンダー情報・工事情報・金額・日付・シート割当を取得
    │
    ▼ (2) 業者ループ: 各ベンダーに対して以下 6 書類を生成
    │
    │   ┌──────────────────────────────────────────────────┐
    │   │ Route A: PDF スタンプ (PyMuPDF)                   │
    │   │ └─ 注文書 / 注文請書 / 新旧対照表 / 契約条件書  │
    │   │    固定レイアウトテンプレート + 座標マップで印字 │
    │   ├──────────────────────────────────────────────────┤
    │   │ Route B: 動的 PDF 生成 (ReportLab Platypus)       │
    │   │ └─ 内訳書                                          │
    │   │    extract_nairaku_data → NairakuData              │
    │   │    → build_nairaku_pdf (自動ページ送り)            │
    │   ├──────────────────────────────────────────────────┤
    │   │ Route C: PDF マージ (pypdf)                       │
    │   │ └─ 約款 PDF を含めて注文書セット・注文請書セットに合冊 │
    │   └──────────────────────────────────────────────────┘
    ▼
BatchResult (dataclass) を返却
```

### 2.2 ルート選択の指針

| ルート | 使用条件 | ライブラリ |
|---|---|---|
| **A** | レイアウトが固定で、印字位置が事前に分かる書類 | PyMuPDF (`fitz`) |
| **B** | 行数が可変で、自動ページ送りが必要な書類 | ReportLab Platypus |
| **C** | 複数 PDF を 1 ファイルに合冊する処理 | pypdf |

新規書類を追加する際は、この 3 択でルートを選ぶ。原則 A が最も低コスト、B は「行数可変」の要件がある場合のみ採用する。

---

## 3. 環境構築

### 3.1 必須ソフトウェア

- Python 3.10 以上
- `requirements.txt` の依存パッケージ一式（主要: `openpyxl`, `PyMuPDF`, `pypdf`, `reportlab`）
- 日本語 TrueType/TrueTypeCollection フォント（デフォルトは MS Mincho）

**不要になったもの**（v2.0 より）:
- LibreOffice
- Microsoft Word / Excel（xls→xlsx 変換時のみ例外）

### 3.2 セットアップ

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3.3 フォントの確認

`config.FONT_FALLBACKS` の先頭から順に存在チェックが走る。最低限 1 つが存在すれば動作する（最終フォールバックとして ReportLab 内蔵の HeiseiMin-W3 CID フォントもあるが、品質低下のため警告ログが出る）。

`FONT_FALLBACKS` は v2.3.18 から `os.name` で OS 別に分岐しており、Linux では Noto CJK / IPAex / IPA フォントを優先的に探索する。

### 3.4 Linux (さくらVPS 等) でのセットアップ

Linux サーバへの導入は `deploy_linux/install_linux.sh` に集約されている（Ubuntu 22.04+ / Debian 12+ 想定）。

**手順:**

```bash
# 1. リポジトリを任意の場所に配置（例: 一時的に /tmp/factoryskills へ）
git clone <repo-url> /tmp/factoryskills
cd /tmp/factoryskills

# 2. インストールスクリプトを root で実行
sudo bash deploy_linux/install_linux.sh
```

`install_linux.sh` が自動で行う処理:

| ステップ | 内容 |
|---|---|
| 1 | `apt update` + Python 3 / venv / build-essential / git 等の基礎パッケージ |
| 2 | 日本語フォント導入（`fonts-noto-cjk` / `fonts-ipafont` / `fonts-ipaexfont`） |
| 3 | アプリ専用ユーザー `factoryskills` の作成（`/usr/sbin/nologin`） |
| 4 | `/opt/factoryskills` への配置と所有権設定 |
| 5 | venv 作成と `requirements.txt` インストール（`pywin32` は環境マーカーで自動 skip） |
| 6 | `playwright install-deps chromium` + `playwright install chromium` |
| 7 | `.env.linux.template` から `/opt/factoryskills/.env` を生成、`SECRET_KEY` を自動生成（48 byte） |
| 8 | `factoryskills.service` を `/etc/systemd/system/` に配置、`enable` + `start` |

**動作確認:**

```bash
sudo systemctl status factoryskills          # 起動状態
sudo journalctl -u factoryskills -f          # ログ追跡
curl http://localhost:8000/health            # "OK" が返れば成功
```

**さくらVPS 特有の注意:**

- パケットフィルタ（コントロールパネル）で TCP 8000 番（または nginx 経由の 80/443）を開放する
- 既定 OS が Ubuntu 22.04 LTS / 24.04 LTS のいずれでも動作する（Debian 系互換）

### 3.5 OS 互換性マトリクス（v2.3.18 時点）

| 機能 | Windows | Linux (Ubuntu/Debian) | macOS |
|---|---|---|---|
| FastAPI / uvicorn 起動 | ✅ | ✅ | ✅ |
| Playwright で PDF 生成 (Route B) | ✅ | ✅ | ✅ |
| PyMuPDF で PDF スタンプ (Route A) | ✅ | ✅ | ✅ |
| pypdf で合冊 (Route C) | ✅ | ✅ | ✅ |
| `.xls → .xlsx` 自動変換（Excel COM） | ✅ | ❌ Linux で `.xls` 入力時は明示エラー | ❌ |
| Tkinter GUI (`start_local.py`) | ✅ | ⚠️ DISPLAY 必要 | ⚠️ |
| `start_system.bat` 等のバッチ | ✅ | ❌（systemd で代替） | ❌ |
| フォルダ自動展開 (`os.startfile`) | ✅ | ⚠️ `xdg-open` フォールバック | ✅ `open` フォールバック |

---

## 4. ディレクトリ構成

```
skills/order_docs/
├── __init__.py
├── config.py                       # ★ 設定の単一真実源
│
│   ── 抽出パイプライン（Phase E + F でモジュール分割済み）──
├── extractor.py                    # extract_data オーケストレータのみ (413 行)
├── extractor_utils.py              # 純粋ユーティリティ + Cell ラッパー + _banner
├── irai_scan_utils.py              # 依頼書キーワード走査 + 金額抽出
├── nairaku_text_utils.py           # 内訳書テキスト判定 + シート種別分類
├── nairaku_extraction.py           # 内訳書抽出本体 + 補助ヘルパー (extract_nairaku_data)
├── sheet_assignment_utils.py       # 業者×シートのマッチングと割り当て
├── terms_extraction.py             # 契約条件書 (TermsData) 構造化抽出 + 契約変更回数
├── vml_utils.py                    # 契約条件書テキスト + VML チェックボックス (extract_joken_text_data)
│
│   ── データモデル ──
├── nairaku_models.py               # 内訳書データクラス
├── terms_models.py                 # 契約条件書データクラス
│
│   ── PDF 生成 / スタンプ / 合冊 ──
├── html_pdf_builder.py             # Route B: HTML テンプレート + Playwright PDF 生成
├── pdf_stamper.py                  # Route A: PyMuPDF スタンプ
├── pdf_merger.py                   # Route C: pypdf 合冊
│
│   ── オーケストレータ / 周辺 ──
├── generate_order_docs.py          # オーケストレータ（エントリポイント）
├── generate_previews.py            # テンプレート確認用プレビュー生成
├── gui_confirm.py                  # CLI 用 Tkinter 確認ダイアログ
├── office_com.py                   # xls→xlsx 変換用（COM、残置）
├── com_lock.py                     # COM 呼び出し排他制御
└── templates/                      # PDF テンプレート + 元 Excel + HTML
```

**抽出モジュール分割の経緯（Phase E + F、2026-05-04 〜 05）**:
`extractor.py` は **3,126 行** に肥大化していたため、責務単位で 7 つの専用モジュールに切り出した（B-3 の完了タスク）。
最終的に `extractor.py` は `extract_data` オーケストレータ **413 行**まで縮小（−87%）し、Phase F で re-export ハブの役割も解消。
**外部からは各モジュールに直接 import するのが正規ルート**である（`from skills.order_docs.extractor import _normalize` のような旧式 import は機能しない）。

---

## 5. モジュール構成と責務

### 5.1 `generate_order_docs.py` — オーケストレータ

**エントリポイント**:
- `generate_for_vendor(vendor_data, excel_path, output_dir, ...) -> OrderDocumentSet`: 1 業者分
- `generate_from_excel(excel_path, output_dir, ...) -> BatchResult`: Excel 一括

**主な責務**:
1. Pre-flight 検証（v2.0 新規）
2. 業者ループ制御
3. 6 書類の生成をルート A/B/C に振り分ける
4. 合冊と結果集約

### 5.2 抽出パイプライン（8 モジュール構成）

Phase E + F の分割により、抽出ロジックは責務単位で 8 モジュールに分散している。
公開 API（外部から直接 import するエントリポイント）は **太字** で示す。

| モジュール | 役割 | 主な公開 API |
|---|---|---|
| `extractor.py` | メインオーケストレータ。依頼書スキャン・業者ループ・シート割り当てを統合する | **`extract_data(excel_path) -> list[dict]`** |
| `extractor_utils.py` | 純粋ユーティリティ + Worksheet/Cell の薄ラッパー | `_normalize`, `_clean_amount`, `_format_wareki`, `_parse_contract_date`, `_parse_kouki`, `_serial_to_datetime`, `_cell_str`, `_cell_raw`, `_safe_int`, `_safe_float`, `_banner` |
| `irai_scan_utils.py` | 依頼書（メインシート）のキーワード走査と金額抽出 | `_detect_vendor_base_cols`, `_scan_keyword_rows`, `_find_sub_keyword_row`, `_extract_kingaku_direct`, `KINGAKU_KEYWORD_VARIANTS` |
| `nairaku_text_utils.py` | 内訳書のテキスト判定 + シート種別分類 | `_classify_sheet_type`, `_is_subtotal_text`, `_is_footer_terminator`, `_is_composite_footer_text`, `_count_indent`, `_normalize_for_footer`, `_NAIRAKU_*_KEYWORDS` |
| `nairaku_extraction.py` | 内訳書抽出本体 + 補助ヘルパー（結合キャッシュ・列スパン解決・ページパディング） | **`extract_nairaku_data(excel_path, sheet) -> NairakuData`**, `apply_nairaku_page_padding`, `_build_merged_cells_cache`, `_resolve_col_spans`, `MergedSpanCache` |
| `sheet_assignment_utils.py` | 業者×シートの貪欲マッチング・排他割り当て + 契約条件書共通データ抽出 | `build_sheet_assignment`, `_match_score`, `_extract_from_first_joken`, `_SHEET_SCAN_MAX_ROW`/`_COL` |
| `terms_extraction.py` | 契約条件書 (`TermsData`) の構造化抽出 + 依頼書からの契約変更回数スキャン | **`extract_terms_data(excel_path, vendor_index) -> TermsData`**, `scan_contract_change_count`, `_is_checked` |
| `vml_utils.py` | 契約条件書シートの全テキストデータ抽出 + VML チェックボックス解析 | **`extract_joken_text_data(excel_path, sheet) -> dict`**, `_extract_checkboxes_from_vml`, `_find_keyword_row`, `_read_adjacent_value` |

**設計上の重要ポイント**:
- `extractor.py` は **オーケストレータ専用**。`extract_data` の中で他モジュールを呼び出すだけで、自前のヘルパは持たない（Phase F で re-export を完全廃止）。
- `extract_data` が呼び出すのは `extractor_utils`（`_cell_str`, `_cell_raw`, `_parse_contract_date`, `_parse_kouki`, `_safe_int`）/ `irai_scan_utils`（4 関数すべて）/ `sheet_assignment_utils`（`build_sheet_assignment`, `_extract_from_first_joken`）の **計 11 シンボル**のみ。
- `_SHEET_SCAN_MAX_ROW` / `_COL` 等のモジュール定数は `config.EXCEL_SCAN_LIMITS` から取得する（変更箇所は `sheet_assignment_utils.py`）。
- 外部から内部ヘルパが必要な場合（テスト等）は **必ず所有モジュールから直接 import する**。`from skills.order_docs.extractor import _normalize` のような旧スタイルは Phase F 後は機能しない。

### 5.3 `nairaku_models.py` — データモデル

| データクラス | 役割 |
|---|---|
| `NairakuHeaderInfo` | 1 ページ目ヘッダー情報（工事名・契約日・元請／下請情報） |
| `NairakuRow` | 1 行分のデータ（row_type: `category` / `item` / `subtotal` / `note`） |
| `NairakuData` | 上記 2 つ + rows + has_henkou を集約 |

`NairakuRow.to_table_row(has_henkou: bool) -> list[str]` が extractor と builder の境界 API。`has_henkou=True` なら 15 要素、`False` なら 9 要素を返す。

### 5.4 `nairaku_builder.py` — Route B の実装

ReportLab Platypus ベース。すべてのレイアウト値は `config.NAIRAKU_LAYOUT` から取得する。

主要関数:
- `_register_fonts(font_path=None) -> str`: `config.FONT_FALLBACKS` を順に試行
- `_compute_col_widths(has_henkou, available_width_pt) -> list[float]`: mm → pt 変換
- `_build_col_header_rows(has_henkou, styles)`: config の SPAN 定義から grid 構築
- `_build_col_header_spans(has_henkou)`: SPAN コマンドのみ抽出
- `build_nairaku_pdf(data, output_path, font_path=None, vendor_data=None)`: エントリポイント

### 5.5 `pdf_stamper.py` — Route A の実装

PyMuPDF で `config.PDF_STAMP_MAP` の座標に vendor_data の値を描画する。フォント・サイズ・揃えはマップ項目ごとに指定可能。

### 5.6 `pdf_merger.py` — Route C の実装

`pypdf.PdfWriter` のラッパ。`config.MERGE_ORDER_CHUMONSHO` / `MERGE_ORDER_UKESHO` で定義された順序で合冊する。

---

## 6. 外部呼び出しインターフェース

### 6.1 `BatchResult` の構造

```python
@dataclass
class BatchResult:
    excel_path: str
    koji_kenmei: str | None = None
    total_vendors: int = 0
    success_count: int = 0
    results: list[OrderDocumentSet] = field(default_factory=list)
    error: str | None = None  # Pre-flight 失敗時もここに入る
```

### 6.2 呼び出し例

```python
from pathlib import Path
from skills.order_docs.generate_order_docs import generate_from_excel

batch = generate_from_excel(
    excel_path=Path("path/to/依頼書.xlsx"),
    output_dir=Path("path/to/output"),
)

if batch.error:
    # Pre-flight 失敗 / 全体エラー
    print(f"ERROR: {batch.error}")
else:
    for vendor_result in batch.results:
        if not vendor_result.success:
            print(f"失敗: {vendor_result.vendor_company} — {vendor_result.error}")
```

---

## 7. 設定の集約 — `config.py` ガイド

**v2.0 の設計思想**: コード中の「動作を決める数値・パス・キーワード」は **全て `config.py` に集約**する。変更時はコードを読まずに config だけを編集すれば済む状態を目指す。

### 7.1 主要な設定ブロック

| 変数／辞書 | 役割 | 典型的な編集理由 |
|---|---|---|
| `FOLDER_*` | 入出力ディレクトリ | 環境移行 |
| `EXCEL_MAP` | 依頼書セルマッピング | 依頼書フォーマット変更 |
| `PDF_TEMPLATES` | PDF テンプレートファイル名 | テンプレート差替え |
| `PDF_STAMP_MAP` | スタンプ座標 (Route A) | レイアウト微調整 |
| `JOKEN_CHECKBOX_CONFIG` | 契約条件書チェックボックス座標 | 契約条件書テンプレ更新 |
| `MERGE_ORDER_CHUMONSHO/UKESHO` | 合冊順序 | 新書類追加時 |
| `NAIRAKU_LAYOUT` | 内訳書レイアウト (Route B) | 列幅／フォント変更 |
| `FONT_FALLBACKS` | 日本語フォント探索パス | サーバ移行 |
| `EXCEL_SCAN_LIMITS` | Excel スキャン上限 | 依頼書の行数増加時 |

### 7.2 `NAIRAKU_LAYOUT` の詳細

```python
NAIRAKU_LAYOUT = {
    # 用紙
    "page_size": "A4_LANDSCAPE",
    "margin_top_mm" / "margin_bottom_mm" / ...: float,

    # 列幅（mm）
    "col_widths_full_mm": [22.0, 34.0, ...],  # has_henkou=True, 15列
    "col_widths_base_mm": [30.0, 48.0, ...],  # has_henkou=False, 9列

    # フォントサイズ（pt）
    "font_size_title" / "font_size_header" / ...: int,

    # 行高さ（mm）
    "row_height_col_head_mm" / ...: float,

    # 罫線
    "grid_line_width" / "outer_line_width" / "subtotal_line_width": float,

    # 列ヘッダー構造（v2.0 新規）
    "col_header_rows": 3,
    "font_name": "NairakuMincho",
    "col_header_spans_full": [
        {"text": "工種", "start": (0, 0), "end": (0, 2)},
        ...
    ],
    "col_header_row2_full": ["", "", ..., "数量", "単価", "金額", ...],
    "col_header_spans_base": [...],
    "col_header_row2_base": [...],
}
```

**SPAN 定義の読み方**:
- `start` / `end` は (col, row) の 0-origin タプル
- `text` は SPAN 範囲の **左上セル（start）にのみ**配置される
- 末端行（row 2）は `col_header_row2_*` で指定する
- ReportLab の仕様上、SPAN 範囲内の他のセルのテキストは表示されないため、必ず start セルに text を書く

### 7.3 `FONT_FALLBACKS` の動作

`nairaku_builder._register_fonts()` が以下の順に試行する:

1. 明示引数 `font_path`（指定があれば最優先）
2. `config.FONT_FALLBACKS` を先頭から順に（ファイル存在チェック付き）
3. 全て失敗 → ReportLab 内蔵 `HeiseiMin-W3` CID フォント（`error` ログを出力）

### 7.4 `EXCEL_SCAN_LIMITS` の動作

依頼書のスキャン範囲を決定する辞書。依頼書の行数が大幅に増えた／減った場合はここを調整する。

| キー | 用途 |
|---|---|
| `sheet_scan_max_row` / `sheet_scan_max_col` | シート種別判定のためのセル値スキャン範囲 |
| `nairaku_max_row_fallback` | `ws.max_row` が取得できないときの上限 |
| `nairaku_header_scan_rows` | 内訳書ヘッダー情報の走査行数 |
| `nairaku_keyword_scan_rows` | 工事名等キーワード検索範囲 |
| `vendor_header_max_scan_row` | ベンダー基準列検出時の上限 |

---

## 8. エラーハンドリング仕様

### 8.1 Pre-flight 検証（v2.0 新規）

`generate_from_excel()` の冒頭で以下を検証し、失敗時は業者ループに入らず即座に `BatchResult(error=...)` を返す:

1. ファイル存在チェック
2. `openpyxl.load_workbook(read_only=True)` による試験読み込み
   - 破損ファイル
   - パスワード保護
   - 非対応フォーマット

### 8.2 業者単位の失敗隔離

1 業者の処理が失敗しても残りの業者は継続処理される。`BatchResult.results` の各 `OrderDocumentSet.success` で個別に判断可能。

### 8.3 書類単位の失敗

- 各書類は `DocumentResult(doc_type, success, error)` として記録される
- `_stamp_joken_checkboxes()` 失敗は契約条件書全体の失敗として伝搬（v2.0 で修正）
- 合冊失敗は `OrderDocumentSet.error` に格納される

### 8.4 ログ出力

- `logger.info`: 正常系進捗
- `logger.warning`: 軽微な異常（フォールバック発動等）
- `logger.error`: 重大な異常（処理は継続）
- `logger.exception`: 例外トレースバック付きエラー

---

## 9. ビジネスロジック・特殊仕様

### 9.1 内訳書の行種別判定

`extractor._detect_row_type()` は以下の優先順位で row_type を決定する:

1. **note**: data_end より下の行
2. **subtotal**: キーワード（合計・小計等）または A-C セル結合
3. **category**: A 列のみに値あり・BCD/FGH 列が空または 0
4. **item**: 上記いずれにも該当しない通常行

### 9.2 変更契約の検出

`has_henkou` フラグは依頼書の「変更契約金額」セルが存在するかで決定。True のとき内訳書は 15 列、False のとき 9 列レイアウトになる。

### 9.3 インデント検出

`_count_indent()` は A 列の**生セル値**（`_cell_raw`）から先頭の全角空白（U+3000）をカウントする。`.strip()` 前の値を使う必要があるため、カテゴリ／アイテム判定の前に必ず `a_indent` を保存する。

### 9.4 ゼロ値抑制

`NairakuRow.to_table_row()` は subtotal／note 行のとき 0.0 を空文字に変換する（Excel の SUM 結果が 0 になる見出し行が「0」と印字されるのを防ぐ）。

### 9.5 契約日のフォールバック

内訳書シートの B4 が `=注文書作成依頼書!E13` のように他シート参照になっている場合、`data_only=True` でも解決できないことがある。`build_nairaku_pdf(vendor_data=...)` に `vendor_data` を渡すと、`contract_year/month/day` から日付を再構成するフォールバックが働く。

---

## 10. 拡張ガイド — 未来の開発者向け

> このセクションは、将来このシステムを拡張する開発者（Claude 自身を含む）向けのハウツーです。必ず読んでから変更を加えてください。

### 10.1 新しい書類を追加する

**Step 1**: ルートを決める（§2.2 参照）
- 固定レイアウト → Route A
- 行数可変 → Route B
- 既存 PDF をそのまま合冊 → Route C のみ

**Step 2 (Route A の場合)**:
1. `config.PDF_TEMPLATES["新書類名"] = "template.pdf"` を追加
2. `config.PDF_STAMP_MAP["新書類名"] = [...]` にスタンプ座標を追加
3. `generate_for_vendor()` に `_stamp_document("新書類名", ...)` 呼び出しを追加
4. `config.MERGE_ORDER_CHUMONSHO/UKESHO` に論理名を追加

**Step 2 (Route B の場合)**:
1. `nairaku_models.py` を参考に dataclass を新規作成
2. `extractor.py` に抽出関数を追加
3. `nairaku_builder.py` をコピーして builder を新規作成
4. `config.py` にレイアウト辞書を追加（`NAIRAKU_LAYOUT` を参考に）
5. `generate_for_vendor()` に呼び出しを追加

**Step 3**: テスト Excel で `generate_from_excel()` を呼び出し、`success_count == total_vendors` を確認。

### 10.2 既存書類のレイアウトを微調整する

- **スタンプ座標（Route A）**: `config.PDF_STAMP_MAP` を編集。コード変更不要。
- **列幅（Route B）**: `config.NAIRAKU_LAYOUT["col_widths_*_mm"]` を編集。合計が用紙幅と一致しなくてもプロポーショナルスケーリングで収まる。
- **フォントサイズ**: `config.NAIRAKU_LAYOUT["font_size_*"]` を編集。
- **列ヘッダーラベル**: `col_header_spans_*` の `text` フィールド、または `col_header_row2_*` のリストを編集。

### 10.3 依頼書のフォーマットが変わった場合

1. `config.EXCEL_MAP` のセル座標を更新
2. 行数が大幅に変わった場合は `config.EXCEL_SCAN_LIMITS` を調整
3. ベンダー検出ロジックに変更が必要な場合のみ `extractor.py` に手を入れる

### 10.4 サーバー移行時（Windows / Linux 共通）

**Windows Server 移行:**
1. `config.FOLDER_*` のパスを新環境に合わせて修正
2. `config.FONT_FALLBACKS` の先頭に新環境で利用可能な日本語フォントを追加
3. `generate_from_excel()` を 1 件だけ実行し、Pre-flight で即座にエラーが出ないことを確認
4. 起動: `start_system.bat` または NSSM 経由 `Factoryskills` サービス

**Linux (さくらVPS 等) 移行:**
1. `deploy_linux/install_linux.sh` を root で実行（§3.4 参照）
2. `/opt/factoryskills/.env` を確認・編集（`SECRET_KEY` は自動生成済、`HOST=0.0.0.0` を確認）
3. `sudo systemctl status factoryskills` で起動確認
4. `curl http://localhost:8000/health` が `OK` を返せば疎通 OK
5. 起動: `sudo systemctl start factoryskills` / 停止: `... stop` / 再起動: `... restart`
6. ログ: `sudo journalctl -u factoryskills -f`

**Linux で COM を使う処理（`.xls → .xlsx` 変換）が呼ばれた場合:**
- `skills.order_docs.office_com._load_win32com()` が `RuntimeError("Office COM 機能は Windows 専用です。")` を投げる
- ユーザーは事前に `.xlsx` 形式に変換してアップロードする運用が必要

### 10.5 やってはいけないこと

- `_deprecated/` 配下から import しない（LibreOffice 依存が復活するため）
- `nairaku_builder.py` に `canvas.drawString()` を直接呼ぶコードを追加しない（Platypus の流儀を壊す）
- ハードコードされた数値・パス・キーワードを追加しない（必ず `config.py` 経由にする）
- `to_table_row()` のシグネチャを変更しない（extractor / builder 境界の API）

---

## 11. 履歴と廃止機能

### 11.1 v2.0 (2026-04) の主な変更

- LibreOffice 経由の内訳書 PDF 化を廃止 → ReportLab Platypus 動的生成に移行
- Word COM を利用した新旧対照表生成を廃止 → PDF スタンプ方式に統一
- Pre-flight 検証を追加
- マジックナンバーを `config.py` に完全集約（`FONT_FALLBACKS`, `EXCEL_SCAN_LIMITS`, `NAIRAKU_LAYOUT` 拡張）
- `libreoffice_converter.py` を `_deprecated/` に退避

### 11.1.1 Phase E + F: extractor.py のモジュール分割 (2026-05-04 〜 05)

**動機**: `extractor.py` が **3,126 行**に肥大化していた（BACKLOG.md B-3）。

**結果**:
- 抽出ロジックを責務別に 7 つの新モジュールへ分散し、`extractor.py` は `extract_data` オーケストレータ **413 行**まで縮小（−87%）。
- Phase F で re-export ハブを完全廃止。外部呼び出し元（`generate_order_docs.py`, `web_app/routers/order_docs.py`, `tests/` 配下 5 ファイル）を所有モジュールへの **直接 import** に切り替え。
- 全期間を通じて `pytest -m "not slow"` → **165 passed**（回帰ゼロ）。
- 詳細は §4 ディレクトリ構成 / §5.2 抽出パイプライン参照。
- 関連コミット: `a2119ec` 〜 `a72af21`（10 コミット）。

**外部 API 互換性**:
- 公開 API（`extract_data`, `extract_nairaku_data`, `extract_terms_data`, `extract_joken_text_data`）は **すべて引き続き利用可能**。
- ただし import 元は変わった。古い `from skills.order_docs.extractor import extract_nairaku_data` のような書き方は機能しない（`extract_data` 以外は所有モジュールから取得する）。

### 11.2 廃止済みモジュール

| モジュール | 廃止理由 |
|---|---|
| `libreoffice_converter.py` | チェックボックスが PDF 化で消失。環境依存。`_deprecated/` に退避。 |
| `office_com.convert_excel_sheets_to_pdf()` | Windows Server に Office が無い構成で動作しない。 |

### 11.3 次回メジャーリリースで削除予定

- `_deprecated/libreoffice_converter.py`（git 履歴から参照可能になったら削除）
- `office_com.py` 内の PDF 変換系関数（xls→xlsx 変換部分は残す）

---

**文書終わり**
