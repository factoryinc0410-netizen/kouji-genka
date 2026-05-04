"""
Excel データ抽出モジュール — キーワード検索方式

依頼書の行がユーザーにより挿入・削除されても追従できるよう、
固定セル番地を使わず A列/B列 のキーワードで行番号を動的に特定する。
業者は「列単位（横並び）」で配置されている前提。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from . import config
from .extractor_utils import (
    _clean_amount,
    _extract_core_name,
    _format_wareki,
    _is_excel_serial,
    _normalize,
    _parse_contract_date,
    _parse_kouki,
    _safe_float,
    _safe_int,
    _serial_to_datetime,
)
from .nairaku_models import NairakuData, NairakuHeaderInfo, NairakuRow
from .terms_models import TermsData, TermsItem, TermsParty, TermsSection

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  可視性の高い診断ログ（黒い画面で一目で見つかる大文字タグ）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ロガーと print 両方に出力する。print はログレベル設定に左右されないため、
# 本番サーバーで logger 設定が INFO 未満でも確実にターミナルに現れる。
def _banner(tag: str, message: str) -> None:
    line = f">>> [{tag}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    logger.info(line)

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
#  法人格除去パターン（シート名あいまい検索用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ユーティリティ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Excelシリアル値 → datetime 変換
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Excel の日付シリアル値の起点: 1899-12-30


def _cell_str(ws: Worksheet, row: int, col: int) -> str | None:
    """セルの値を文字列で返す。空や None は None。

    デフォルトでは前後空白を strip する（後方互換）。
    内訳書のように先頭インデント・中間空白を保持したい場合は
    `_cell_str_preserve()` を使用すること。
    """
    try:
        val = ws.cell(row=row, column=col).value
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None
    except Exception:
        return None


def _cell_str_preserve(ws: Worksheet, row: int, col: int) -> str | None:
    """セルの値を空白を保持したまま文字列で返す。

    先頭の全角/半角空白（インデント）と中間空白（例: "土　工"）を
    そのまま維持する。完全に空（None or "" のみ）の場合のみ None。

    Excel 上で「見た目の余白」が意味を持つケース（内訳書の工種名等）で使う。
    """
    try:
        val = ws.cell(row=row, column=col).value
        if val is None:
            return None
        s = str(val)
        # 完全空文字のみ None 扱い（rstrip せず "   " も保持する）
        return s if s != "" else None
    except Exception:
        return None


def _cell_raw(ws: Worksheet, row: int, col: int) -> Any:
    """セルの生値を返す（数値比較や日付変換用）。"""
    try:
        return ws.cell(row=row, column=col).value
    except Exception:
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  iter_rows の Cell オブジェクトを直接扱うヘルパー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# `_cell_str` / `_cell_str_preserve` は (ws, row, col) インタフェースだが、
# `ws.iter_rows(values_only=False)` から受け取る Cell を直接評価したい
# 場合に使うバリアント。ws.cell() のランダムアクセスを排除できる。

def _cell_value_preserve(cell: Any) -> str | None:
    """Cell オブジェクトから空白を保持した文字列を返す。

    `_cell_str_preserve` の Cell 版。先頭/中間空白を維持し、
    完全空（None or ""）のみ None を返す。
    iter_rows(values_only=False) の戻り値セルに対して使用する。
    """
    try:
        val = cell.value
    except Exception:
        return None
    if val is None:
        return None
    s = str(val)
    return s if s != "" else None


def _cell_value_strip(cell: Any) -> str | None:
    """Cell オブジェクトから前後空白を strip した文字列を返す。

    `_cell_str` の Cell 版。単位列など「空白が意味を持たない」列向け。
    """
    try:
        val = cell.value
    except Exception:
        return None
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  日付・工期パーサー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  関連シート検索（内訳書・契約条件書）
#  戦略: セル値ベース検索 → シート名ファジーマッチ(フォールバック)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# セル値スキャン範囲: 内訳書はヘッダ部(上位)、契約条件書は下部(行60-65付近)
# 上限値は config.EXCEL_SCAN_LIMITS に集約。
# （モジュールレベル変数は後方互換のため残置）
_SHEET_SCAN_MAX_ROW = config.EXCEL_SCAN_LIMITS["sheet_scan_max_row"]
_SHEET_SCAN_MAX_COL = config.EXCEL_SCAN_LIMITS["sheet_scan_max_col"]

# シート種別を判定するためのキーワード
_NAIRAKU_KEYWORDS = ["内訳書", "内訳"]
_JOKEN_KEYWORDS = ["契約条件書", "条件書", "契約条件"]


def _classify_sheet_type(sheet_name: str) -> str | None:
    """
    シート名から種別（"nairaku" / "joken"）を判定する。
    判定できない場合は None。
    """
    norm = _normalize(sheet_name)
    # 条件書を先に判定（「条件」は「内訳」と重複しないため順序は自由）
    for kw in _JOKEN_KEYWORDS:
        if _normalize(kw) in norm:
            return "joken"
    for kw in _NAIRAKU_KEYWORDS:
        if _normalize(kw) in norm:
            return "nairaku"
    return None


def _sheet_contains_vendor(
    wb,
    sheet_name: str,
    vendor_company: str,
    core_name: str,
) -> bool:
    """
    シート内のセル値に業者名（またはコア名称）が含まれるかを判定する。
    行1-70 x 列1-15 の範囲をスキャンする。

    検索キーとセル値の両方を正規化（空白・改行・ゼロ幅文字除去）した上で
    コア名での部分一致を主軸に判定する。
    """
    try:
        ws = wb[sheet_name]
    except KeyError:
        return False

    # 検索キーを事前に正規化
    norm_company = _normalize(vendor_company)
    norm_core = _normalize(core_name) if core_name else ""

    max_row = min(ws.max_row or _SHEET_SCAN_MAX_ROW, _SHEET_SCAN_MAX_ROW)
    max_col = min(ws.max_column or _SHEET_SCAN_MAX_COL, _SHEET_SCAN_MAX_COL)

    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            val = ws.cell(row=row, column=col).value
            if val is None:
                continue
            norm_val = _normalize(str(val))
            if not norm_val or len(norm_val) < 2:
                continue

            # 1) 正規化済み業者名での双方向部分一致
            if norm_company in norm_val or norm_val in norm_company:
                return True

            # 2) コア名（法人格除去後）での双方向部分一致（主軸）
            #    例: コア名 "飯盛グリーン開発" ↔ セル値 "飯盛グリーン開発"
            #    例: セル "株式会社\n飯盛グリーン開発" → 正規化 "株式会社飯盛グリーン開発"
            #         → コア名 "飯盛グリーン開発" が含まれる → マッチ
            if norm_core and len(norm_core) >= 2:
                if norm_core in norm_val:
                    return True
                # セル値からも法人格を除去して比較（セル値が短縮名の場合）
                cell_core = _extract_core_name(str(val))
                if cell_core and len(cell_core) >= 2:
                    if cell_core in norm_core or norm_core in cell_core:
                        return True

    return False


def build_sheet_assignment(
    sheet_names: list[str],
    vendor_companies: list[str],
    wb=None,
) -> dict[str, dict[str, str | None]]:
    """
    全シート × 全業者のマッチングを一括で行い、排他的に割り当てる。

    1シートは最大1業者にしか紐付かない。
    同じシートが複数業者にマッチした場合は、コア名の類似度で最適な業者に割り当て。

    Parameters
    ----------
    sheet_names : list[str]
        ワークブック内の全シート名。
    vendor_companies : list[str]
        全業者名のリスト。
    wb : openpyxl.Workbook | None
        セル値検索用のワークブックオブジェクト。

    Returns
    -------
    dict[str, dict[str, str | None]]
        {業者名: {"nairaku_sheet": str|None, "joken_sheet": str|None}, ...}
    """
    # ── 1) 候補シートの分類 ──
    # メインシート・稟議書を除外し、残りをタイプ別に分類
    typed_sheets: list[tuple[str, str]] = []  # [(sheet_name, type), ...]
    for name in sheet_names:
        norm_name = _normalize(name)
        if "依頼" in norm_name or "稟議" in norm_name:
            continue
        sheet_type = _classify_sheet_type(name)
        if sheet_type is not None:
            typed_sheets.append((name, sheet_type))

    logger.debug("シート分類結果: %s", [(n, t) for n, t in typed_sheets])

    # ── 2) 全シート × 全業者のマッチングスコアを算出 ──
    # scores[sheet_name] = [(vendor_company, score), ...]
    scores: dict[str, list[tuple[str, int]]] = {}

    vendor_cores = {vc: _extract_core_name(vc) for vc in vendor_companies}

    for sheet_name, sheet_type in typed_sheets:
        sheet_matches: list[tuple[str, int]] = []

        for vendor_company in vendor_companies:
            core_name = vendor_cores[vendor_company]
            score = _match_score(
                wb, sheet_name, sheet_names, vendor_company, core_name,
            )
            if score > 0:
                sheet_matches.append((vendor_company, score))

        if sheet_matches:
            # スコア降順でソート（最もマッチ度の高い業者が先頭）
            sheet_matches.sort(key=lambda x: x[1], reverse=True)
            scores[sheet_name] = sheet_matches
            logger.debug(
                "シート '%s' (%s) マッチ候補: %s",
                sheet_name, sheet_type,
                [(v, s) for v, s in sheet_matches],
            )

    # ── 3) 排他的割り当て（貪欲法） ──
    # スコアが高いペアから順に割り当て。1シート=1業者。
    all_pairs: list[tuple[int, str, str, str]] = []  # (score, sheet, vendor, type)
    for sheet_name, sheet_type in typed_sheets:
        if sheet_name in scores:
            for vendor, score in scores[sheet_name]:
                all_pairs.append((score, sheet_name, vendor, sheet_type))

    # スコア降順ソート
    all_pairs.sort(key=lambda x: x[0], reverse=True)

    # 割り当て結果
    assignment: dict[str, dict[str, str | None]] = {
        vc: {"nairaku_sheet": None, "joken_sheet": None}
        for vc in vendor_companies
    }
    used_sheets: set[str] = set()

    for score, sheet_name, vendor, sheet_type in all_pairs:
        # 既に割り当て済みのシートはスキップ
        if sheet_name in used_sheets:
            continue
        # この業者の同タイプが既に割り当て済みならスキップ
        key = f"{sheet_type}_sheet"
        if assignment[vendor][key] is not None:
            continue

        assignment[vendor][key] = sheet_name
        used_sheets.add(sheet_name)
        logger.info(
            "シート割当: '%s' → %s の %s (score=%d)",
            sheet_name, vendor, sheet_type, score,
        )

    return assignment


def _match_score(
    wb,
    sheet_name: str,
    all_sheet_names: list[str],
    vendor_company: str,
    core_name: str,
) -> int:
    """
    シートと業者のマッチ度をスコアで返す。
    0 = マッチしない。数値が大きいほど確実。

    スコア体系:
      100 = セル値に正規化済み業者名（フル）が含まれる
       80 = セル値にコア名称が含まれる
       50 = シート名に正規化済み業者名が含まれる
       30 = シート名にコア名称が含まれる
    """
    norm_company = _normalize(vendor_company)
    norm_core = _normalize(core_name) if core_name else ""

    best_score = 0

    # --- セル値ベース検索（wb がある場合） ---
    if wb is not None:
        try:
            ws = wb[sheet_name]
        except KeyError:
            pass
        else:
            max_row = min(ws.max_row or _SHEET_SCAN_MAX_ROW, _SHEET_SCAN_MAX_ROW)
            max_col = min(ws.max_column or _SHEET_SCAN_MAX_COL, _SHEET_SCAN_MAX_COL)

            for row in range(1, max_row + 1):
                for col in range(1, max_col + 1):
                    val = ws.cell(row=row, column=col).value
                    if val is None:
                        continue
                    norm_val = _normalize(str(val))
                    if not norm_val or len(norm_val) < 2:
                        continue

                    # フル名マッチ（最高スコア）
                    if norm_company in norm_val or norm_val in norm_company:
                        return 100  # 即確定

                    # コア名マッチ
                    if norm_core and len(norm_core) >= 2:
                        if norm_core in norm_val:
                            best_score = max(best_score, 80)
                        else:
                            cell_core = _extract_core_name(str(val))
                            if cell_core and len(cell_core) >= 2:
                                if cell_core in norm_core or norm_core in cell_core:
                                    best_score = max(best_score, 80)

    # --- シート名ベース検索（フォールバック） ---
    if best_score < 50:
        norm_name = _normalize(sheet_name)

        if norm_company in norm_name:
            best_score = max(best_score, 50)
        elif norm_core and len(norm_core) >= 2:
            if norm_core in norm_name:
                best_score = max(best_score, 30)
            else:
                # シート名からキーワードを除去して残りで比較
                sheet_part = norm_name
                for kw in _NAIRAKU_KEYWORDS + _JOKEN_KEYWORDS:
                    sheet_part = sheet_part.replace(_normalize(kw), "")
                sheet_part = re.sub(r"[()（）\[\]【】\s書]", "", sheet_part)
                if sheet_part and len(sheet_part) >= 2:
                    if sheet_part in norm_core or norm_core in sheet_part:
                        best_score = max(best_score, 30)

    return best_score


def _find_related_sheets(
    sheet_names: list[str],
    vendor_company: str,
    wb=None,
) -> dict[str, str | None]:
    """
    後方互換のためのラッパー。
    単一業者の場合は build_sheet_assignment を呼び出す。
    """
    result = build_sheet_assignment(sheet_names, [vendor_company], wb=wb)
    return result.get(vendor_company, {"nairaku_sheet": None, "joken_sheet": None})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約条件書からの共通データ抽出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_from_first_joken(
    wb,
    sheet_assignment: dict[str, dict[str, str | None]],
) -> dict[str, str | None]:
    """
    最初に見つかった契約条件書シートから「元請負人名称」「代表構成員」を抽出する。

    全業者で共通のデータなので、1シートから1回だけ取得すれば十分。

    Returns
    -------
    {"motouke_company": str|None, "daikyo_koseiin": str|None}
    """
    result: dict[str, str | None] = {
        "motouke_company": None,
        "daikyo_koseiin": None,
    }

    # 最初の契約条件書シート名を取得
    joken_sheet_name: str | None = None
    for vendor, sheets in sheet_assignment.items():
        joken_sheet_name = sheets.get("joken_sheet")
        if joken_sheet_name:
            break

    if not joken_sheet_name:
        logger.warning("契約条件書シートが1つも見つからないため、共通データ抽出をスキップ")
        return result

    try:
        ws = wb[joken_sheet_name]
    except KeyError:
        logger.warning("契約条件書シート '%s' が開けません", joken_sheet_name)
        return result

    logger.info("契約条件書共通データ抽出: シート '%s'", joken_sheet_name)

    max_row = min(ws.max_row or 100, 100)

    for row in range(1, max_row + 1):
        cell_val = _cell_str(ws, row, 1)  # A列
        if cell_val is None:
            continue

        norm_cell = _normalize(cell_val)

        # ── 元請負人名称: A列「商号又は名称」→ B列の値 ──
        if result["motouke_company"] is None:
            if "商号又は名称" in norm_cell or "商号または名称" in norm_cell:
                b_val = _cell_str(ws, row, 2)  # B列（右隣）
                if b_val:
                    result["motouke_company"] = b_val
                    logger.debug("元請負人名称: R%d:B = '%s'", row, b_val)

        # ── 代表構成員: A列「代表構成員」を含むセル ──
        if result["daikyo_koseiin"] is None:
            if "代表構成員" in norm_cell:
                # パターンA: セル自体に会社名が含まれている
                #   例: "【代表構成員】株式会社○○"
                text_after = re.sub(
                    r".*代表構成員[】\]」]?\s*", "", cell_val,
                ).strip()
                if text_after:
                    result["daikyo_koseiin"] = text_after
                    logger.debug("代表構成員(パターンA): R%d:A = '%s'", row, text_after)
                else:
                    # パターンB: 右隣 B列に会社名がある
                    b_val = _cell_str(ws, row, 2)
                    if b_val:
                        result["daikyo_koseiin"] = b_val
                        logger.debug("代表構成員(パターンB): R%d:B = '%s'", row, b_val)

        # 両方取得できたら終了
        if result["motouke_company"] and result["daikyo_koseiin"]:
            break

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約条件書テキストデータ抽出（スタンプ方式 Route A 用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── VML Y座標 → Excel 行番号(1-indexed) マッピング ──────────
# チェックボックスは VML 描画として配置されており、margin-top の
# pt 値から Excel 行番号を逆算する。
# 基準: VML y=71.0pt → Excel row 6, 間隔=13.0pt/行。
_VML_Y_BASE = 71.0
_VML_Y_STEP = 13.0
_VML_ROW_BASE = 6

# 元方列・下請列の VML X 座標 (margin-left の pt 値)
_VML_MOTOKATA_X = 316.5
_VML_SHITAUKE_X = 357.5
# 「9.適切な下請契約等」セクション用の特殊 X 座標
_VML_SECTION9_X = 357.5

# 費用負担チェックボックスが存在する Excel 行 (1-indexed)
_CHECKBOX_ROWS = [
    6, 7, 8,                            # 1.測量関係費
    10, 11, 12, 13, 14,                  # 2.安全関係費
    15, 16, 17, 18, 19, 20, 21, 22,      # 3.現場事務所
    24, 25, 26, 27, 28, 29, 30,          # 4.管理費用
    32, 33,                              # 5.環境改善
    35, 36, 37, 38, 39, 40, 41,          # 6.その他費用
    43, 44, 45,                          # 7.別途協議
    48, 49,                              # 8.その他
]


def _vml_y_to_row(vml_y: float) -> int:
    """VML の margin-top (pt) を Excel 行番号 (1-indexed) に変換する。"""
    return _VML_ROW_BASE + round((vml_y - _VML_Y_BASE) / _VML_Y_STEP)


def _extract_checkboxes_from_vml(
    excel_path: Path,
    sheet_name: str,
) -> dict[int, dict[str, bool]]:
    """
    契約条件書の VML からチェックボックスの状態を抽出する。

    .xlsx を ZIP として開き、vmlDrawing*.vml を解析して
    各行・各列のチェック状態を返す。
    openpyxl はフォームコントロールを読み取れないため、
    VML XML を直接パースする方式を採用する。

    Parameters
    ----------
    excel_path : Path
        Excel ファイルのパス。
    sheet_name : str
        対象シート名（VML ファイルの特定に使用）。

    Returns
    -------
    dict[int, dict[str, bool]]
        {Excel行番号: {"motokata": bool, "shitauke": bool}, ...}
    """
    import zipfile

    result: dict[int, dict[str, bool]] = {}

    try:
        with zipfile.ZipFile(str(excel_path), "r") as zf:
            # VML ファイルを探す
            vml_files = [
                f for f in zf.namelist()
                if "vmlDrawing" in f and f.endswith(".vml")
            ]
            if not vml_files:
                logger.warning("VML ファイルが見つかりません: %s", excel_path.name)
                return result

            vml_content = zf.read(vml_files[0]).decode("utf-8")
    except Exception:
        logger.error("VML 読込失敗: %s", excel_path.name, exc_info=True)
        return result

    # 各 v:shape を解析
    shapes = vml_content.split("<v:shape ")[1:]

    for shape_text in shapes:
        # margin-left / margin-top を抽出
        ml_match = re.search(r"margin-left:\s*([\d.]+)pt", shape_text)
        mt_match = re.search(r"margin-top:\s*([\d.]+)pt", shape_text)
        if not ml_match or not mt_match:
            continue

        vml_x = float(ml_match.group(1))
        vml_y = float(mt_match.group(1))
        is_checked = "<x:Checked>1</x:Checked>" in shape_text

        # チェックされていないものは処理不要（デフォルトは全て未チェック扱い）
        if not is_checked:
            continue

        excel_row = _vml_y_to_row(vml_y)

        # 行エントリを初期化
        if excel_row not in result:
            result[excel_row] = {"motokata": False, "shitauke": False}

        # 列を判定
        if abs(vml_x - _VML_MOTOKATA_X) < 5.0:
            result[excel_row]["motokata"] = True
        elif abs(vml_x - _VML_SHITAUKE_X) < 5.0:
            result[excel_row]["shitauke"] = True

    logger.info(
        "VML チェックボックス抽出完了: %d 行にチェックあり",
        len(result),
    )
    return result


def _find_keyword_row(
    ws: Worksheet,
    keyword: str,
    *,
    scan_cols: tuple[int, ...] = (1, 2, 3, 4, 5),
    max_row: int | None = None,
) -> int | None:
    """
    ワークシート内でキーワードを含むセルの行番号を返す。

    Parameters
    ----------
    ws : Worksheet
        検索対象のワークシート。
    keyword : str
        検索キーワード（正規化後に部分一致で検索）。
    scan_cols : tuple[int, ...]
        検索対象の列番号 (1-indexed)。
    max_row : int | None
        検索範囲の最大行。None の場合は ws.max_row を使用。

    Returns
    -------
    int | None
        見つかった行番号 (1-indexed)、見つからなければ None。
    """
    limit = min(max_row or (ws.max_row or 100), 100)
    norm_kw = _normalize(keyword)

    for row in range(1, limit + 1):
        for col in scan_cols:
            val = _cell_str(ws, row, col)
            if val and norm_kw in _normalize(val):
                return row
    return None


def _read_adjacent_value(
    ws: Worksheet,
    row: int,
    label_col: int,
    *,
    scan_right: int = 4,
    scan_below: int = 1,
) -> str | None:
    """
    ラベルセルの右隣または下のセルから値を取得する。

    セル結合されている場合のフォールバック処理を含む。

    Parameters
    ----------
    row : int
        ラベルの行番号 (1-indexed)。
    label_col : int
        ラベルの列番号 (1-indexed)。
    scan_right : int
        右方向にスキャンする列数。
    scan_below : int
        下方向にスキャンする行数。
    """
    max_col = ws.max_column or 9

    # 右方向をスキャン
    for c in range(label_col + 1, min(label_col + scan_right + 1, max_col + 1)):
        v = _cell_str(ws, row, c)
        if v:
            return v

    # 下方向をスキャン（セル結合対応）
    for r in range(row + 1, row + scan_below + 1):
        v = _cell_str(ws, r, label_col)
        if v:
            return v
        # 下の行の右隣もチェック
        for c in range(label_col + 1, min(label_col + scan_right + 1, max_col + 1)):
            v = _cell_str(ws, r, c)
            if v:
                return v

    return None


def extract_joken_text_data(
    excel_path: Path,
    sheet_name: str,
) -> dict[str, str | None]:
    """
    契約条件書シートからスタンプ用の全テキストデータを抽出する。

    抽出対象:
    1. 現場代理人名
    2. 元方（左側）の商号又は名称・住所・氏名
    3. 下請（右側）の商号又は名称・住所・氏名
    4. 備考エリアのテキスト
    5. チェックボックスの状態（VML 解析）

    Parameters
    ----------
    excel_path : Path
        元の Excel ファイル。
    sheet_name : str
        契約条件書のシート名。

    Returns
    -------
    dict
        キー: スタンプフィールド名, 値: テキスト（見つからなければ None）。
        チェックボックスは "joken_checkboxes" キーに
        {行番号: {"motokata": bool, "shitauke": bool}} 形式で格納。
    """
    result: dict[str, Any] = {
        "joken_genba_dairinin": None,
        # 左側（元請）署名欄
        "joken_left_company": None,
        "joken_left_address": None,
        "joken_left_name": None,
        # 右側（下請）署名欄
        "joken_right_company": None,
        "joken_right_address": None,
        "joken_right_name": None,
        # 備考
        "joken_biko": None,
        # チェックボックス状態
        "joken_checkboxes": {},
    }

    # ── 1) チェックボックス抽出（ZIP/VML 直接解析） ──
    result["joken_checkboxes"] = _extract_checkboxes_from_vml(
        excel_path, sheet_name,
    )

    # ── 2) テキストデータ抽出（openpyxl） ──
    try:
        wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    except Exception:
        logger.error("契約条件書 Excel 読込失敗: %s", excel_path, exc_info=True)
        return result

    try:
        if sheet_name not in wb.sheetnames:
            logger.warning("契約条件書シート '%s' が存在しません", sheet_name)
            return result

        ws = wb[sheet_name]
        max_row = ws.max_row or 100
        max_col = ws.max_column or 9

        # ── 2a) 現場代理人 ──
        r = _find_keyword_row(ws, "現場代理人", max_row=10)
        if r is not None:
            for c in range(1, max_col + 1):
                v = _cell_str(ws, r, c)
                if v and "現場代理人" in _normalize(v):
                    val = _read_adjacent_value(ws, r, c)
                    if val:
                        result["joken_genba_dairinin"] = val
                        logger.debug("現場代理人: R%d → '%s'", r, val)
                    break

        # ── 2b) 署名欄（「商号又は名称」キーワードで検索） ──
        # 構造: 左側=A列ラベル→B列値、右側=D列ラベル→E/F列値
        # 「商号又は名称」の行を探し、その下に「住所」「氏名」がある
        shogo_rows: list[int] = []
        for row in range(max(max_row - 20, 1), max_row + 1):
            for col in (1, 2, 3, 4, 5, 6):
                v = _cell_str(ws, row, col)
                if v and "商号" in _normalize(v):
                    shogo_rows.append(row)
                    break

        # 最初の「商号又は名称」→ 左側（元請）
        # 2番目の「商号又は名称」→ 右側（下請）、同じ行の D列以降
        if shogo_rows:
            left_row = shogo_rows[0]
            # 左側: A列ラベル → B列に値（B列が結合でC列のこともある）
            result["joken_left_company"] = _read_adjacent_value(
                ws, left_row, 1, scan_right=2,
            )
            logger.debug("左側商号: R%d → '%s'", left_row, result["joken_left_company"])

            # 左側の下の行から住所・氏名を探す
            for dr in range(1, 6):
                r = left_row + dr
                v = _cell_str(ws, r, 1)
                if v is None:
                    continue
                norm = _normalize(v)
                if "住" in norm and "所" in norm and result["joken_left_address"] is None:
                    result["joken_left_address"] = _read_adjacent_value(
                        ws, r, 1, scan_right=2,
                    )
                    logger.debug("左側住所: R%d → '%s'", r, result["joken_left_address"])
                elif "氏" in norm and "名" in norm and result["joken_left_name"] is None:
                    result["joken_left_name"] = _read_adjacent_value(
                        ws, r, 1, scan_right=2,
                    )
                    logger.debug("左側氏名: R%d → '%s'", r, result["joken_left_name"])

            # 右側: 同じ行の D列以降
            for col in range(4, max_col + 1):
                v = _cell_str(ws, left_row, col)
                if v and "商号" in _normalize(v):
                    result["joken_right_company"] = _read_adjacent_value(
                        ws, left_row, col, scan_right=3,
                    )
                    logger.debug(
                        "右側商号: R%d:C%d → '%s'",
                        left_row, col, result["joken_right_company"],
                    )
                    # 右側の住所・氏名
                    for dr in range(1, 6):
                        r = left_row + dr
                        rv = _cell_str(ws, r, col)
                        if rv is None:
                            continue
                        norm = _normalize(rv)
                        if "住" in norm and "所" in norm and result["joken_right_address"] is None:
                            result["joken_right_address"] = _read_adjacent_value(
                                ws, r, col, scan_right=3,
                            )
                            logger.debug("右側住所: R%d → '%s'", r, result["joken_right_address"])
                        elif "氏" in norm and "名" in norm and result["joken_right_name"] is None:
                            result["joken_right_name"] = _read_adjacent_value(
                                ws, r, col, scan_right=3,
                            )
                            logger.debug("右側氏名: R%d → '%s'", r, result["joken_right_name"])
                    break

        # ── 2c) 備考エリア ──
        # 「備考」列 (G列=7) の全データを行ごとに収集
        biko_parts: list[str] = []
        biko_col: int | None = None

        # 「備考」ヘッダの列を検索（通常 G4=7列目）
        for col in range(1, max_col + 1):
            for row in range(1, 10):
                v = _cell_str(ws, row, col)
                if v and "備" in v and "考" in v:
                    biko_col = col
                    break
            if biko_col:
                break

        if biko_col:
            for row in range(6, max_row + 1):
                v = _cell_str(ws, row, biko_col)
                if v:
                    biko_parts.append(v.strip())

        if biko_parts:
            result["joken_biko"] = "\n".join(biko_parts)
            logger.debug("備考: %d 件取得", len(biko_parts))

    except Exception:
        logger.error("契約条件書データ抽出失敗: %s", sheet_name, exc_info=True)
    finally:
        wb.close()

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン抽出関数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_data(excel_path: Path) -> list[dict[str, str | None]]:
    """
    注文書作成依頼書 (.xlsx) から業者ごとのデータを抽出する。

    キーワード検索方式により、行がユーザーによって挿入・削除されても
    正しいデータ位置を動的に特定できる。

    Parameters
    ----------
    excel_path : Path
        読み込む Excel ファイルのパス。

    Returns
    -------
    list[dict[str, str | None]]
        業者ごとの辞書リスト。各辞書には共通項目・業者固有項目・
        契約日(令和)・工期・金額・対応シート名が含まれる。
        業者が 0 社の場合は空リスト。

    Raises
    ------
    FileNotFoundError
        ファイルが存在しない場合。
    ValueError
        対象シートが見つからない場合。
    """
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel ファイルが見つかりません: {excel_path}")

    logger.info("Excel 読み込み開始: %s", excel_path.name)

    excel_map = config.EXCEL_MAP
    target_sheet_kw = excel_map["sheet_name"]
    common_keywords = excel_map["common_keywords"]
    vendor_keywords = excel_map["vendor_keywords"]
    fallback_base_cols: list[int] = excel_map["vendor_base_cols"]
    kingaku_sub_kw: dict[str, str] = excel_map.get("kingaku_sub_keywords", {})

    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    try:
        all_sheet_names: list[str] = wb.sheetnames

        # ── 1) 対象シートを特定（部分一致） ──
        ws: Worksheet | None = None
        for name in all_sheet_names:
            if target_sheet_kw in name:
                ws = wb[name]
                logger.info("対象シート特定: '%s'", name)
                break

        if ws is None:
            raise ValueError(
                f"シート名に '{target_sheet_kw}' を含むシートが見つかりません。"
                f" 存在するシート: {all_sheet_names}"
            )

        # シートの有効行数を取得（スキャン範囲）
        max_row = ws.max_row or 200
        if max_row < 10:
            max_row = 200  # 安全マージン

        # ── 1b) 業者基準列を自動検出 ──
        detected_cols = _detect_vendor_base_cols(ws, max_row)
        if detected_cols:
            vendor_base_cols = detected_cols
        else:
            vendor_base_cols = fallback_base_cols
            logger.warning(
                "業者基準列の自動検出に失敗 → config フォールバック値を使用: %s",
                fallback_base_cols,
            )

        # ── 2) キーワードで行番号を特定 ──
        all_keywords = {}
        all_keywords.update(common_keywords)
        all_keywords.update(vendor_keywords)

        keyword_rows = _scan_keyword_rows(ws, all_keywords, max_row)
        logger.info("キーワード検出結果: %s", {k: v for k, v in keyword_rows.items()})

        # ── 3) 共通データ取得（見つかった行の最初の業者基準列から取得） ──
        common_col = vendor_base_cols[0] if vendor_base_cols else 3
        common_data: dict[str, str | None] = {}
        for field_name in common_keywords:
            row = keyword_rows.get(field_name)
            if row is not None:
                common_data[field_name] = _cell_str(ws, row, common_col)
            else:
                common_data[field_name] = None
                logger.warning("共通項目 '%s' の行が未検出", field_name)

        logger.info("共通データ: %s", common_data)

        # ── 4) 契約日ヘッダの下にある「当初」行・「変更1」行を特定 ──
        contract_date_row: int | None = None
        contract_change_row: int | None = None
        contract_header_row = keyword_rows.get("contract_date_header")
        if contract_header_row:
            # 「当初」を探す（ヘッダの 1〜5 行下）
            tosho_row = _find_sub_keyword_row(ws, contract_header_row + 1, "当初", max_search=5)
            if tosho_row:
                contract_date_row = tosho_row
                logger.info("契約日「当初」行: %d", tosho_row)
                # 「変更」行を「当初」の直下で検出
                change_row = _find_sub_keyword_row(ws, tosho_row + 1, "変更", max_search=3)
                if change_row:
                    contract_change_row = change_row
                    logger.info("契約日「変更1」行: %d", change_row)
            else:
                # 「当初」が無い場合はヘッダの直下を使う
                contract_date_row = contract_header_row + 1
                logger.warning("契約日「当初」未検出 → ヘッダ+1行（%d行目）を使用", contract_date_row)

        # ── 5) 工期ヘッダの下にある「当初」行・「変更1」行を特定 ──
        kouki_row: int | None = None
        kouki_change_row: int | None = None
        kouki_header_row = keyword_rows.get("kouki_header")
        if kouki_header_row:
            tosho_row = _find_sub_keyword_row(ws, kouki_header_row + 1, "当初", max_search=5)
            if tosho_row:
                kouki_row = tosho_row
                logger.info("工期「当初」行: %d", tosho_row)
                # 「変更」行を「当初」の直下で検出
                change_row = _find_sub_keyword_row(ws, tosho_row + 1, "変更", max_search=3)
                if change_row:
                    kouki_change_row = change_row
                    logger.info("工期「変更1」行: %d", change_row)
            else:
                kouki_row = kouki_header_row + 1
                logger.warning("工期「当初」未検出 → ヘッダ+1行（%d行目）を使用", kouki_row)

        # ── 6) 金額ヘッダの下にある「当初」行・「変更1」行を特定 ──
        kingaku_tosho_row: int | None = None
        kingaku_change_row: int | None = None
        kingaku_header_row = keyword_rows.get("kingaku_header")
        if kingaku_header_row:
            tosho_row = _find_sub_keyword_row(ws, kingaku_header_row + 1, "当初", max_search=5)
            if tosho_row:
                kingaku_tosho_row = tosho_row
                logger.info("金額「当初」行: %d", tosho_row)
                change_row = _find_sub_keyword_row(ws, tosho_row + 1, "変更", max_search=5)
                if change_row:
                    kingaku_change_row = change_row
                    logger.info("金額「変更1」行: %d", change_row)
            else:
                kingaku_tosho_row = kingaku_header_row + 1
                logger.warning("金額「当初」未検出 → ヘッダ+1行（%d行目）を使用", kingaku_tosho_row)

        # ── 7) 業者名を先行収集（シート一括割り当て用） ──
        # 空欄列はスキップし、途中に空きがあっても後方の業者を拾う
        company_row = keyword_rows.get("vendor_company")
        vendor_companies: list[str] = []
        vendor_active_cols: list[int] = []  # 実際に業者がいる列番号
        if company_row:
            for base_col in vendor_base_cols:
                company = _cell_str(ws, company_row, base_col)
                if company is None or company.strip() == "":
                    continue  # 空欄列はスキップ
                vendor_companies.append(company)
                vendor_active_cols.append(base_col)

        # ── 8) シート一括排他割り当て ──
        # 全業者分を一度に処理し、1シート=1業者の排他制御を保証
        sheet_assignment = build_sheet_assignment(
            all_sheet_names, vendor_companies, wb=wb,
        )
        logger.info("シート割当結果: %s", {
            vc: {k: v for k, v in sheets.items() if v}
            for vc, sheets in sheet_assignment.items()
        })

        # ── 8b) 契約条件書から共通データを抽出 ──
        # 元請負人名称・代表構成員は全業者共通なので、最初の契約条件書から1回だけ取得
        joken_common = _extract_from_first_joken(wb, sheet_assignment)
        if joken_common.get("motouke_company"):
            logger.info("元請負人名称: '%s'", joken_common["motouke_company"])
        else:
            logger.warning("元請負人名称（商号又は名称）が取得できませんでした")
        if joken_common.get("daikyo_koseiin"):
            logger.info("代表構成員: '%s'", joken_common["daikyo_koseiin"])
        else:
            logger.warning("代表構成員が取得できませんでした")

        # ── 9) 業者ループ（列単位） ──
        results: list[dict[str, str | None]] = []

        if not vendor_active_cols:
            if company_row is None:
                logger.warning("【業者名】キーワード未検出 → 業者走査中断")
            else:
                logger.info("業者名が1社も検出できませんでした")

        for col_idx, base_col in enumerate(vendor_active_cols):
            company = vendor_companies[col_idx]
            logger.info("業者 %d: '%s' (基準列=%d)", col_idx + 1, company, base_col)

            vendor_data: dict[str, str | None] = {}
            vendor_data.update(common_data)  # 共通データをマージ
            vendor_data.update(joken_common)  # 契約条件書の共通データをマージ
            vendor_data["vendor_company"] = company

            # ── 代表者名 ──
            name_row = keyword_rows.get("vendor_name")
            if name_row:
                vendor_data["vendor_name"] = _cell_str(ws, name_row, base_col)

            # ── 住所 ──
            addr_row = keyword_rows.get("vendor_address")
            if addr_row:
                vendor_data["vendor_address"] = _cell_str(ws, addr_row, base_col)

            # ── 変更回数 ──
            henkou_row = keyword_rows.get("henkou_kaisuu")
            kaisuu = 0
            if henkou_row:
                # 大見出し行(40行目など)から下方向にスキャンし、各業者の列にある「変更回数」ラベルを探す
                target_row = henkou_row
                for r in range(henkou_row, henkou_row + 5):
                    val = _cell_str(ws, r, base_col)
                    if val and "変更回数" in val:
                        target_row = r
                        break
                
                # 見つけた行の右隣 (base_col + 1) から値を取得 (例: C列がラベルならD列が値)
                raw_kaisuu = _cell_raw(ws, target_row, base_col + 1)
                
                # もし右隣が空なら、元の列も念のため確認
                if raw_kaisuu in (None, "", 0, "0"):
                    raw_kaisuu = _cell_raw(ws, target_row, base_col)
                
                # 数値の抽出（1, 1.0, "第1回" などの表記揺れにすべて対応）
                raw_str = str(raw_kaisuu).strip()
                if raw_str and raw_str not in ("None", "0"):
                    m = re.search(r'\d+', raw_str)
                    if m:
                        kaisuu = int(m.group())
            
            if kaisuu >= 1:
                vendor_data["henkou_kaisuu"] = str(kaisuu)
                vendor_data["henkou_flag"] = f"第{kaisuu}回変更"
            else:
                vendor_data["henkou_kaisuu"] = "0"
                vendor_data["henkou_flag"] = ""

            # ── 契約日（「当初」→「変更1」で上書き判定） ──
            if contract_date_row:
                raw_date = _cell_raw(ws, contract_date_row, base_col)
                date_dict = _parse_contract_date(raw_date)
                # 変更1行があれば上書き判定
                if contract_change_row:
                    change_raw = _cell_raw(ws, contract_change_row, base_col)
                    if change_raw is not None and str(change_raw).strip() != "":
                        date_dict = _parse_contract_date(change_raw)
                        logger.info("  契約日: 変更1の値を採用 (行%d)", contract_change_row)
                vendor_data.update(date_dict)
            else:
                vendor_data.update({
                    "contract_year": None,
                    "contract_month": None,
                    "contract_day": None,
                })

            # ── 工期（「当初」→「変更1」で上書き判定） ──
            if kouki_row:
                raw_kouki = _cell_raw(ws, kouki_row, base_col)
                kouki_dict = _parse_kouki(raw_kouki)
                # 変更1行があれば上書き判定
                if kouki_change_row:
                    change_raw = _cell_raw(ws, kouki_change_row, base_col)
                    if change_raw is not None and str(change_raw).strip() != "":
                        kouki_dict = _parse_kouki(change_raw)
                        logger.info("  工期: 変更1の値を採用 (行%d)", kouki_change_row)
                vendor_data.update(kouki_dict)
            else:
                vendor_data.update({
                    "kouki_start_year": None, "kouki_start_month": None,
                    "kouki_start_day": None,
                    "kouki_end_year": None, "kouki_end_month": None,
                    "kouki_end_day": None,
                })

            # ── 金額（「当初」取得 →「変更1」があれば差額計算） ──
            # 当初の金額を取得（スキャン範囲: 当初行～変更1行の手前）
            tosho_end = (kingaku_change_row - 1) if kingaku_change_row else max_row
            kingaku_tosho = _extract_kingaku_direct(
                ws, base_col, max_row,
                start_row=kingaku_tosho_row or 1,
                end_row=tosho_end,
            )

            if kingaku_change_row:
                # 変更1の金額を取得（スキャン範囲: 変更1行から5行分）
                kingaku_henkou = _extract_kingaku_direct(
                    ws, base_col, max_row,
                    start_row=kingaku_change_row,
                    end_row=kingaku_change_row + 5,
                )
                henkou_ukeoi = _safe_int(kingaku_henkou.get("kingaku_ukeoi"))

                if henkou_ukeoi > 0:
                    # 変更あり → 差額を計算
                    kingaku_found: dict[str, str | None] = {}
                    for k in ("kingaku_koji", "kingaku_zei", "kingaku_ukeoi"):
                        diff = _safe_int(kingaku_henkou[k]) - _safe_int(kingaku_tosho[k])
                        kingaku_found[k] = str(diff)
                    # 増減方向を判定（合計の差額で判定）
                    diff_ukeoi = _safe_int(kingaku_found["kingaku_ukeoi"])
                    if diff_ukeoi >= 0:
                        kingaku_found["kingaku_direction"] = "増"
                    else:
                        kingaku_found["kingaku_direction"] = "減"
                    logger.info(
                        "  金額: 差額を採用（変更1 - 当初）→ 工事価格=%s, 消費税=%s, 合計=%s [%s]",
                        kingaku_found["kingaku_koji"],
                        kingaku_found["kingaku_zei"],
                        kingaku_found["kingaku_ukeoi"],
                        kingaku_found["kingaku_direction"],
                    )
                else:
                    # 変更1の合計が空 or 0 → 当初をそのまま採用
                    kingaku_found = kingaku_tosho
                    kingaku_found["kingaku_direction"] = None
                    logger.info("  金額: 変更1の合計が空/0 → 当初の金額を採用")
            else:
                # 変更1行自体が存在しない → 当初をそのまま採用
                kingaku_found = kingaku_tosho
                kingaku_found["kingaku_direction"] = None

            vendor_data.update(kingaku_found)
            for k in ("kingaku_koji", "kingaku_zei", "kingaku_ukeoi"):
                if kingaku_found.get(k) is None:
                    logger.warning("  金額未取得: %s (業者='%s')", k, company)

            # ── 関連シート（排他割り当て済みの結果を使用） ──
            related = sheet_assignment.get(
                company, {"nairaku_sheet": None, "joken_sheet": None},
            )
            vendor_data.update(related)

            if related["nairaku_sheet"]:
                logger.info("  内訳書シート: '%s'", related["nairaku_sheet"])
            else:
                logger.warning("  内訳書シートが見つかりません: 業者='%s'", company)

            if related["joken_sheet"]:
                logger.info("  契約条件書シート: '%s'", related["joken_sheet"])
            else:
                logger.warning("  契約条件書シートが見つかりません: 業者='%s'", company)

            results.append(vendor_data)
            logger.info("業者 %d 抽出完了: %s", col_idx + 1, company)

            # ── 業者データ可視化（全フィールドをダンプ出力） ──
            logger.info("--- 業者 %d データダンプ開始: %s ---", col_idx + 1, company)
            for k, v in vendor_data.items():
                logger.info("  %-25s : %s", k, v)
            logger.info("--- 業者 %d データダンプ終了 ---", col_idx + 1)

        logger.info("Excel 読み込み完了: %d 社分のデータを抽出", len(results))
        return results

    finally:
        wb.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書データ抽出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 内訳書の合計セクションで使われるキーワード ──
# これらのキーワードを含む行は subtotal として扱う
_NAIRAKU_SUBTOTAL_KEYWORDS: list[str] = [
    "直接工事費",
    "共通仮設費",
    "純工事費",
    "現場管理費",
    "法定福利費",
    "工事原価",
    "一般管理費",
    "工事価格",
    "消費税",
    "下請金額",
    "下請代金",
]

# ── 注記セクションのキーワード ──
_NAIRAKU_NOTE_KEYWORDS: list[str] = [
    "注記",
    "労務費",
    "法廷福利費",      # Excelの原文ママ（「法定」の誤字対応）
    "法定福利費",
]

# ── フッター終端キーワード ──
# 内訳書の最終行には必ず下記のフッター項目が記載される想定（ユーザー要件）:
#   ・「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」
#   ・「労務費」
#   ・「法定福利費」  ← これが最後
# このうち「法定福利費」を含む行を検出したら、その行を抽出対象の最終行として
# 処理し、以降の行読み込みを完全に停止（break）する。
# 注: 「法定福利費」は主表内の合計行（subtotal）としても出現するため、
#     チェックは必ず data_end（下請金額行）より下でのみ行うこと。
_NAIRAKU_FOOTER_TERMINATORS: list[str] = [
    "法定福利費",
]

# ── 単一セル「複合フッター」終端キーワード ──
# Excelの版によっては、フッターが 1 つのセルにまとめて記載されるケースがある。
# 例: 「【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、
#         労務費、法定福利費】」
# この種のセルは row_num > data_end の制約なしに検出しても誤マッチしにくい
# （下請金額 / 労務費 / 法定福利費 の 3 語が同一セル内に必ず揃う）ため、
# 下記「3 語すべて含有」を条件とした検出を行い、検出行を最終行として出力した
# 直後に抽出ループを break する。
_NAIRAKU_COMPOSITE_FOOTER_REQUIRED: list[str] = [
    "下請金額",
    "労務費",
    "法定福利費",
]


def _normalize_for_footer(text: str) -> str:
    """フッター検出用の緩い正規化。

    `_normalize` に加えて、全角括弧類と記号を取り除き、表記ゆれに
    より強い部分一致マッチを可能にする。
    """
    norm = _normalize(text)
    # 括弧・記号は検出ノイズになりやすいため除去
    for ch in ("【", "】", "『", "』", "（", "）", "(", ")",
               "「", "」", "※", "、", ",", "。", ".", " ", "\u3000"):
        norm = norm.replace(ch, "")
    return norm


def _is_composite_footer_text(text: str) -> bool:
    """単一セルに「下請金額+労務費+法定福利費」を列挙した複合フッターか判定する。

    判定条件（すべて満たす場合に True）:
      1. セル内に「下請金額」の部分文字列が含まれる
      2. セル内に「労務費」が 2 回以上出現
      3. セル内に「法定福利費」が 2 回以上出現

    想定する対象テキスト:
      「【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、
        労務費、法定福利費】」
    この形式はラベル内で「労務費」「法定福利費」がそれぞれ 2 回登場する
    （前半の説明文と末尾の列挙）という特徴を持つ。

    旧式の前置き文（1 回のみ出現するもの、例:
      「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」）
    は本判定では False となり、従来どおり次行以降のフッター 2 行
    （労務費 / 法定福利費）の抽出が継続される。
    """
    if not text:
        return False
    norm = _normalize_for_footer(text)
    if _normalize_for_footer("下請金額") not in norm:
        return False
    return (
        norm.count(_normalize_for_footer("労務費")) >= 2
        and norm.count(_normalize_for_footer("法定福利費")) >= 2
    )


def _row_contains_composite_footer(*cell_texts: str | None) -> bool:
    """行内のいずれかのセルに複合フッター文字列が含まれるか判定する。"""
    for t in cell_texts:
        if t and _is_composite_footer_text(t):
            return True
    return False


def _is_footer_terminator(text: str) -> bool:
    """テキストがフッター終端キーワードを含むか判定する。

    フッターは 3 行構成:
      1. 「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」（前置き文）
      2. 「労務費」                （値行）
      3. 「法定福利費」             （値行 ← 終端）
    前置き文にも「法定福利費」の文字が含まれるが、そこで break すると
    後続の 2 行が読み飛ばされてしまうため、前置きマーカーを持つ行は
    終端と判定しない。

    主表領域（下請金額の前）での subtotal 「法定福利費」との誤マッチを避けるため、
    呼び出し側で row_num > data_end のガードも必ず行うこと。
    """
    if not text:
        return False
    # 前置き文（「※…について」形式）を除外する
    if "※" in text or "について" in text:
        return False
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in _NAIRAKU_FOOTER_TERMINATORS)


# ── 列ヘッダ行を検出するキーワード ──
# 内訳書の列ヘッダは以下の 3 段構造（Excel 実測）:
#   Row N  : 「工種 / 種別 / 細別・規格 / 単位 / 元請契約 / 下請契約 / 備考」
#   Row N+1: 「当初 / 変更金額 / 変更(増減)」  ← 中見出し
#   Row N+2: 「数量 / 数量 / 単価 / 金額 / ...」← 末端ヘッダ
# 「工種」「種別」だけでは Row N までしか検出できず、
# Row N+1 / Row N+2 がデータ領域に含まれてしまうため、
# 「数量」（Row N+2 の col E に必ず存在）をキーワードに含めて
# ヘッダ領域の最終行を正しく特定する。
_NAIRAKU_HEADER_KEYWORDS: list[str] = ["工種", "種別", "数量"]

# ── 内訳書の1ページあたり行数（旧仕様・参考値のみ）──
# A4 横 + 12mm 余白 + thead 3行 + 本体 7.5pt + padding 1pt/2pt で、
# 1ページに約 57 行の明細が収まる設計。
# ※ v1.1.0 以降: 強制パディングは廃止（動的行数に移行）。
#   この定数は互換性のため残しているが、apply_nairaku_page_padding()
#   の既定引数以外では参照されない。
NAIRAKU_ROWS_PER_PAGE = 57


def _count_indent(text: str) -> int:
    """先頭の全角スペース (U+3000) の数をインデント段数として返す。"""
    count = 0
    for ch in text:
        if ch == "\u3000":
            count += 1
        else:
            break
    return count


def _is_subtotal_text(text: str) -> bool:
    """テキストが合計行のキーワードを含むか判定する。"""
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in _NAIRAKU_SUBTOTAL_KEYWORDS)


def _is_note_text(text: str) -> bool:
    """テキストが注記行のキーワードを含むか判定する。"""
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in _NAIRAKU_NOTE_KEYWORDS)


def _detect_header_end_row(ws: Worksheet, max_scan: int = 15) -> int:
    """内訳書の列ヘッダの最終行を検出する。

    「工種」「種別」などのキーワードを含む行を探し、
    その最後の行番号を返す。見つからなければ 9（デフォルト）。
    """
    header_rows: list[int] = []
    for row in range(1, max_scan + 1):
        for col in range(1, 6):
            val = _cell_str(ws, row, col)
            if val is None:
                continue
            norm = _normalize(val)
            if any(_normalize(kw) in norm for kw in _NAIRAKU_HEADER_KEYWORDS):
                header_rows.append(row)
                break
    if header_rows:
        return max(header_rows)
    return 9


def _detect_data_end_row(
    ws: Worksheet,
    start_row: int,
    max_row: int,
) -> int:
    """内訳書のデータ領域の最終行を検出する。

    「下請金額」「下請代金」行を探し、その行番号を返す。
    見つからなければ max_row をそのまま返す。
    """
    for row in range(start_row, max_row + 1):
        for col in (1, 2, 3):
            val = _cell_str(ws, row, col)
            if val is None:
                continue
            norm = _normalize(val)
            if "下請金額" in norm or "下請代金" in norm:
                return row
    return max_row


def _is_merged_across_abc(ws: Worksheet, row: int) -> bool:
    """指定行の A〜C 列にまたがるセル結合があるか判定する。"""
    for merged_range in ws.merged_cells.ranges:
        if (merged_range.min_row <= row <= merged_range.max_row
                and merged_range.min_col == 1
                and merged_range.max_col >= 3):
            return True
    return False


# セル結合キャッシュの型エイリアス。
# キー: (row, col) は 1-indexed の Excel 座標。
# 値: span 情報
#   - >=2 : このセルは結合のアンカー（colspan=値 で描画）
#   - 0   : 左側のセルからの結合範囲に覆われており、描画不要
# 「結合なし」のセルはキャッシュに登録しない（dict.get のデフォルト 1 で対応）。
MergedSpanCache = dict[tuple[int, int], int]


def _build_merged_cells_cache(
    ws: Worksheet, max_col: int = 15,
) -> MergedSpanCache:
    """``ws.merged_cells.ranges`` を 1 回だけ走査し、行ごとの結合情報を
    ``(row, col) → span`` の辞書に展開する。

    ここでの前処理により、行ごとの col_spans 算出は O(max_col) の dict
    ルックアップに落ちる（従来は 1 行あたり全結合範囲を線形走査していた）。

    ルール:
      - 結合範囲は ``[1, max_col]`` にクリップする（範囲外は無視）。
      - 単一セル結合 (min_col == max_col) はキャッシュに登録しない。
      - 垂直方向の結合は、通過する全行に対して水平スパン幅を登録する。

    Parameters
    ----------
    ws : Worksheet
        openpyxl のワークシート。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    MergedSpanCache
        ``(row, col)`` をキーとした結合情報辞書。
        結合に関与しないセルはキーが存在しない（ルックアップ時に既定値 1 を使う）。
    """
    cache: MergedSpanCache = {}
    for merged_range in ws.merged_cells.ranges:
        # [1, max_col] にクリップ
        start_col = max(merged_range.min_col, 1)
        end_col = min(merged_range.max_col, max_col)
        if start_col > max_col or end_col < 1 or end_col < start_col:
            continue
        # 単一セル（水平方向に結合なし）はキャッシュ不要
        if start_col == end_col:
            continue
        span_width = end_col - start_col + 1
        # 垂直方向に広がる結合も、各行について同じ水平スパン幅を記録する
        for r in range(merged_range.min_row, merged_range.max_row + 1):
            cache[(r, start_col)] = span_width          # アンカー
            for c in range(start_col + 1, end_col + 1):
                cache[(r, c)] = 0                        # 隠蔽セル
    return cache


def _compute_col_spans(
    cache: MergedSpanCache, row: int, max_col: int = 15,
) -> list[int]:
    """行ごとの col_spans リストを事前構築キャッシュから O(max_col) で取得する。

    返り値 (長さ ``max_col``):
      - ``1``   : 結合なし（単独セル） — キャッシュにキーが存在しない
      - ``n``   : 当該セルから右に n 個の結合 → ``colspan=n`` で描画
      - ``0``   : 左側のセルからの結合範囲に覆われており、描画不要

    Parameters
    ----------
    cache : MergedSpanCache
        ``_build_merged_cells_cache`` で事前構築された結合情報辞書。
    row : int
        1-indexed の Excel 行番号。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    list[int]
        長さ ``max_col`` の結合情報配列。
    """
    return [cache.get((row, col), 1) for col in range(1, max_col + 1)]


def _apply_dynamic_pseudo_merge(
    col_spans: list[int],
    val_a: str | None,
    val_b: str | None,
    val_c: str | None,
    threshold: int | None = None,
) -> list[int]:
    """A/B/C 列の内容に応じた「動的疑似結合」を col_spans に被せて返す。

    下請代金内訳書では、作業者が Excel でセル結合を張り忘れたまま長い
    工種名や種別名を入力しているケースが多く、そのままレンダリングすると
    右隣の空セルに文字が「はみ出て見える」ように罫線が走る。このヘルパー
    は「対象セルの文字数が閾値以上で長い」ときに限り A/B/C 列の colspan
    を自動補正する。閾値未満の短いテキストには一切手を出さず、独立セル
    としての罫線を維持する（これが旧仕様からの最重要な差分）。

    ルール (厳格な文字数ロック方式, 優先順位順に 1 つだけ適用):
      (a) len(A.strip()) >= threshold  AND  B 空  AND  C 空
          → col_spans[0]=3, [1]=0, [2]=0    （A が A:C にまたがる）
      (b) len(A.strip()) >= threshold  AND  B 空  AND  C 有り
          → col_spans[0]=2, [1]=0           （A が A:B にまたがる, C は単独）
      (c) len(B.strip()) >= threshold  AND  C 空  （A の長さは問わない）
          → col_spans[1]=2, [2]=0           （A 単独, B が B:C にまたがる）

      上記いずれにも該当しない（対象セルが空, または閾値未満）場合は
      col_spans をそのまま返す（= 独立セル + 罫線維持）。

    除外条件:
      Excel 側で既に A/B/C のいずれかに結合が設定されている場合
      （= ``col_spans[0..2]`` に 1 以外の値が 1 つでも含まれる場合）は、
      作業者の明示的な意図を尊重してこのロジック全体をスキップする。

    文字数の判定:
      必ず ``.strip()`` で前後空白を除去してから ``len()`` を取る。
      全角/半角とも 1 文字 = 1 カウント。既定閾値は
      ``config.NAIRAKU_AUTO_MERGE_THRESHOLD`` （全角 8 文字）。

    Parameters
    ----------
    col_spans : list[int]
        ``_compute_col_spans()`` の結果。15 要素を想定。
        この関数は新しいリストを返すため、入力は破壊しない。
    val_a, val_b, val_c : str | None
        A/B/C 列の原文。None または空白のみは「空」と見なす。
    threshold : int | None
        「長い」と判定する文字数（これ以上で結合発動）。``None`` の場合は
        ``config.NAIRAKU_AUTO_MERGE_THRESHOLD`` を参照。

    Returns
    -------
    list[int]
        A/B/C 列について疑似結合を反映した新しい col_spans リスト。
        入力の他列 (D..O) は変更しない。いずれのルールも非該当であれば
        入力と等価な（ただし独立した）リストを返す。
    """
    new_spans = list(col_spans)

    # ── 除外: Excel 側で A/B/C のいずれかに既存結合がある ─────────────
    # _compute_col_spans の結果で col_spans[0..2] がすべて 1 のときだけ
    # 動的疑似結合を発動する。1 つでも 1 以外ならユーザー指定を尊重する。
    if (len(new_spans) < 3
            or new_spans[0] != 1
            or new_spans[1] != 1
            or new_spans[2] != 1):
        return new_spans

    if threshold is None:
        threshold = config.NAIRAKU_AUTO_MERGE_THRESHOLD

    # 前後空白を除去してから長さを評価。空白のみは「空」扱い。
    a = (val_a or "").strip()
    b = (val_b or "").strip()
    c = (val_c or "").strip()

    # ルール (a): A が長い & B 空 & C 空 → A を 3 列分にスパン
    if len(a) >= threshold and not b and not c:
        new_spans[0] = 3
        new_spans[1] = 0
        new_spans[2] = 0
    # ルール (b): A が長い & B 空 & C 有り → A を 2 列分にスパン, C は単独
    elif len(a) >= threshold and not b and c:
        new_spans[0] = 2
        new_spans[1] = 0
        # new_spans[2] は 1 のまま
    # ルール (c): B が長い & C 空 → B を 2 列分にスパン（A の長さは問わない）
    elif len(b) >= threshold and not c:
        # new_spans[0] は 1 のまま（A 単独で罫線維持）
        new_spans[1] = 2
        new_spans[2] = 0
    # 上記いずれにも該当しなければ結合せず罫線維持

    return new_spans


def _resolve_col_spans(
    cache: MergedSpanCache,
    row: int,
    val_a: str | None = None,
    val_b: str | None = None,
    val_c: str | None = None,
    max_col: int = 15,
) -> list[int]:
    """行の col_spans を「Excel 結合 + 動的疑似結合」の合成で決定する。

    呼び出し側はこの 1 関数だけを使えば、キャッシュ参照と A/B/C の
    自動補正が両方適用された最終的な col_spans を得られる。
    val_a/val_b/val_c を省略した場合は動的疑似結合は発動しない
    （= Excel の結合情報のみが反映される）。

    Parameters
    ----------
    cache : MergedSpanCache
        ``_build_merged_cells_cache`` で事前構築された結合情報辞書。
    row : int
        1-indexed の Excel 行番号。
    val_a, val_b, val_c : str | None
        疑似結合判定に使う A/B/C 列の値。NairakuRow に実際に格納される
        値（レンダリング対象の値）を渡すこと。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    list[int]
        最終的な col_spans（長さ max_col）。
    """
    spans = _compute_col_spans(cache, row, max_col)
    return _apply_dynamic_pseudo_merge(spans, val_a, val_b, val_c)


def extract_nairaku_data(
    excel_path: Path,
    sheet_name: str,
) -> NairakuData:
    """
    内訳書シートからデータを構造化して抽出する。

    Parameters
    ----------
    excel_path : Path
        Excel ファイルのパス。
    sheet_name : str
        内訳書のシート名。

    Returns
    -------
    NairakuData
        抽出された内訳書データ。ReportLab での PDF 生成に直接使用可能。
    """
    result = NairakuData()
    _banner("NAIRAKU", f"抽出開始 sheet='{sheet_name}' file='{excel_path.name}'")

    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            _banner("NAIRAKU-SKIPPED",
                    f"シート '{sheet_name}' が存在しません → rows=0 で返却")
            return result

        ws = wb[sheet_name]
        _scan_limits = config.EXCEL_SCAN_LIMITS
        max_row = ws.max_row or _scan_limits["nairaku_max_row_fallback"]
        _header_scan = _scan_limits["nairaku_header_scan_rows"]

        # ── 結合セルキャッシュの事前構築 ──
        # ws.merged_cells.ranges を 1 回だけ走査して (row, col) → span の
        # 辞書を作り、以降の行処理は O(1) ルックアップで col_spans を生成する。
        # これにより「シート内の結合数 × 抽出対象行数」だった計算量を
        # 「結合セル総数 + 対象行数 × 15」に圧縮する。
        _merge_cache = _build_merged_cells_cache(ws)
        logger.debug(
            "内訳書: 結合セルキャッシュ構築完了 (entries=%d, ranges=%d)",
            len(_merge_cache), len(ws.merged_cells.ranges),
        )

        # ── 1) ヘッダ情報の抽出 (上部 _header_scan 行以内) ──
        header = NairakuHeaderInfo()

        # 工事名: A列「工事名」のキーワード行 → B列の値
        for row in range(1, _header_scan):
            val_a = _cell_str(ws, row, 1)
            if val_a and "工事名" in _normalize(val_a):
                header.koji_kenmei = _cell_str(ws, row, 2) or ""
                break

        # 契約年月日: A列「契約年月日」→ B列（和暦整形）
        for row in range(1, _header_scan):
            val_a = _cell_str(ws, row, 1)
            if val_a and "契約年月日" in _normalize(val_a):
                raw = _cell_raw(ws, row, 2)
                if isinstance(raw, datetime):
                    header.contract_date = _format_wareki(raw)
                elif raw is not None:
                    header.contract_date = str(raw).strip()
                break

        # 工期: A列「工期」→ B列
        # Excel セルが「令和8年2月1日〜令和8年11月20日」のような整形済み文字列の
        # 場合はそのまま保持する。datetime が 1 つだけ返ってくる場合は和暦整形。
        for row in range(1, _header_scan):
            val_a = _cell_str(ws, row, 1)
            if val_a and _normalize(val_a) == "工期":
                raw = _cell_raw(ws, row, 2)
                if isinstance(raw, datetime):
                    header.kouki = _format_wareki(raw)
                elif raw is not None:
                    header.kouki = str(raw).strip()
                break

        # ── 元請人情報 ──
        # Excel の「(元請負人)」ラベルをキーワード検索で動的に特定する。
        # セル番地（E3/E4 等）のハードコードは行挿入に弱いため避ける。
        #
        # 典型レイアウト:
        #   JV (共同企業体)   : (元請負人) + 右隣セルに JV 名
        #                      すぐ下の行に (代表構成員)、さらに下に 住所/商号/氏名
        #   単独企業 (非 JV)  : (元請負人) と同じ行に 住所ラベル + 住所値
        #                      (代表構成員) ラベルは存在しない
        motouke_row: int | None = None
        motouke_col: int | None = None
        for row in range(1, _header_scan):
            for col in range(1, 16):
                v = _cell_str(ws, row, col)
                if not v:
                    continue
                norm_v = _normalize(v)
                # 「(元請負人)」単独ラベルのみ拾う（「元請負人名称」や
                # 「(下請負人)」は除外）。
                if "(元請負人)" in norm_v and "代表構成員" not in norm_v:
                    motouke_row = row
                    motouke_col = col
                    break
            if motouke_row is not None:
                break

        # 「(元請負人)」の右隣セル（連続する空セルを飛ばしマージセルにも対応）を
        # 読み、「特定建設工事共同企業体」が含まれていれば JV 名として採用。
        if motouke_row is not None and motouke_col is not None:
            right_val: str | None = None
            for right_col in range(motouke_col + 1, min(motouke_col + 8, 16)):
                v = _cell_str(ws, motouke_row, right_col)
                if v:
                    right_val = v
                    break
            if right_val and "特定建設工事共同企業体" in right_val:
                header.jv_name = right_val
                header.motouke_group_name = right_val  # 後方互換

            # 「(代表構成員)」ラベルの有無で is_jv を決定する。
            # (元請負人) 行の直下 1〜3 行を走査（通常は 1 行下）。
            for row in range(motouke_row, min(motouke_row + 4, _header_scan)):
                for col in range(1, 16):
                    v = _cell_str(ws, row, col)
                    if not v:
                        continue
                    norm_v = _normalize(v)
                    if "(代表構成員)" in norm_v or "代表構成員" in norm_v:
                        header.is_jv = True
                        break
                if header.is_jv:
                    break

        # 住所/商号/氏名は JV・非 JV 問わず F 列（ラベル）→ G 列（値）という
        # 同じ構造で配置されているため、従来のスキャンをそのまま活かす。
        for row in range(1, _header_scan):
            val_f = _cell_str(ws, row, 6)  # F列: ラベル
            if val_f is None:
                continue
            norm_f = _normalize(val_f)
            if "住所" in norm_f:
                header.motouke_address = _cell_str(ws, row, 7) or ""
            elif "商号" in norm_f or "名称" in norm_f:
                header.motouke_company = _cell_str(ws, row, 7) or ""
            elif "氏名" in norm_f:
                header.motouke_name = _cell_str(ws, row, 7) or ""

        # ── 下請負人情報 ──
        # Excel 構造 (実測):
        #   Row 4: K='（下請負人）', L='住所', M4:O4 merged = 住所の値
        #   Row 5:                 L='商号又は名称', M5:O5 merged = 商号
        #   Row 6:                 L='氏名',       M6 = 氏名
        # ラベルは L 列 (col=12)、値は M 列 (col=13) に存在する。
        for row in range(1, _header_scan):
            val_l = _cell_str(ws, row, 12)  # L列: ラベル
            if val_l is None:
                continue
            norm_l = _normalize(val_l)
            if "住所" in norm_l:
                header.shitauke_address = _cell_str(ws, row, 13) or ""
            elif "商号" in norm_l or "名称" in norm_l:
                header.shitauke_company = _cell_str(ws, row, 13) or ""
            elif "氏名" in norm_l:
                header.shitauke_name = _cell_str(ws, row, 13) or ""

        result.header = header
        logger.info(
            "内訳書ヘッダ抽出: 工事名='%s', 元請共同企業体='%s', 元請代表構成員='%s', 下請='%s'",
            header.koji_kenmei, header.motouke_group_name,
            header.motouke_company, header.shitauke_company,
        )

        # ── 2) データ行の範囲を特定 ──
        header_end = _detect_header_end_row(ws)
        data_start = header_end + 1
        data_end = _detect_data_end_row(ws, data_start, max_row)

        logger.info(
            "内訳書データ範囲: 行 %d 〜 %d (ヘッダ末尾: 行 %d)",
            data_start, data_end, header_end,
        )

        # ── 3) 注記セクションの終端（下請金額行の後 +20行まで） ──
        note_end = min(data_end + 20, max_row)

        # ── 4) 行ごとのデータ抽出 (iter_rows で [data_start, note_end] を厳密走査) ──
        #
        # 【レイアウト忠実再現の契約】
        #   この範囲内の行は「1 Excel 行 = 1 NairakuRow」で 1:1 出力する。
        #   空セル・空行も spacer として保持し、絶対に詰めない。
        #   例外は以下のみ:
        #     - rd.hidden=True の非表示行 (Excel 上で意図的に隠されている)
        #     - rd.height < 5.0pt の装飾行 (Excel 上で視覚的に存在しない区切り)
        #   これらは「Excel 上で見えない行」なので spacer としても出力しない。
        #
        # 行アクセスはすべて ws.iter_rows() 経由で行い、values_only=False で
        # Cell オブジェクトを受け取ることで cell.row から実 Excel 行番号を取得する。
        has_henkou = False

        for row_cells in ws.iter_rows(
            min_row=data_start,
            max_row=note_end,
            max_col=15,
            values_only=False,
        ):
            # 実 Excel 行番号（iter_rows は行を詰めずに返す）
            row_num = row_cells[0].row

            # ── フッター領域フラグ ──
            # 下請金額行 (data_end) より下は「フッター領域」とみなす。
            # この領域では Excel のレイアウトを厳密に保持するため、
            # 非表示・装飾行スキップを適用しない（代わりに spacer として保持）。
            # これにより「下請金額 → 注記行」間の空行数が PDF にもそのまま反映される。
            in_footer_region = row_num > data_end

            # 非表示行: 主表領域（data_end 以下）でのみスキップ
            rd = ws.row_dimensions.get(row_num)
            if rd and rd.hidden and not in_footer_region:
                logger.debug("内訳書: 行 %d は非表示 → スキップ", row_num)
                continue

            # 行高 0 相当の装飾行 (Excel 上で見えない区切り): 主表領域のみスキップ
            # フッター領域では Excel 原本の行位置を 1:1 で PDF に反映させるため、
            # 装飾行扱いでも spacer としてレイアウトに残す。
            if (
                rd and rd.height is not None and rd.height < 5.0
                and not in_footer_region
            ):
                logger.debug(
                    "内訳書: 行 %d は装飾行 (height=%.2fpt) → スキップ",
                    row_num, rd.height,
                )
                continue

            # ── 単一セル複合フッター終端の早期検出 ──
            # 「【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、
            #   労務費、法定福利費】」のように 3 語すべてが 1 セルに含まれる
            # パターン。検出したら当該行を note として出力し、即座に抽出を
            # 打ち切る（以降の行を一切読まない）。
            _probe_a = _cell_value_preserve(row_cells[0]) or ""
            _probe_b = _cell_value_preserve(row_cells[1]) or ""
            _probe_c = _cell_value_preserve(row_cells[2]) or ""
            _probe_o = _cell_value_preserve(row_cells[14]) or ""
            if _row_contains_composite_footer(_probe_a, _probe_b,
                                               _probe_c, _probe_o):
                footer_text = next(
                    (t for t in (_probe_a, _probe_b, _probe_c, _probe_o)
                     if t and _is_composite_footer_text(t)),
                    _probe_a,
                )
                result.rows.append(NairakuRow(
                    row_type="note",
                    koji_shu=footer_text,
                    # 複合フッターは A 列に全文を集約して描画するため、
                    # B/C 相当の位置は空として疑似結合を判定する。
                    col_spans=_resolve_col_spans(
                        _merge_cache, row_num,
                        val_a=footer_text, val_b=None, val_c=None,
                    ),
                ))
                _banner(
                    "NAIRAKU-COMPOSITE-FOOTER",
                    f"行 {row_num} で複合フッター終端を検知 → ループ終了 "
                    f"(text={footer_text[:60]!r})",
                )
                break

            # ── セル値を row_cells タプルから取得（ws.cell() は使わない） ──
            # row_cells[i] は 0-indexed の Cell。A列=[0], B列=[1], … O列=[14]
            cell_a = row_cells[0]
            cell_b = row_cells[1]
            cell_c = row_cells[2]
            cell_d = row_cells[3]
            cell_e = row_cells[4]
            cell_f = row_cells[5]
            cell_g = row_cells[6]
            cell_h = row_cells[7]
            cell_i = row_cells[8]
            cell_j = row_cells[9]
            cell_k = row_cells[10]
            cell_l = row_cells[11]
            cell_m = row_cells[12]
            cell_n = row_cells[13]
            cell_o = row_cells[14]

            # A列: インデント判定のため raw → strip 前の値も保持
            raw_a = cell_a.value
            raw_a_str = str(raw_a).rstrip() if raw_a is not None else ""
            val_a = raw_a_str if raw_a_str else None
            a_indent = _count_indent(raw_a_str)

            # テキスト列は空白保持版を使用（内訳書の意図的な余白を反映）
            val_b = _cell_value_preserve(cell_b)   # B: 種別
            val_c = _cell_value_preserve(cell_c)   # C: 細別・規格
            val_d = _cell_value_strip(cell_d)      # D: 単位（空白保持不要）
            val_e = _safe_float(cell_e.value)      # E: 元請契約数量
            val_f = _safe_float(cell_f.value)      # F: 当初数量
            val_g = _safe_float(cell_g.value)      # G: 当初単価
            val_h = _safe_float(cell_h.value)      # H: 当初金額
            val_i = _safe_float(cell_i.value)      # I: 変更数量
            val_j = _safe_float(cell_j.value)      # J: 変更単価
            val_k = _safe_float(cell_k.value)      # K: 変更金額
            val_l = _safe_float(cell_l.value)      # L: 増減数量
            val_m = _safe_float(cell_m.value)      # M: 増減単価
            val_n = _safe_float(cell_n.value)      # N: 増減金額
            val_o = _cell_value_preserve(cell_o)   # O: 備考

            all_texts = [val_a, val_b, val_c, val_d, val_o]
            all_nums = [val_e, val_f, val_g, val_h, val_i, val_j,
                        val_k, val_l, val_m, val_n]

            # ── 空行判定 (レイアウト保持ルール) ──
            # テキスト 5 列 (A/B/C/D/O) がすべて None または空白のみ、かつ
            # 数値 10 列 (E〜N) がすべて None または 0.0 の行は、Excel 原本の
            # 「意図的な余白」または「完全未使用行」と見なし、spacer として 1 行保持する。
            # 以前は完全 None 行を continue でスキップしていたが、その挙動は
            # Excel の行位置関係を破壊するため撤廃した (確定事項: レイアウト非圧縮)。
            is_blank_row = (
                all(v is None or (isinstance(v, str) and v.strip() == "")
                    for v in all_texts)
                and all(v is None or v == 0.0 for v in all_nums)
            )
            if is_blank_row:
                # spacer 行は全セル空のため動的疑似結合は発動しない。
                # Excel の結合情報だけを反映する。
                result.rows.append(NairakuRow(
                    row_type="spacer",
                    col_spans=_resolve_col_spans(_merge_cache, row_num),
                ))
                continue

            # 変更列にデータがあるかチェック
            if any(v is not None for v in [val_i, val_j, val_k, val_l, val_m, val_n]):
                has_henkou = True

            # ── 太字判定 ──
            cell_font = ws.cell(row=row_num, column=1).font
            is_bold = bool(cell_font and cell_font.bold)
            # B列も確認（合計行は B列が太字の場合がある）
            if not is_bold and val_b:
                cell_font_b = ws.cell(row=row_num, column=2).font
                is_bold = bool(cell_font_b and cell_font_b.bold)

            # ── row_type の判定 ──
            a_text = val_a or ""                # 表示用（空白保持）
            a_text_stripped = a_text.strip()    # 判定用

            # 注記・フッター行: 下請金額行より後
            if row_num > data_end:
                # H/K/N 列いずれかに金額があれば「値付きフッター行」(footer_item):
                #   例: 「　労務費」「　法定福利費」— A列にラベル、
                #   H/K/N に当初/変更/増減の金額が入っている。これらは item と
                #   同じテーブル構造で描画する必要がある。
                # A列にテキストがあるだけの「純粋な注記文」(note):
                #   例: 「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」
                #   数値は無く、colspan=15 で一行描画する。
                has_numbers = any(
                    v is not None for v in (val_e, val_f, val_g, val_h,
                                             val_i, val_j, val_k,
                                             val_l, val_m, val_n)
                )
                has_any_text = bool(a_text_stripped) or bool(val_b) or bool(val_c)
                if has_numbers and has_any_text:
                    # 値付きフッター行 (労務費 / 法定福利費 等)
                    # A/B/C 列は Excel と同じ値がレンダリングされるため、
                    # そのまま疑似結合判定に渡す。
                    nrow = NairakuRow(
                        row_type="footer_item",
                        indent=a_indent,
                        koji_shu=a_text,              # 空白保持
                        shubetsu=val_b or "",
                        saibetsu=val_c or "",
                        tani=val_d or "",
                        motouke_suryo=val_e,
                        suryo=val_f,
                        tanka=val_g,
                        kingaku=val_h,
                        henkou_suryo=val_i,
                        henkou_tanka=val_j,
                        henkou_kingaku=val_k,
                        zougen_suryo=val_l,
                        zougen_tanka=val_m,
                        zougen_kingaku=val_n,
                        biko=val_o or "",
                        is_bold=is_bold,
                        col_spans=_resolve_col_spans(
                            _merge_cache, row_num,
                            val_a=val_a, val_b=val_b, val_c=val_c,
                        ),
                    )
                    # has_henkou も footer_item の変更列から検出
                    if any(v is not None for v in (val_i, val_j, val_k,
                                                    val_l, val_m, val_n)):
                        has_henkou = True
                    result.rows.append(nrow)
                    # ── フッター終端検知 ──
                    if _is_footer_terminator(a_text_stripped) or \
                       (val_b and _is_footer_terminator(val_b)):
                        logger.info(
                            "内訳書: 行 %d でフッター終端『法定福利費』を検知 → "
                            "抽出ループを終了します",
                            row_num,
                        )
                        break
                    continue
                elif has_any_text:
                    # 純粋な注記文 (※で始まる前置き文など)。
                    # NairakuRow には saibetsu (C) を渡さないため、疑似結合
                    # 判定でも val_c=None として「C は空」と見なす。
                    nrow = NairakuRow(
                        row_type="note",
                        koji_shu=a_text,              # 空白保持
                        shubetsu=val_b or "",
                        biko=val_o or "",
                        is_bold=is_bold,
                        col_spans=_resolve_col_spans(
                            _merge_cache, row_num,
                            val_a=a_text, val_b=val_b, val_c=None,
                        ),
                    )
                    result.rows.append(nrow)
                    # 前置き文には ※ が含まれるため _is_footer_terminator は
                    # False を返すが、念のため終端チェックは実施
                    if _is_footer_terminator(a_text_stripped) or \
                       (val_b and _is_footer_terminator(val_b)):
                        logger.info(
                            "内訳書: 行 %d でフッター終端『法定福利費』を検知 → "
                            "抽出ループを終了します",
                            row_num,
                        )
                        break
                    continue
                # ── 数値もテキストも主要セルに無い行 ──
                # 下請金額と注記の間の空行等。Excel のレイアウトをそのまま
                # 反映するため spacer として保持する（以前は silent skip していた）。
                # 全セル空のため動的疑似結合は発動しない。
                result.rows.append(NairakuRow(
                    row_type="spacer",
                    col_spans=_resolve_col_spans(_merge_cache, row_num),
                ))
                continue

            # 合計行: A〜C結合 or 合計キーワードを含む
            is_subtotal = False
            subtotal_label = ""
            if _is_subtotal_text(a_text_stripped):
                is_subtotal = True
                subtotal_label = a_text  # 空白保持
            elif val_b and _is_subtotal_text(val_b.strip()):
                is_subtotal = True
                subtotal_label = val_b  # 空白保持
            elif _is_merged_across_abc(ws, row_num):
                is_subtotal = True
                subtotal_label = a_text  # 空白保持

            if is_subtotal:
                # 疑似結合は「他の明細行と同じく」Excel の raw val_a/b/c で
                # 判定する。短いラベル（例:「直接工事費」）では閾値未満
                # として結合せず罫線を残し、Excel 側で A:C 結合が設定されて
                # いる場合は _compute_col_spans の除外条件で colspan=3 が
                # 反映される。
                nrow = NairakuRow(
                    row_type="subtotal",
                    koji_shu=subtotal_label,
                    tani=val_d or "",
                    motouke_suryo=val_e,
                    suryo=val_f,
                    tanka=val_g,
                    kingaku=val_h,
                    henkou_suryo=val_i,
                    henkou_tanka=val_j,
                    henkou_kingaku=val_k,
                    zougen_suryo=val_l,
                    zougen_tanka=val_m,
                    zougen_kingaku=val_n,
                    biko=val_o or "",
                    is_bold=True,  # 合計行は常に太字
                    col_spans=_resolve_col_spans(
                        _merge_cache, row_num,
                        val_a=val_a, val_b=val_b, val_c=val_c,
                    ),
                )
                result.rows.append(nrow)
                continue

            # カテゴリ行: A列にテキストあり、B/C/D列が空、
            # かつ F〜H列が空または 0（SUM数式が 0 を返すケース対応）
            is_category = (
                a_text_stripped != ""
                and val_b is None
                and val_c is None
                and val_d is None
                and (val_f is None or val_f == 0.0)
                and (val_g is None or val_g == 0.0)
                and (val_h is None or val_h == 0.0)
            )

            if is_category:
                # 疑似結合は item 行と同じく Excel の raw val_a/b/c で判定。
                # 工種名が閾値以上なら自動で A:C 結合され、短い工種名では
                # 結合せず罫線が残る（短文での罫線消失を防止）。
                # なお category 検出時は val_b/val_c は常に None なので、
                # 実質的に val_a の長さだけが結合発動の分岐条件となる。
                nrow = NairakuRow(
                    row_type="category",
                    indent=a_indent,
                    koji_shu=a_text,              # 空白保持
                    motouke_suryo=val_e,
                    is_bold=is_bold,
                    col_spans=_resolve_col_spans(
                        _merge_cache, row_num,
                        val_a=val_a, val_b=val_b, val_c=val_c,
                    ),
                )
                result.rows.append(nrow)
                continue

            # 明細行（デフォルト）
            # Excel の A/B/C をそのまま疑似結合判定に渡す。長い工種名に
            # B/C が追随していないケースでルール (a)/(b)/(c) が発動して
            # 結合として描画される。
            nrow = NairakuRow(
                row_type="item",
                indent=a_indent,
                koji_shu=a_text,                  # 空白保持
                shubetsu=val_b or "",
                saibetsu=val_c or "",
                tani=val_d or "",
                motouke_suryo=val_e,
                suryo=val_f,
                tanka=val_g,
                kingaku=val_h,
                henkou_suryo=val_i,
                henkou_tanka=val_j,
                henkou_kingaku=val_k,
                zougen_suryo=val_l,
                zougen_tanka=val_m,
                zougen_kingaku=val_n,
                biko=val_o or "",
                is_bold=is_bold,
                col_spans=_resolve_col_spans(
                    _merge_cache, row_num,
                    val_a=val_a, val_b=val_b, val_c=val_c,
                ),
            )
            result.rows.append(nrow)

        result.has_henkou = has_henkou

        # ── ページパディングは廃止（動的行数）──
        # 以前は apply_nairaku_page_padding() で 57 行/ページの倍数に
        # 切り上げていたが、Excel 原本の実行数で終わるほうが自然かつ
        # 複合フッター終端検出と相性が良いため、強制パディングを廃止した。

        # ── 統計ログ ──
        type_counts: dict[str, int] = {}
        for r in result.rows:
            type_counts[r.row_type] = type_counts.get(r.row_type, 0) + 1
        logger.info(
            "内訳書データ抽出完了: %d 行 (category=%d, item=%d, subtotal=%d, "
            "note=%d, footer_item=%d, spacer=%d, pad=%d), has_henkou=%s",
            len(result.rows),
            type_counts.get("category", 0),
            type_counts.get("item", 0),
            type_counts.get("subtotal", 0),
            type_counts.get("note", 0),
            type_counts.get("footer_item", 0),
            type_counts.get("spacer", 0),
            type_counts.get("pad", 0),
            has_henkou,
        )
        if result.rows:
            _banner(
                "NAIRAKU-SUCCESS",
                f"sheet='{sheet_name}' rows={len(result.rows)} "
                f"(item={type_counts.get('item', 0)}, "
                f"subtotal={type_counts.get('subtotal', 0)}, "
                f"footer_item={type_counts.get('footer_item', 0)}, "
                f"note={type_counts.get('note', 0)})",
            )
        else:
            _banner(
                "NAIRAKU-FAILED",
                f"sheet='{sheet_name}' 抽出結果が 0 行。"
                f"data_start={data_start} data_end={data_end} max_row={max_row} "
                f"— Excel側の『下請金額』行の存在／列ヘッダ検出を確認してください",
            )

    except Exception as exc:
        _banner(
            "NAIRAKU-ERROR",
            f"sheet='{sheet_name}' 例外発生: {type(exc).__name__}: {exc}",
        )
        logger.error("内訳書データ抽出失敗: %s / %s", excel_path, sheet_name, exc_info=True)
    finally:
        wb.close()

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書ページパディング
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def apply_nairaku_page_padding(
    data: "NairakuData",
    *,
    rows_per_page: int = NAIRAKU_ROWS_PER_PAGE,
) -> None:
    """【廃止予定】内訳書の最終ページが rows_per_page 行分で終わるよう
    パッド行を挿入する。

    .. deprecated:: v1.1.0
        強制パディングは廃止された。内訳書は Excel 原本の行数で終わる
        （複合フッター終端を検出した時点で break）。この関数は後方
        互換性のためにのみ残されており、抽出パイプラインから呼び出されない。
        呼び出しても従来どおり動作するため、既存のカスタムフローでは
        引き続き利用できる。

    挿入位置は「body（明細・カテゴリ・合計・意図的空白行）」と
    「footer（note 行の連続）」の境界。footer の前方にパッド行を挟むことで、
    ※下請金額 / 労務費 / 法定福利費 のフッターが常に最終ページ末尾に位置する。

    Parameters
    ----------
    data : NairakuData
        抽出済みデータ。data.rows が in-place で書き換えられる。
    rows_per_page : int
        1 ページあたりの行数（デフォルト 57）。

    Notes
    -----
    - rows_per_page <= 0 の場合は何もしない。
    - data.rows が空の場合も何もしない。
    - body の末尾が既に rows_per_page の倍数ならパッド行は追加されない。
    - footer が存在しない（note 行が無い）場合は単純に末尾にパッド行を付ける。
    """
    if rows_per_page <= 0:
        return
    rows = data.rows
    if not rows:
        return

    # footer 開始 index を探す
    # footer は次のいずれかの連続ブロックとして扱う:
    #   - "note"        : ※ で始まる前置き文
    #   - "footer_item" : 労務費 / 法定福利費 のような値付きフッター行
    # 先に出現したほうを footer の開始位置とする。
    footer_start: int | None = None
    for i, r in enumerate(rows):
        if r.row_type in ("note", "footer_item"):
            footer_start = i
            break

    if footer_start is None:
        body_rows = rows
        footer_rows: list[NairakuRow] = []
    else:
        body_rows = rows[:footer_start]
        footer_rows = rows[footer_start:]

    total = len(body_rows) + len(footer_rows)
    # 総行数を rows_per_page の倍数に切り上げるのに必要なパッド行数
    pad_count = (rows_per_page - (total % rows_per_page)) % rows_per_page

    if pad_count == 0:
        logger.info(
            "内訳書パディング不要: total=%d は %d の倍数 (body=%d, footer=%d)",
            total, rows_per_page, len(body_rows), len(footer_rows),
        )
        return

    pad_rows = [NairakuRow(row_type="pad") for _ in range(pad_count)]
    data.rows = body_rows + pad_rows + footer_rows

    logger.info(
        "内訳書パディング適用: body=%d + pad=%d + footer=%d = %d 行 "
        "(%d 行/ページ × %d ページ)",
        len(body_rows), pad_count, len(footer_rows), len(data.rows),
        rows_per_page, len(data.rows) // rows_per_page,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約変更回数の自動スキャン（注文書作成依頼書）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scan_contract_change_count(
    excel_path: Path,
    *,
    sheet_keyword: str = "注文書作成依頼書",
    scan_rows_around: int = 15,
) -> dict[int, int]:
    """依頼書シートから業者ごとの契約変更回数を抽出する。

    構造（実物ファイル解析に基づく）:
      - 業者は横並び（業者1→C/D列, 業者2→E/F列, …, 業者5→K/L列）
      - "変更回数" ラベルの右隣セルに値（int）

    検出ロジック:
      1. 全シートから `sheet_keyword` を含むシートを特定
      2. 各セルを走査し "変更回数" 文字列を含むセルを発見したら、
         その右隣（+1列）セルの値を int に変換
      3. 左隣から業者番号を推定（業者基準列リストと照合）

    Returns
    -------
    dict[int, int]
        業者番号 (1-indexed) → 変更回数。値が空/0 の業者はエントリなし。
    """
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel ファイルが見つかりません: {excel_path}")

    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    try:
        # 対象シートを特定
        ws: Worksheet | None = None
        for name in wb.sheetnames:
            if sheet_keyword in name:
                ws = wb[name]
                break
        if ws is None:
            logger.warning("変更回数スキャン: シート '%s' 未検出", sheet_keyword)
            return {}

        # 業者基準列（1-indexed）: 自動検出 or config フォールバック
        max_row = ws.max_row or 50
        detected = _detect_vendor_base_cols(ws, max_row)
        base_cols = detected or config.EXCEL_MAP["vendor_base_cols"]

        # base_col → 業者番号
        col_to_vendor = {c: i + 1 for i, c in enumerate(base_cols)}

        result: dict[int, int] = {}
        max_col = ws.max_column or 20

        # 全セル走査（ヘッダ域付近のみ想定: row 1..max_row）
        for row in range(1, min(max_row + 1, 200)):
            for col in range(1, max_col + 1):
                val = _cell_str(ws, row, col)
                if not val or "変更回数" not in val:
                    continue
                # 右隣セルの値
                raw = _cell_raw(ws, row, col + 1)
                if raw is None or raw == "":
                    continue
                try:
                    count = int(float(str(raw)))
                except (ValueError, TypeError):
                    logger.debug("変更回数の数値変換失敗 row=%d col=%d raw=%r", row, col, raw)
                    continue
                if count <= 0:
                    continue
                # 業者番号の特定: col 自体 or 直近の base_col
                vendor_no = col_to_vendor.get(col)
                if vendor_no is None:
                    # ラベル列の1つ左が base_col であるケース
                    # 例: C38=ラベル, D38=値 → base_col=C(3), 業者1
                    vendor_no = col_to_vendor.get(col - 1)
                    # さらに許容: label 列自体が base_col から 1 ずれる場合
                if vendor_no is None:
                    # base_cols で最も近い列を採用
                    nearest = min(base_cols, key=lambda bc: abs(bc - col))
                    vendor_no = col_to_vendor[nearest]
                if vendor_no and vendor_no not in result:
                    result[vendor_no] = count

        logger.info("契約変更回数スキャン結果: %s", result)
        return result
    finally:
        wb.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約条件書データ抽出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# セクション定義テーブル（実ファイル解析に基づく固定レイアウト）
# 各エントリ: (section_no, title_row, item_rows, layout)
_TERMS_SECTIONS_SPEC: list[tuple[int, int, list[int], str]] = [
    (1,  6,  [6, 7, 8],                            "std"),      # 1.測量関係費
    (2,  10, [10, 11, 12, 13, 14],                 "std"),      # 2.安全関係費
    (3,  15, [15, 16, 17, 18, 19, 20, 21, 22],     "std"),      # 3.現場事務所・仮設電力費
    (4,  24, [24, 25, 26, 27, 28, 29, 30],         "std"),      # 4.管理費用
    (5,  32, [32, 33],                             "std"),      # 5.現場環境改善費用
    (6,  35, [35, 36, 37, 38, 39, 40, 41],         "std"),      # 6.その他費用
    (7,  43, [43, 44, 45],                         "std"),      # 7.別途協議事項
    (8,  47, [48, 49],                             "std"),      # 8.その他
    (9,  50, [51, 52, 53, 54, 55, 56],             "wide"),     # 9.適切な下請契約等 (B:D=label, E:F=check)
    (10, 58, [58],                                 "single"),   # 10.4週8休等の実施
]


def _is_checked(cell_value: Any) -> bool:
    """セル値が ✓ チェック済みかを判定する。

    data validation dropdown で選択された "✓" 文字列を検出。
    空白・None はすべて未チェック扱い。
    """
    if cell_value is None:
        return False
    s = str(cell_value).strip()
    if not s:
        return False
    # ✓ (U+2713), ✔ (U+2714), レ点 (U+30EC), "v"/"V"/"x"/"X" 等を許容
    return s in ("✓", "✔", "レ", "v", "V", "×", "x", "X", "○", "●")


def extract_terms_data(
    excel_path: Path,
    vendor_index: int,
    *,
    sheet_keyword: str = "契約条件書",
) -> TermsData:
    """契約条件書シートから TermsData を抽出する。

    Parameters
    ----------
    excel_path : Path
        Excel ファイル。
    vendor_index : int
        業者番号 (1-indexed)。シート名 "契約条件書 （業者名N）" の N に対応。
    sheet_keyword : str
        シート名の識別キーワード。

    Returns
    -------
    TermsData
        抽出結果。該当シート未検出時は空の TermsData（sections=[]）。
    """
    result = TermsData()
    _banner("JOKEN", f"抽出開始 vendor_index={vendor_index} keyword='{sheet_keyword}'")

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel ファイルが見つかりません: {excel_path}")

    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    try:
        # シート検索: "契約条件書" + 業者番号 を含む
        target_name: str | None = None
        for name in wb.sheetnames:
            if sheet_keyword in name and str(vendor_index) in name:
                target_name = name
                break
        if target_name is None:
            # フォールバック: 業者番号が連番でシートに含まれていない場合、
            # "契約条件書" を含むシートを順次カウント
            joken_sheets = [n for n in wb.sheetnames if sheet_keyword in n]
            if 1 <= vendor_index <= len(joken_sheets):
                target_name = joken_sheets[vendor_index - 1]

        if target_name is None:
            _banner(
                "JOKEN-SKIPPED",
                f"契約条件書シート未検出 vendor_index={vendor_index} "
                f"keyword='{sheet_keyword}' sheets={wb.sheetnames}",
            )
            return result

        ws = wb[target_name]
        result.source_sheet = target_name

        # ── ヘッダ情報 ──
        # B2:D2 merged → 工事名値
        result.koji_kenmei = _cell_str_preserve(ws, 2, 2) or ""
        # G2 → 現場代理人氏名
        result.genba_dairinin = _cell_str_preserve(ws, 2, 7) or ""

        # ── セクション・明細行 ──
        for sec_no, title_row, item_rows, layout in _TERMS_SECTIONS_SPEC:
            # 大分類タイトル (A列)
            title = _cell_str(ws, title_row, 1) or ""
            section = TermsSection(number=sec_no, title=title, layout=layout)

            for r in item_rows:
                if layout == "std":
                    # C:D merged = label, E = 元方, F = 下請, G = 備考
                    label = _cell_str_preserve(ws, r, 3) or ""
                    mk = _is_checked(_cell_raw(ws, r, 5))
                    st = _is_checked(_cell_raw(ws, r, 6))
                    biko = _cell_str_preserve(ws, r, 7) or ""
                elif layout == "wide":
                    # B:D merged = label, E:F merged = check, G = 備考
                    label = _cell_str_preserve(ws, r, 2) or ""
                    mk = _is_checked(_cell_raw(ws, r, 5))
                    st = _is_checked(_cell_raw(ws, r, 6))
                    biko = _cell_str_preserve(ws, r, 7) or ""
                elif layout == "single":
                    # セクション10 (row 58): ラベルは A列, E/F でチェック, G で備考
                    label = ""  # セクションタイトル自体が明細を兼ねる
                    mk = _is_checked(_cell_raw(ws, r, 5))
                    st = _is_checked(_cell_raw(ws, r, 6))
                    biko = _cell_str_preserve(ws, r, 7) or ""
                else:
                    continue

                section.items.append(TermsItem(
                    excel_row=r,
                    label=label,
                    motokata_checked=mk,
                    shitauke_checked=st,
                    biko=biko,
                    layout=layout,
                ))

            result.sections.append(section)

        # ── サインブロック ──
        # B61:D61 → 共同企業体名
        result.motouke_group_name = _cell_str_preserve(ws, 61, 2) or ""
        # A62 → （代表構成員）
        koseiin = _cell_str_preserve(ws, 62, 1) or "（代表構成員）"
        result.daikyo_koseiin_label = koseiin

        # 左側（元請）: 行 62:住, 63:商, 64:氏 — C列に値
        result.motouke = TermsParty(
            address=_cell_str_preserve(ws, 62, 3) or "",
            company=_cell_str_preserve(ws, 63, 3) or "",
            name=_cell_str_preserve(ws, 64, 3) or "",
        )
        # 右側（下請）: 行 62:商, 63:住, 64:氏 — F列に値（F:G merged）
        result.shitauke = TermsParty(
            address=_cell_str_preserve(ws, 63, 6) or "",
            company=_cell_str_preserve(ws, 62, 6) or "",
            name=_cell_str_preserve(ws, 64, 6) or "",
        )

        logger.info(
            "契約条件書データ抽出完了: %s / vendor=%d / sections=%d, "
            "checked(元方)=%d, checked(下請)=%d",
            target_name, vendor_index, len(result.sections),
            sum(1 for s in result.sections for it in s.items if it.motokata_checked),
            sum(1 for s in result.sections for it in s.items if it.shitauke_checked),
        )
        if result.sections:
            total_items = sum(len(s.items) for s in result.sections)
            _banner(
                "JOKEN-SUCCESS",
                f"sheet='{target_name}' vendor_index={vendor_index} "
                f"sections={len(result.sections)} items={total_items}",
            )
        else:
            _banner(
                "JOKEN-FAILED",
                f"sheet='{target_name}' vendor_index={vendor_index} "
                f"sections=0 — Excel の _TERMS_SECTIONS_SPEC 対応行を確認してください",
            )

    except Exception as exc:
        _banner(
            "JOKEN-ERROR",
            f"vendor_index={vendor_index} 例外: {type(exc).__name__}: {exc}",
        )
        logger.error("契約条件書データ抽出失敗: %s / vendor=%d",
                     excel_path, vendor_index, exc_info=True)
    finally:
        wb.close()

    return result
