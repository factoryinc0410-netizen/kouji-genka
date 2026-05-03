"""
ファイル返却のパス検証ユーティリティ — ディレクトリトラバーサル対策

外部由来のパス（URL パスパラメータ、DB に保存された値など）から FileResponse を
返す際は、必ず safe_file_response() を経由させること。base_dir の外側を指す
リクエストや、シンボリックリンクで base_dir 外に逃がす攻撃を resolve() による
正規化で完全に防ぐ。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse


def _resolve_within(requested_path: str | Path, base_dir: Path) -> Path:
    """requested_path を絶対パスに正規化し、base_dir 配下にあることを確認する。

    範囲外・存在しない・ファイルでない場合は HTTPException(404) を送出する。
    範囲外と非存在を区別せず 404 を返すのは、攻撃者へ情報を与えないため。
    """
    base_resolved = base_dir.resolve()
    try:
        # strict=True でシンボリックリンク・".." を含めて完全に解決し、
        # 実体が存在しない場合は FileNotFoundError を送出させる
        target = Path(requested_path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ファイルが見つかりません",
        )

    # base_dir 配下に収まっているか検証（Python 3.9+ 互換のため relative_to を使用）
    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ファイルが見つかりません",
        )

    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ファイルが見つかりません",
        )

    return target


def safe_file_response(
    requested_path: str | Path,
    base_dir: Path,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> FileResponse:
    """base_dir の外を指していないことを検証してから FileResponse を返す。

    Args:
        requested_path: 返却したいファイルのパス（外部由来でよい）。
        base_dir: 許可するベースディレクトリ。
        filename: ダウンロード時のファイル名。
        media_type: MIME タイプ。

    Raises:
        HTTPException: パスが base_dir の外側を指す、または存在しない場合に 404。
    """
    target = _resolve_within(requested_path, base_dir)
    return FileResponse(
        path=str(target),
        filename=filename,
        media_type=media_type,
    )
