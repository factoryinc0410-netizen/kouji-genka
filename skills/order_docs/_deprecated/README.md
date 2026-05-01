# _deprecated/

このフォルダには、現行のパイプラインからは使用されなくなったモジュールを退避している。
履歴参照・ロールバック用途でのみ残しているため、**新規コードから import してはならない**。

## 収録モジュール

### `libreoffice_converter.py`
- **役割（旧）**: LibreOffice Headless で Excel シートを PDF に変換していた（旧 Route B）。
- **廃止理由**:
  - Excel のフォームコントロール（チェックボックス）が PDF 化時に消失する。
  - LibreOffice のランタイム環境依存が強く、Windows Server 展開時の障害点になっていた。
  - 内訳書のように行数可変のレイアウトでページ制御ができなかった。
- **代替**: `nairaku_builder.py`（ReportLab Platypus による動的 PDF 生成）。

## 削除の目安
次回メジャーリリースで完全削除する。削除前に git 履歴で十分参照できることを確認すること。
