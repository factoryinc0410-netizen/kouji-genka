"""GET/POST /master/qualifications のテスト (資格マスタ管理画面)。

カバー範囲:
- 一覧 (フィルタ / include_inactive / 使用件数表示)
- 新規追加 (正常系 / バリデーション / 重複)
- 更新   (正常系 / 404 / 重複 / 自分自身の name 維持 / 論理無効化)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _seed_master(
    db_path: Path,
    *,
    qual_id: int,
    name: str,
    category: str = "技能講習",
    renewal_required: int = 1,
    default_valid_years: int | None = None,
    is_active: int = 1,
    display_order: int = 0,
) -> None:
    """テスト用の q_qualifications 行を 1 件投入する (cert は作らない)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_qualifications WHERE qual_id = ?", (qual_id,))
    conn.execute(
        """
        INSERT INTO q_qualifications
            (qual_id, name, category, renewal_required,
             default_valid_years, is_active, display_order)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (qual_id, name, category, renewal_required,
         default_valid_years, is_active, display_order),
    )
    conn.commit()
    conn.close()


def _get_master(db_path: Path, qual_id: int) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_qualifications WHERE qual_id = ?", (qual_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_master_by_name(db_path: Path, name: str) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_qualifications WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ────────────────────────────────────────────
# GET /master/qualifications — 一覧表示
# ────────────────────────────────────────────

class TestMasterIndex:
    def test_renders_active_master(self, app_env):
        _seed_master(app_env["db_path"], qual_id=400, name="MASTER-INDEX-001")
        r = app_env["client"].get("/master/qualifications")
        assert r.status_code == 200
        assert "MASTER-INDEX-001" in r.text
        assert "資格マスタ管理" in r.text

    def test_default_hides_inactive(self, app_env):
        _seed_master(
            app_env["db_path"], qual_id=401, name="MASTER-IDX-INACT-001",
            is_active=0,
        )
        r = app_env["client"].get("/master/qualifications")
        assert "MASTER-IDX-INACT-001" not in r.text

    def test_include_inactive_shows_all(self, app_env):
        _seed_master(
            app_env["db_path"], qual_id=402, name="MASTER-IDX-INACT-002",
            is_active=0,
        )
        r = app_env["client"].get("/master/qualifications?include_inactive=1")
        assert "MASTER-IDX-INACT-002" in r.text

    def test_filter_by_keyword_in_name(self, app_env):
        _seed_master(app_env["db_path"], qual_id=403, name="MASTER-KW-AAA")
        _seed_master(app_env["db_path"], qual_id=404, name="MASTER-KW-BBB")
        r = app_env["client"].get("/master/qualifications?q=AAA")
        assert "MASTER-KW-AAA" in r.text
        assert "MASTER-KW-BBB" not in r.text

    def test_filter_by_category(self, app_env):
        _seed_master(
            app_env["db_path"], qual_id=405, name="MASTER-CAT-001",
            category="技能講習",
        )
        _seed_master(
            app_env["db_path"], qual_id=406, name="MASTER-CAT-002",
            category="特別教育",
        )
        r = app_env["client"].get("/master/qualifications?category=特別教育")
        assert "MASTER-CAT-002" in r.text
        assert "MASTER-CAT-001" not in r.text

    def test_use_count_reflects_confirmed_certs(self, app_env):
        """使用件数列は status='confirmed' の cert のみカウントする (archived は除外)。"""
        _seed_master(app_env["db_path"], qual_id=410, name="MASTER-USE-COUNT-001")
        conn = sqlite3.connect(str(app_env["db_path"]))
        # 確定 2 件 + アーカイブ 1 件
        for cert_id, st in ((4001, "confirmed"), (4002, "confirmed"), (4003, "archived")):
            conn.execute(
                """
                INSERT INTO q_certificates
                    (cert_id, worker_id, qual_id, certificate_no, issued_on,
                     renewal_required, status, original_files_json,
                     created_by, created_at, updated_at)
                VALUES (?, 1, 410, ?, '2024-01-01', 0, ?,
                        '[]', 'admin-id',
                        datetime('now','localtime'), datetime('now','localtime'))
                """,
                (cert_id, f"USE-{cert_id}", st),
            )
        conn.commit()
        conn.close()

        r = app_env["client"].get("/master/qualifications?q=MASTER-USE-COUNT-001")
        assert "MASTER-USE-COUNT-001" in r.text
        # archived は除外されるので 2 件のはず (3 件ではない)
        assert "2 件" in r.text


# ────────────────────────────────────────────
# POST /master/qualifications/new — 新規追加
# ────────────────────────────────────────────

class TestMasterCreate:
    def test_creates_new_master(self, app_env):
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={
                "name": "MASTER-CREATE-001",
                "category": "免許",
                "renewal_required": "1",
                "default_valid_years": "5",
                "is_active": "1",
                "display_order": "10",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/master/qualifications"

        m = _get_master_by_name(app_env["db_path"], "MASTER-CREATE-001")
        assert m is not None
        assert m["category"] == "免許"
        assert m["default_valid_years"] == 5
        assert m["renewal_required"] == 1
        assert m["is_active"] == 1
        assert m["display_order"] == 10

    def test_400_when_name_empty(self, app_env):
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={"name": "", "is_active": "1"},
        )
        assert r.status_code == 400

    def test_400_when_name_duplicate(self, app_env):
        _seed_master(
            app_env["db_path"], qual_id=420, name="MASTER-CR-DUP-001",
        )
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={"name": "MASTER-CR-DUP-001", "is_active": "1"},
        )
        assert r.status_code == 400
        # detail に資格名が含まれる (UI でメッセージ表示する想定)
        assert "MASTER-CR-DUP-001" in r.text or "既に存在" in r.text

    def test_400_when_invalid_valid_years_text(self, app_env):
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={
                "name": "MASTER-CR-BAD-001",
                "default_valid_years": "abc",
                "is_active": "1",
            },
        )
        assert r.status_code == 400

    def test_400_when_negative_valid_years(self, app_env):
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={
                "name": "MASTER-CR-NEG-001",
                "default_valid_years": "-1",
                "is_active": "1",
            },
        )
        assert r.status_code == 400

    def test_inactive_when_unchecked(self, app_env):
        """is_active を送らない (チェックを外した状態) なら 0 で登録される。"""
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={"name": "MASTER-CR-INACT-001"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master_by_name(app_env["db_path"], "MASTER-CR-INACT-001")
        assert m["is_active"] == 0

    def test_default_valid_years_blank_means_null(self, app_env):
        """有効年数を空欄で送ると NULL で保存される (= 無期限)。"""
        r = app_env["client"].post(
            "/master/qualifications/new",
            data={
                "name": "MASTER-CR-NULL-001",
                "default_valid_years": "",
                "is_active": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master_by_name(app_env["db_path"], "MASTER-CR-NULL-001")
        assert m["default_valid_years"] is None


# ────────────────────────────────────────────
# POST /master/qualifications/{id}/edit — 更新
# ────────────────────────────────────────────

class TestMasterUpdate:
    def test_updates_existing_master(self, app_env):
        _seed_master(
            app_env["db_path"], qual_id=430, name="MASTER-UP-001",
            category="技能講習", default_valid_years=3,
        )
        r = app_env["client"].post(
            "/master/qualifications/430/edit",
            data={
                "name": "MASTER-UP-001-RENAMED",
                "category": "免許",
                "renewal_required": "1",
                "default_valid_years": "10",
                "is_active": "1",
                "display_order": "5",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        m = _get_master(app_env["db_path"], 430)
        assert m["name"] == "MASTER-UP-001-RENAMED"
        assert m["category"] == "免許"
        assert m["default_valid_years"] == 10
        assert m["display_order"] == 5

    def test_404_when_not_found(self, app_env):
        r = app_env["client"].post(
            "/master/qualifications/99999/edit",
            data={"name": "X", "is_active": "1"},
        )
        assert r.status_code == 404

    def test_400_when_name_duplicates_other_master(self, app_env):
        """別レコードと同じ name に変更しようとすると 400。"""
        _seed_master(app_env["db_path"], qual_id=431, name="MASTER-UP-DUP-A")
        _seed_master(app_env["db_path"], qual_id=432, name="MASTER-UP-DUP-B")
        r = app_env["client"].post(
            "/master/qualifications/431/edit",
            data={"name": "MASTER-UP-DUP-B", "is_active": "1"},
        )
        assert r.status_code == 400
        # 元の name は保持されている
        assert _get_master(app_env["db_path"], 431)["name"] == "MASTER-UP-DUP-A"

    def test_can_keep_own_name(self, app_env):
        """name を変更せず他フィールドを更新できる (自分自身の name は重複扱いしない)。"""
        _seed_master(
            app_env["db_path"], qual_id=433, name="MASTER-UP-SAME-001",
            category="技能講習",
        )
        r = app_env["client"].post(
            "/master/qualifications/433/edit",
            data={
                "name": "MASTER-UP-SAME-001",  # 同じ name
                "category": "新カテゴリ",
                "is_active": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master(app_env["db_path"], 433)
        assert m["category"] == "新カテゴリ"

    def test_toggle_to_inactive(self, app_env):
        """is_active を未送信で送るとマスタが無効化される。"""
        _seed_master(
            app_env["db_path"], qual_id=434, name="MASTER-UP-DEACT-001",
            is_active=1,
        )
        r = app_env["client"].post(
            "/master/qualifications/434/edit",
            data={"name": "MASTER-UP-DEACT-001"},   # is_active を送らない
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master(app_env["db_path"], 434)
        assert m["is_active"] == 0

    def test_toggle_to_active(self, app_env):
        """無効化されたマスタを再有効化できる。"""
        _seed_master(
            app_env["db_path"], qual_id=435, name="MASTER-UP-REACT-001",
            is_active=0,
        )
        r = app_env["client"].post(
            "/master/qualifications/435/edit",
            data={"name": "MASTER-UP-REACT-001", "is_active": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert _get_master(app_env["db_path"], 435)["is_active"] == 1

    def test_400_when_name_empty(self, app_env):
        _seed_master(app_env["db_path"], qual_id=436, name="MASTER-UP-EMPTY-001")
        r = app_env["client"].post(
            "/master/qualifications/436/edit",
            data={"name": "", "is_active": "1"},
        )
        assert r.status_code == 400

    def test_clearing_valid_years_to_null(self, app_env):
        """有効年数を空欄に更新すると NULL で保存される。"""
        _seed_master(
            app_env["db_path"], qual_id=437, name="MASTER-UP-CLEAR-001",
            default_valid_years=5,
        )
        r = app_env["client"].post(
            "/master/qualifications/437/edit",
            data={
                "name": "MASTER-UP-CLEAR-001",
                "default_valid_years": "",
                "is_active": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master(app_env["db_path"], 437)
        assert m["default_valid_years"] is None

    def test_updated_at_is_refreshed(self, app_env):
        """編集すると updated_at が更新される。"""
        _seed_master(app_env["db_path"], qual_id=438, name="MASTER-UP-TS-001")
        # 過去日付に書き換え
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "UPDATE q_qualifications SET updated_at='2020-01-01 00:00:00' "
            "WHERE qual_id = 438"
        )
        conn.commit()
        conn.close()

        r = app_env["client"].post(
            "/master/qualifications/438/edit",
            data={"name": "MASTER-UP-TS-001", "is_active": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        m = _get_master(app_env["db_path"], 438)
        assert m["updated_at"] != "2020-01-01 00:00:00"


# ────────────────────────────────────────────
# UI レンダリング (モーダルが各行ごとに出ているか)
# ────────────────────────────────────────────

class TestMasterRenders:
    def test_edit_modal_rendered_per_row(self, app_env):
        """各行ごとに固有 ID の編集モーダルが描画される。"""
        _seed_master(app_env["db_path"], qual_id=440, name="MASTER-RNDR-001")
        r = app_env["client"].get("/master/qualifications")
        assert 'id="editMasterModal-440"' in r.text
        assert 'action="/master/qualifications/440/edit"' in r.text

    def test_new_modal_rendered(self, app_env):
        """新規追加モーダルが常に描画される。"""
        r = app_env["client"].get("/master/qualifications")
        assert 'id="newMasterModal"' in r.text
        assert 'action="/master/qualifications/new"' in r.text
