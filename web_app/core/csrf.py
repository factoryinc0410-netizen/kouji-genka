"""
CSRF 対策ヘルパ

このアプリは2つのパターンを使い分ける:

- 認証済みフォーム（管理者画面・パスワード変更など）
  → Synchronizer Token Pattern
  ログイン時に発行したトークンを sessions テーブルに保存し、フォームの
  hidden input と比較する。core/auth.py / core/dependencies.py で扱う。

- 未認証フォーム（ログイン画面）
  → Double-Submit Cookie Pattern
  GET /login 時に短命の csrf_login Cookie とフォーム hidden に同値を
  発行し、POST /login で両方を比較する。

どちらも本モジュールの `generate_csrf_token` と `tokens_match` を共有する。
"""
from __future__ import annotations

import secrets

# Cookie 名（ログイン専用 Double-Submit 用）
CSRF_LOGIN_COOKIE = "csrf_login"

# Form 側の hidden 名（両パターン共通）
CSRF_FORM_FIELD = "csrf_token"


def generate_csrf_token(num_bytes: int = 32) -> str:
    """URL セーフなランダムトークンを返す。32 バイト=約 256bit エントロピー。"""
    return secrets.token_urlsafe(num_bytes)


def tokens_match(submitted: str | None, expected: str | None) -> bool:
    """タイミング攻撃に強い形でトークンを比較する。

    どちらかが空・None なら False。長さが違う場合も False。
    `secrets.compare_digest` は型と長さが一致しないと TypeError を投げるため、
    str 化と存在チェックを先に行う。
    """
    if not submitted or not expected:
        return False
    s = str(submitted)
    e = str(expected)
    if len(s) != len(e):
        return False
    return secrets.compare_digest(s, e)
