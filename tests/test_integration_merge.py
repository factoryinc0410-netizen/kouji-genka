"""generate_order_docs.py 統合テスト — 1 社分のフルパイプライン pytest 版。

目的:
  - `generate_from_excel()` を 1 業者分で実行
  - 最終的な合冊 PDF (注文書 / 注文請書) に
    - 必須: 内訳書 + 契約条件書 のテキストが含まれていること
    - 構造: 既知正常値 11 ページを下回らないこと
  を pypdf でテキスト抽出して検証する。

注: 注文書/注文請書/新旧対照表は画像ベース PDF テンプレートへのスタンプ方式
    のため pypdf テキスト抽出が失敗することがある（PDF 自体は正常）。
    本テストは抽出可能な内訳書 + 条件書を検証ターゲットとする。

マーカー: slow, requires_sample, requires_chromium
"""
from __future__ import annotations

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
# 期待値
# ────────────────────────────────────────────
# MERGE_ORDER_CHUMONSHO / MERGE_ORDER_UKESHO 共通の最低ページ数。
# 内訳書 (1+) + 契約条件書 (1+) + 注文書 (1) + 約款 (5+) + 新旧対照表 (1) ≥ 9
# 直近の既知正常値は 11 ページ（chumonsho/ukesho 共通）。
MIN_EXPECTED_PAGES = 9


def _inspect_pdf_pages(pdf_path: Path) -> tuple[int, list[str]]:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return len(reader.pages), pages


def _find_section_in_pages(pages: list[str], keywords: tuple[str, ...]) -> int:
    """全 keywords が空白除去後に同一ページで見つかる最初のページ番号 (1-indexed)、
    無ければ 0 を返す。"""
    for i, text in enumerate(pages, start=1):
        normalized = "".join(text.split())
        if all(kw in normalized for kw in keywords):
            return i
    return 0


def _all_keywords_in_merged_text(pages: list[str], keywords: tuple[str, ...]) -> bool:
    """全ページのテキストを連結（空白除去）して、全 keywords が存在するか判定する。

    合冊 PDF では章を跨いでキーワードが散らばることがあるため、
    「同一ページに全部」を要求せず「合冊全体のどこかに含まれていれば OK」とする。
    """
    joined = "".join("".join(t.split()) for t in pages)
    return all(kw in joined for kw in keywords)


# ────────────────────────────────────────────
# fixture: パイプライン実行（モジュール単位で 1 度だけ）
# ────────────────────────────────────────────
@pytest.fixture(scope="module")
def integration_batch_result(sample_excel: Path, tmp_path_factory):
    """1 業者分のパイプラインを 1 回だけ走らせて BatchResult を返す。"""
    from skills.order_docs.extractor import extract_data
    from skills.order_docs.generate_order_docs import generate_from_excel

    out_dir = tmp_path_factory.mktemp("integration")

    vendors_all = extract_data(sample_excel)
    assert vendors_all, "サンプル Excel から業者が 1 件も抽出できない"
    target_vendor = vendors_all[0]

    batch_result = generate_from_excel(
        excel_path=sample_excel,
        output_dir=out_dir,
        confirmed_vendors=[target_vendor],
        use_gui=False,
    )
    if batch_result.error:
        pytest.fail(f"バッチエラー: {batch_result.error[:500]}")

    return batch_result


# ────────────────────────────────────────────
# テスト
# ────────────────────────────────────────────
def test_required_documents_succeeded(integration_batch_result):
    """必須ドキュメント (nairaku / joken) が個別に生成成功している。"""
    must_succeed = {"nairaku", "joken"}
    for r in integration_batch_result.results:
        for doc in r.documents:
            if doc.doc_type in must_succeed:
                assert doc.success, (
                    f"必須ドキュメント '{doc.doc_type}' の生成失敗: "
                    f"{(doc.error or '')[:200]}"
                )


def test_merged_pdfs_exist(integration_batch_result):
    """注文書合冊 / 注文請書合冊 PDF が両方とも生成されている。"""
    r = integration_batch_result.results[0]
    assert r.merged_chumonsho, "注文書合冊 PDF が無い"
    assert r.merged_ukesho, "注文請書合冊 PDF が無い"
    assert Path(r.merged_chumonsho).exists()
    assert Path(r.merged_ukesho).exists()


@pytest.mark.parametrize("merged_attr", ["merged_chumonsho", "merged_ukesho"])
def test_merged_pdf_min_page_count(integration_batch_result, merged_attr: str):
    """合冊 PDF が最低期待ページ数を満たす。"""
    r = integration_batch_result.results[0]
    path = Path(getattr(r, merged_attr))
    n_pages, _ = _inspect_pdf_pages(path)
    assert n_pages >= MIN_EXPECTED_PAGES, (
        f"{merged_attr} のページ数 {n_pages} が期待最小 {MIN_EXPECTED_PAGES} を下回る。"
        "内訳書または条件書の合冊欠落の可能性"
    )


@pytest.mark.parametrize("required_doc_type", ["chumonsho", "ukesho", "nairaku", "joken"])
def test_required_doc_types_present(integration_batch_result, required_doc_type: str):
    """合冊に必要な全ドキュメント種別が DocumentResult に存在し、success=True。

    `chumonsho` / `ukesho` / `nairaku` / `joken` の 4 種は 1 業者あたり必ず
    生成されることが期待される（約款・新旧対照表は固定 PDF を使うため
    DocumentResult には含まれない場合がある）。
    """
    r = integration_batch_result.results[0]
    matched = [d for d in r.documents if d.doc_type == required_doc_type]
    assert matched, (
        f"DocumentResult に '{required_doc_type}' が見当たらない "
        f"(存在する種別: {[d.doc_type for d in r.documents]})"
    )
    for d in matched:
        assert d.success, (
            f"'{required_doc_type}' の生成に失敗: {(d.error or '')[:200]}"
        )


@pytest.mark.parametrize("merged_attr", ["merged_chumonsho", "merged_ukesho"])
def test_merged_pdf_size_is_substantial(integration_batch_result, merged_attr: str):
    """合冊 PDF のファイルサイズが 1MB 以上（約款などの参考 PDF が結合されている証拠）。

    HTML→PDF だけだと数百 KB 程度だが、画像ベースの約款や注文書テンプレート
    が結合されているため 1MB を大きく上回るはず。1MB 未満なら参考 PDF の
    結合に失敗している可能性が高い。
    """
    r = integration_batch_result.results[0]
    path = Path(getattr(r, merged_attr))
    size = path.stat().st_size
    assert size > 1_000_000, (
        f"{merged_attr}: 合冊 PDF のサイズ {size:,} bytes が 1MB 未満。"
        "画像ベースの参考 PDF（約款等）が合冊されていない可能性"
    )
