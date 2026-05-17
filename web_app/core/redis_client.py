"""Redis (kgk-redis) との非同期 client。

SSO 統合 (ADR-003) で KGK と共有する Redis 接続を一元化する。
- 接続先: REDIS_URL 環境変数 (既定 redis://localhost:6379)
- スコープ: SSO ワンタイムチケットの SETEX / GETDEL のみ (KGK 側 session store と
  同じインスタンスだが key prefix `kgk:sso:` で論理分離)
- 再利用: アプリ起動から終了まで単一インスタンス (asyncio loop に紐づく接続プール)
"""
from __future__ import annotations

import os

from redis.asyncio import Redis

_client: Redis | None = None


def get_redis() -> Redis:
    """非同期 Redis client を返す (lazy init、同一プロセスで共有)。

    decode_responses=True で str を直接やりとりする (SSO の payload は JSON 文字列)。
    """
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _client = Redis.from_url(url, decode_responses=True)
    return _client


async def close_redis() -> None:
    """シャットダウン時の接続クリーンアップ (lifespan 終了で呼ぶ想定)。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
