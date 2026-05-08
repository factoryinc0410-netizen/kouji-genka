"""
管理者画面用フラッシュメッセージ（プロセス内・1回読み）

セッショントークン（Cookie の `session_token`）に紐づけて、リダイレクト後の
画面で 1 回だけ表示するメッセージを保持する。pop した時点で消去される。

- プロセス再起動で揮発する（セッション再ログインで埋まり直すため許容）
- 単一プロセス運用前提（uvicorn workers=1 を維持）
"""
from __future__ import annotations

import asyncio
from typing import Any

_store: dict[str, list[dict[str, Any]]] = {}
_lock = asyncio.Lock()


async def push(token: str, category: str, message: str, **extra: Any) -> None:
    """フラッシュを 1 件積む。`category` は Bootstrap のアラートカラー。"""
    if not token:
        return
    async with _lock:
        _store.setdefault(token, []).append(
            {"category": category, "message": message, **extra}
        )


async def pop(token: str) -> list[dict[str, Any]]:
    """積まれているフラッシュを取り出して空にする。未ログイン時は空配列。"""
    if not token:
        return []
    async with _lock:
        return _store.pop(token, [])
