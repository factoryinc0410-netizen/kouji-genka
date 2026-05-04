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
    _banner,
    _cell_raw,
    _cell_str,
    _cell_str_preserve,
    _cell_value_preserve,
    _cell_value_strip,
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
from .irai_scan_utils import (
    KINGAKU_KEYWORD_VARIANTS,
    _KINGAKU_LABELS,
    _detect_vendor_base_cols,
    _extract_kingaku_direct,
    _find_sub_keyword_row,
    _scan_keyword_rows,
)
from .nairaku_models import NairakuData, NairakuHeaderInfo, NairakuRow
from .sheet_assignment_utils import (
    _SHEET_SCAN_MAX_COL,
    _SHEET_SCAN_MAX_ROW,
    _extract_from_first_joken,
    _find_related_sheets,
    _match_score,
    _sheet_contains_vendor,
    build_sheet_assignment,
)
from .nairaku_text_utils import (
    NAIRAKU_ROWS_PER_PAGE,
    _classify_sheet_type,
    _count_indent,
    _is_composite_footer_text,
    _is_footer_terminator,
    _is_note_text,
    _is_subtotal_text,
    _JOKEN_KEYWORDS,
    _NAIRAKU_COMPOSITE_FOOTER_REQUIRED,
    _NAIRAKU_FOOTER_TERMINATORS,
    _NAIRAKU_HEADER_KEYWORDS,
    _NAIRAKU_KEYWORDS,
    _NAIRAKU_NOTE_KEYWORDS,
    _NAIRAKU_SUBTOTAL_KEYWORDS,
    _normalize_for_footer,
    _row_contains_composite_footer,
)
from .terms_extraction import (
    _TERMS_SECTIONS_SPEC,
    _is_checked,
    extract_terms_data,
    scan_contract_change_count,
)
from .terms_models import TermsData, TermsItem, TermsParty, TermsSection
from .vml_utils import (
    _CHECKBOX_ROWS,
    _VML_MOTOKATA_X,
    _VML_ROW_BASE,
    _VML_SECTION9_X,
    _VML_SHITAUKE_X,
    _VML_Y_BASE,
    _VML_Y_STEP,
    _extract_checkboxes_from_vml,
    _find_keyword_row,
    _read_adjacent_value,
    _vml_y_to_row,
    extract_joken_text_data,
)

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  分離済みモジュール (re-export はファイル先頭の import を参照)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   extractor_utils.py        — 純粋ユーティリティ + Cell アクセスラッパー
#                                (_normalize, _clean_amount, _format_wareki,
#                                 _serial_to_datetime, _cell_str, _banner ...)
#   irai_scan_utils.py        — 依頼書キーワード走査 + 金額抽出
#                                (_detect_vendor_base_cols, _scan_keyword_rows,
#                                 _extract_kingaku_direct, _KINGAKU_LABELS ...)
#   nairaku_text_utils.py     — 内訳書のテキスト判定 + シート種別分類
#                                (_classify_sheet_type, _is_subtotal_text,
#                                 _is_footer_terminator, _NAIRAKU_*_KEYWORDS ...)
#   sheet_assignment_utils.py — 業者×シートのマッチングと割り当て
#                                (build_sheet_assignment, _match_score,
#                                 _extract_from_first_joken ...)
#   vml_utils.py              — 契約条件書テキスト抽出 + VML チェックボックス
#                                (extract_joken_text_data,
#                                 _extract_checkboxes_from_vml ...)
#   terms_extraction.py       — 契約条件書 (TermsData) 構造化抽出 +
#                                依頼書からの契約変更回数スキャン
#                                (extract_terms_data, scan_contract_change_count,
#                                 _is_checked, _TERMS_SECTIONS_SPEC)


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
# 内訳書のフッター/サブトータル/ヘッダー判定と関連定数
# (_NAIRAKU_*_KEYWORDS, _is_subtotal_text, _is_footer_terminator 等) は
# `nairaku_text_utils.py` に分離。このモジュールは Worksheet 走査側の
# ロジック (`extract_nairaku_data`, `_detect_header_end_row` 等) を担当する。


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

