"""skills/order_docs/extractor.py の純粋ヘルパに対するユニットテスト。

Excel I/O を伴う関数（extract_data / extract_nairaku_data 等）は
別ファイルで Excel フィクスチャを使ってカバーする。本ファイルは
Excel 不要で常時実行できる軽量な関数のみを対象とする。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from skills.order_docs.extractor import (
    _classify_sheet_type,
    _clean_amount,
    _count_indent,
    _extract_core_name,
    _format_wareki,
    _is_composite_footer_text,
    _is_excel_serial,
    _is_footer_terminator,
    _is_note_text,
    _is_subtotal_text,
    _normalize,
    _normalize_for_footer,
    _parse_contract_date,
    _parse_kouki,
    _row_contains_composite_footer,
    _safe_float,
    _safe_int,
    _serial_to_datetime,
)


# ────────────────────────────────────────────
# _normalize / _extract_core_name
# ────────────────────────────────────────────
class TestNormalize:
    def test_full_width_paren_to_half(self):
        assert _normalize("内訳書（業者1）") == "内訳書(業者1)"

    def test_strip_whitespace(self):
        assert _normalize("内 訳\n書") == "内訳書"

    def test_zero_width_chars_removed(self):
        # ZWSP (U+200B) を含む
        assert _normalize("内​訳書") == "内訳書"

    def test_full_width_to_half_alphanumeric(self):
        assert _normalize("ＡＢＣ１２３") == "ABC123"


class TestExtractCoreName:
    @pytest.mark.parametrize("inp,expected", [
        ("株式会社　法面", "法面"),
        ("有限会社 山田建設", "山田建設"),
        ("(株)テスト工業", "テスト工業"),
        ("テスト工業", "テスト工業"),  # 法人格無しはそのまま
    ])
    def test_strips_corporate_suffix(self, inp, expected):
        assert _extract_core_name(inp) == expected


# ────────────────────────────────────────────
# Excel シリアル値
# ────────────────────────────────────────────
class TestIsExcelSerial:
    @pytest.mark.parametrize("val", [1, 100.0, 46091, 73415, "46091"])
    def test_within_range(self, val):
        assert _is_excel_serial(val) is True

    @pytest.mark.parametrize("val", [0, -1, 100000, 73416, "abc", "12.5", None])
    def test_out_of_range(self, val):
        assert _is_excel_serial(val) is False


class TestSerialToDatetime:
    def test_known_value(self):
        # 46091 = 2026-03-10 (Excel epoch 1899-12-30 + 46091 days)
        result = _serial_to_datetime(46091)
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 10

    def test_string_input(self):
        result = _serial_to_datetime("46091")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 10


# ────────────────────────────────────────────
# 金額パーサ
# ────────────────────────────────────────────
class TestCleanAmount:
    @pytest.mark.parametrize("inp,expected", [
        (1000000, "1000000"),
        (1234.0, "1234"),         # 小数点無しの float は int に
        ("1,000,000", "1000000"),
        ("¥1,000", "1000"),
        ("￥1,000円", "1000"),
        ("1 000 000", "1000000"),
        ("　1,000　", "1000"),
        (None, None),
        ("", None),
        ("abc", None),
    ])
    def test_various_inputs(self, inp, expected):
        assert _clean_amount(inp) == expected


class TestSafeInt:
    @pytest.mark.parametrize("inp,expected", [
        ("1234", 1234),
        ("1,234", 1234),
        ("¥1,000", 1000),
        ("-500", -500),
        (None, 0),
        ("", 0),
        ("abc", 0),
    ])
    def test_various_inputs(self, inp, expected):
        assert _safe_int(inp) == expected


class TestSafeFloat:
    @pytest.mark.parametrize("inp,expected", [
        (1.5, 1.5),
        (3, 3.0),
        ("1.5", 1.5),
        ("1,234.5", 1234.5),
        (None, None),
        ("", None),
        ("invalid", None),
    ])
    def test_various_inputs(self, inp, expected):
        assert _safe_float(inp) == expected


# ────────────────────────────────────────────
# 和暦変換
# ────────────────────────────────────────────
class TestFormatWareki:
    def test_reiwa_8(self):
        # 2026 = 令和8年
        assert _format_wareki(datetime(2026, 1, 28)) == "令和8年1月28日"

    def test_reiwa_1(self):
        # 2019-05-01 が令和元年（本実装では「令和1年」表記）
        assert _format_wareki(datetime(2019, 5, 1)) == "令和1年5月1日"

    def test_pre_reiwa_falls_back_to_seireki(self):
        # 平成以前は西暦フォールバック
        assert _format_wareki(datetime(2018, 12, 31)) == "2018年12月31日"

    def test_none_returns_empty(self):
        assert _format_wareki(None) == ""


# ────────────────────────────────────────────
# 契約日パーサ
# ────────────────────────────────────────────
class TestParseContractDate:
    def test_datetime_input(self):
        r = _parse_contract_date(datetime(2026, 1, 28))
        assert r == {
            "contract_year": "8",
            "contract_month": "1",
            "contract_day": "28",
        }

    def test_excel_serial(self):
        # 46050 = 2026-01-19
        r = _parse_contract_date(46050)
        assert r["contract_year"] == "8"
        assert r["contract_month"] == "1"

    def test_wareki_string(self):
        r = _parse_contract_date("令和7年12月1日")
        assert r == {
            "contract_year": "7",
            "contract_month": "12",
            "contract_day": "1",
        }

    @pytest.mark.parametrize("text", ["2025-12-01", "2025/12/1"])
    def test_seireki_string(self, text):
        r = _parse_contract_date(text)
        assert r["contract_year"] == "7"  # 2025 - 2018 = 7
        assert r["contract_month"] == "12"

    def test_none_returns_all_none(self):
        r = _parse_contract_date(None)
        assert all(v is None for v in r.values())

    def test_unparseable_returns_all_none(self):
        r = _parse_contract_date("解読不能なテキスト")
        assert all(v is None for v in r.values())


# ────────────────────────────────────────────
# 工期パーサ
# ────────────────────────────────────────────
class TestParseKouki:
    @pytest.mark.parametrize("sep", ["〜", "～", "~", "-"])
    def test_wareki_range_with_separators(self, sep):
        text = f"令和7年12月8日{sep}令和10年3月31日"
        r = _parse_kouki(text)
        assert r["kouki_start_year"] == "7"
        assert r["kouki_start_month"] == "12"
        assert r["kouki_start_day"] == "8"
        assert r["kouki_end_year"] == "10"
        assert r["kouki_end_month"] == "3"
        assert r["kouki_end_day"] == "31"

    def test_seireki_range(self):
        r = _parse_kouki("2025/12/8〜2028/3/31")
        assert r["kouki_start_year"] == "7"   # 2025 - 2018
        assert r["kouki_end_year"] == "10"    # 2028 - 2018

    def test_datetime_input_single_day(self):
        r = _parse_kouki(datetime(2026, 2, 1))
        assert r["kouki_start_year"] == "8"
        assert r["kouki_end_year"] is None

    def test_none_returns_all_none(self):
        r = _parse_kouki(None)
        assert all(v is None for v in r.values())


# ────────────────────────────────────────────
# シート種別判定
# ────────────────────────────────────────────
class TestClassifySheetType:
    @pytest.mark.parametrize("name,expected", [
        ("内訳書（業者1）", "nairaku"),
        ("内訳書", "nairaku"),
        ("内訳", "nairaku"),
        ("契約条件書 （業者2）", "joken"),
        ("条件書", "joken"),
        ("依頼書", None),
        ("注文書テンプレート", None),
    ])
    def test_classifies(self, name, expected):
        assert _classify_sheet_type(name) == expected


# ────────────────────────────────────────────
# 内訳書フッター判定
# ────────────────────────────────────────────
class TestNormalizeForFooter:
    def test_strips_full_width_paren_and_punctuation(self):
        # _normalize により（）→() に変換され、_normalize_for_footer が () と「、」を除去する。
        # 【】は _normalize で[] に変換されるが、_normalize_for_footer の除去リストに
        # [] は含まれていないため [下請金額] の角括弧はそのまま残る（実装の現挙動を固定）。
        result = _normalize_for_footer("【下請金額】（テスト）、")
        assert "（" not in result and "）" not in result  # 全角括弧は完全に除去
        assert "(" not in result and ")" not in result  # 半角丸括弧も除去
        assert "、" not in result
        assert "下請金額" in result
        assert "テスト" in result


class TestIsCompositeFooterText:
    def test_composite_footer_detected(self):
        # 労務費・法定福利費 がそれぞれ 2 回以上、かつ「下請金額」が含まれる
        text = (
            "【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、"
            "労務費、法定福利費】"
        )
        assert _is_composite_footer_text(text) is True

    def test_old_style_preface_not_composite(self):
        # 旧式の前置き文（労務費・法定福利費が 1 回ずつ）は False
        text = "※下請金額に含まれる労務費及び法定福利費（事業者負担分）について"
        assert _is_composite_footer_text(text) is False

    def test_empty_returns_false(self):
        assert _is_composite_footer_text("") is False
        assert _is_composite_footer_text(None) is False


class TestRowContainsCompositeFooter:
    def test_one_cell_matches(self):
        composite = (
            "【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、"
            "労務費、法定福利費】"
        )
        assert _row_contains_composite_footer("通常テキスト", composite, None) is True

    def test_no_cell_matches(self):
        assert _row_contains_composite_footer("aaa", "bbb", None) is False


class TestIsFooterTerminator:
    def test_excludes_preface(self):
        # 前置き文は終端と判定しない
        text = "※下請金額に含まれる労務費及び法定福利費（事業者負担分）について"
        assert _is_footer_terminator(text) is False

    def test_empty_returns_false(self):
        assert _is_footer_terminator("") is False
        assert _is_footer_terminator(None) is False


# ────────────────────────────────────────────
# インデント・小計・注記
# ────────────────────────────────────────────
class TestCountIndent:
    @pytest.mark.parametrize("text,expected", [
        ("　工種A", 1),
        ("　　細別A", 2),
        ("　　　規格A", 3),
        ("インデントなし", 0),
        ("", 0),
    ])
    def test_counts(self, text, expected):
        assert _count_indent(text) == expected

    def test_only_leading_zenkaku_counted(self):
        # 先頭以外の全角空白は数えない
        assert _count_indent("　工　種") == 1


class TestIsSubtotalText:
    def test_known_keywords(self):
        # _NAIRAKU_SUBTOTAL_KEYWORDS の中身は config 由来なのでスモーク確認のみ
        # （keyword が一切無いテキストは False のはず）
        assert _is_subtotal_text("普通の作業内容テキスト") is False

    def test_empty_returns_false(self):
        # _normalize("") == "" になるが any() は False を返す
        assert _is_subtotal_text("") is False


class TestIsNoteText:
    def test_unrelated_text_returns_false(self):
        assert _is_note_text("通常の項目名テキスト") is False
