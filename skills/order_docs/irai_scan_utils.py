"""
依頼書（メインシート）走査ユーティリティ。

extractor.py から切り出した、注文書作成依頼書 (.xlsx) のメインシートを
キーワードベースで走査するロジックを集約する。

含まれる主な機能:
  - 「業者名１」「業者名２」… ヘッダーから業者基準列を自動検出
  - A列/B列を上から走査してキーワード行番号を一括取得
  - サブキーワードを下方向に探索（複数表記バリエーション対応）
  - 業者列を縦スキャンして金額ラベル（工事価格/消費税/合計）の右隣値を取得

extractor.py からは re-export することで、既存の
`from skills.order_docs.extractor import KINGAKU_KEYWORD_VARIANTS` 等の
インポートを壊さずに済む。
"""
from __future__ import annotations

import logging
import re

from openpyxl.worksheet.worksheet import Worksheet

from .extractor_utils import _cell_raw, _cell_str, _clean_amount, _normalize

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  金額キーワード表記揺れ対応テーブル
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# config の kingaku_sub_keywords の各キーに対して、
# 複数の表記バリエーションを定義する。
KINGAKU_KEYWORD_VARIANTS: dict[str, list[str]] = {
    "kingaku_koji":  ["工事価格", "工事金額", "内工事金額", "請負金額"],
    "kingaku_zei":   ["消費税", "内消費税額", "消費税額", "税額", "内消費税"],
    "kingaku_ukeoi": ["合計", "税込合計", "発注金額", "総計", "請負代金額", "合計金額"],
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  業者基準列の自動検出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _detect_vendor_base_cols(
    ws: Worksheet,
    max_row: int,
) -> list[int] | None:
    """
    シート内の「業者名１」「業者名２」… ヘッダーセルを探し、
    業者データの基準列番号リストを自動検出する。

    検出ロジック:
      1. 各行を横スキャンし「業者名」を含むセルを収集
      2. 同一行に2つ以上見つかればヘッダー行と判定
      3. 見つかったセルの列番号をソートして返す

    Returns
    -------
    list[int] | None
        検出された基準列番号リスト。検出失敗時は None。
    """
    for row in range(1, min(max_row + 1, 30)):  # ヘッダーは先頭付近にある想定
        cols_with_vendor: list[int] = []
        for col in range(1, (ws.max_column or 20) + 1):
            val = _cell_str(ws, row, col)
            if val and "業者名" in val:
                cols_with_vendor.append(col)

        if len(cols_with_vendor) >= 2:
            cols_with_vendor.sort()
            logger.info(
                "業者基準列を自動検出: 行%d → %s",
                row, cols_with_vendor,
            )
            return cols_with_vendor

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  キーワード行番号スキャン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _scan_keyword_rows(
    ws: Worksheet,
    keywords: dict[str, str],
    max_row: int,
    scan_cols: tuple[int, ...] = (1, 2),
) -> dict[str, int]:
    """
    A列 (1) と B列 (2) を上から下へスキャンし、
    各キーワードが最初に出現する行番号を返す。

    Parameters
    ----------
    ws : Worksheet
    keywords : {"logical_name": "検索文字列", ...}
    max_row : スキャン上限行
    scan_cols : スキャンする列番号タプル（デフォルト: A, B）

    Returns
    -------
    {"logical_name": row_number, ...}  ※ 見つからなかったキーは含まれない
    """
    remaining = dict(keywords)  # まだ見つかっていないキーワード
    result: dict[str, int] = {}

    for row in range(1, max_row + 1):
        if not remaining:
            break  # 全キーワード検出済み

        for col in scan_cols:
            cell_val = _cell_str(ws, row, col)
            if cell_val is None:
                continue
            norm_cell = _normalize(cell_val)

            # 残りキーワードと照合
            found_keys: list[str] = []
            for key, keyword in remaining.items():
                norm_kw = _normalize(keyword)
                if norm_kw in norm_cell:
                    result[key] = row
                    found_keys.append(key)
                    logger.debug("キーワード検出: '%s' → 行 %d", keyword, row)

            for k in found_keys:
                del remaining[k]

    if remaining:
        logger.warning("未検出キーワード: %s", list(remaining.values()))

    return result


def _find_sub_keyword_row(
    ws: Worksheet,
    start_row: int,
    sub_keyword: str,
    max_search: int = 15,
    scan_cols: tuple[int, ...] = (1, 2),
    keyword_variants: list[str] | None = None,
) -> int | None:
    """
    start_row から下方向に最大 max_search 行をスキャンし、
    sub_keyword（またはそのバリエーション）を含むセルがある行番号を返す。

    keyword_variants が指定された場合、それらすべてのキーワードで検索する。
    セル内の空白は除去してからキーワード比較を行う（例: 「合 計」→「合計」）。
    """
    # 検索キーワードリストを構築
    search_keywords = [sub_keyword]
    if keyword_variants:
        search_keywords = keyword_variants

    norm_keywords = [_normalize(kw) for kw in search_keywords]

    for row in range(start_row, start_row + max_search):
        for col in scan_cols:
            cell_val = _cell_str(ws, row, col)
            if cell_val is None:
                continue
            # セル内の空白を除去してから正規化比較
            norm_cell = _normalize(cell_val)
            for norm_kw in norm_keywords:
                if norm_kw in norm_cell:
                    return row
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  金額抽出 — 「見つけて右隣を取る」方式
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 金額ラベル → 論理フィールド名のマッピング
_KINGAKU_LABELS: dict[str, str] = {
    "工事価格": "kingaku_koji",
    "消費税":   "kingaku_zei",
    "合計":     "kingaku_ukeoi",
}


def _extract_kingaku_direct(
    ws: Worksheet,
    base_col: int,
    max_row: int,
    start_row: int = 1,
    end_row: int | None = None,
) -> dict[str, str | None]:
    """
    業者の base_col を縦スキャンし、「工事価格」「消費税」「合計」ラベルを見つけて
    その右隣セル (base_col + 1) の値を金額として取得する。

    シンプルな「見つけて右隣を取る」方式。

    Parameters
    ----------
    start_row : スキャン開始行（デフォルト=1）
    end_row   : スキャン終了行（デフォルト=None → max_row を使用）
    """
    scan_end = end_row if end_row is not None else max_row

    result: dict[str, str | None] = {
        "kingaku_koji": None,
        "kingaku_zei": None,
        "kingaku_ukeoi": None,
    }
    remaining = dict(_KINGAKU_LABELS)  # まだ見つかっていないラベル

    for row in range(start_row, scan_end + 1):
        if not remaining:
            break  # 全ラベル検出済み

        val = ws.cell(row=row, column=base_col).value
        if val is None:
            continue

        # セル値の前後空白・改行を除去して比較
        cell_text = str(val).strip().replace("\n", "").replace("\r", "")
        cell_text = re.sub(r"\s+", "", cell_text)  # 中間の空白も除去

        found_key: str | None = None
        for label, field_name in remaining.items():
            if label in cell_text or cell_text == label:
                found_key = label
                # 右隣セルから金額を取得
                raw_amount = _cell_raw(ws, row, base_col + 1)
                cleaned = _clean_amount(raw_amount)
                result[field_name] = cleaned
                logger.debug(
                    "金額取得: R%d:C%d '%s' → 右隣 R%d:C%d = %s → %s",
                    row, base_col, label, row, base_col + 1, repr(raw_amount), cleaned,
                )
                break

        if found_key:
            del remaining[found_key]

    return result
