"""SSO ルーター — KGK アドオンへのシームレス遷移 (ADR-003)。

`GET /sso/kgk/start` が認証済みユーザの payload を Redis にワンタイムチケットとして
書き込み、KGK 側 callback URL へ 302 リダイレクトする。チケットは TTL 30 秒で
失効し、KGK 側で `GETDEL` により atomic に消費される (replay 防止)。

ロールマッピング:
  - is_admin = True            → admin
  - kouji_genka = 'manager'    → admin
  - kouji_genka = 'general'    → planner
  - その他                      → 拒否 (/ へ戻す)

詳細: skills/kouji-genka/docs/adr/ADR-003-sso-integration.md
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from web_app.core.dependencies import get_current_user
from web_app.core.redis_client import get_redis

logger = logging.getLogger("web_app.sso")

router = APIRouter(prefix="/sso", tags=["sso"])

_TICKET_TTL_SEC = int(os.getenv("KGK_SSO_TICKET_TTL_SEC", "30"))
_KGK_CALLBACK_URL = os.getenv(
    "KGK_SSO_CALLBACK_URL", "http://localhost:3000/api/sso/callback"
)
_TICKET_KEY_PREFIX = "kgk:sso:ticket:"

# 権限レベルの順序 (Factoryskills 内で has_permission と同じ序列)
_LEVEL_RANK = {"none": 0, "general": 1, "manager": 2}


def _shared_secret() -> str:
    """共有秘密鍵を取得。未設定なら起動を続けるが SSO は無効化される。"""
    return os.getenv("KGK_SSO_SHARED_SECRET", "")


def _sign(payload: dict) -> str:
    """payload に HMAC-SHA256 署名を付与。

    重要: separators=(",", ":") を指定して空白なし JSON を生成する。
    JS 側の JSON.stringify(obj) は既定で空白なしを返すため、それと一致させる。
    sort_keys=True で key 順を安定化 (両側で同じ raw bytes を作る前提)。
    """
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(
        _shared_secret().encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()


def _kgk_role_for(user: dict) -> str | None:
    """Factoryskills user dict から KGK ロールへマッピング。

    返り値:
      'admin' / 'planner' — KGK のいずれかのロール
      None                — KGK アクセス権なし (SSO を断る)
    """
    if user.get("is_admin"):
        return "admin"
    perms = user.get("permissions") or {}
    role_perms = user.get("role_permissions") or {}
    user_level = perms.get("kouji_genka", "none")
    role_level = role_perms.get("kouji_genka", "none")
    # 個別権限と role 権限の OR (max rank)
    effective = max(user_level, role_level, key=lambda lvl: _LEVEL_RANK.get(lvl, 0))
    if effective == "manager":
        return "admin"
    if effective == "general":
        return "planner"
    return None


@router.get("/kgk/start")
async def kgk_sso_start(user: dict = Depends(get_current_user)):
    """KGK アドオンへ SSO で遷移する。

    認証済みユーザのみ到達 (get_current_user が未認証時に RequiresLoginException を送出)。
    SSO 秘密鍵が未設定なら 503、KGK アクセス権がなければ / にリダイレクト。
    """
    secret = _shared_secret()
    if not secret:
        logger.error(
            "SSO 中断: KGK_SSO_SHARED_SECRET が未設定。"
            "Factoryskills と KGK 両側の .env に同じ値を設定してください。"
        )
        # 開発便宜: 503 ではなく / に戻してフラッシュ風メッセージを出すべきだが、
        # ここではログだけ残して 503 を返す (設定不備を見逃さない)。
        return RedirectResponse(url="/?sso_error=not_configured", status_code=303)

    role = _kgk_role_for(user)
    if role is None:
        logger.info(
            "SSO 拒否: user=%s に KGK (kouji_genka) アクセス権なし",
            user.get("username"),
        )
        return RedirectResponse(url="/?sso_error=no_permission", status_code=303)

    # payload に署名を付与
    payload: dict = {
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": role,
        "iat": int(time.time()),
    }
    payload["sig"] = _sign(payload)

    # 256-bit ランダムチケット
    ticket = secrets.token_urlsafe(32)

    # Redis に TTL 付きで保存 (TTL = 短命)
    redis = get_redis()
    await redis.setex(
        f"{_TICKET_KEY_PREFIX}{ticket}",
        _TICKET_TTL_SEC,
        json.dumps(payload, ensure_ascii=False),
    )

    logger.info(
        "SSO 発行: user=%s role=%s ttl=%ds",
        user["username"], role, _TICKET_TTL_SEC,
    )

    target = f"{_KGK_CALLBACK_URL}?{urlencode({'ticket': ticket})}"
    return RedirectResponse(url=target, status_code=303)
