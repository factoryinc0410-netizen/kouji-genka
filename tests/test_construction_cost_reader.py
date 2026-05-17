"""skills/construction_cost/reader.py の純粋ヘルパに対するユニットテスト。

ここで扱う関数群は Excel I/O を持たない純粋関数であり、サンプルデータ
不要・高速に検証できる。日報 Excel の読み込みロジック (`read_daily_sheets`)
は別の Excel 依存テストでカバーする。
"""
from __future__ import annotations

import datetime as dt

import pytest

from skills.construction_cost.reader import (
    _calc_working_hours,
    _find_column,
    _is_empty,
    _map_columns,
    _time_to_hours,
    normalize_str,
)


# ────────────────────────────────────────────
# normalize_str
# ────────────────────────────────────────────
class TestNormalizeStr:
    def test_none_returns_empty(self):
        assert normalize_str(None) == ""

    @pytest.mark.parametrize("val", ["nan", "None", "NaT", "NAN", "none"])
    def test_nan_like_strings_become_empty(self, val):
        assert normalize_str(val) == ""

    def test_nan_float_becomes_empty(self):
        assert normalize_str(float("nan")) == ""

    def test_full_width_to_half_width(self):
        # 全角英数 + 全角スペース → 半角
        assert normalize_str("ＡＢＣ１２３") == "ABC123"

    def test_full_width_space_collapses(self):
        # U+3000 を半角スペースに置換し、連続空白は 1 つにまとめる
        assert normalize_str("山田　太郎") == "山田 太郎"
        assert normalize_str("山田  太郎") == "山田 太郎"

    def test_strip_leading_trailing(self):
        assert normalize_str("  山田 ") == "山田"

    def test_int_input(self):
        assert normalize_str(123) == "123"


# ────────────────────────────────────────────
# _is_empty
# ────────────────────────────────────────────
class TestIsEmpty:
    @pytest.mark.parametrize("val", [None, "", "  ", float("nan"), "nan", "NONE", "NaT"])
    def test_empty_values(self, val):
        assert _is_empty(val) is True

    @pytest.mark.parametrize("val", ["山田", 0, 0.0, "0"])
    def test_non_empty_values(self, val):
        # 注: 0 / "0" は空ではない（数値の 0 は意味のあるデータ）
        assert _is_empty(val) is False


# ────────────────────────────────────────────
# _time_to_hours
# ────────────────────────────────────────────
class TestTimeToHours:
    def test_datetime_time(self):
        assert _time_to_hours(dt.time(7, 30)) == pytest.approx(7.5)

    def test_datetime_time_with_seconds(self):
        # 7:30:00 → 7.5h
        assert _time_to_hours(dt.time(7, 30, 0)) == pytest.approx(7.5)

    def test_datetime_datetime(self):
        assert _time_to_hours(dt.datetime(2026, 5, 4, 17, 0)) == pytest.approx(17.0)

    def test_timedelta(self):
        assert _time_to_hours(dt.timedelta(hours=7, minutes=30)) == pytest.approx(7.5)

    @pytest.mark.parametrize("text,expected", [
        ("07:30", 7.5),
        ("7:30", 7.5),
        ("17:15:00", 17.25),
        ("0:00", 0.0),
        ("23:59", pytest.approx(23.9833, abs=1e-3)),
    ])
    def test_string_hh_mm(self, text, expected):
        assert _time_to_hours(text) == expected

    def test_excel_serial_fraction(self):
        # 0.3125 = 7.5h（Excel 時刻シリアル値）
        assert _time_to_hours(0.3125) == pytest.approx(7.5)

    def test_plain_float_passthrough(self):
        # 1 以上の float はそのまま時間とみなす
        assert _time_to_hours(8.0) == pytest.approx(8.0)

    def test_empty_returns_zero(self):
        assert _time_to_hours(None) == 0.0
        assert _time_to_hours("") == 0.0
        assert _time_to_hours(float("nan")) == 0.0

    def test_invalid_string_returns_zero_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            assert _time_to_hours("invalid") == 0.0


# ────────────────────────────────────────────
# _calc_working_hours
# ────────────────────────────────────────────
class TestCalcWorkingHours:
    def test_normal_day(self):
        # 8:00-17:00 休憩 1h → 8h
        assert _calc_working_hours(8.0, 17.0, 1.0) == pytest.approx(8.0)

    def test_no_break(self):
        assert _calc_working_hours(9.0, 18.0, 0.0) == pytest.approx(9.0)

    def test_overnight_shift(self):
        # 22:00 開始 → 翌 6:00 終了, 休憩 1h = 7h
        assert _calc_working_hours(22.0, 6.0, 1.0) == pytest.approx(7.0)

    def test_zero_span(self):
        assert _calc_working_hours(10.0, 10.0, 0.0) == 0.0

    def test_break_consumes_all(self):
        # 休憩が労働時間を上回るケース → 負値（reader.py 側で 0 以下を除外）
        assert _calc_working_hours(8.0, 9.0, 2.0) == pytest.approx(-1.0)


# ────────────────────────────────────────────
# _find_column
# ────────────────────────────────────────────
class TestFindColumn:
    def test_exact_match(self):
        cols = ["作業員名", "現場名", "開始時間"]
        assert _find_column(cols, "現場名") == "現場名"

    def test_normalized_match(self):
        # 全角空白入りの列名でも target と正規化後一致する
        cols = ["作業員　名", "現場名"]
        assert _find_column(cols, "作業員 名") == "作業員　名"

    def test_partial_substring_match(self):
        # target が列名に部分一致する
        cols = ["氏名（作業員名）", "現場"]
        assert _find_column(cols, "作業員名") == "氏名（作業員名）"

    def test_not_found(self):
        assert _find_column(["A", "B"], "存在しない") is None


# ────────────────────────────────────────────
# _map_columns
# ────────────────────────────────────────────
class TestMapColumns:
    def test_all_columns_found(self):
        df_cols = ["作業員名", "現場名", "備考欄"]
        result = _map_columns(df_cols, ["作業員名", "備考欄"], "1日")
        assert result == {"作業員名": "作業員名", "備考欄": "備考欄"}

    def test_missing_column_skipped(self, caplog):
        df_cols = ["作業員名"]
        with caplog.at_level("WARNING"):
            result = _map_columns(df_cols, ["作業員名", "現場名"], "1日")
        assert "作業員名" in result
        assert "現場名" not in result
