"""
業者 × シート (内訳書 / 契約条件書) のマッチングと割り当て処理。

extractor.py から切り出した「全シート × 全業者」のスコアリング
および排他的割り当てロジックを集約する。

戦略:
  1. シート名のキーワード分類で内訳書/契約条件書/その他に振り分け
  2. 各シートと各業者の組み合わせをスコアリング
     (セル値ベース → シート名ベースのフォールバック)
  3. スコア降順で貪欲に「1シート=1業者」の排他的割り当て

extractor.py からは re-export することで、既存の
`from skills.order_docs.extractor import build_sheet_assignment` 等の
インポートを壊さずに済む。
"""
from __future__ import annotations

import logging
import re

from . import config
from .extractor_utils import _cell_str, _extract_core_name, _normalize
from .nairaku_text_utils import (
    _JOKEN_KEYWORDS,
    _NAIRAKU_KEYWORDS,
    _classify_sheet_type,
)

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  シート走査範囲の上限
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# セル値スキャン範囲: 内訳書はヘッダ部(上位)、契約条件書は下部(行60-65付近)
# 上限値は config.EXCEL_SCAN_LIMITS に集約。
# （モジュールレベル変数は後方互換のため残置）
_SHEET_SCAN_MAX_ROW = config.EXCEL_SCAN_LIMITS["sheet_scan_max_row"]
_SHEET_SCAN_MAX_COL = config.EXCEL_SCAN_LIMITS["sheet_scan_max_col"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  業者 × シートのマッチング判定
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
