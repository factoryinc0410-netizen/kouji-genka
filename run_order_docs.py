"""
注文書作成 CLI スクリプト（GUI 確認画面付き）

使い方:
    python run_order_docs.py <Excelファイルパス>
    python run_order_docs.py <Excelファイルパス> --output-dir <出力先>
    python run_order_docs.py <Excelファイルパス> --no-gui        # GUI なし（従来互換）
"""
import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注文書作成依頼書（Excel）から注文書PDFを一括生成する",
    )
    parser.add_argument(
        "excel_path",
        type=Path,
        help="注文書作成依頼書の Excel ファイルパス (.xlsx / .xls)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="PDF の出力先ディレクトリ（省略時は自動決定）",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="GUI 確認画面を表示せずにバッチ実行する",
    )
    args = parser.parse_args()

    # ── ログ設定 ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── 入力ファイル存在チェック ──
    excel_path: Path = args.excel_path.resolve()
    if not excel_path.exists():
        print(f"エラー: ファイルが見つかりません: {excel_path}", file=sys.stderr)
        sys.exit(1)

    # ── 実行 ──
    from skills.order_docs.generate_order_docs import generate_from_excel

    result = generate_from_excel(
        excel_path=excel_path,
        output_dir=args.output_dir,
        use_gui=not args.no_gui,
    )

    # ── 結果表示 ──
    if result.error:
        print(f"\n処理エラー: {result.error}", file=sys.stderr)
        sys.exit(1)

    print(f"\n処理完了: {result.success_count}/{result.total_vendors} 社成功")
    for r in result.results:
        status = "OK" if r.success else "NG"
        print(f"  [{status}] {r.vendor_company}")
        if r.merged_chumonsho:
            print(f"        注文書  : {r.merged_chumonsho}")
        if r.merged_ukesho:
            print(f"        注文請書: {r.merged_ukesho}")
        if r.error:
            print(f"        エラー  : {r.error[:100]}")


if __name__ == "__main__":
    main()
