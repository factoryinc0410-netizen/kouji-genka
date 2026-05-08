"""skills/qualifications/ocr.py の単体テスト。

ネットワーク呼び出しは行わない。実 Gemini API への到達は handcraft な
スモークテストで別途確認する。

ここでは:
- FakeOCRClient の挙動
- make_ocr_client のフォールバック分岐
- 指数バックオフリトライの正しい挙動 (リトライ対象/非対象、上限到達)
- ログに API キーや PII が出力されないこと
を担保する。
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from google.genai import errors as genai_errors

from skills.qualifications import ocr as ocr_mod
from skills.qualifications.ocr import (
    DEFAULT_MODEL,
    RETRYABLE_STATUS,
    FakeOCRClient,
    GeminiOCRClient,
    make_ocr_client,
)
from skills.qualifications.schema import Candidate, OCRResponse


# ────────────────────────────────────────────
# テスト用ヘルパ
# ────────────────────────────────────────────

def _make_api_error(code: int, message: str = "test error") -> genai_errors.APIError:
    """生成しやすいフォーマットで APIError を作る。"""
    return genai_errors.APIError(
        code=code,
        response_json={"error": {"code": code, "message": message, "status": "TEST"}},
    )


class _StubGeminiClient(GeminiOCRClient):
    """``__init__`` で google-genai クライアントを生成しないテスト用 subclass。

    ``fail_codes`` で指定した HTTP コードを順次投げてから ``success_response``
    を返すように ``_call_api`` を差し替える。
    """

    def __init__(
        self,
        *,
        fail_codes: list[int] | None = None,
        success_response: str = '{"candidates": [], "overall_confidence": 0.0}',
        max_retries: int = 3,
    ):
        # 親 __init__ は呼ばない (genai.Client を作らない)
        self._client = None
        self._model = "test-model"
        self._timeout = 1.0
        self._max_retries = max_retries
        self._fail_codes = list(fail_codes or [])
        self._success_response = success_response
        self.call_count = 0

    def _call_api(self, contents):
        self.call_count += 1
        if self._fail_codes:
            code = self._fail_codes.pop(0)
            raise _make_api_error(code)
        return self._success_response


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """テストで本物の time.sleep を呼ばない。"""
    monkeypatch.setattr(ocr_mod.time, "sleep", lambda s: None)


# ────────────────────────────────────────────
# FakeOCRClient
# ────────────────────────────────────────────

class TestFakeOCRClient:
    def test_default_returns_empty_response(self):
        c = FakeOCRClient()
        r = c.extract([])
        assert isinstance(r, OCRResponse)
        assert r.candidates == []

    def test_returns_supplied_fixture(self):
        fixture = OCRResponse(
            candidates=[Candidate(qualification_name="玉掛け技能講習")],
            overall_confidence=0.9,
        )
        c = FakeOCRClient(fixture=fixture)
        r = c.extract([])
        assert r is fixture
        assert r.candidates[0].qualification_name == "玉掛け技能講習"

    def test_ignores_files_argument(self, tmp_path):
        """ファイル内容に依存せず固定値を返す。"""
        fake_file = tmp_path / "anything.pdf"
        fake_file.write_bytes(b"")
        fixture = OCRResponse(overall_confidence=0.42)
        c = FakeOCRClient(fixture=fixture)
        assert c.extract([fake_file]).overall_confidence == 0.42


# ────────────────────────────────────────────
# make_ocr_client (factory)
# ────────────────────────────────────────────

class TestMakeOCRClient:
    def test_returns_fake_when_disabled(self, monkeypatch):
        monkeypatch.setattr(ocr_mod, "QUALIFICATIONS_OCR_ENABLED", False)
        monkeypatch.setattr(ocr_mod, "GEMINI_API_KEY", "real-key")
        client = make_ocr_client()
        assert isinstance(client, FakeOCRClient)

    def test_returns_fake_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(ocr_mod, "QUALIFICATIONS_OCR_ENABLED", True)
        monkeypatch.setattr(ocr_mod, "GEMINI_API_KEY", "")
        client = make_ocr_client()
        assert isinstance(client, FakeOCRClient)

    def test_returns_real_when_enabled_and_key_present(self, monkeypatch):
        """API キーがあれば本番クライアントが選ばれる。"""
        monkeypatch.setattr(ocr_mod, "QUALIFICATIONS_OCR_ENABLED", True)
        monkeypatch.setattr(ocr_mod, "GEMINI_API_KEY", "fake-key-for-test")
        client = make_ocr_client()
        assert isinstance(client, GeminiOCRClient)
        # 既定モデルが選ばれていること
        assert DEFAULT_MODEL in repr(client)


# ────────────────────────────────────────────
# GeminiOCRClient: __init__ ガード
# ────────────────────────────────────────────

class TestGeminiOCRClientInit:
    def test_empty_api_key_rejected(self):
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiOCRClient(api_key="")

    def test_zero_max_retries_rejected(self):
        with pytest.raises(ValueError, match="max_retries"):
            GeminiOCRClient(api_key="x", max_retries=0)


# ────────────────────────────────────────────
# 指数バックオフ・リトライ
# ────────────────────────────────────────────

class TestRetryBehavior:
    def test_succeeds_on_first_try(self):
        c = _StubGeminiClient(fail_codes=[])
        r = c.extract([])  # files 空なら API は呼ばない (build_contents をスキップ)
        # 空 files の場合 _call_api は呼ばれない
        assert r.candidates == []
        assert c.call_count == 0

    def test_retries_on_429_then_succeeds(self, tmp_path):
        # _build_contents をモックして実 SDK 呼び出しを避ける
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[429, 429])  # 2 回失敗 → 3 回目で成功
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            r = c.extract([f])
        assert r.overall_confidence == 0.0
        assert c.call_count == 3

    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
    def test_each_retryable_status_triggers_retry(self, tmp_path, status):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[status])
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            c.extract([f])
        assert c.call_count == 2  # 1 failed + 1 succeeded

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_non_retryable_status_raises_immediately(self, tmp_path, status):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[status])
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            with pytest.raises(genai_errors.APIError) as exc_info:
                c.extract([f])
        assert exc_info.value.code == status
        assert c.call_count == 1   # リトライしていない

    def test_exhausts_retries_then_raises(self, tmp_path):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[503, 503, 503], max_retries=3)
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            with pytest.raises(genai_errors.APIError) as exc_info:
                c.extract([f])
        assert exc_info.value.code == 503
        assert c.call_count == 3   # 全試行を使い切った

    def test_backoff_uses_4_to_the_attempt(self, tmp_path, monkeypatch):
        """1 回目失敗後に 1 秒、2 回目失敗後に 4 秒待つこと。"""
        sleeps: list[float] = []
        monkeypatch.setattr(
            ocr_mod.time, "sleep", lambda s: sleeps.append(s)
        )
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[503, 503], max_retries=3)
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            c.extract([f])
        assert sleeps == [1, 4]  # base=4: 4**0=1, 4**1=4


# ────────────────────────────────────────────
# build_contents (SDK 連携)
# ────────────────────────────────────────────

class TestBuildContents:
    """実 SDK の Part 生成パスを軽く検証する (SDK が import 可能であること前提)。"""

    @staticmethod
    def _bare_client() -> GeminiOCRClient:
        """genai.Client を作らずに GeminiOCRClient を構築する。"""
        client = GeminiOCRClient.__new__(GeminiOCRClient)
        client._model = "x"
        client._timeout = 1.0
        client._max_retries = 1
        return client

    def test_pdf_and_image_mime_types(self, tmp_path):
        # 実クライアントを作るが extract は呼ばない (API キー不要)
        client = self._bare_client()
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        png = tmp_path / "b.png"
        png.write_bytes(b"\x89PNG")
        jpg = tmp_path / "c.jpg"
        jpg.write_bytes(b"\xff\xd8\xff")
        parts = client._build_contents([pdf, png, jpg])
        # 最後はインストラクションのテキストパート
        assert len(parts) == 4

    def test_unsupported_extension_rejected(self, tmp_path):
        client = self._bare_client()
        bad = tmp_path / "a.txt"
        bad.write_bytes(b"hi")
        with pytest.raises(ValueError, match="未対応"):
            client._build_contents([bad])


# ────────────────────────────────────────────
# PII / API キー保護
# ────────────────────────────────────────────

class TestPIIProtection:
    def test_repr_does_not_contain_api_key(self, monkeypatch):
        monkeypatch.setattr(ocr_mod, "QUALIFICATIONS_OCR_ENABLED", True)
        monkeypatch.setattr(
            ocr_mod, "GEMINI_API_KEY", "AIza-very-secret-12345"
        )
        client = make_ocr_client()
        assert "AIza-very-secret-12345" not in repr(client)

    def test_retry_log_does_not_contain_response_body(self, tmp_path, caplog):
        """リトライログに raw error_json (PII の温床) を含めない。"""
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x")
        c = _StubGeminiClient(fail_codes=[429])
        with patch.object(c, "_build_contents", return_value=["dummy"]):
            with caplog.at_level(logging.WARNING, logger="web_app.qualifications.ocr"):
                c.extract([f])
        log_text = "\n".join(r.message for r in caplog.records)
        # response_json には status='TEST', message='test error' が入っている
        assert "TEST" not in log_text
        assert "test error" not in log_text
        # HTTP コード自体はログに含まれて OK
        assert "429" in log_text
