"""GET/POST /edit/{cert_id} と POST /delete/{cert_id} のテスト。

archived された資格者証は一覧 / 編集画面から消えるが、DB 行は保持されることも検証する。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _cert_no_for(cert_id: int) -> str:
    """cert_id ごとにユニークな cert_no を返す (テスト間で衝突しないように)。"""
    return f"第ED{cert_id:04d}号"


def _seed_one_certificate(db_path: Path, *, cert_id: int = 201) -> None:
    """1 件の確定済み資格者証を投入する。

    cert_no は cert_id 由来でユニーク化することで、複数テストが
    同じ DB に追記しても識別できる。
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO q_qualifications
            (qual_id, name, category, renewal_required)
        VALUES (50, '玉掛け技能講習', '技能講習', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, 1, 50, ?, '○○協会',
                '2024-04-01', '2029-03-31', 0, 'confirmed',
                ?, 'admin-id', datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, _cert_no_for(cert_id),
         '["qualifications/test-job-id/sample.pdf"]'),
    )
    conn.commit()
    conn.close()


def _get_cert(db_path: Path, cert_id: int) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_certificates WHERE cert_id = ?", (cert_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ────────────────────────────────────────────
# GET /edit/{cert_id}
# ────────────────────────────────────────────

class TestEditGet:
    def test_renders_form_with_current_values(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=210)
        # _parse_original_files は物理ファイル実体の存在も検証するため、
        # テスト時は staging に該当ファイルを作っておく。
        physical = app_env["staging_root"] / "test-job-id" / "sample.pdf"
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get("/qualifications/edit/210")
            assert r.status_code == 200
            # 現在値がフォームにプリフィル
            assert f'value="{_cert_no_for(210)}"' in r.text
            assert 'value="2024-04-01"' in r.text
            assert 'value="2029-03-31"' in r.text
            assert "玉掛け技能講習" in r.text
            # 作業員 1 (山田太郎) が選択済み
            assert 'value="1"' in r.text and "山田太郎" in r.text
            # 原本ファイルへのリンク
            assert "/qualifications/files/test-job-id/sample.pdf" in r.text
        finally:
            physical.unlink(missing_ok=True)

    def test_preview_url_uses_relative_path_with_download_false(self, app_env):
        """edit 画面の embed / 別タブリンクは絶対 URL (http://...) ではなく
        サイトルート相対パス + ``?download=false`` を使う。

        これによりブラウザは現在ページの origin (scheme/host/port) で URL を
        解決するので、外部 IP からアクセスした際にポート番号を取りこぼして
        ERR_CONNECTION_REFUSED になる事故が起きない。"""
        _seed_one_certificate(app_env["db_path"], cert_id=212)
        physical = app_env["staging_root"] / "test-job-id" / "sample.pdf"
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get("/qualifications/edit/212")
            assert r.status_code == 200
            # PDF プレビューは <embed type="application/pdf"> で描画
            assert (
                '<embed src="/qualifications/files/test-job-id/sample.pdf?download=false"'
                in r.text
            )
            assert 'type="application/pdf"' in r.text
            # フォールバック「📄 別ウィンドウで開く」リンクが描画される
            assert "📄 別ウィンドウで開く" in r.text
            assert 'target="_blank"' in r.text
            # 別タブリンクも ?download=false の相対パスを使う
            assert (
                'href="/qualifications/files/test-job-id/sample.pdf?download=false"'
                in r.text
            )
            # 絶対 URL (http:// 始まり) は出ない (CDN 除く)
            # → 厳密版: testserver スキームは絶対に出ない
            assert "://testserver" not in r.text
        finally:
            physical.unlink(missing_ok=True)

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/edit/99999")
        assert r.status_code == 404

    def test_410_when_cert_archived(self, app_env):
        """archived な資格者証は編集できない (410 Gone)。"""
        _seed_one_certificate(app_env["db_path"], cert_id=211)
        # 直接 DB で archived にする
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("UPDATE q_certificates SET status='archived' WHERE cert_id = 211")
        conn.commit()
        conn.close()

        r = app_env["client"].get("/qualifications/edit/211")
        assert r.status_code == 410


# ────────────────────────────────────────────
# POST /edit/{cert_id}
# ────────────────────────────────────────────

class TestEditPost:
    def test_updates_existing_certificate(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=220)
        r = app_env["client"].post(
            "/qualifications/edit/220",
            data={
                "worker_id": "2",                  # 山田 → 佐藤に変更
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": "第ED001号_修正",
                "issuer": "新団体",
                "issued_on": "2024-04-01",
                "expires_on": "2030-04-01",       # 期限を延長
                "renewal_required": "1",          # 更新必要に変更
                "notes": "誤入力を修正",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

        cert = _get_cert(app_env["db_path"], 220)
        assert cert["worker_id"] == 2
        assert cert["certificate_no"] == "第ED001号_修正"
        assert cert["issuer"] == "新団体"
        assert cert["expires_on"] == "2030-04-01"
        assert cert["renewal_required"] == 1
        assert cert["notes"] == "誤入力を修正"
        # status は confirmed のまま
        assert cert["status"] == "confirmed"

    def test_changing_qualification_name_creates_new_master(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=221)
        r = app_env["client"].post(
            "/qualifications/edit/221",
            data={
                "worker_id": "1",
                "qualification_name": "新しい資格名_テスト",   # 新規
                "category": "その他",
                "certificate_no": "x",
                "issuer": "x",
                "issued_on": "2024-04-01",
                "expires_on": "",
                "renewal_required": "1",
                "notes": "",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        # q_qualifications に新規行が作られる
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row
        q = conn.execute(
            "SELECT * FROM q_qualifications WHERE name = '新しい資格名_テスト'"
        ).fetchone()
        assert q is not None
        # cert がその qual_id を指している
        cert = _get_cert(app_env["db_path"], 221)
        assert cert["qual_id"] == q["qual_id"]
        conn.close()

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].post(
            "/qualifications/edit/99998",
            data={"worker_id": "1", "qualification_name": "x", "issued_on": "2024-01-01"},
        )
        assert r.status_code == 404

    @pytest.mark.parametrize("missing_field", [
        "worker_id",
        "qualification_name",
        "issued_on",
    ])
    def test_required_field_missing(self, app_env, missing_field):
        _seed_one_certificate(app_env["db_path"], cert_id=222)
        data = {
            "worker_id": "1",
            "qualification_name": "x",
            "issued_on": "2024-01-01",
        }
        # 該当フィールドだけ空にする
        data[missing_field] = "" if missing_field != "worker_id" else "0"
        r = app_env["client"].post("/qualifications/edit/222", data=data)
        assert r.status_code == 400

    def test_replaces_attached_file_when_uploaded(self, app_env):
        """multipart で file を送ると original_files_json が新ファイルへ差し替わる。"""
        import json as _json

        _seed_one_certificate(app_env["db_path"], cert_id=223)
        # 旧ファイルが指していたパスを記録 (差し替え後に変わることを検証)
        before = _get_cert(app_env["db_path"], 223)
        before_paths = _json.loads(before["original_files_json"] or "[]")

        r = app_env["client"].post(
            "/qualifications/edit/223",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": _cert_no_for(223),
                "issuer": "○○協会",
                "issued_on": "2024-04-01",
                "expires_on": "2029-03-31",
                "renewal_required": "0",
                "notes": "",
            },
            files={"file": ("replacement.pdf", b"%PDF-1.4 replacement bytes",
                            "application/pdf")},
            follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert(app_env["db_path"], 223)
        new_paths = _json.loads(cert["original_files_json"] or "[]")
        assert new_paths != before_paths
        assert len(new_paths) == 1
        # 命名スキームは "qualifications/edit_<uuid>/<filename>"
        assert new_paths[0].startswith("qualifications/edit_")
        assert new_paths[0].endswith("/replacement.pdf")
        # 物理ファイルが staging に書き出されている
        rel = new_paths[0].split("/", 1)[1]
        physical = app_env["staging_root"] / rel
        assert physical.is_file()
        assert physical.read_bytes() == b"%PDF-1.4 replacement bytes"

    def test_keeps_existing_file_when_no_upload(self, app_env):
        """ファイル未送信のときは original_files_json をそのまま保持する。"""
        import json as _json

        _seed_one_certificate(app_env["db_path"], cert_id=224)
        before = _get_cert(app_env["db_path"], 224)

        r = app_env["client"].post(
            "/qualifications/edit/224",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": _cert_no_for(224),
                "issuer": "○○協会",
                "issued_on": "2024-04-01",
                "expires_on": "2029-03-31",
                "renewal_required": "0",
                "notes": "ファイル無変更",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert(app_env["db_path"], 224)
        # original_files_json は変わらない
        assert (
            _json.loads(cert["original_files_json"] or "[]")
            == _json.loads(before["original_files_json"] or "[]")
        )
        # 他のフィールドは更新される
        assert cert["notes"] == "ファイル無変更"

    def test_rejects_disallowed_extension_on_replace(self, app_env):
        """非対応拡張子のファイル差し替え試行は 400 で拒否される。"""
        _seed_one_certificate(app_env["db_path"], cert_id=225)
        r = app_env["client"].post(
            "/qualifications/edit/225",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": _cert_no_for(225),
                "issuer": "○○協会",
                "issued_on": "2024-04-01",
                "expires_on": "2029-03-31",
                "renewal_required": "0",
                "notes": "",
            },
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
            follow_redirects=False,
        )
        assert r.status_code == 400

    def test_replacement_deletes_old_physical_file(self, app_env):
        """差し替え成功後、旧 staging ファイルが物理削除される
        (DB / ディスクの状態を整合させる)。"""
        # 共有 staging を避けるためユニークな job 名で cert を作成
        cert_id = 226
        unique_job = f"job-edit-test-{cert_id}"
        unique_path = f"qualifications/{unique_job}/sample.pdf"
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
        conn.execute(
            "INSERT OR REPLACE INTO q_qualifications "
            "(qual_id, name, category, renewal_required) "
            "VALUES (50, '玉掛け技能講習', '技能講習', 0)"
        )
        conn.execute(
            """
            INSERT INTO q_certificates
                (cert_id, worker_id, qual_id, certificate_no, issuer,
                 issued_on, expires_on, renewal_required, status,
                 original_files_json, created_by, created_at, updated_at)
            VALUES (?, 1, 50, ?, '○○協会', '2024-04-01', '2029-03-31',
                    0, 'confirmed', ?, 'admin-id',
                    datetime('now','localtime'), datetime('now','localtime'))
            """,
            (cert_id, _cert_no_for(cert_id), f'["{unique_path}"]'),
        )
        conn.commit()
        conn.close()

        # 旧物理ファイルを作成
        old_dir = app_env["staging_root"] / unique_job
        old_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_dir / "sample.pdf"
        old_file.write_bytes(b"%PDF-1.4 OLD content")
        assert old_file.is_file()

        r = app_env["client"].post(
            f"/qualifications/edit/{cert_id}",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": _cert_no_for(cert_id),
                "issuer": "○○協会",
                "issued_on": "2024-04-01",
                "expires_on": "2029-03-31",
                "renewal_required": "0",
                "notes": "",
            },
            files={"file": ("new.pdf", b"%PDF-1.4 NEW content",
                            "application/pdf")},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # 旧物理ファイル + 親ディレクトリは消えている (= 孤児を残さない)
        assert not old_file.exists(), "旧ファイルが削除されていない"
        assert not old_dir.exists(), "空になった旧ディレクトリも削除されるべき"

    def test_replacement_keeps_shared_file(self, app_env):
        """旧パスを別の cert がまだ参照している場合は物理削除をスキップする
        (OCR ジョブが複数 cert に分かれた staging 共有ケースを保護)。"""
        shared_job = "job-shared-edit-test"
        shared_path = f"qualifications/{shared_job}/sample.pdf"
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "INSERT OR REPLACE INTO q_qualifications "
            "(qual_id, name, category, renewal_required) "
            "VALUES (50, '玉掛け技能講習', '技能講習', 0)"
        )
        for cid in (227, 228):
            conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cid,))
            conn.execute(
                """
                INSERT INTO q_certificates
                    (cert_id, worker_id, qual_id, certificate_no, issuer,
                     issued_on, expires_on, renewal_required, status,
                     original_files_json, created_by, created_at, updated_at)
                VALUES (?, 1, 50, ?, '○○協会', '2024-04-01', '2029-03-31',
                        0, 'confirmed', ?, 'admin-id',
                        datetime('now','localtime'), datetime('now','localtime'))
                """,
                (cid, _cert_no_for(cid), f'["{shared_path}"]'),
            )
        conn.commit()
        conn.close()

        # 物理ファイルを共有 staging に置く (両 cert から参照される)
        old_dir = app_env["staging_root"] / shared_job
        old_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_dir / "sample.pdf"
        old_file.write_bytes(b"%PDF-1.4 SHARED content")

        # cert_id=227 だけファイル差し替え
        r = app_env["client"].post(
            "/qualifications/edit/227",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "category": "技能講習",
                "certificate_no": _cert_no_for(227),
                "issuer": "○○協会",
                "issued_on": "2024-04-01",
                "expires_on": "2029-03-31",
                "renewal_required": "0",
                "notes": "",
            },
            files={"file": ("new.pdf", b"%PDF-1.4 NEW", "application/pdf")},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # 旧ファイルは cert_id=228 がまだ参照しているので残る
        assert old_file.is_file(), (
            "別 cert が参照中の旧ファイルを削除してはいけない"
        )


# ────────────────────────────────────────────
# POST /delete/{cert_id}
# ────────────────────────────────────────────

class TestDelete:
    def test_archives_certificate(self, app_env):
        _seed_one_certificate(app_env["db_path"], cert_id=230)
        r = app_env["client"].post(
            "/qualifications/delete/230", follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert(app_env["db_path"], 230)
        assert cert is not None  # 物理削除されていない
        assert cert["status"] == "archived"

    def test_archived_disappears_from_index(self, app_env):
        cert_id = 231
        _seed_one_certificate(app_env["db_path"], cert_id=cert_id)
        cert_no = _cert_no_for(cert_id)
        # アーカイブ前は出る
        r1 = app_env["client"].get("/qualifications/?status=all")
        assert cert_no in r1.text
        # アーカイブ
        post_resp = app_env["client"].post(
            f"/qualifications/delete/{cert_id}", follow_redirects=False,
        )
        assert post_resp.status_code == 303
        # アーカイブ後は出ない (この cert_no はユニークなので他テスト由来でも混ざらない)
        r2 = app_env["client"].get("/qualifications/?status=all")
        assert cert_no not in r2.text

    def test_404_when_cert_not_found(self, app_env):
        r = app_env["client"].post("/qualifications/delete/99997")
        assert r.status_code == 404

    def test_idempotent_on_already_archived(self, app_env):
        """既に archived のものを再度アーカイブしても 303 で正常終了 (冪等)。"""
        _seed_one_certificate(app_env["db_path"], cert_id=232)
        app_env["client"].post(
            "/qualifications/delete/232", follow_redirects=False,
        )
        # 二度目
        r = app_env["client"].post(
            "/qualifications/delete/232", follow_redirects=False,
        )
        assert r.status_code == 303
        cert = _get_cert(app_env["db_path"], 232)
        assert cert["status"] == "archived"

    def test_redirects_to_index_by_default(self, app_env):
        """``next`` 未指定時は従来通り /qualifications/ に戻る (後方互換)。"""
        _seed_one_certificate(app_env["db_path"], cert_id=233)
        r = app_env["client"].post(
            "/qualifications/delete/233", follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

    def test_redirects_to_safe_next_when_provided(self, app_env):
        """``next=/qualifications/staff/<id>`` を渡すとそこに戻る。

        ``next`` の値はホワイトリストで文字列検証するだけなので、
        URL 中の worker_id が seed 値と一致する必要はない。
        """
        _seed_one_certificate(app_env["db_path"], cert_id=234)
        r = app_env["client"].post(
            "/qualifications/delete/234",
            data={"next": "/qualifications/staff/42"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/staff/42"

    def test_rejects_unsafe_next_falls_back_to_index(self, app_env):
        """open-redirect を狙う ``next`` は無視して /qualifications/ に戻す。"""
        _seed_one_certificate(app_env["db_path"], cert_id=235)
        for unsafe in (
            "https://evil.example.com/",     # 絶対 URL
            "//evil.example.com/",            # プロトコル相対
            "/admin/",                        # 別アプリ配下
            "/qualifications/\r\nSet-Cookie: x=1",  # ヘッダ injection
        ):
            r = app_env["client"].post(
                "/qualifications/delete/235",
                data={"next": unsafe},
                follow_redirects=False,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/qualifications/", (
                f"unsafe next should be rejected: {unsafe!r}"
            )
