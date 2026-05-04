"""
extractor.py から切り出した純粋ユーティリティ関数群。

ここに置かれる関数は以下の条件をすべて満たす:
  - クラスのインスタンス状態 (self) に依存しない
  - openpyxl の Worksheet / Workbook を引数に取らない
  - モジュール外部の可変状態 (グローバル変数, I/O) に依存しない
  - 入力値のみで結果が決まる（同じ引数なら同じ結果）

extractor.py からは re-export することで、既存の
`from skills.order_docs.extractor import _normalize` 等のインポートを
壊さずに済む。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """全角→半角、空白・改行・ゼロ幅文字除去、括弧統一など揺れを吸収した比較用文字列を返す。"""
    s = unicodedata.normalize("NFKC", text)
    # 空白・改行・タブ・全角スペース・ノーブレークスペース・ゼロ幅文字を除去
    s = re.sub(r"[\s　 ​‌‍﻿\r\n\t]+", "", s)
    s = s.replace("（", "(").replace("）", ")")    # 全角丸括弧→半角
    s = s.replace("【", "[").replace("】", "]")    # 全角隅付き→半角角括弧
    return s


def _is_excel_serial(value: Any) -> bool:
    """値が Excel の日付シリアル値と思われるかを判定する。"""
    if isinstance(value, (int, float)):
        # 1900年〜2100年の範囲: シリアル値 1 ～ 73415
        return 1 <= value <= 73415
    if isinstance(value, str):
        # 数字のみの文字列で5桁程度
        stripped = value.strip()
        if re.fullmatch(r"\d{4,6}", stripped):
            num = int(stripped)
            return 1 <= num <= 73415
    return False


def _clean_amount(raw: Any) -> str | None:
    """
    金額の生値をクリーニングして文字列で返す。
    - 数値型 (int/float) はそのまま文字列化
    - 文字列の場合はカンマ・円マーク・¥・スペースを除去して int 変換を試行
    - 変換できなければ None
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # 小数点以下がない場合は整数文字列に
        if isinstance(raw, float) and raw == int(raw):
            return str(int(raw))
        return str(raw)
    # 文字列の場合: カンマ・円・¥・スペースを除去
    s = str(raw).strip()
    s = re.sub(r"[,，、円\xa5￥\s　]+", "", s)
    if not s:
        return None
    try:
        return str(int(s))
    except ValueError:
        try:
            return str(int(float(s)))
        except ValueError:
            logger.warning("金額変換失敗: '%s' (元値: %s)", s, repr(raw))
            return None


def _safe_int(s: str | None) -> int:
    """金額文字列を安全に int に変換する。None/空/変換不可は 0 を返す。"""
    if not s:
        return 0
    try:
        return int(re.sub(r"[^\d\-]", "", s))
    except ValueError:
        return 0


def _safe_float(value: Any) -> float | None:
    """セル値を float に変換する。None / 空 / 変換不可は None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None
