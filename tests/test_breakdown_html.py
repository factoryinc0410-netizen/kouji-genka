"""Jinja2 + Playwright による「内訳書」一括生成テスト。

目的:
  - 全5業者の内訳書 (下請代金内訳書) を HTML テンプレート経由で PDF 化
  - 複数ページにまたがる業者 (業者3/5) で thead (表ヘッダ) が
    2 ページ目以降の先頭に自動繰返しされているかを pypdf で検証

実行（プロジェクトルートから）:
    .venv\\Scripts\\python.exe tests\\test_breakdown_html.py
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


def inspect_pdf(pdf_path: Path) -> tuple[int, list[str]]:
    """PDF のページ数と各ページ冒頭のテキストを抽出する。

    Returns
    -------
    (page_count, first_line_per_page)
        first_line_per_page[i] = i ページ目の冒頭～最初の改行までのテキスト
    """
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    heads: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        # 先頭から改行 2 回分を抽出（ヘッダ領域のスニペット）
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        heads.append(" / ".join(lines[:6]))
    return len(reader.pages), heads


async def main_async() -> int:
    from skills.order_docs.extractor import extract_data, extract_nairaku_data
    from skills.order_docs.html_pdf_builder import (
        HtmlPdfBuilder, _patch_nairaku_contract_date,
    )

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
        return 1

    out_dir = here / "_test_html_pdf"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Excel   : {excel_path}")
    print(f"[INFO] 出力先   : {out_dir}")
    print(f"[INFO] テンプレ : breakdown.html")
    print()

    # ── 業者一覧 ──
    vendors = extract_data(excel_path)
    print(f"[INFO] 業者数: {len(vendors)}")
    print()

    # ── 各業者の NairakuData を抽出 → バッチ PDF 化 ──
    builder = HtmlPdfBuilder("breakdown.html")

    jobs: list[tuple[dict, Path]] = []
    skipped: list[str] = []
    row_counts: dict[str, int] = {}   # PDF 名 → NairakuData.rows の要素数

    for idx, vendor in enumerate(vendors, start=1):
        company = vendor.get("vendor_company") or f"業者{idx}"
        nairaku_sheet = vendor.get("nairaku_sheet")
        if not nairaku_sheet:
            skipped.append(f"業者{idx} ({company}): 内訳書シート未割当")
            continue
        try:
            nd = extract_nairaku_data(excel_path, nairaku_sheet)
        except Exception as e:
            skipped.append(f"業者{idx} ({company}): {type(e).__name__}: {e}")
            continue

        # 契約年月日の補完（vendor_data → header）
        _patch_nairaku_contract_date(nd, vendor)

        out_pdf = out_dir / f"内訳書_業者{idx}_{company}.pdf"
        jobs.append(({"data": nd}, out_pdf))
        row_counts[out_pdf.name] = len(nd.rows)
        print(f"[QUEUE] 業者{idx}: {company}  (rows={len(nd.rows)}, "
              f"has_henkou={nd.has_henkou})  → {out_pdf.name}")

    if not jobs:
        print("[ERROR] 生成対象 0 件。")
        return 2
    print()

    # ── Playwright 一括生成 ──
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
    print()

    # ── 結果サマリ ──
    print("=" * 70)
    print("■ 生成ファイル一覧 + ページ数検証")
    print("=" * 70)

    multi_page_pdfs: list[Path] = []
    for p in sorted(out_dir.glob("内訳書_*.pdf")):
        try:
            size_kb = p.stat().st_size / 1024
            n_pages, heads = inspect_pdf(p)
            rows = row_counts.get(p.name, 0)
            marker = "  [複数P]" if n_pages > 1 else ""
            print(f"  {p.name}")
            print(f"    サイズ: {size_kb:,.1f} KB  |  行数: {rows}  |  "
                  f"ページ数: {n_pages}{marker}")
            if n_pages > 1:
                multi_page_pdfs.append(p)
                for i, snippet in enumerate(heads, start=1):
                    short = (snippet[:90] + "…") if len(snippet) > 90 else snippet
                    print(f"    [P{i}] {short}")
            print()
        except Exception as e:
            print(f"  {p.name}: 検証失敗 ({e})")
            print()

    # ── thead 繰返し検証 ──
    print("=" * 70)
    print("■ <thead> 自動繰返し検証")
    print("=" * 70)
    if not multi_page_pdfs:
        print("  2 ページ以上にまたがった PDF がありません。")
        print("  (全業者が 1 ページに収まった可能性があります)")
    else:
        header_keywords = ("工種", "種別", "細別", "元請", "下請", "備考")
        for p in multi_page_pdfs:
            n_pages, heads = inspect_pdf(p)
            print(f"\n  {p.name}: {n_pages} ページ")
            all_ok = True
            for i, snippet in enumerate(heads, start=1):
                # pypdf は文字間に半角スペースを挿入することがあるため、
                # 全ての空白を除去してから包含判定する
                normalized = "".join(snippet.split())
                matched = [kw for kw in header_keywords if kw in normalized]
                ok = len(matched) >= 3  # 3 語以上一致すれば thead と判定
                mark = "✅" if ok else "❌"
                matched_str = "/".join(matched) if matched else "(該当なし)"
                print(f"    [P{i}] {mark} ヘッダ語検出: {matched_str}")
                if i >= 2 and not ok:
                    all_ok = False
            verdict = "✅ thead 自動繰返し成功" if all_ok else "❌ 2ページ目以降にヘッダ欠落の疑い"
            print(f"    → {verdict}")

    if skipped:
        print()
        print(f"スキップ: {len(skipped)} 件")
        for s in skipped:
            print(f"  {s}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
