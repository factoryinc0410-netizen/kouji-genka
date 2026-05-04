"""既存 PDF 出力をスナップショットとして固定する回帰テスト。

`tests/_test_html_pdf/` と `tests/_test_integration/` に保存されている
PDF を「現在の出力 = 正」と見なし、ページ数・テキストキーワード等の
構造的な指標を期待値として焼き付ける。

これらの PDF が再生成されて期待値が変わった場合は、本ファイルの
`SNAPSHOT_PAGES` を更新すること（明示的な意思決定としてレビュー対象に
する目的）。

全テストは PDF が存在しなければ skip する（CI 環境で _test_*/ が
配置されていないケースに対応）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

# pypdf は extra テキスト抽出時に大量の DEBUG/WARNING を出すため抑制
import logging
logging.getLogger("pypdf").setLevel(logging.ERROR)


# ────────────────────────────────────────────
# 期待値スナップショット
# ────────────────────────────────────────────
# (filename, expected_page_count, expected_keywords)
# expected_keywords は「PDF 全テキストを空白除去後に検索して全て含まれること」を要求する。
SNAPSHOT_HTML_PDF: list[tuple[str, int, tuple[str, ...]]] = [
    # 内訳書: 業者 1〜4 は 1 ページ、業者 5 のみ 2 ページ（複数ページ thead 検証対象）
    ("内訳書_業者1_株式会社長崎西部建設.pdf", 1, ("工種", "細別", "備考")),
    ("内訳書_業者2_株式会社諫早港湾建設.pdf", 1, ("工種", "細別", "備考")),
    ("内訳書_業者3_有限会社西海マリン工業.pdf", 1, ("工種", "細別", "備考")),
    ("内訳書_業者4_株式会社大村土木.pdf", 1, ("工種", "細別", "備考")),
    ("内訳書_業者5_有限会社島原海洋開発.pdf", 2, ("工種", "細別", "備考")),
    # 契約条件書: 全業者 1 ページ
    ("契約条件書_業者1_株式会社長崎西部建設.pdf", 1, ("契約条件書",)),
    ("契約条件書_業者2_株式会社佐世保海洋工業.pdf", 1, ("契約条件書",)),
    ("契約条件書_業者3_有限会社九州マリン資材.pdf", 1, ("契約条件書",)),
    ("契約条件書_業者4_株式会社壱岐土木工業.pdf", 1, ("契約条件書",)),
    ("契約条件書_業者5_西海建設工業株式会社.pdf", 1, ("契約条件書",)),
]

# 統合テスト：注文書/注文請書 合冊 PDF
# - 期待ページ数: 11（注文書 1P + 約款 5P + 新旧対照表 1P + 内訳書 1P + 条件書 1P + バインド構成）
# - キーワード: 内訳書由来の列ヘッダ + 契約条件書 が含まれること
SNAPSHOT_INTEGRATION: list[tuple[str, int, tuple[str, ...]]] = [
    ("注文書_株式会社長崎西部建設.pdf", 11, ("工種", "細別", "備考", "契約条件書")),
    ("注文請書_株式会社長崎西部建設.pdf", 11, ("工種", "細別", "備考", "契約条件書")),
]


def _read_all_text(path: Path) -> str:
    """PDF 全ページのテキストを連結して返す（空白除去後）。"""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            pass
    return "".join("".join(c.split()) for c in chunks)


def _page_count(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


# ────────────────────────────────────────────
# HTML→PDF 出力の検証
# ────────────────────────────────────────────
@pytest.mark.parametrize("filename,expected_pages,expected_keywords", SNAPSHOT_HTML_PDF)
def test_html_pdf_snapshot(
    pdf_html_dir: Path,
    filename: str,
    expected_pages: int,
    expected_keywords: tuple[str, ...],
):
    pdf_path = pdf_html_dir / filename
    if not pdf_path.exists():
        pytest.skip(f"スナップショット PDF が存在しません: {pdf_path}")

    # ページ数の固定
    assert _page_count(pdf_path) == expected_pages, (
        f"{filename}: ページ数が期待値 {expected_pages} と異なる"
    )

    # ファイルサイズが極端に小さくないこと（破損検出）
    assert pdf_path.stat().st_size > 10_000, (
        f"{filename}: ファイルサイズが 10KB 未満。生成失敗の可能性"
    )

    # テキスト内容のキーワード包含
    text = _read_all_text(pdf_path)
    for kw in expected_keywords:
        assert kw in text, (
            f"{filename}: 期待キーワード '{kw}' が抽出テキストから検出されない"
        )


def test_html_pdf_breakdown_multi_page_has_thead_repeated(pdf_html_dir: Path):
    """複数ページの内訳書では 2 ページ目以降にもヘッダ語が繰返されること。

    現状で 2 ページ以上ある内訳書は「業者5_有限会社島原海洋開発」のみ。
    pypdf でテキスト抽出 → 空白除去 → ヘッダ語 3 語以上含むかで判定。
    """
    pdf_path = pdf_html_dir / "内訳書_業者5_有限会社島原海洋開発.pdf"
    if not pdf_path.exists():
        pytest.skip(f"対象 PDF 不在: {pdf_path}")

    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) >= 2

    header_keywords = ("工種", "種別", "細別", "元請", "下請", "備考")
    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        normalized = "".join(txt.split())
        matched = [kw for kw in header_keywords if kw in normalized]
        assert len(matched) >= 3, (
            f"P{i}: ヘッダ語が 3 語未満しか検出されない (matched={matched})。"
            f"thead 自動繰返しが破綻した可能性"
        )


# ────────────────────────────────────────────
# 統合（合冊）PDF の検証
# ────────────────────────────────────────────
@pytest.mark.parametrize("filename,expected_pages,expected_keywords", SNAPSHOT_INTEGRATION)
def test_integration_merged_pdf_snapshot(
    pdf_integration_dir: Path,
    filename: str,
    expected_pages: int,
    expected_keywords: tuple[str, ...],
):
    pdf_path = pdf_integration_dir / filename
    if not pdf_path.exists():
        pytest.skip(f"スナップショット PDF が存在しません: {pdf_path}")

    assert _page_count(pdf_path) == expected_pages

    # 合冊 PDF はサイズが大きい (画像 PDF 含む) → 1MB 以上を期待
    assert pdf_path.stat().st_size > 1_000_000, (
        f"{filename}: 合冊 PDF のサイズが 1MB 未満。約款などの参考 PDF が"
        "結合されていない可能性"
    )

    text = _read_all_text(pdf_path)
    for kw in expected_keywords:
        assert kw in text, (
            f"{filename}: 期待キーワード '{kw}' が抽出テキストから検出されない"
        )
