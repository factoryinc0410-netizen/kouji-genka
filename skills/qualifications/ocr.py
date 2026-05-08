"""Gemini API クライアント (gemini-2.5-flash)。

責務:
- 資格者証ファイル (PDF / JPEG / PNG) を Gemini に送信し ``OCRResponse`` を返す
- 429 / 5xx で指数バックオフ最大 3 回リトライ
- ``GEMINI_API_KEY`` 未設定 / 機能無効時は ``FakeOCRClient`` を返す

PII 保護:
- API キーは ``__repr__`` に出さない
- raw レスポンス (氏名・資格番号などを含む) は logger に出さない
- リトライ時のログは HTTP コード・試行回数のみで、リクエスト内容は出さない
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from skills.qualifications.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from skills.qualifications.schema import OCRResponse
from web_app.core.config import GEMINI_API_KEY, QUALIFICATIONS_OCR_ENABLED

logger = logging.getLogger("web_app.qualifications.ocr")


# ────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3

# 指数バックオフの基数。試行 i 回目で base ** i 秒待つ (1, 4, 16 秒)。
_BACKOFF_BASE = 4

# リトライ対象の HTTP ステータスコード。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# 拡張子 → MIME タイプ。Gemini への送信時に使用。
_MIME_BY_EXT: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
}


# ────────────────────────────────────────────
# Protocol — クライアント共通インタフェース
# ────────────────────────────────────────────

class OCRClient(Protocol):
    """Gemini クライアント / Fake クライアントが満たすインタフェース。"""

    def extract(self, files: list[Path]) -> OCRResponse:
        ...


# ────────────────────────────────────────────
# Fake クライアント — テスト・OCR 無効化時に使用
# ────────────────────────────────────────────

class FakeOCRClient:
    """テストおよび OCR 機能無効時に使う固定値クライアント。

    ``fixture`` を指定すれば任意のレスポンスを返せる。指定なしなら空応答。
    """

    def __init__(self, fixture: OCRResponse | None = None):
        self._fixture = fixture if fixture is not None else OCRResponse()

    def extract(self, files: list[Path]) -> OCRResponse:
        # files の中身は使わない。テスト時の挙動を予測可能にするため。
        return self._fixture

    def __repr__(self) -> str:
        return f"FakeOCRClient(candidates={len(self._fixture.candidates)})"


# ────────────────────────────────────────────
# Gemini 本番クライアント
# ────────────────────────────────────────────

class GeminiOCRClient:
    """Google Gemini API で OCR を実行する本番クライアント。

    google-genai SDK は遅延 import するため、本モジュールを単に import する
    だけでは google-genai のインストールは不要。``__init__`` を呼んだ時点で
    初めて SDK を要求する。
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY が空です。本番クライアントの初期化には API キーが必要です。")
        if max_retries < 1:
            raise ValueError(f"max_retries は 1 以上である必要があります: {max_retries}")

        # 遅延 import: google-genai 未インストールでもモジュールロードは可能
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    def __repr__(self) -> str:
        # API キー値そのものは絶対に出さない
        return (
            f"GeminiOCRClient(model={self._model!r}, "
            f"timeout={self._timeout}, max_retries={self._max_retries})"
        )

    # ── 公開 API ───────────────────────────

    def extract(self, files: list[Path]) -> OCRResponse:
        """ファイル群を Gemini に送って OCRResponse を返す。"""
        if not files:
            return OCRResponse()

        contents = self._build_contents(files)
        raw_text = self._call_with_retry(contents)
        # response_schema により Gemini は schema 準拠の JSON を返すが、
        # 念のため Pydantic で検証してから返す。
        return OCRResponse.model_validate_json(raw_text)

    # ── 内部処理 ───────────────────────────

    def _build_contents(self, files: list[Path]) -> list:
        """Gemini SDK に渡す ``contents`` を組み立てる。

        各ファイルを inline bytes として詰める (5MB 以下の想定)。
        末尾に短い指示文を置く。
        """
        # 遅延 import
        from google.genai import types as genai_types

        parts: list = []
        for f in files:
            ext = f.suffix.lower()
            mime = _MIME_BY_EXT.get(ext)
            if mime is None:
                raise ValueError(f"未対応のファイル拡張子: {f.suffix}")
            parts.append(
                genai_types.Part.from_bytes(data=f.read_bytes(), mime_type=mime)
            )
        parts.append(genai_types.Part.from_text(text=USER_INSTRUCTION))
        return parts

    def _call_api(self, contents: list) -> str:
        """1 回の API 呼び出し。raw JSON 文字列を返す。

        テストで挙動をシミュレートするにはこのメソッドを subclass で override する。
        """
        from google.genai import types as genai_types

        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=OCRResponse,
                temperature=0.1,
                max_output_tokens=4096,
            ),
        )
        # response.text には JSON 文字列が入る。空応答対策で fallback。
        return response.text or "{}"

    def _call_with_retry(self, contents: list) -> str:
        """``_call_api`` を ``max_retries`` 回まで呼ぶ。

        - 429 / 5xx は指数バックオフでリトライ (1s, 4s, 16s)
        - 4xx (429 除く) など非リトライ系のエラーは即座に上げる
        """
        from google.genai import errors as genai_errors

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self._call_api(contents)
            except genai_errors.APIError as e:
                code = self._extract_status(e)
                if code not in RETRYABLE_STATUS:
                    # 4xx などはリトライしても成功しない — 即座に上げる
                    logger.warning(
                        "Gemini API 非リトライエラー (HTTP %s) — 即座に終了",
                        code or "?",
                    )
                    raise
                last_error = e
                if attempt == self._max_retries - 1:
                    break  # 最後の試行 — ループを抜けて raise
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Gemini API リトライ %d/%d (HTTP %d) — %d 秒後に再試行",
                    attempt + 1, self._max_retries, code, wait,
                )
                time.sleep(wait)

        assert last_error is not None  # 上のループで設定されているはず
        logger.error(
            "Gemini API リトライ上限 (%d 回) 到達 — 最終エラーを送出",
            self._max_retries,
        )
        raise last_error

    @staticmethod
    def _extract_status(exc) -> int:
        """APIError から HTTP ステータスを取り出す (取れない場合は 0)。"""
        return int(getattr(exc, "code", 0) or 0)


# ────────────────────────────────────────────
# ファクトリ
# ────────────────────────────────────────────

def make_ocr_client() -> OCRClient:
    """環境設定に従って本番 / Fake クライアントを返す。

    - ``QUALIFICATIONS_OCR_ENABLED=false`` ならば Fake
    - ``GEMINI_API_KEY`` 未設定でも Fake (＝手動入力モード)
    - それ以外は本番 ``GeminiOCRClient``
    """
    if not QUALIFICATIONS_OCR_ENABLED:
        logger.info("OCR 機能はフラグで無効化されています — FakeOCRClient を返します")
        return FakeOCRClient()
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY 未設定 — 手動入力モードで動作します (FakeOCRClient)")
        return FakeOCRClient()
    return GeminiOCRClient(api_key=GEMINI_API_KEY)


__all__ = [
    "OCRClient",
    "FakeOCRClient",
    "GeminiOCRClient",
    "make_ocr_client",
    "RETRYABLE_STATUS",
    "DEFAULT_MODEL",
]
