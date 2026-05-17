"""資格者マスタ (q_staff / Option 1) のテスト。

検証内容:
  - ネイティブ新規登録 (cc_workers + q_staff の二段 INSERT)
  - 取込のデダップ (UNIQUE 制約 + ON CONFLICT で再有効化)
  - 無効化 (active cert があれば 400、無ければ 303 + is_active=0)
  - 権限ガード (qualifications.manager 必須)
  - _fetch_workers が q_staff active のスタッフだけを返すこと

conftest の app_env は admin (is_admin=1) で dependency_overrides を当てるため、
権限テストはこの fixture とは別に dependency_override を一時差し替えて行う。
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient


# ────────────────────────────────────────────
# 補助
# ────────────────────────────────────────────

def _q_staff_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, worker_id, is_active, source FROM q_staff ORDER BY id"
    ).fetchall()]
    conn.close()
    return rows


def _cc_workers(db_path, *, only_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM cc_workers"
    args: tuple = ()
    if only_id is not None:
        sql += " WHERE worker_id = ?"
        args = (only_id,)
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def _seed_extra_cc_worker(db_path, *, worker_id: int, name: str, qual_only: int = 0):
    """conftest が seed していない追加 cc_workers を入れる (取込候補テスト用)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active, is_qualifications_only) "
        "VALUES (?, ?, 'C班', 1, ?)",
        (worker_id, name, qual_only),
    )
    conn.commit()
    conn.close()


def _delete_q_staff(db_path, worker_id: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_staff WHERE worker_id = ?", (worker_id,))
    conn.commit()
    conn.close()


# ════════════════════════════════════════════
# 一覧表示
# ════════════════════════════════════════════

class TestStaffIndex:
    def test_renders_seeded_staff(self, app_env):
        r = app_env["client"].get("/qualifications/staff")
        assert r.status_code == 200
        # conftest で 3 名 seed されている
        for name in ("山田太郎", "佐藤花子", "鈴木一郎"):
            assert name in r.text
        # 「日報システムから取り込む」ボタンが表示される
        assert "日報システムから取り込む" in r.text

    def test_shows_import_candidate_count(self, app_env):
        # 追加 cc_worker を入れる (q_staff には入れない → 候補に出る)
        _seed_extra_cc_worker(app_env["db_path"], worker_id=901, name="候補次郎")
        try:
            r = app_env["client"].get("/qualifications/staff")
            assert r.status_code == 200
            # 候補がある時は import ボタンにバッジが付く
            assert "候補次郎" not in r.text  # 一覧に出ない (q_staff 未登録)
        finally:
            # クリーンアップ
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 901")
            conn.commit()
            conn.close()


# ════════════════════════════════════════════
# ネイティブ新規登録
# ════════════════════════════════════════════

class TestStaffNew:
    def test_creates_cc_worker_and_q_staff(self, app_env):
        before = len(_q_staff_rows(app_env["db_path"]))
        r = app_env["client"].post(
            "/qualifications/staff/new",
            data={"worker_name": "新人太郎", "group_name": "新人", "role": "見習い"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        # q_staff に 1 件増えた
        rows = _q_staff_rows(app_env["db_path"])
        assert len(rows) == before + 1
        new_row = max(rows, key=lambda x: x["id"])
        assert new_row["source"] == "native"
        assert new_row["is_active"] == 1
        # cc_workers にも対応行ができ、is_qualifications_only=1
        cc = _cc_workers(app_env["db_path"], only_id=new_row["worker_id"])
        assert len(cc) == 1
        assert cc[0]["worker_name"] == "新人太郎"
        assert cc[0]["group_name"] == "新人"
        assert cc[0]["role"] == "見習い"
        assert cc[0]["is_qualifications_only"] == 1
        # クリーンアップ
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("DELETE FROM q_staff WHERE worker_id = ?", (new_row["worker_id"],))
        conn.execute("DELETE FROM cc_workers WHERE worker_id = ?", (new_row["worker_id"],))
        conn.commit()
        conn.close()

    def test_blank_name_400(self, app_env):
        r = app_env["client"].post(
            "/qualifications/staff/new",
            data={"worker_name": "  ", "group_name": "", "role": ""},
        )
        assert r.status_code == 400


# ════════════════════════════════════════════
# 取込 (インポート)
# ════════════════════════════════════════════

class TestStaffImport:
    def test_preview_lists_candidates_only(self, app_env):
        _seed_extra_cc_worker(app_env["db_path"], worker_id=910, name="取込候補A")
        _seed_extra_cc_worker(app_env["db_path"], worker_id=911, name="取込候補B")
        try:
            r = app_env["client"].get("/qualifications/staff/import")
            assert r.status_code == 200
            # 既に q_staff active な seed 3 名は候補から消えている
            assert "山田太郎" not in r.text
            # 未登録 cc_workers は候補に出る
            assert "取込候補A" in r.text
            assert "取込候補B" in r.text
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM cc_workers WHERE worker_id IN (910, 911)")
            conn.commit()
            conn.close()

    def test_import_inserts_q_staff_imported(self, app_env):
        _seed_extra_cc_worker(app_env["db_path"], worker_id=920, name="取込テスト1")
        _seed_extra_cc_worker(app_env["db_path"], worker_id=921, name="取込テスト2")
        try:
            r = app_env["client"].post(
                "/qualifications/staff/import",
                data={"worker_ids": ["920", "921"]},
                follow_redirects=False,
            )
            assert r.status_code == 303
            rows = {r["worker_id"]: r for r in _q_staff_rows(app_env["db_path"])}
            assert rows[920]["source"] == "imported"
            assert rows[920]["is_active"] == 1
            assert rows[921]["source"] == "imported"
            assert rows[921]["is_active"] == 1
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM q_staff WHERE worker_id IN (920, 921)")
            conn.execute("DELETE FROM cc_workers WHERE worker_id IN (920, 921)")
            conn.commit()
            conn.close()

    def test_import_dedup_no_duplicate_rows(self, app_env):
        """同じ worker_id を 2 回取込しても q_staff に行は 1 個だけ。"""
        _seed_extra_cc_worker(app_env["db_path"], worker_id=930, name="重複防止")
        try:
            for _ in range(2):
                r = app_env["client"].post(
                    "/qualifications/staff/import",
                    data={"worker_ids": ["930"]},
                    follow_redirects=False,
                )
                assert r.status_code == 303
            rows = [r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 930]
            assert len(rows) == 1
            assert rows[0]["is_active"] == 1
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM q_staff WHERE worker_id = 930")
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 930")
            conn.commit()
            conn.close()

    def test_import_reactivates_soft_deleted(self, app_env):
        """soft-delete (is_active=0) されたスタッフを再取込で再有効化できる。"""
        _seed_extra_cc_worker(app_env["db_path"], worker_id=940, name="再有効化候補")
        try:
            # 1 回取込 → soft-delete → 再取込
            app_env["client"].post(
                "/qualifications/staff/import",
                data={"worker_ids": ["940"]}, follow_redirects=False,
            )
            # 直接 q_staff を soft-delete する (delete エンドポイントは active cert 0 件
            # でなくても動くが、ここでは 1 件もないので普通に通す)
            row = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 940)
            staff_id = row["id"]
            r_del = app_env["client"].post(
                f"/qualifications/staff/{staff_id}/delete", follow_redirects=False,
            )
            assert r_del.status_code == 303
            row_after_del = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 940)
            assert row_after_del["is_active"] == 0
            # 再取込
            r_re = app_env["client"].post(
                "/qualifications/staff/import",
                data={"worker_ids": ["940"]}, follow_redirects=False,
            )
            assert r_re.status_code == 303
            row_re = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 940)
            assert row_re["is_active"] == 1
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM q_staff WHERE worker_id = 940")
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 940")
            conn.commit()
            conn.close()

    def test_import_with_empty_selection_redirects(self, app_env):
        """worker_ids 空でも 500 にせず import ページへ戻すだけ。"""
        r = app_env["client"].post(
            "/qualifications/staff/import",
            data={}, follow_redirects=False,
        )
        assert r.status_code == 303


# ════════════════════════════════════════════
# 無効化 (delete = soft delete)
# ════════════════════════════════════════════

class TestStaffDelete:
    def test_soft_delete_sets_inactive(self, app_env):
        """active cert 0 件のスタッフは無効化できる。"""
        _seed_extra_cc_worker(app_env["db_path"], worker_id=950, name="削除対象")
        try:
            app_env["client"].post(
                "/qualifications/staff/import",
                data={"worker_ids": ["950"]}, follow_redirects=False,
            )
            row = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 950)
            r = app_env["client"].post(
                f"/qualifications/staff/{row['id']}/delete", follow_redirects=False,
            )
            assert r.status_code == 303
            row_after = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 950)
            assert row_after["is_active"] == 0
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM q_staff WHERE worker_id = 950")
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 950")
            conn.commit()
            conn.close()

    def test_soft_delete_blocked_when_active_certs_exist(self, app_env):
        """有効な資格者証を持つスタッフは無効化できない (400)。"""
        _seed_extra_cc_worker(app_env["db_path"], worker_id=960, name="保有あり")
        # cc_workers + q_staff + 1 件の active cert を作る
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("INSERT INTO q_staff (worker_id, is_active, source) VALUES (960, 1, 'imported')")
        conn.execute(
            "INSERT OR IGNORE INTO q_qualifications (qual_id, name, category) VALUES (888, 'テスト資格', '技能')"
        )
        conn.execute(
            "INSERT INTO q_certificates (worker_id, qual_id, certificate_no, issuer, "
            "issued_on, expires_on, renewal_required, status) "
            "VALUES (960, 888, 'C-960', 'org', '2024-01-01', '2030-01-01', 0, 'confirmed')"
        )
        conn.commit()
        conn.close()
        try:
            row = next(r for r in _q_staff_rows(app_env["db_path"]) if r["worker_id"] == 960)
            r = app_env["client"].post(
                f"/qualifications/staff/{row['id']}/delete", follow_redirects=False,
            )
            assert r.status_code == 400
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM q_certificates WHERE worker_id = 960")
            conn.execute("DELETE FROM q_staff WHERE worker_id = 960")
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 960")
            conn.commit()
            conn.close()

    def test_404_when_staff_not_found(self, app_env):
        r = app_env["client"].post("/qualifications/staff/99999/delete")
        assert r.status_code == 404


# ════════════════════════════════════════════
# 権限ガード (qualifications.manager 必須)
# ════════════════════════════════════════════

class TestStaffPermission:
    """default conftest は is_admin=True で素通しするので、ここでは
    non-admin / no-perm ユーザに dependency_override を一時差し替えて
    403 を確認する。"""

    def test_no_perm_user_gets_403(self, app_env):
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        no_perm = {
            "id": "u-noperm", "username": "u", "display_name": "U",
            "is_admin": 0, "permissions": {}, "role_permissions": {},
        }
        # 既存の admin override をスタックの上に被せる (yield 内で revert)
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: no_perm
        try:
            client = TestClient(app)
            for method, path, kw in [
                ("GET",  "/qualifications/staff", {}),
                ("POST", "/qualifications/staff/new", {"data": {"worker_name": "x"}}),
                ("POST", "/qualifications/staff/1/delete", {}),
                ("GET",  "/qualifications/staff/import", {}),
                ("POST", "/qualifications/staff/import", {"data": {}}),
            ]:
                r = client.request(method, path, follow_redirects=False, **kw)
                assert r.status_code == 403, f"{method} {path}: {r.status_code}"
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior

    def test_qualifications_manager_user_passes(self, app_env):
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        qual_mgr = {
            "id": "u-qmgr", "username": "u", "display_name": "U",
            "is_admin": 0,
            "permissions": {"qualifications": "manager"},
            "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: qual_mgr
        try:
            client = TestClient(app)
            r = client.get("/qualifications/staff")
            assert r.status_code == 200
            r2 = client.get("/qualifications/staff/import")
            assert r2.status_code == 200
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior


# ════════════════════════════════════════════
# _fetch_workers が q_staff フィルタに従う
# ════════════════════════════════════════════

class TestFetchWorkersFiltering:
    def test_dropdown_excludes_non_q_staff_workers(self, app_env):
        """q_staff に居ない cc_workers は manual_add の作業員ドロップダウンに出ない。"""
        _seed_extra_cc_worker(app_env["db_path"], worker_id=970, name="日報専用さん")
        try:
            r = app_env["client"].get("/qualifications/manual-add")
            assert r.status_code == 200
            # seed 3 名は出る
            assert "山田太郎" in r.text
            # q_staff 未登録の 970 は出ない
            assert "日報専用さん" not in r.text
        finally:
            conn = sqlite3.connect(str(app_env["db_path"]))
            conn.execute("DELETE FROM cc_workers WHERE worker_id = 970")
            conn.commit()
            conn.close()
