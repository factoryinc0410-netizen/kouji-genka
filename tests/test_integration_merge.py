"""generate_order_docs.py 統合テスト — 1社分のフルパイプライン検証

目的:
  - `generate_from_excel()` を 1 業者分のデータで実行
  - 最終的な合冊 PDF に「契約条件書 (joken)」と「内訳書 (nairaku)」が
    正しく結合されているかを pypdf で検証
  - HTML/Playwright 新方式が旧 PDF スタンプ方式と同等に統合されているか確認

実行（プロジェクトルートから）:
    .venv\\Scripts\\python.exe tests\\test_integration_merge.py
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import sys
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


def inspect_pdf_pages(pdf_path: Path) -> tuple[int, list[str]]:
    """PDF の全ページテキストを抽出する。"""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return len(reader.pages), pages


def find_section_in_pages(pages: list[str], keywords: tuple[str, ...]) -> int:
    """keywords が空白無視で全て含まれる最初のページ番号を返す (1-indexed)、なければ 0。"""
    for i, text in enumerate(pages, start=1):
        normalized = "".join(text.split())
        if all(kw in normalized for kw in keywords):
            return i
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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
        print("[ERROR] Excel が見つかりません")
        return 1

    # ── 1 社分のみに絞る ──
    from skills.order_docs.extractor import extract_data
    vendors_all = extract_data(excel_path)
    if not vendors_all:
        print("[ERROR] 業者が 0 件")
        return 1

    vendor_idx = 1  # 業者1 を対象（joken/nairaku 両方がある前提）
    target_vendor = vendors_all[vendor_idx - 1]
    company = target_vendor.get("vendor_company", "(不明)")
    print(f"[INFO] テスト対象: 業者{vendor_idx} = {company}")
    print(f"[INFO]   joken_sheet   = {target_vendor.get('joken_sheet')}")
    print(f"[INFO]   nairaku_sheet = {target_vendor.get('nairaku_sheet')}")
    print()

    # ── 出力先をクリア ──
    out_dir = here / "_test_integration"
    if out_dir.exists():
        shutil.rmtree(str(out_dir), ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── パイプライン実行（confirmed_vendors で 1 件だけ渡す） ──
    from skills.order_docs.generate_order_docs import generate_from_excel

    print("[INFO] generate_from_excel() 実行中...")
    batch_result = generate_from_excel(
        excel_path=excel_path,
        output_dir=out_dir,
        confirmed_vendors=[target_vendor],   # 1 業者のみ
        use_gui=False,
    )

    if batch_result.error:
        print(f"[FAIL] バッチエラー: {batch_result.error[:500]}")
        return 2

    print(
        f"[INFO] 完了: {batch_result.success_count}/{batch_result.total_vendors} 社成功"
    )
    print()

    # ── DocumentResult レベルの事前ガード ──
    # 合冊 PDF ファイルを開く前に、必須ドキュメント (nairaku/joken) が
    # 個別に生成失敗していないかを確認する。ここで失敗している場合、
    # 合冊 PDF に含まれないのは必然なので早期に具体的なエラーを返す。
    _MUST_SUCCEED_DOCS = {"nairaku", "joken"}
    for r0 in batch_result.results:
        for doc in r0.documents:
            if doc.doc_type in _MUST_SUCCEED_DOCS and not doc.success:
                print(
                    f"[FAIL] 必須ドキュメント '{doc.doc_type}' の生成に失敗: "
                    f"{(doc.error or '').splitlines()[0][:200] if doc.error else 'no error msg'}"
                )
                return 6

    # ── 結果詳細 ──
    print("=" * 70)
    print("■ 業者ごとの DocumentResult")
    print("=" * 70)
    for r in batch_result.results:
        print(f"  業者: {r.vendor_company}  success={r.success}")
        for doc in r.documents:
            mark = "✅" if doc.success else "❌"
            extra = f"  → {Path(doc.output_path).name}" if doc.output_path else ""
            err_short = f"  (err: {doc.error[:80]})" if doc.error else ""
            print(f"    {mark} {doc.doc_type:10s}{extra}{err_short}")
        if r.merged_chumonsho:
            print(f"    📘 注文書合冊 : {Path(r.merged_chumonsho).name}")
        if r.merged_ukesho:
            print(f"    📗 注文請書合冊: {Path(r.merged_ukesho).name}")
        print()

    # ── 合冊 PDF の内容検証 ──
    print("=" * 70)
    print("■ 合冊 PDF の中身検証（pypdf でテキスト抽出）")
    print("=" * 70)

    r = batch_result.results[0]
    if not r.merged_chumonsho:
        print("[FAIL] 注文書合冊 PDF が生成されていません")
        return 3
    if not r.merged_ukesho:
        print("[FAIL] 注文請書合冊 PDF が生成されていません")
        return 3

    # ── 期待合冊構成 ──
    # MERGE_ORDER_CHUMONSHO / MERGE_ORDER_UKESHO 共通:
    #   1. 注文書/請書  (1ページ, スタンプ PDF)
    #   2. 約款        (複数ページ, 参考値: 5-6ページ)
    #   3. 新旧対照表   (1ページ, スタンプ PDF)
    #   4. 内訳書      (≧1ページ, HTML→PDF)
    #   5. 契約条件書   (≧2ページ, HTML→PDF)
    #
    # 総計は業者データに依存するが、全コンポーネントが正常に生成・合冊
    # されていれば 9 ページ以上を下回ることはない。
    # 直近の既知正常値は 11 ページ (chumonsho/ukesho 共通)。
    MIN_EXPECTED_PAGES = 9

    merged_path = Path(r.merged_chumonsho)
    n_pages, pages = inspect_pdf_pages(merged_path)
    print(f"  ファイル: {merged_path.name}")
    print(f"  サイズ  : {merged_path.stat().st_size / 1024:,.1f} KB")
    print(f"  ページ数: {n_pages}")
    print()

    # ── 注文請書合冊も同時検証（両方が揃ってこそ合格） ──
    uke_path = Path(r.merged_ukesho)
    uke_pages_count, _ = inspect_pdf_pages(uke_path)
    print(f"  ファイル: {uke_path.name}  ({uke_pages_count} ページ)")
    print()

    # ── 厳格なページ数ガード ──
    if n_pages < MIN_EXPECTED_PAGES:
        print("=" * 70)
        print(f"❌ ページ数不足: 注文書合冊 {n_pages} ページ "
              f"(最低 {MIN_EXPECTED_PAGES} ページ必要)")
        print("   → 内訳書 (nairaku) または契約条件書 (joken) が"
              "合冊プロセスから欠落している可能性が高い")
        print("=" * 70)
        return 5
    if uke_pages_count < MIN_EXPECTED_PAGES:
        print("=" * 70)
        print(f"❌ ページ数不足: 注文請書合冊 {uke_pages_count} ページ "
              f"(最低 {MIN_EXPECTED_PAGES} ページ必要)")
        print("=" * 70)
        return 5

    # ── 各ページ冒頭を表示（診断用） ──
    print("  ── 各ページ冒頭スニペット ──")
    for i, text in enumerate(pages, start=1):
        normalized = "".join(text.split())
        snippet = normalized[:60] if normalized else "(テキスト抽出不可 — 画像ベースPDFの可能性)"
        print(f"    [P{i:2d}] {snippet}")
    print()

    # ── 各セクションのキーワード検出 ──
    # 注: 注文書・注文請書・新旧対照表は画像ベースPDFテンプレートへのスタンプ方式のため、
    # pypdf での表層テキスト抽出に失敗することがある（警告: pypdf._cmap invalid hex）。
    # この場合でもファイル自体は正常で、Adobe Reader 等での閲覧には問題ない。
    # 本テストは「本質的な検証対象 = 新 HTML 方式の条件書・内訳書が合冊されているか」
    # を最優先に判定する。
    sections_to_find = [
        ("注文書",       ("注文書",),                           False),  # 参考検出
        ("約款",         ("下請契約約款",),                       False),  # 参考検出
        ("新旧対照表",   ("新旧対照表",),                        False),  # 参考検出
        ("内訳書",       ("工種", "細別", "備考"),                True),   # ★必須
        ("契約条件書",   ("契約条件書", "費用負担"),              True),   # ★必須
    ]

    critical_ok = True
    reference_results: list[str] = []
    for name, kws, is_critical in sections_to_find:
        page_num = find_section_in_pages(pages, kws)
        marker = "★必須" if is_critical else "    "
        if page_num > 0:
            print(f"  ✅ {marker} {name:10s} → P{page_num} で検出 (keywords={'/'.join(kws)})")
        else:
            status = "❌" if is_critical else "⚠ "
            print(f"  {status} {marker} {name:10s} → 検出失敗 (keywords={'/'.join(kws)})")
            if is_critical:
                critical_ok = False
            else:
                reference_results.append(
                    f"{name} は pypdf テキスト抽出不可（画像ベース PDF の典型症状、PDF 自体は正常）"
                )

    print()
    if reference_results:
        print("  参考: 以下は PDF 品質には影響しない参考検出の失敗:")
        for msg in reference_results:
            print(f"    - {msg}")
        print()

    if critical_ok:
        print("=" * 70)
        print("✅ 統合テスト成功 — 契約条件書と内訳書が合冊 PDF に正しく含まれています")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("❌ 統合テスト失敗 — 契約条件書または内訳書が合冊 PDF から欠落しています")
        print("=" * 70)
        return 4


if __name__ == "__main__":
    sys.exit(main())
