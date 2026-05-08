"""skills/qualifications/schema.py の単体テスト。

Gemini が返す JSON を Pydantic で安全に取り込めることを保証する。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from skills.qualifications.schema import (
    Candidate,
    FieldConfidences,
    OCRResponse,
)


# ────────────────────────────────────────────
# OCRResponse: トップレベル
# ────────────────────────────────────────────

class TestOCRResponse:
    def test_empty_response_is_valid(self):
        """空応答（Gemini が何も抽出できなかった）も妥当として通す。"""
        r = OCRResponse()
        assert r.candidates == []
        assert r.overall_confidence == 0.0

    def test_round_trip_full_payload(self):
        """Gemini が返す典型レスポンスを丸ごと取り込めること。"""
        payload = {
            "candidates": [
                {
                    "page_indices": [0, 1],
                    "qualification_name": "玉掛け技能講習",
                    "category": "技能講習",
                    "worker_name": "山田太郎",
                    "certificate_no": "第12345号",
                    "issued_on": "2024-04-01",
                    "expires_on": None,
                    "renewal_required": False,
                    "issuer": "○○技能講習センター",
                    "field_confidences": {
                        "qualification_name": 0.96,
                        "worker_name": 0.99,
                        "certificate_no": 0.97,
                        "issued_on": 0.93,
                        "expires_on": None,
                        "issuer": 0.78,
                    },
                }
            ],
            "overall_confidence": 0.92,
        }
        r = OCRResponse.model_validate(payload)
        assert len(r.candidates) == 1
        c = r.candidates[0]
        assert c.qualification_name == "玉掛け技能講習"
        assert c.renewal_required is False
        assert c.expires_on is None
        assert c.field_confidences.worker_name == 0.99
        assert c.field_confidences.expires_on is None

    def test_overall_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            OCRResponse(overall_confidence=1.5)
        with pytest.raises(ValidationError):
            OCRResponse(overall_confidence=-0.01)

    def test_extra_fields_are_ignored(self):
        """Gemini が schema にない追加フィールドを返しても落ちない。"""
        r = OCRResponse.model_validate({
            "candidates": [],
            "overall_confidence": 0.5,
            "debug_info": "Gemini は親切に余計な情報を入れてくることがある",
        })
        assert r.overall_confidence == 0.5


# ────────────────────────────────────────────
# Candidate: 個別の資格者証
# ────────────────────────────────────────────

class TestCandidate:
    def test_minimal_candidate(self):
        c = Candidate()
        assert c.page_indices == []
        assert c.qualification_name is None
        assert c.renewal_required is True   # 保守的デフォルト

    def test_iso_date_accepted(self):
        c = Candidate(issued_on="2024-04-01", expires_on="2029-03-31")
        assert c.issued_on == "2024-04-01"
        assert c.expires_on == "2029-03-31"

    def test_empty_string_date_normalized_to_none(self):
        """空文字も None と同等に扱う（Gemini が "" を返すケース対策）。"""
        c = Candidate(issued_on="", expires_on="")
        assert c.issued_on is None
        assert c.expires_on is None

    @pytest.mark.parametrize("bad_date", [
        "2024/04/01",     # スラッシュ区切り
        "2024-4-1",       # ゼロ埋めなし
        "令和6年4月1日",  # 和暦未変換 (Gemini プロンプトで防ぐが念のため)
        "2024-13-01",     # 存在しない月
        "2024-02-30",     # 存在しない日
        "abcdef",
    ])
    def test_invalid_date_rejected(self, bad_date):
        with pytest.raises(ValidationError):
            Candidate(issued_on=bad_date)

    def test_negative_page_index_rejected(self):
        with pytest.raises(ValidationError):
            Candidate(page_indices=[0, -1])

    def test_multi_page_pair(self):
        """表裏セット: page_indices に 2 ページ分。"""
        c = Candidate(page_indices=[0, 1])
        assert c.page_indices == [0, 1]


# ────────────────────────────────────────────
# FieldConfidences: フィールド単位の信頼度
# ────────────────────────────────────────────

class TestFieldConfidences:
    def test_all_optional(self):
        fc = FieldConfidences()
        assert fc.qualification_name is None
        assert fc.worker_name is None

    @pytest.mark.parametrize("v", [-0.01, 1.01, 2.0, -1.0])
    def test_out_of_range_rejected(self, v):
        with pytest.raises(ValidationError):
            FieldConfidences(qualification_name=v)

    @pytest.mark.parametrize("v", [0.0, 0.5, 1.0])
    def test_boundary_accepted(self, v):
        fc = FieldConfidences(qualification_name=v)
        assert fc.qualification_name == v
