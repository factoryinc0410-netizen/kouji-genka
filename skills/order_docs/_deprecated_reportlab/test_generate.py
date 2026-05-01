"""全5業者の「内訳書」＋「契約条件書」PDF 一括生成テスト。

実行例:
    .venv\\Scripts\\python.exe test_generate.py
"""
from __future__ import annotations

import io
import os
import sys
import traceback
from pathlib import Path

# ── Windows cp932 対策: 標準出力を UTF-8 化 ──
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass


def main() -> int:
    from skills.order_docs.extractor import (
        extract_data,
        extract_nairaku_data,
        extract_terms_data,
        scan_contract_change_count,
    )
    from skills.order_docs.nairaku_builder import build_nairaku_pdf
    from skills.order_docs.terms_builder import build_terms_pdf

    # ── 入出力パス ──
    here = Path(__file__).resolve().parent
    excel_candidates = [
        here / "注文書作成依頼（サンプルデータ版）.xlsx",
        Path(r"C:\Users\factory\OneDrive\Desktop\注文書作成依頼（サンプルデータ版）.xlsx"),
    ]
    excel_path: Path | None = None
    for cand in excel_candidates:
        if cand.exists():
            excel_path = cand
            break
    if excel_path is None:
        print("[ERROR] Excel ファイルが見つかりません。以下を試行しました:")
        for c in excel_candidates:
            print(f"  - {c}")
        return 1

    out_dir = here / "_test_pdf"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Excel: {excel_path}")
    print(f"[INFO] 出力先: {out_dir}")
    print()

    # ── 契約変更回数の一括スキャン ──
    try:
        change_counts = scan_contract_change_count(excel_path)
        print(f"[INFO] 契約変更回数: {change_counts}")
    except Exception as e:
        print(f"[WARN] 契約変更回数スキャン失敗: {e}")
        change_counts = {}
    print()

    # ── 業者データ一括抽出 ──
    vendors = extract_data(excel_path)
    print(f"[INFO] 業者数: {len(vendors)}")
    print()

    generated: list[Path] = []
    errors: list[tuple[str, str]] = []

    for idx, vendor in enumerate(vendors, start=1):
        company = vendor.get("vendor_company") or f"業者{idx}"
        nairaku_sheet = vendor.get("nairaku_sheet")
        print(f"=== 業者 {idx}: {company} ===")

        # ── 内訳書 PDF ──
        if nairaku_sheet:
            try:
                nd = extract_nairaku_data(excel_path, nairaku_sheet)
                out_pdf = out_dir / f"内訳書_業者{idx}_{company}.pdf"
                build_nairaku_pdf(nd, out_pdf, vendor_data=vendor)
                generated.append(out_pdf)
                print(f"  [OK] 内訳書 → {out_pdf.name}  ({len(nd.rows)} 行)")
            except Exception as e:
                errors.append((f"内訳書 業者{idx}", f"{type(e).__name__}: {e}"))
                traceback.print_exc()
                print(f"  [FAIL] 内訳書: {type(e).__name__}: {e}")
        else:
            print("  [SKIP] 内訳書シート未割当")

        # ── 契約条件書 PDF ──
        try:
            td = extract_terms_data(excel_path, idx)
            if not td.sections:
                print(f"  [SKIP] 契約条件書: セクション 0 件（シート未検出？）")
            else:
                out_pdf = out_dir / f"契約条件書_業者{idx}_{company}.pdf"
                build_terms_pdf(td, out_pdf)
                generated.append(out_pdf)
                print(f"  [OK] 契約条件書 → {out_pdf.name}  ({len(td.sections)} セクション)")
        except Exception as e:
            errors.append((f"契約条件書 業者{idx}", f"{type(e).__name__}: {e}"))
            traceback.print_exc()
            print(f"  [FAIL] 契約条件書: {type(e).__name__}: {e}")

        print()

    # ── 結果サマリ ──
    print("=" * 60)
    print(f"生成成功: {len(generated)} ファイル")
    print(f"エラー  : {len(errors)} 件")
    if errors:
        print("\n--- エラー詳細 ---")
        for where, msg in errors:
            print(f"  {where}: {msg}")
    print()
    print("--- 生成ファイル一覧 ---")
    for p in generated:
        try:
            size_kb = p.stat().st_size / 1024
            print(f"  {p.name}  ({size_kb:,.1f} KB)")
        except Exception:
            print(f"  {p.name}")

    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
