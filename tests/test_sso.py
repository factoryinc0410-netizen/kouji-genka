"""SSO ルーター (web_app/routers/sso.py) の単体テスト (ADR-003)。

- ロールマッピング (_kgk_role_for) — Factoryskills の権限を KGK ロールに正しく射影
- HMAC 署名 (_sign) — 秘密鍵 / payload / key 順 に対する性質

Redis を実際に叩く統合テストは別途 (kgk-redis 起動環境のみ)。
"""
from __future__ import annotations

import pytest

from web_app.routers.sso import _kgk_role_for, _sign


class TestKgkRoleMapping:
    """ADR-003 §4 のロールマッピング表に従う。"""

    def test_admin_user_maps_to_admin(self):
        u = {"is_admin": True, "permissions": {}, "role_permissions": {}}
        assert _kgk_role_for(u) == "admin"

    def test_kouji_genka_manager_maps_to_admin(self):
        u = {
            "is_admin": False,
            "permissions": {"kouji_genka": "manager"},
            "role_permissions": {},
        }
        assert _kgk_role_for(u) == "admin"

    def test_kouji_genka_general_maps_to_planner(self):
        """一般ユーザは planner (業務寄り、UPP で工事単位制御)。"""
        u = {
            "is_admin": False,
            "permissions": {"kouji_genka": "general"},
            "role_permissions": {},
        }
        assert _kgk_role_for(u) == "planner"

    def test_kouji_genka_general_via_role_maps_to_planner(self):
        """個別 permissions ではなく role_permissions 経由でも general → planner。"""
        u = {
            "is_admin": False,
            "permissions": {},
            "role_permissions": {"kouji_genka": "general"},
        }
        assert _kgk_role_for(u) == "planner"

    def test_no_permission_returns_none(self):
        """KGK アクセス権なしは None (SSO 拒否)。"""
        u = {"is_admin": False, "permissions": {}, "role_permissions": {}}
        assert _kgk_role_for(u) is None

    def test_individual_higher_than_role_uses_individual(self):
        """個別 permission が role より高い場合は個別が優先 (max rank)。"""
        u = {
            "is_admin": False,
            "permissions": {"kouji_genka": "manager"},
            "role_permissions": {"kouji_genka": "general"},
        }
        assert _kgk_role_for(u) == "admin"

    def test_role_higher_than_individual_uses_role(self):
        """role が個別より高い場合は role が優先 (max rank)。"""
        u = {
            "is_admin": False,
            "permissions": {"kouji_genka": "general"},
            "role_permissions": {"kouji_genka": "manager"},
        }
        assert _kgk_role_for(u) == "admin"

    def test_missing_keys_default_to_none_level(self):
        """permissions / role_permissions キー自体が欠落していても安全に None を返す。"""
        u = {"is_admin": False}
        assert _kgk_role_for(u) is None


class TestSsoSignature:
    """HMAC-SHA256 署名の決定性 / 秘密鍵依存 / payload 依存 / key 順非依存。"""

    @pytest.fixture(autouse=True)
    def _set_secret(self, monkeypatch):
        monkeypatch.setenv("KGK_SSO_SHARED_SECRET", "unit-test-secret")

    def test_signature_is_deterministic(self):
        sig_a = _sign({"username": "u1", "role": "planner", "iat": 100})
        sig_b = _sign({"username": "u1", "role": "planner", "iat": 100})
        assert sig_a == sig_b

    def test_signature_changes_with_secret(self, monkeypatch):
        sig_a = _sign({"username": "u1", "role": "planner", "iat": 100})
        monkeypatch.setenv("KGK_SSO_SHARED_SECRET", "different-secret")
        sig_b = _sign({"username": "u1", "role": "planner", "iat": 100})
        assert sig_a != sig_b

    def test_signature_changes_with_payload(self):
        sig_a = _sign({"username": "u1", "role": "planner", "iat": 100})
        sig_b = _sign({"username": "u2", "role": "planner", "iat": 100})
        assert sig_a != sig_b

    def test_signature_key_order_independent(self):
        """sort_keys=True なので dict の挿入順が違っても sig は同一。
        この性質が JS 側との HMAC 一致を支える。"""
        sig_a = _sign({"username": "u1", "role": "planner", "iat": 100})
        sig_b = _sign({"iat": 100, "role": "planner", "username": "u1"})
        assert sig_a == sig_b

    def test_signature_matches_expected_for_known_input(self):
        """JS 側との互換性回帰テスト。
        Python: json.dumps(sorted, separators=(",", ":")) → JS: JSON.stringify(sorted) と
        bit 単位で同一であることを、固定 payload + 固定 secret で固定 hex digest として
        固定する (将来 sign 実装を書き換えた際に bit 不整合を即検出する)。"""
        # secret=unit-test-secret, payload={"iat":100,"role":"planner","username":"u1"}
        # raw = '{"iat":100,"role":"planner","username":"u1"}'
        # → HMAC-SHA256(unit-test-secret, raw) を固定値として記録
        sig = _sign({"username": "u1", "role": "planner", "iat": 100})
        # 期待値は実行時に確定する (compute_expected_for_known_input ヘルパで生成)
        import hashlib
        import hmac
        raw = b'{"iat":100,"role":"planner","username":"u1"}'
        expected = hmac.new(b"unit-test-secret", raw, hashlib.sha256).hexdigest()
        assert sig == expected
