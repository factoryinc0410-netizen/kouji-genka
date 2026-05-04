"""Excel データ抽出の正答性テスト（サンプル Excel 必須）。

サンプル Excel 由来の `extracted_vendors.json` を「正」として
`extract_data()` の現在の出力を回帰検証する。
extract_nairaku_data / extract_terms_data についても、最低限の
構造（行数 ≥ 1、セクション ≥ 1 等）が崩れていないかを確認する。

サンプル Excel が無い環境では sample_excel fixture により skip される。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_sample


# ────────────────────────────────────────────
# extract_data: 業者一覧の構造的同一性
# ────────────────────────────────────────────
class TestExtractData:
    def test_returns_list_of_dicts(self, sample_excel: Path):
        from skills.order_docs.extractor import extract_data
        vendors = extract_data(sample_excel)
        assert isinstance(vendors, list)
        assert len(vendors) >= 1
        for v in vendors:
            assert isinstance(v, dict)
            assert "vendor_company" in v

    def test_vendor_count_matches_extracted_json(
        self, sample_excel: Path, sample_extracted_json: Path | None
    ):
        if sample_extracted_json is None:
            pytest.skip("extracted_vendors.json が無いため照合できない")

        from skills.order_docs.extractor import extract_data
        actual = extract_data(sample_excel)
        expected = json.loads(sample_extracted_json.read_text(encoding="utf-8"))
        assert len(actual) == len(expected), (
            f"業者数: actual={len(actual)} expected={len(expected)}"
        )

    def test_vendor_companies_match_extracted_json(
        self, sample_excel: Path, sample_extracted_json: Path | None
    ):
        if sample_extracted_json is None:
            pytest.skip("extracted_vendors.json が無いため照合できない")

        from skills.order_docs.extractor import extract_data
        actual = extract_data(sample_excel)
        expected = json.loads(sample_extracted_json.read_text(encoding="utf-8"))

        actual_names = [v.get("vendor_company") for v in actual]
        expected_names = [v.get("vendor_company") for v in expected]
        assert actual_names == expected_names

    @pytest.mark.parametrize("field", [
        "koji_kenmei", "koji_basho", "vendor_company",
        "contract_year", "contract_month", "contract_day",
        "kingaku_koji", "kingaku_zei", "kingaku_ukeoi",
        "nairaku_sheet", "joken_sheet",
    ])
    def test_critical_fields_match_extracted_json(
        self, sample_excel: Path, sample_extracted_json: Path | None, field: str
    ):
        """各業者ごとに、保存された期待値（extracted_vendors.json）と
        全業者で同一の値を抽出することを確認する。"""
        if sample_extracted_json is None:
            pytest.skip("extracted_vendors.json が無いため照合できない")

        from skills.order_docs.extractor import extract_data
        actual = extract_data(sample_excel)
        expected = json.loads(sample_extracted_json.read_text(encoding="utf-8"))

        for i, (a, e) in enumerate(zip(actual, expected)):
            assert a.get(field) == e.get(field), (
                f"業者{i+1} の {field}: actual={a.get(field)!r} "
                f"expected={e.get(field)!r}"
            )


# ────────────────────────────────────────────
# extract_nairaku_data: 内訳書の構造
# ────────────────────────────────────────────
class TestExtractNairakuData:
    def test_returns_nairaku_data_for_first_vendor(self, sample_excel: Path):
        from skills.order_docs.extractor import extract_data, extract_nairaku_data
        from skills.order_docs.nairaku_models import NairakuData

        vendors = extract_data(sample_excel)
        v1 = vendors[0]
        sheet = v1.get("nairaku_sheet")
        assert sheet, "業者1 の nairaku_sheet が未設定"

        nd = extract_nairaku_data(sample_excel, sheet)
        assert isinstance(nd, NairakuData)
        # 行と列ヘッダ・本体構造が崩れていないことを確認
        assert len(nd.rows) >= 1
        assert nd.header is not None

    def test_all_assigned_vendors_have_nairaku_rows(self, sample_excel: Path):
        from skills.order_docs.extractor import extract_data, extract_nairaku_data

        vendors = extract_data(sample_excel)
        for i, v in enumerate(vendors, start=1):
            sheet = v.get("nairaku_sheet")
            if not sheet:
                continue  # 未割当の業者はスキップ
            nd = extract_nairaku_data(sample_excel, sheet)
            assert len(nd.rows) >= 1, (
                f"業者{i} ({v.get('vendor_company')}): 内訳書の行が 0"
            )


# ────────────────────────────────────────────
# extract_terms_data: 契約条件書の構造
# ────────────────────────────────────────────
class TestExtractTermsData:
    def test_returns_sections_for_first_vendor(self, sample_excel: Path):
        from skills.order_docs.extractor import extract_data, extract_terms_data
        from skills.order_docs.terms_models import TermsData

        vendors = extract_data(sample_excel)
        td = extract_terms_data(sample_excel, 1)
        assert isinstance(td, TermsData)
        assert len(td.sections) >= 1, "契約条件書のセクションが 0"

    def test_all_vendors_yield_terms_data(self, sample_excel: Path):
        from skills.order_docs.extractor import extract_data, extract_terms_data

        vendors = extract_data(sample_excel)
        for i in range(1, len(vendors) + 1):
            td = extract_terms_data(sample_excel, i)
            assert len(td.sections) >= 1, (
                f"業者{i}: 契約条件書のセクションが 0"
            )
