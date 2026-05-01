"""
工事日報集計スキル — 日報Excel読み込み

日報ファイル構成:
  - 日別シート（"1日"〜"31日"）: ヘッダ1行目（index=0）
    列: 作業員名, 現場名, 開始時間, 終了時間, 休憩時間, 備考欄

読み込み後、以下を自動計算して DataFrame に追加する:
  - 実労働時間 = (終了 - 開始 - 休憩)
  - 基本 = min(実労働, 8.0)
  - 残業 = max(実労働 - 8.0, 0.0)
"""
from __future__ import annotations

import datetime
import logging
import math
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from skills.construction_cost.config import (
    BASIC_HOURS_LIMIT,
    DAILY_SHEET_HEADER_ROW,
    DAILY_SHEET_SUFFIX,
    EXCLUDE_SITE_KEYWORDS,
)

logger = logging.getLogger("skills.construction_cost.reader")


# ────────────────────────────────────────────
# 文字列正規化ユーティリティ
# ────────────────────────────────────────────

def normalize_str(val) -> str:
    """文字列を正規化する。

    - None / NaN / "nan" → 空文字
    - 全角英数・カナ → 半角に統一 (NFKC)
    - 前後の空白・全角スペース除去
    - 連続する空白を1つに縮約
    """
    if val is None:
        return ""
    s = str(val)
    if s.lower() in ("nan", "none", "nat"):
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_empty(val) -> bool:
    """値が実質的に空かどうか判定する。"""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "none", "nat")


# ────────────────────────────────────────────
# 時間変換
# ────────────────────────────────────────────

def _time_to_hours(val) -> float:
    """時刻値を十進数の時間に変換する。

    対応形式:
      - datetime.time / datetime.datetime (07:30:00 → 7.5)
      - datetime.timedelta (7:30:00 → 7.5)
      - 文字列 "07:30" / "7:30" / "HH:MM:SS"
      - 数値 0.3125 (Excelシリアル値) → 7.5
      - 空 / NaN → 0.0
    """
    if _is_empty(val):
        return 0.0

    if isinstance(val, (int, float)):
        if math.isnan(val):
            return 0.0
        if 0 < val < 1:
            return round(val * 24, 4)
        return float(val)

    if isinstance(val, datetime.timedelta):
        return round(val.total_seconds() / 3600, 4)

    if isinstance(val, datetime.time):
        return val.hour + val.minute / 60 + val.second / 3600

    if isinstance(val, datetime.datetime):
        return val.hour + val.minute / 60 + val.second / 3600

    s = normalize_str(str(val))
    if not s:
        return 0.0

    m = re.match(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", s)
    if m:
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        return round(h + mi / 60 + se / 3600, 4)

    try:
        fval = float(s)
        if math.isnan(fval):
            return 0.0
        if 0 < fval < 1:
            return round(fval * 24, 4)
        return fval
    except ValueError:
        logger.warning("時間変換不可 (0.0として扱います): '%s'", val)
        return 0.0


# ────────────────────────────────────────────
# シート検出
# ────────────────────────────────────────────

def _find_daily_sheets(xls: pd.ExcelFile) -> list[str]:
    """日別シート名（"1日"〜"31日"）を抽出して返す。"""
    sheets = []
    for name in xls.sheet_names:
        stripped = normalize_str(name)
        if stripped.endswith(DAILY_SHEET_SUFFIX):
            num_part = stripped[: -len(DAILY_SHEET_SUFFIX)]
            if num_part.isdigit() and 1 <= int(num_part) <= 31:
                sheets.append(name)
    return sheets


# ────────────────────────────────────────────
# 列名のファジーマッチ
# ────────────────────────────────────────────

def _find_column(columns: list[str], target: str) -> str | None:
    """列名リストから target に最も近い列名を返す（正規化比較）。"""
    target_norm = normalize_str(target)
    for col in columns:
        if normalize_str(col) == target_norm:
            return col
    for col in columns:
        if target_norm in normalize_str(col):
            return col
    return None


def _map_columns(df_columns: list[str], required: list[str], sheet_name: str) -> dict[str, str]:
    """DataFrameの実際の列名 → 正規化した列名 のマッピングを返す。"""
    mapping = {}
    for target in required:
        found = _find_column(df_columns, target)
        if found:
            mapping[target] = found
        else:
            logger.warning("シート '%s': 列 '%s' が見つかりません", sheet_name, target)
    return mapping


# ────────────────────────────────────────────
# 実労働時間の計算
# ────────────────────────────────────────────

def _calc_working_hours(start_h: float, end_h: float, break_h: float) -> float:
    """開始・終了・休憩から実労働時間を算出する。

    日跨ぎ（終了 < 開始）にも対応。
    """
    span = end_h - start_h
    if span < 0:
        span += 24.0  # 日跨ぎ補正
    actual = span - break_h
    return round(actual, 4)


# ────────────────────────────────────────────
# 日別シート読み込み
# ────────────────────────────────────────────

def read_daily_sheets(source: str | Path | BinaryIO) -> pd.DataFrame:
    """全日別シートを読み込んで統合 DataFrame を返す。

    Returns:
        DataFrame columns:
          [日付, 作業員名, 現場名, 開始時間, 終了時間, 休憩時間,
           実労働時間, 基本, 残業, 備考欄]
        - 作業員名・現場名は normalize_str() で正規化済み
        - 時間列は十進数の時間（float）に変換済み
    """
    xls = pd.ExcelFile(source, engine="openpyxl")
    daily_sheets = _find_daily_sheets(xls)

    if not daily_sheets:
        logger.error("シート一覧: %s", xls.sheet_names)
        raise ValueError("日別シート（'1日'〜'31日'）が見つかりません。")

    logger.info("検出された日別シート: %s", [str(s) for s in daily_sheets])
    all_rows: list[pd.DataFrame] = []

    for sheet_name in daily_sheets:
        day_num = int(normalize_str(sheet_name)[: -len(DAILY_SHEET_SUFFIX)])
        try:
            df = pd.read_excel(
                xls,
                sheet_name=sheet_name,
                header=DAILY_SHEET_HEADER_ROW,
                dtype=str,
            )
        except Exception as e:
            logger.warning("シート '%s' の読み込みをスキップ: %s", sheet_name, e)
            continue

        raw_columns = [str(c).strip() for c in df.columns]
        df.columns = raw_columns

        # 列名マッピング（表記揺れ対応）
        col_map = _map_columns(
            raw_columns,
            ["作業員名", "現場名", "開始時間", "終了時間", "休憩時間", "備考欄"],
            sheet_name,
        )

        name_col = col_map.get("作業員名")
        site_col = col_map.get("現場名")
        start_col = col_map.get("開始時間")
        end_col = col_map.get("終了時間")
        break_col = col_map.get("休憩時間")
        note_col = col_map.get("備考欄")

        if not name_col or not site_col:
            logger.warning(
                "シート '%s': 作業員名/現場名列が特定できません。スキップ (列: %s)",
                sheet_name, raw_columns,
            )
            continue

        # 作業員名の正規化 & 空行除外
        df["_作業員名"] = df[name_col].apply(normalize_str)
        df["_現場名"] = df[site_col].apply(normalize_str)
        df = df[df["_作業員名"].ne("")]

        if df.empty:
            continue

        # 除外処理: 現場名が空 or 除外キーワードを含む
        def _should_exclude(site: str) -> bool:
            if not site:
                return True
            return any(kw in site for kw in EXCLUDE_SITE_KEYWORDS)

        mask_exclude = df["_現場名"].apply(_should_exclude)
        df_valid = df[~mask_exclude].copy()
        excluded_count = mask_exclude.sum()

        if df_valid.empty:
            if excluded_count > 0:
                logger.info("  シート '%s': %d行すべて除外（休日/有休/欠勤/空）", sheet_name, excluded_count)
            continue

        # 時間列の変換
        df_valid["_開始h"] = df_valid[start_col].apply(_time_to_hours) if start_col else 0.0
        df_valid["_終了h"] = df_valid[end_col].apply(_time_to_hours) if end_col else 0.0
        df_valid["_休憩h"] = df_valid[break_col].apply(_time_to_hours) if break_col else 0.0

        # 開始 or 終了が空のレコードを除外
        if start_col and end_col:
            has_start = df_valid[start_col].apply(lambda v: not _is_empty(v))
            has_end = df_valid[end_col].apply(lambda v: not _is_empty(v))
            df_valid = df_valid[has_start & has_end].copy()

        if df_valid.empty:
            continue

        # 実労働時間の自動計算
        actual_hours = []
        basic_hours = []
        overtime_hours = []
        valid_mask = []

        for _, row in df_valid.iterrows():
            actual = _calc_working_hours(row["_開始h"], row["_終了h"], row["_休憩h"])
            if actual <= 0:
                logger.warning(
                    "  シート '%s': %s の実労働時間が0以下 (%.2fh) → 除外",
                    sheet_name, row["_作業員名"], actual,
                )
                valid_mask.append(False)
                actual_hours.append(0.0)
                basic_hours.append(0.0)
                overtime_hours.append(0.0)
            else:
                valid_mask.append(True)
                actual_hours.append(actual)
                basic_hours.append(min(actual, BASIC_HOURS_LIMIT))
                overtime_hours.append(max(actual - BASIC_HOURS_LIMIT, 0.0))

        df_valid["_実労働"] = actual_hours
        df_valid["_基本"] = basic_hours
        df_valid["_残業"] = overtime_hours
        df_valid = df_valid[valid_mask].copy()

        if df_valid.empty:
            continue

        # 出力用 DataFrame 構築
        n = len(df_valid)
        out = pd.DataFrame({
            "日付": [day_num] * n,
            "作業員名": df_valid["_作業員名"].values,
            "現場名": df_valid["_現場名"].values,
            "開始時間": df_valid["_開始h"].values,
            "終了時間": df_valid["_終了h"].values,
            "休憩時間": df_valid["_休憩h"].values,
            "実労働時間": df_valid["_実労働"].values,
            "基本": df_valid["_基本"].values,
            "残業": df_valid["_残業"].values,
            "備考欄": df_valid[note_col].apply(normalize_str).values if note_col else [""] * n,
        })

        all_rows.append(out)
        logger.info("  シート '%s': %d件読み込み（除外%d件）", sheet_name, len(out), excluded_count)

    if not all_rows:
        raise ValueError("有効なデータが1件も見つかりませんでした。")

    result = pd.concat(all_rows, ignore_index=True)
    logger.info("日別シート読み込み完了: 合計 %d 件", len(result))
    logger.info("  作業員一覧 (%d名): %s", result["作業員名"].nunique(), sorted(result["作業員名"].unique()))
    logger.info("  現場名一覧 (%d件): %s", result["現場名"].nunique(), sorted(result["現場名"].unique()))

    return result
