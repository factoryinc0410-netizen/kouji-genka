"""construction_cost: グループマスタ削除エンドポイントの統合テスト。

検証範囲:
  - 未使用グループは物理削除できる
  - 作業員 (cc_workers) が参照しているグループは削除拒否
  - 現場予算 (cc_site_group_budgets) が参照しているグループは削除拒否
  - 存在しない group_id は warning メッセージ付きで一覧へリダイレクト
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import unquote

import pytest


@pytest.fixture(scope="module")
def cc_env():
    """temp DB + TestClient + admin auth。

    qualifications 側 conftest と独立した env を用意する (Cross-cutting に
    DB を初期化する必要があり、scope=module で十分高速)。
    """
    from _pytest.monkeypatch import MonkeyPatch

    tmp_root = Path(tempfile.mkdtemp(prefix="cc_groups_env_"))
    tmp_db = tmp_root / "app.db"
    os.environ["DATABASE_PATH"] = str(tmp_db)

    from fastapi.testclient import TestClient

    from web_app.core import config as cfg
    from web_app.core import database as db
    from web_app.core.dependencies import get_current_user, require_admin
    from web_app.main import app

    mp = MonkeyPatch()
    mp.setattr(cfg, "DATABASE_PATH", tmp_db, raising=False)
    mp.setattr(db, "_DB_PATH", str(tmp_db), raising=False)
    asyncio.run(db.init_db())

    # admin user (is_admin → 全権限) と日報 manager 権限を持たせる。
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
        "VALUES ('admin-id', 'admin', '管理者', 'x', 1)"
    )
    conn.commit()
    conn.close()

    admin_user = {
        "id": "admin-id", "username": "admin",
        "display_name": "管理者", "is_admin": 1,
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user

    client = TestClient(app)
    yield {"client": client, "db_path": tmp_db}

    app.dependency_overrides.clear()
    mp.undo()
    shutil.rmtree(tmp_root, ignore_errors=True)


def _insert_group(db_path, *, name: str, group_id: int | None = None):
    conn = sqlite3.connect(str(db_path))
    if group_id is None:
        conn.execute(
            "INSERT INTO cc_groups (group_name) VALUES (?)", (name,),
        )
        gid = conn.execute(
            "SELECT group_id FROM cc_groups WHERE group_name=?", (name,)
        ).fetchone()[0]
    else:
        conn.execute(
            "INSERT INTO cc_groups (group_id, group_name) VALUES (?, ?)",
            (group_id, name),
        )
        gid = group_id
    conn.commit()
    conn.close()
    return gid


def _delete_group_row(db_path, group_id: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM cc_groups WHERE group_id=?", (group_id,))
    conn.commit()
    conn.close()


def _count_group(db_path, group_id: int) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM cc_groups WHERE group_id=?", (group_id,)
    ).fetchone()[0]
    conn.close()
    return n


def _insert_worker(db_path, *, name: str, group_name: str):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO cc_workers (worker_name, group_name, is_active) "
        "VALUES (?, ?, 1)", (name, group_name),
    )
    conn.commit()
    conn.close()


def _insert_site_budget(db_path, *, site_id: int, group_name: str):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO cc_sites (site_id, site_name) VALUES (?, ?)",
        (site_id, f"現場-{site_id}"),
    )
    conn.execute(
        "INSERT INTO cc_site_group_budgets (site_id, group_name, budget) "
        "VALUES (?, ?, 0)", (site_id, group_name),
    )
    conn.commit()
    conn.close()


# ════════════════════════════════════════════════════════════
# 削除成功ケース
# ════════════════════════════════════════════════════════════

class TestGroupDeleteSuccess:
    def test_delete_unused_group(self, cc_env):
        """参照ゼロのグループは物理削除できる。"""
        gid = _insert_group(cc_env["db_path"], name="DEL-FREE")
        try:
            r = cc_env["client"].post(
                f"/construction-cost/groups/{gid}/delete",
                follow_redirects=False,
            )
            assert r.status_code == 303
            location = r.headers["location"]
            assert "/construction-cost/groups" in location
            assert "cat=success" in location
            assert "削除しました" in unquote(location)
            # DB から消えている
            assert _count_group(cc_env["db_path"], gid) == 0
        finally:
            # 念のためクリーンアップ (失敗時でも DB を汚さない)
            _delete_group_row(cc_env["db_path"], gid)


# ════════════════════════════════════════════════════════════
# 削除拒否ケース (整合性チェック)
# ════════════════════════════════════════════════════════════

class TestGroupDeleteRejected:
    def test_reject_when_workers_reference(self, cc_env):
        """作業員が group_name を参照しているグループは削除できない。"""
        gid = _insert_group(cc_env["db_path"], name="DEL-USED-W")
        _insert_worker(cc_env["db_path"], name="使用中ワーカー", group_name="DEL-USED-W")
        try:
            r = cc_env["client"].post(
                f"/construction-cost/groups/{gid}/delete",
                follow_redirects=False,
            )
            assert r.status_code == 303
            location = unquote(r.headers["location"])
            assert "cat=warning" in r.headers["location"]
            assert "使用中" in location
            assert "作業員" in location
            # DB に残っている
            assert _count_group(cc_env["db_path"], gid) == 1
        finally:
            conn = sqlite3.connect(str(cc_env["db_path"]))
            conn.execute(
                "DELETE FROM cc_workers WHERE group_name=?", ("DEL-USED-W",),
            )
            conn.commit()
            conn.close()
            _delete_group_row(cc_env["db_path"], gid)

    def test_reject_when_site_budget_references(self, cc_env):
        """現場別グループ予算が参照しているグループも削除できない。"""
        gid = _insert_group(cc_env["db_path"], name="DEL-USED-B")
        _insert_site_budget(cc_env["db_path"], site_id=9001, group_name="DEL-USED-B")
        try:
            r = cc_env["client"].post(
                f"/construction-cost/groups/{gid}/delete",
                follow_redirects=False,
            )
            assert r.status_code == 303
            location = unquote(r.headers["location"])
            assert "cat=warning" in r.headers["location"]
            assert "現場予算" in location
            assert _count_group(cc_env["db_path"], gid) == 1
        finally:
            conn = sqlite3.connect(str(cc_env["db_path"]))
            conn.execute(
                "DELETE FROM cc_site_group_budgets WHERE group_name=?",
                ("DEL-USED-B",),
            )
            conn.execute("DELETE FROM cc_sites WHERE site_id=?", (9001,))
            conn.commit()
            conn.close()
            _delete_group_row(cc_env["db_path"], gid)


# ════════════════════════════════════════════════════════════
# 存在しないグループ
# ════════════════════════════════════════════════════════════

class TestGroupDeleteMissing:
    def test_unknown_group_id_redirects_with_warning(self, cc_env):
        r = cc_env["client"].post(
            "/construction-cost/groups/9999999/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        location = unquote(r.headers["location"])
        assert "見つかりません" in location
        assert "cat=warning" in r.headers["location"]


# ════════════════════════════════════════════════════════════
# UI 描画 — 一覧画面に削除ボタンが出る
# ════════════════════════════════════════════════════════════

class TestGroupListRendersDeleteButton:
    def test_groups_page_has_delete_form(self, cc_env):
        gid = _insert_group(cc_env["db_path"], name="DEL-UI")
        try:
            r = cc_env["client"].get("/construction-cost/groups")
            assert r.status_code == 200
            # 削除フォーム + ゴミ箱アイコン + 確認ダイアログ
            assert f'/construction-cost/groups/{gid}/delete' in r.text
            assert 'bi-trash' in r.text
            assert 'onsubmit="return confirm' in r.text
        finally:
            _delete_group_row(cc_env["db_path"], gid)
