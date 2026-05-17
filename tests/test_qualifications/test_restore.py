"""POST /qualifications/{cert_id}/restore および index の include_archived のテスト。

Restore のセマンティクス (delete とは異なる):
  - archived → confirmed への遷移のみ許容
  - 既に confirmed なら 400 (重複復元防止: 冪等にしない)
  - 存在しない cert_id は 404
  - 成功時は ``?include_archived=1`` 付きで一覧へリダイレクト

include_archived フィルタ:
  - デフォルト: archived 行は表示しない (既存挙動)
  - チェック ON: archived も表示し、復元ボタンを出す
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _seed_master(db_path: Path, *, qual_id: int = 60) -> None:
    """資格マスタを 1 件投入 (冪等)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT OR IGNORE INTO q_qualifications
            (qual_id, name, category, renewal_required)
        VALUES (?, '玉掛け技能講習', '技能講習', 0)
        """,
        (qual_id,),
    )
    conn.commit()
    conn.close()


def _insert_cert(
    db_path: Path,
    *,
    cert_id: int,
    status: str = "archived",
    cert_no: str | None = None,
    qual_id: int = 60,
) -> None:
    """テスト用の cert を 1 件投入する。

    cert_no を None のままにするとテストごとに cert_id 由来の値が入り、
    HTML に出る文字列が他テストと衝突しなくなる。
    """
    if cert_no is None:
        cert_no = f"第RES{cert_id:04d}号"
    _seed_master(db_path, qual_id=qual_id)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, 1, ?, ?, '○○協会',
                '2024-04-01', '2029-03-31', 0, ?,
                '[]', 'admin-id',
                datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, qual_id, cert_no, status),
    )
    conn.commit()
    conn.close()


def _get_status(db_path: Path, cert_id: int) -> str | None:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM q_certificates WHERE cert_id = ?", (cert_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ────────────────────────────────────────────
# POST /{cert_id}/restore — 正常系
# ────────────────────────────────────────────

class TestRestoreSuccess:
    def test_archived_to_confirmed(self, app_env):
        """archived の cert を POST すると confirmed に戻る。"""
        _insert_cert(app_env["db_path"], cert_id=300, status="archived")
        assert _get_status(app_env["db_path"], 300) == "archived"

        r = app_env["client"].post(
            "/qualifications/300/restore", follow_redirects=False,
        )
        assert r.status_code == 303
        assert _get_status(app_env["db_path"], 300) == "confirmed"

    def test_redirects_to_archived_view(self, app_env):
        """成功後は ?include_archived=1 付きで一覧へリダイレクト
        (操作直後のレコードがそのまま見えるよう archived ビューに留める)。"""
        _insert_cert(app_env["db_path"], cert_id=301, status="archived")
        r = app_env["client"].post(
            "/qualifications/301/restore", follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/?include_archived=1"

    def test_updated_at_is_refreshed(self, app_env):
        """復元時に updated_at が更新される (監査用)。"""
        _insert_cert(app_env["db_path"], cert_id=302, status="archived")
        # updated_at を過去日付に書き換える
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "UPDATE q_certificates SET updated_at='2020-01-01 00:00:00' "
            "WHERE cert_id = 302"
        )
        conn.commit()
        conn.close()

        r = app_env["client"].post(
            "/qualifications/302/restore", follow_redirects=False,
        )
        assert r.status_code == 303

        conn = sqlite3.connect(str(app_env["db_path"]))
        updated_at = conn.execute(
            "SELECT updated_at FROM q_certificates WHERE cert_id = 302"
        ).fetchone()[0]
        conn.close()
        # 過去の固定値ではなくなっている
        assert updated_at != "2020-01-01 00:00:00"

    def test_restored_cert_appears_in_default_index(self, app_env):
        """復元後はデフォルト一覧 (include_archived=0) に表示される。"""
        cert_no = "第RESVIS001号"
        _insert_cert(
            app_env["db_path"], cert_id=303,
            status="archived", cert_no=cert_no,
        )
        # アーカイブ中: デフォルト一覧には出ない
        r1 = app_env["client"].get("/qualifications/?status=all")
        assert cert_no not in r1.text

        # 復元
        app_env["client"].post(
            "/qualifications/303/restore", follow_redirects=False,
        )

        # 復元後: デフォルト一覧に出る
        r2 = app_env["client"].get("/qualifications/?status=all")
        assert cert_no in r2.text


# ────────────────────────────────────────────
# POST /{cert_id}/restore — 異常系
# ────────────────────────────────────────────

class TestRestoreErrors:
    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].post("/qualifications/99990/restore")
        assert r.status_code == 404

    def test_400_when_already_confirmed(self, app_env):
        """confirmed の cert を復元しようとすると 400 (重複復元防止)。"""
        _insert_cert(app_env["db_path"], cert_id=310, status="confirmed")
        r = app_env["client"].post("/qualifications/310/restore")
        assert r.status_code == 400
        # status は変わらない
        assert _get_status(app_env["db_path"], 310) == "confirmed"

    def test_400_when_already_draft(self, app_env):
        """draft 状態の cert に対しても restore は 400。"""
        _insert_cert(app_env["db_path"], cert_id=311, status="draft")
        r = app_env["client"].post("/qualifications/311/restore")
        assert r.status_code == 400
        assert _get_status(app_env["db_path"], 311) == "draft"

    def test_double_restore_second_call_400(self, app_env):
        """1 回目は 303、2 回目は 400 (冪等にしない)。"""
        _insert_cert(app_env["db_path"], cert_id=312, status="archived")
        r1 = app_env["client"].post(
            "/qualifications/312/restore", follow_redirects=False,
        )
        assert r1.status_code == 303
        assert _get_status(app_env["db_path"], 312) == "confirmed"

        r2 = app_env["client"].post(
            "/qualifications/312/restore", follow_redirects=False,
        )
        assert r2.status_code == 400
        # status は confirmed のまま
        assert _get_status(app_env["db_path"], 312) == "confirmed"


# ────────────────────────────────────────────
# GET / — include_archived フィルタの動作
# ────────────────────────────────────────────

class TestIncludeArchivedFilter:
    def test_default_hides_archived(self, app_env):
        """include_archived 無指定なら archived は出ない (既存挙動の保証)。"""
        cert_no = "第RESHIDE001号"
        _insert_cert(
            app_env["db_path"], cert_id=320,
            status="archived", cert_no=cert_no,
        )
        r = app_env["client"].get("/qualifications/?status=all")
        assert r.status_code == 200
        assert cert_no not in r.text

    def test_include_archived_shows_archived(self, app_env):
        cert_no = "第RESSHOW001号"
        _insert_cert(
            app_env["db_path"], cert_id=321,
            status="archived", cert_no=cert_no,
        )
        r = app_env["client"].get("/qualifications/?status=all&include_archived=1")
        assert r.status_code == 200
        assert cert_no in r.text

    def test_archived_row_renders_restore_button(self, app_env):
        """archived 行には復元ボタン (form action="/<id>/restore") が出る。"""
        _insert_cert(app_env["db_path"], cert_id=322, status="archived")
        r = app_env["client"].get("/qualifications/?status=all&include_archived=1")
        assert r.status_code == 200
        assert 'action="/qualifications/322/restore"' in r.text
        assert "bi-arrow-counterclockwise" in r.text

    def test_archived_row_hides_edit_and_delete_buttons(self, app_env):
        """archived 行には編集/削除ボタンを出さない (この cert に対してのみ判定)。"""
        _insert_cert(app_env["db_path"], cert_id=323, status="archived")
        r = app_env["client"].get("/qualifications/?status=all&include_archived=1")
        assert "/qualifications/edit/323" not in r.text
        assert "/qualifications/delete/323" not in r.text

    def test_confirmed_row_keeps_edit_and_delete(self, app_env):
        """include_archived=1 でも confirmed 行は編集/削除を表示し続ける。"""
        _insert_cert(app_env["db_path"], cert_id=324, status="confirmed")
        r = app_env["client"].get("/qualifications/?status=all&include_archived=1")
        assert "/qualifications/edit/324" in r.text
        assert "/qualifications/delete/324" in r.text
        # 復元ボタンはこの cert には出ない
        assert "/qualifications/324/restore" not in r.text

    def test_include_archived_query_param_still_routed(self, app_env):
        """UI のチェックボックスは廃止したが、``?include_archived=1`` クエリは
        引き続き router 側で受理し、archived も描画する (restore 後の再表示で利用)。"""
        _insert_cert(app_env["db_path"], cert_id=327, status="archived")
        r_off = app_env["client"].get("/qualifications/?status=all")
        # クエリ無しでは archived 行は出ない
        assert 'action="/qualifications/327/restore"' not in r_off.text

        r_on = app_env["client"].get("/qualifications/?status=all&include_archived=1")
        assert 'action="/qualifications/327/restore"' in r_on.text

    def test_keyword_filter_combines_with_archived(self, app_env):
        """キーワード絞込 + include_archived の組合せで両方効く。"""
        cert_no_target = "第RESCOMBO001号"
        cert_no_other  = "第RESCOMBO999号"
        _insert_cert(
            app_env["db_path"], cert_id=325,
            status="archived", cert_no=cert_no_target,
        )
        _insert_cert(
            app_env["db_path"], cert_id=326,
            status="archived", cert_no=cert_no_other,
        )
        r = app_env["client"].get(
            f"/qualifications/?include_archived=1&q={cert_no_target}"
        )
        assert r.status_code == 200
        assert cert_no_target in r.text
        assert cert_no_other not in r.text
