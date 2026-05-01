# _deprecated_reportlab — ReportLab 旧実装アーカイブ

このフォルダは HTML/CSS + Playwright ベースの新アーキテクチャへ移行した際に
退避した ReportLab ベースの旧実装を保管するためのアーカイブです。
本番コードからは参照されておらず、依存関係 (`reportlab` パッケージ) も
`requirements.txt` / `skill.yaml` から削除済みです。

## 退避日
2026-04-18

## 退避の背景
- 本プロジェクトは「Excel 依頼書 → PDF 書類セット一括生成」システム。
- 内訳書 (下請代金内訳書) と契約条件書は当初 ReportLab Platypus で動的生成していたが、
  以下の理由で **HTML/CSS テンプレート + Playwright (headless Chromium) PDF 化** に移行:
  - レイアウト調整が CSS のほうが直感的で保守性が高い
  - `<thead>` の自動繰返し・`page-break-inside: avoid` 等のブラウザ印刷仕様を活用できる
  - Excel → LibreOffice Headless 方式で発生していた環境依存・ゾンビプロセス問題を解消

## 退避されたファイル一覧

| ファイル | 役割 | 新アーキテクチャの代替 |
|---------|------|----------------------|
| `base_builder.py` | ReportLab 帳票生成の共通基盤 (ABC クラス) | `html_pdf_builder.HtmlPdfBuilder` |
| `nairaku_builder.py` | 内訳書 PDF 動的生成 (`BaseBuilder` 継承) | `html_pdf_builder.build_breakdown_pdf()` + `html_templates/breakdown.html` |
| `terms_builder.py` | 契約条件書 PDF 動的生成 (`BaseBuilder` 継承) | `html_pdf_builder.build_condition_pdf()` + `html_templates/condition.html` |
| `_test_nairaku_synthetic.py` | 内訳書レイアウトの合成データテスト | `test_breakdown_html.py` (プロジェクトルート) |
| `_test_nairaku_synthetic.pdf` | 上記テストの出力例 | — |
| `test_generate.py` | 全業者 ReportLab 経由一括生成テスト | `test_breakdown_html.py` + `test_condition_html.py` |

## 復元する場合
1. 本フォルダ内の `.py` を親フォルダ (`skills/order_docs/`) にコピーバック
2. `requirements.txt` に `reportlab>=4.0.0` を復活
3. `generate_order_docs.py` の import を `html_pdf_builder.build_breakdown_pdf` から
   `nairaku_builder.build_nairaku_pdf` に戻す
4. `.venv/Scripts/python -m pip install reportlab` でパッケージをインストール

## 参考
- 新アーキテクチャの仕様は `SYSTEM_SPEC.md` を参照 (移行後の版)
- テンプレートは `skills/order_docs/html_templates/` 配下
