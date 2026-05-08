"""Gemini OCR の構造化出力スキーマ (Pydantic v2)。

このモジュールは Gemini API の ``response_schema`` パラメータに直接渡せる
形のモデル群を提供する。Pydantic 側で範囲チェック/フォーマット検証を
行うことで、Gemini が壊れた JSON を返した場合でも、ルーターやワーカーに
妥当性の確かなオブジェクトだけが伝播するようにする。

参照:
    docs/.../phase-2 設計提案 §3
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ISO 8601 日付フォーマット (YYYY-MM-DD)。Gemini プロンプトで西暦変換を強制する。
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 信頼度の許容範囲。0.0 = 完全に不確か、1.0 = 完全に確実。
_CONFIDENCE_MIN = 0.0
_CONFIDENCE_MAX = 1.0


def _validate_iso_date(value: str | None) -> str | None:
    """ISO 8601 (YYYY-MM-DD) フォーマットと実在性をチェックする。

    None は許容（不明値の表現）。空文字は None に正規化する。
    """
    if value is None or value == "":
        return None
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"日付は YYYY-MM-DD 形式で指定してください: {value!r}")
    # 実在性チェック (例: 2024-02-30 を弾く)
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"存在しない日付です: {value!r}") from e
    return value


def _validate_confidence(value: float | None) -> float | None:
    if value is None:
        return None
    if not (_CONFIDENCE_MIN <= value <= _CONFIDENCE_MAX):
        raise ValueError(
            f"信頼度は {_CONFIDENCE_MIN}〜{_CONFIDENCE_MAX} の範囲で指定してください: {value}"
        )
    return value


# ────────────────────────────────────────────
# モデル定義
# ────────────────────────────────────────────

class FieldConfidences(BaseModel):
    """各抽出フィールドごとの信頼度 (0.0-1.0)。

    Gemini が読み取れなかった/未推定のフィールドは None。
    レビュー画面で field 単位の警告色を出すために使う。
    """

    model_config = ConfigDict(extra="ignore")

    qualification_name: float | None = None
    worker_name:        float | None = None
    certificate_no:     float | None = None
    issued_on:          float | None = None
    expires_on:         float | None = None
    issuer:             float | None = None

    @field_validator(
        "qualification_name", "worker_name", "certificate_no",
        "issued_on", "expires_on", "issuer",
    )
    @classmethod
    def _check_range(cls, v: float | None) -> float | None:
        return _validate_confidence(v)


class Candidate(BaseModel):
    """1 つの資格者証候補。

    Gemini は 1 ファイル中の複数枚を 1 つの ``Candidate`` に
    束ねる/分けるどちらも行いうる。表裏セットなら ``page_indices`` が
    [0, 1] のように複数値、単票なら [0] になる。
    """

    model_config = ConfigDict(extra="ignore")

    # ページ番号 (0-origin)。空配列は「ページマッピング不明」を示す。
    page_indices: list[int] = Field(default_factory=list)

    qualification_name: str | None = None
    # 技能講習 / 特別教育 / 免許 / その他 ... のような大分類。
    category:           str | None = None
    worker_name:        str | None = None
    certificate_no:     str | None = None

    # ISO 8601 (YYYY-MM-DD)。和暦は Gemini プロンプト側で西暦に変換させる。
    issued_on:          str | None = None
    expires_on:         str | None = None

    # 更新が必要な資格か。デフォルトは保守的に True。
    renewal_required:   bool = True

    issuer:             str | None = None

    field_confidences:  FieldConfidences = Field(default_factory=FieldConfidences)

    @field_validator("page_indices")
    @classmethod
    def _check_page_indices(cls, v: list[int]) -> list[int]:
        for i in v:
            if i < 0:
                raise ValueError(f"page_indices は 0 以上である必要があります: {v}")
        # 重複は許容するが警告したい — 実装は classifier 側で
        return v

    @field_validator("issued_on", "expires_on")
    @classmethod
    def _check_iso_date(cls, v: str | None) -> str | None:
        return _validate_iso_date(v)


class OCRResponse(BaseModel):
    """Gemini が返す全体応答。複数の Candidate を含みうる。"""

    model_config = ConfigDict(extra="ignore")

    candidates: list[Candidate] = Field(default_factory=list)
    # 全候補・全フィールドを統合した自信度の平均。
    overall_confidence: float = 0.0

    @field_validator("overall_confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (_CONFIDENCE_MIN <= v <= _CONFIDENCE_MAX):
            raise ValueError(
                f"overall_confidence は {_CONFIDENCE_MIN}〜{_CONFIDENCE_MAX} の範囲で指定してください: {v}"
            )
        return v


__all__ = ["FieldConfidences", "Candidate", "OCRResponse"]
