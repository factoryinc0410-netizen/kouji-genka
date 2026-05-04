"""Jinja2 + Playwright による「契約条件書」一括生成の pytest 版。

目的:
  - HTML/CSS 経由で A4 縦の契約条件書 PDF が業者全員分生成できることを検証
  - 各 PDF が 1 ページ以上、契約条件書としての特徴的キーワードを含むこと

マーカー:
  - slow             : Playwright 起動 + PDF 実生成のため重い
  - requires_sample  : サンプル Excel が必要
  - requires_chromium: Playwright Chromium が必要
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


def _read_all_text_normalized(pdf_path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            pass
    return "".join("".join(c.split()) for c in chunks)


@pytest.fixture(scope="module")
def generated_condition_pdfs(sample_excel: Path, tmp_path_factory) -> dict[str, Path]:
    """全業者の契約条件書を 1 度だけ生成して PDF パスマップを返す。"""
    from skills.order_docs.extractor import extract_data, extract_terms_data
    from skills.order_docs.html_pdf_builder import HtmlPdfBuilder

    out_dir = tmp_path_factory.mktemp("condition_html")
    vendors = extract_data(sample_excel)
    builder = HtmlPdfBuilder("condition.html")

    jobs: list[tuple[dict, Path]] = []
    label_for_path: dict[Path, str] = {}

    for idx, vendor in enumerate(vendors, start=1):
        company = vendor.get("vendor_company") or f"業者{idx}"
        try:
            td = extract_terms_data(sample_excel, idx)
        except Exception as e:
            pytest.fail(f"業者{idx} ({company}) の条件書抽出失敗: {e}")
        if not td.sections:
            continue
        out_pdf = out_dir / f"契約条件書_業者{idx}_{company}.pdf"
        jobs.append(({"data": td}, out_pdf))
        label_for_path[out_pdf] = f"業者{idx}_{company}"

    assert jobs, "契約条件書の生成対象が 0 件"

    asyncio.run(builder.build_pdfs_batch(jobs, save_html=False, concurrency=3))

    return {label_for_path[p]: p for _, p in jobs}


def test_all_condition_pdfs_generated(generated_condition_pdfs: dict[str, Path]):
    """全業者の契約条件書 PDF がディスクに生成されている。"""
    assert len(generated_condition_pdfs) >= 1
    for label, p in generated_condition_pdfs.items():
        assert p.exists(), f"{label}: PDF が無い"
        assert p.stat().st_size > 10_000, (
            f"{label}: PDF サイズ < 10KB（生成失敗の可能性）"
        )


def test_condition_pdfs_contain_title_keyword(generated_condition_pdfs: dict[str, Path]):
    """契約条件書 PDF のテキストに「契約条件書」のタイトル文字列が含まれる。"""
    for label, p in generated_condition_pdfs.items():
        text = _read_all_text_normalized(p)
        assert "契約条件書" in text, (
            f"{label}: 抽出テキストに「契約条件書」キーワードが見つからない"
        )


def test_condition_pdfs_have_pages(generated_condition_pdfs: dict[str, Path]):
    """契約条件書 PDF が 1 ページ以上あること。"""
    from pypdf import PdfReader
    for label, p in generated_condition_pdfs.items():
        reader = PdfReader(str(p))
        assert len(reader.pages) >= 1, f"{label}: ページ数 0"
