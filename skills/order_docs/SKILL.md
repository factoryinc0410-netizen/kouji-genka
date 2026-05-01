---
name: order-docs
description: 建設工事の Excel 依頼書から下請業者向け注文書類（注文書・注文請書・内訳書・契約条件書・新旧対照表・約款）を一括生成し、業者ごとに注文書セット／注文請書セットとして合冊する。ユーザーが「依頼書から注文書を作って」「この Excel から下請契約書類一式を生成して」「内訳書と契約条件書をまとめて PDF 化して」などと依頼したときに使う。
---

# order-docs スキル

建設工事の注文書自動作成システム。Excel 依頼書 1 ファイルを入力として、複数業者分の契約書類 PDF を一括生成する。

## いつ使うか

- Excel 依頼書から注文書セットを生成したいとき
- 1 業者あたり 6 種類（注文書 / 注文請書 / 内訳書 / 契約条件書 / 新旧対照表 / 約款）の契約書類をまとめて作りたいとき
- 複数業者を一括処理したいとき

## いつ使わないか

- 既存 PDF への軽微な編集のみ
- 注文書以外の書類（請求書・見積書など）
- Excel の閲覧・データ確認のみで PDF 出力が不要なとき

## 呼び出し方

```python
from pathlib import Path
from skills.order_docs.generate_order_docs import generate_from_excel

batch = generate_from_excel(
    excel_path=Path("path/to/注文書作成依頼書.xlsx"),
    output_dir=Path("path/to/output"),  # 省略可（config.FOLDER_DONE 配下に自動生成）
)

# 結果確認
if batch.error:
    print(f"全体エラー: {batch.error}")
else:
    print(f"成功: {batch.success_count}/{batch.total_vendors}")
    for vr in batch.results:
        print(f"  {vr.vendor_company}: {'OK' if vr.success else vr.error}")
```

## アーキテクチャ

3 つのルートをハイブリッドで使い分ける:

| ルート | 用途 | ライブラリ |
|---|---|---|
| A | 固定レイアウト書類へのスタンプ | PyMuPDF |
| B | 行数可変の内訳書を動的生成 | ReportLab Platypus |
| C | 複数 PDF の合冊 | pypdf |

## 設定の集約

すべての設定は `skills/order_docs/config.py` に集約されている。動作を変えたいときはまず config を編集する。主要ブロック:

- `FOLDER_*` — 入出力ディレクトリ
- `PDF_STAMP_MAP` — スタンプ座標（Route A）
- `NAIRAKU_LAYOUT` — 内訳書レイアウト（Route B）
- `FONT_FALLBACKS` — 日本語フォント探索パス
- `EXCEL_SCAN_LIMITS` — Excel スキャン上限
- `MERGE_ORDER_*` — 合冊順序

## 詳細

- フル仕様書: [`SYSTEM_SPEC.md`](../../SYSTEM_SPEC.md)
- デプロイ手順: [`DEPLOY_GUIDE.md`](../../DEPLOY_GUIDE.md)
- 廃止モジュール: [`_deprecated/README.md`](./_deprecated/README.md)
- 新規帳票追加手順: SYSTEM_SPEC.md §10「拡張ガイド」
