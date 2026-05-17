"""verify_csrf_token が X-CSRF-Token ヘッダーも受理することの単体テスト。

pending.html の JS は ``fetch('...', { headers: { 'X-CSRF-Token': ... } })``
で送る。verify_csrf_token は (a) ヘッダー (b) form フィールド の双方を見るので、
AJAX / form どちらの呼び出しでも認可が成立する必要がある。
"""
from __future__ import annotations

import asyncio

import pytest

from fastapi import HTTPException

from web_app.core.dependencies import verify_csrf_token


class _FakeRequest:
    """verify_csrf_token が叩く属性 (headers / form) だけ実装した簡易 Request。"""

    def __init__(self, headers: dict | None = None, form_data: dict | None = None):
        self.headers = headers or {}
        self._form = form_data or {}

    async def form(self):
        return self._form


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if asyncio.get_event_loop().is_running() is False \
        else asyncio.run(coro)


def test_accepts_csrf_via_header():
    """X-CSRF-Token ヘッダーで送ったトークンを受理する (form は空)。"""
    user = {"id": "u1", "csrf_token": "TOKEN_ABC_123"}
    req = _FakeRequest(headers={"x-csrf-token": "TOKEN_ABC_123"})
    out = asyncio.run(verify_csrf_token(req, user=user))
    assert out is user


def test_accepts_csrf_via_form_when_header_missing():
    """ヘッダーが無くても form の csrf_token フィールドで受理する (既存挙動)。"""
    user = {"id": "u1", "csrf_token": "TOKEN_FORM_999"}
    req = _FakeRequest(form_data={"csrf_token": "TOKEN_FORM_999"})
    out = asyncio.run(verify_csrf_token(req, user=user))
    assert out is user


def test_rejects_when_neither_header_nor_form():
    """ヘッダーにも form にも無ければ 403。"""
    user = {"id": "u1", "csrf_token": "EXPECTED"}
    req = _FakeRequest()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(verify_csrf_token(req, user=user))
    assert exc.value.status_code == 403


def test_rejects_when_header_value_mismatches():
    """ヘッダー値が不一致なら 403 (form を見に行ってフォールバックしない)。"""
    user = {"id": "u1", "csrf_token": "EXPECTED_VALUE"}
    req = _FakeRequest(
        headers={"x-csrf-token": "WRONG_VALUE"},
        form_data={"csrf_token": "EXPECTED_VALUE"},  # form 側は正しいが見ない
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(verify_csrf_token(req, user=user))
    assert exc.value.status_code == 403
