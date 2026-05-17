"""
ファイル返却のパス検証ユーティリティ — ディレクトリトラバーサル対策

外部由来のパス（URL パスパラメータ、DB に保存された値など）から FileResponse を
返す際は、必ず safe_file_response() を経由させること。base_dir の外側を指す
リクエストや、シンボリックリンクで base_dir 外に逃がす攻撃を resolve() による
正規化で完全に防ぐ。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, status
from fastapi.responses import FileResponse


def build_content_disposition(filename: str, disposition_type: str = "inline") -> str:
    """RFC 5987 / RFC 6266 準拠の ``Content-Disposition`` 値を組み立てる。

    日本語などの非 ASCII を含むファイル名でも、全主要ブラウザで正しく解釈される
    よう **ASCII フォールバック** (``filename="..."``) と **UTF-8 エンコード版**
    (``filename*=UTF-8''...``) の両方を出力する:

        Content-Disposition: inline;
            filename="_____.pdf";
            filename*=UTF-8''%E8%B3%87%E6%A0%BC%E8%A8%BC.pdf

    Starlette の FileResponse は片方しか出さないため、互換性確保のため
    アプリ側で組み立ててこちらを優先させる。
    """
    # ASCII フォールバック: 非 ASCII は ``_`` に倒し、ダブルクォート / バックスラッシュ
    # をエスケープしておく。これは古い IE / Safari など RFC 5987 を理解しない
    # クライアント向けの保険。
    ascii_fallback = "".join(
        c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename
    )
    if not ascii_fallback.strip("_"):
        ascii_fallback = "file"
    # RFC 5987: UTF-8 でパーセントエンコード (safe="" → 何もスキップしない)
    encoded = quote(filename, safe="")
    return (
        f'{disposition_type}; '
        f'filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


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
    content_disposition_type: str = "attachment",
    extra_headers: dict[str, str] | None = None,
) -> FileResponse:
    """base_dir の外を指していないことを検証してから FileResponse を返す。

    Args:
        requested_path: 返却したいファイルのパス（外部由来でよい）。
        base_dir: 許可するベースディレクトリ。
        filename: ダウンロード時のファイル名 (非 ASCII を含むなら自動で
            ``filename=`` ASCII フォールバック + ``filename*=UTF-8''...`` の
            両形式の Content-Disposition を組み立てる)。
        media_type: MIME タイプ。
        content_disposition_type: ``"attachment"`` (既定: ダウンロード強制) /
            ``"inline"`` (ブラウザでインライン表示)。
        extra_headers: 追加でセットするレスポンスヘッダー (例: ``X-Frame-Options``)。
            ``Content-Disposition`` を含めれば自動生成より優先される。

    Raises:
        HTTPException: パスが base_dir の外側を指す、または存在しない場合に 404。
    """
    target = _resolve_within(requested_path, base_dir)
    headers: dict[str, str] = dict(extra_headers or {})
    # filename がある場合は両形式の Content-Disposition を組み立て、
    # extra_headers で明示されていない場合に限り上書きする。
    if filename and "content-disposition" not in {k.lower() for k in headers}:
        headers["content-disposition"] = build_content_disposition(
            filename, content_disposition_type,
        )
    # FileResponse の自動 Content-Disposition 生成と二重に出ないよう、
    # 自前で組み立てた場合は filename=None を渡す。
    return FileResponse(
        path=str(target),
        filename=None,
        media_type=media_type,
        content_disposition_type=content_disposition_type,
        headers=headers or None,
    )
