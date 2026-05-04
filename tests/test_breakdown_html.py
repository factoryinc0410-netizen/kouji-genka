"""Jinja2 + Playwright による「内訳書」一括生成の pytest 版。

目的:
  - 全業者の内訳書 (下請代金内訳書) を HTML テンプレート経由で PDF 化
  - 複数ページにまたがる業者で thead (表ヘッダ) が
    2 ページ目以降の先頭にも繰返されているかを pypdf で検証

マーカー:
  - slow             : Playwright 起動 + PDF 実生成のため重い
  - requires_sample  : サンプル Excel が必要
  - requires_chromium: Playwright Chromium が必要

旧版 (def main()) は pytest fixture を使う関数群に置換した。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

logging.getLogger("pypdf").setLevel(logging.ERROR)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.requires_sample,
    pytest.mark.requires_chromium,
]


# ────────────────────────────────────────────
# ヘルパ
# ────────────────────────────────────────────
def _inspect_pdf(pdf_path: Path) -> tuple[int, list[str]]:
    """PDF のページ数と各ページの正規化済みテキストを返す。"""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    heads: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        heads.append(txt)
    return len(reader.pages), heads


# ────────────────────────────────────────────
# fixture: 内訳書バッチ生成（テスト関数間で共有）
# ────────────────────────────────────────────
@pytest.fixture(scope="module")
def generated_breakdown_pdfs(sample_excel: Path, tmp_path_factory) -> dict[str, Path]:
    """全業者の内訳書を 1 度だけ生成して PDF パスマップを返す。

    Returns
    -------
    dict[str, Path]
        {company_label: pdf_path}
    """
    from skills.order_docs.extractor import extract_data, extract_nairaku_data
    from skills.order_docs.html_pdf_builder import (
        HtmlPdfBuilder, _patch_nairaku_contract_date,
    )

    out_dir = tmp_path_factory.mktemp("breakdown_html")
    vendors = extract_data(sample_excel)
    builder = HtmlPdfBuilder("breakdown.html")

    jobs: list[tuple[dict, Path]] = []
    label_for_path: dict[Path, str] = {}

    for idx, vendor in enumerate(vendors, start=1):
        company = vendor.get("vendor_company") or f"業者{idx}"
        nairaku_sheet = vendor.get("nairaku_sheet")
        if not nairaku_sheet:
            continue
        try:
            nd = extract_nairaku_data(sample_excel, nairaku_sheet)
        except Exception as e:
            pytest.fail(f"業者{idx} ({company}) の内訳書抽出失敗: {e}")
        _patch_nairaku_contract_date(nd, vendor)
        out_pdf = out_dir / f"内訳書_業者{idx}_{company}.pdf"
        jobs.append(({"data": nd}, out_pdf))
        label_for_path[out_pdf] = f"業者{idx}_{company}"

    assert jobs, "生成対象の業者が 0 件"

    asyncio.run(builder.build_pdfs_batch(jobs, save_html=False, concurrency=3))

    return {label_for_path[p]: p for _, p in jobs}


# ────────────────────────────────────────────
# テスト
# ────────────────────────────────────────────
def test_all_breakdown_pdfs_generated(generated_breakdown_pdfs: dict[str, Path]):
    """全業者分の内訳書 PDF がディスクに生成されていること。"""
    assert len(generated_breakdown_pdfs) >= 1
    for label, p in generated_breakdown_pdfs.items():
        assert p.exists(), f"{label}: PDF が存在しない"
        assert p.stat().st_size > 10_000, (
            f"{label}: PDF サイズが 10KB 未満。生成失敗の可能性"
        )


def test_breakdown_pdfs_have_at_least_one_page(generated_breakdown_pdfs: dict[str, Path]):
    """各 PDF が 1 ページ以上ある + ヘッダ語が冒頭ページに含まれる。"""
    header_keywords = ("工種", "種別", "細別", "備考")
    for label, p in generated_breakdown_pdfs.items():
        n_pages, page_texts = _inspect_pdf(p)
        assert n_pages >= 1, f"{label}: ページ数 0"
        first_page_norm = "".join(page_texts[0].split())
        matched = [kw for kw in header_keywords if kw in first_page_norm]
        assert len(matched) >= 3, (
            f"{label}: 1 ページ目にヘッダ語が 3 語未満しか検出されない "
            f"(matched={matched})"
        )


def test_multi_page_breakdown_repeats_thead(generated_breakdown_pdfs: dict[str, Path]):
    """複数ページにまたがる内訳書では 2 ページ目以降にも thead が繰返される。

    複数ページの PDF が 1 件もない場合は xfail/xpass ではなく skip する
    （データ依存のため正常）。
    """
    header_keywords = ("工種", "種別", "細別", "元請", "下請", "備考")

    multi_page_found = False
    for label, p in generated_breakdown_pdfs.items():
        n_pages, page_texts = _inspect_pdf(p)
        if n_pages < 2:
            continue
        multi_page_found = True
        for i, text in enumerate(page_texts, start=1):
            normalized = "".join(text.split())
            matched = [kw for kw in header_keywords if kw in normalized]
            assert len(matched) >= 3, (
                f"{label}: P{i} にヘッダ語が 3 語未満。"
                f"thead 自動繰返しが破綻 (matched={matched})"
            )

    if not multi_page_found:
        pytest.skip("複数ページにまたがった内訳書 PDF が無いため検証不可")
