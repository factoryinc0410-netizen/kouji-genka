"""Jinja2 + Playwright による「契約条件書」最小テスト。

目的:
  - ReportLab 実装とは独立に、HTML/CSS 経由で A4 縦 1 ページの
    契約条件書 PDF が生成できることを検証する。
  - 業者1〜5 全員分の PDF を `_test_html_pdf/` に出力。
  - 併せて同名 .html も保存し、ブラウザで直接レイアウトを確認できるようにする。

実行（プロジェクトルートから）:
    .venv\\Scripts\\python.exe tests\\test_condition_html.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import traceback
from pathlib import Path

# ── プロジェクトルートを sys.path に追加（直接実行時の保険） ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass


async def main_async() -> int:
    from skills.order_docs.extractor import extract_data, extract_terms_data
    from skills.order_docs.html_pdf_builder import HtmlPdfBuilder

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
        print("[ERROR] Excel ファイルが見つかりません。")
        for c in excel_candidates:
            print(f"  - {c}")
        return 1

    out_dir = here / "_test_html_pdf"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Excel   : {excel_path}")
    print(f"[INFO] 出力先   : {out_dir}")
    print(f"[INFO] テンプレ : condition.html")
    print()

    # ── 業者一覧 ──
    vendors = extract_data(excel_path)
    print(f"[INFO] 業者数: {len(vendors)}")
    print()

    # ── 各業者の TermsData をレンダ → 1 回のブラウザ起動で一括 PDF 化 ──
    builder = HtmlPdfBuilder("condition.html")

    jobs: list[tuple[dict, Path]] = []
    skipped: list[str] = []

    for idx, vendor in enumerate(vendors, start=1):
        company = vendor.get("vendor_company") or f"業者{idx}"
        try:
            td = extract_terms_data(excel_path, idx)
        except Exception as e:
            skipped.append(f"業者{idx} ({company}): {type(e).__name__}: {e}")
            continue
        if not td.sections:
            skipped.append(f"業者{idx} ({company}): セクション 0 件")
            continue
        out_pdf = out_dir / f"契約条件書_業者{idx}_{company}.pdf"
        jobs.append(({"data": td}, out_pdf))
        print(f"[QUEUE] 業者{idx}: {company}  → {out_pdf.name}")

    if not jobs:
        print("[ERROR] 生成対象 0 件。終了。")
        return 2
    print()

    # ── Playwright 一括生成 (ブラウザ 1 起動, 並列 3) ──
    try:
        print("[INFO] Playwright 起動中...")
        generated = await builder.build_pdfs_batch(
            jobs, save_html=True, concurrency=3,
        )
        print(f"[INFO] 生成成功: {len(generated)} ファイル")
    except Exception:
        traceback.print_exc()
        print("[FAIL] PDF 生成中に例外発生")
        return 3

    # ── 結果サマリ ──
    print()
    print("=" * 60)
    if skipped:
        print(f"スキップ: {len(skipped)} 件")
        for s in skipped:
            print(f"  {s}")
        print()
    print(f"生成ファイル一覧（{out_dir}）:")
    for p in sorted(out_dir.iterdir()):
        try:
            size_kb = p.stat().st_size / 1024
            print(f"  {p.name}  ({size_kb:,.1f} KB)")
        except Exception:
            print(f"  {p.name}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
