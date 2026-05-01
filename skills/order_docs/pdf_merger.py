"""
PDF 合冊モジュール — pypdf の PdfWriter / PdfReader で複数 PDF を結合する。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> Path:
    """
    複数の PDF を指定順に結合して 1 つの PDF として保存する。

    Parameters
    ----------
    pdf_paths : list[Path]
        結合する PDF の絶対パスリスト（先頭が 1 ページ目）。
        存在しないファイルはスキップしてログに警告を出す。
    output_path : Path
        結合済み PDF の出力先。

    Returns
    -------
    Path
        出力先パス。

    Raises
    ------
    ValueError
        有効な PDF が 1 つもない場合。
    """
    writer = PdfWriter()
    added_count = 0

    try:
        for pdf_path in pdf_paths:
            if not pdf_path.exists():
                logger.warning("合冊対象が見つかりません — スキップ: %s", pdf_path)
                continue

            try:
                reader = PdfReader(str(pdf_path))
                for page in reader.pages:
                    writer.add_page(page)
                added_count += len(reader.pages)
                logger.debug(
                    "合冊に追加: %s (%d ページ)", pdf_path.name, len(reader.pages)
                )
            except Exception:
                logger.warning(
                    "PDF 読み込み失敗 — スキップ: %s", pdf_path.name, exc_info=True
                )

        if added_count == 0:
            raise ValueError("結合可能な PDF が 1 つもありません")

        # 重複リソース除去 & ストリーム圧縮
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(output_path), "wb") as f:
            writer.write(f)

        logger.info("PDF 合冊完了: %d ページ → %s", added_count, output_path.name)
        return output_path

    finally:
        writer.close()
