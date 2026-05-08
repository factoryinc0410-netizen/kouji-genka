"""資格者証アップロードのファイル保存ヘルパ。

レイアウト::

    <UPLOAD_DIR>/qualifications/<job_id>/<safe_filename>

job_id は q_upload_jobs.job_id と一致させる。安全なファイル名は
``sanitize_filename`` で正規化する（パス区切り除去・拡張子保持）。

OCR が完了して q_certificates にひも付けた後は別ディレクトリに移すか、
同じ場所のまま original_files_json に絶対パスを記録するかを Phase 2 で決める。
ここでは「ステージング」フェーズの責務のみを扱う。
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path

from web_app.core.config import UPLOAD_DIR

# pdf / 画像 のみを受け入れる。Gemini も同じ拡張子をサポート。
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".jpg", ".jpeg", ".png"})

# サブディレクトリの根。job_id ごとにこの下にディレクトリを切る。
QUALIFICATIONS_STAGING_ROOT: Path = UPLOAD_DIR / "qualifications"


def is_allowed_extension(filename: str) -> bool:
    """対応拡張子か判定する（小文字比較）。"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def sanitize_filename(filename: str) -> str:
    """パス区切り・制御文字・先頭/末尾の空白を除去した安全なファイル名を返す。

    元の拡張子は保持する。空文字や全部記号だった場合は uuid4 で代替する。
    """
    # NFKC で全角/半角・互換文字を正規化
    name = unicodedata.normalize("NFKC", filename)
    # パス区切りを除去（OS 別記号を一括処理）
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    # 拡張子を保持しつつ stem を安全化
    p = Path(name)
    stem = p.stem
    suffix = p.suffix.lower()
    # 制御文字・記号類を _ に寄せる（半角英数 / ハイフン / アンダースコア / 日本語以外を除外）
    stem = re.sub(r"[^\w぀-ヿ㐀-鿿\-]", "_", stem)
    stem = stem.strip("._-")
    if not stem:
        stem = uuid.uuid4().hex[:8]
    # 長すぎるファイル名は切り詰める（OS 互換）
    if len(stem) > 80:
        stem = stem[:80]
    return f"{stem}{suffix}"


def staging_dir_for(job_id: str) -> Path:
    """job_id 用のステージングディレクトリを返す（未作成なら作る）。"""
    p = QUALIFICATIONS_STAGING_ROOT / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_staged_files(job_id: str) -> list[Path]:
    """ステージングディレクトリに保存済みのファイルを名前順で返す。"""
    d = QUALIFICATIONS_STAGING_ROOT / job_id
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())
