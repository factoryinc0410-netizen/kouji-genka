"""Phase 5: /qualifications/staff/{worker_id} 個票ビューのテスト。

検証内容:
  - 200: q_staff にいる worker は個票表示できる (cert カードが描画される)
  - 404: 存在しない worker_id
  - 404: cc_workers には居るが q_staff に居ない (= 資格管理対象外) worker
  - 200: q_staff.is_active=0 でも個票は出る (履歴閲覧)、ただし無効化バナー
  - 認可: general で 200 通過、no-perm で 403
  - 一覧画面のリンクが /staff/{worker_id} を指している (既存 link 差替え検証)
  - manager にだけ「プレビュー」ボタンが描画される (一般は plain 表示)
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient


# ────────────────────────────────────────────
# 補助
# ────────────────────────────────────────────

def _seed_cert(db_path, *, cert_id: int, worker_id: int, cert_no: str,
               expires: str = "2030-01-01", with_file: bool = False):
    """1 件の confirmed 資格を seed する。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT OR IGNORE INTO q_qualifications
            (qual_id, name, category, renewal_required)
        VALUES (700, '玉掛け技能講習', '技能講習', 0)
        """
    )
    files_json = (
        '["qualifications/test-job-id/sample.pdf"]' if with_file else '[]'
    )
    conn.execute(
        """
        INSERT INTO q_certificates
            (cert_id, worker_id, qual_id, certificate_no, issuer,
             issued_on, expires_on, renewal_required, status,
             original_files_json, created_by, created_at, updated_at)
        VALUES (?, ?, 700, ?, 'org',
                '2024-04-01', ?, 0, 'confirmed',
                ?, 'admin-id', datetime('now','localtime'), datetime('now','localtime'))
        """,
        (cert_id, worker_id, cert_no, expires, files_json),
    )
    conn.commit()
    conn.close()


def _delete_cert(db_path, cert_id: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.commit()
    conn.close()


def _add_cc_worker_only(db_path, *, worker_id: int, name: str):
    """cc_workers のみに足し、q_staff には入れない (= 資格管理対象外) 状態を作る。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (?, ?, '日報専用', 1)",
        (worker_id, name),
    )
    conn.commit()
    conn.close()


def _cleanup_cc_worker(db_path, worker_id: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_staff WHERE worker_id = ?", (worker_id,))
    conn.execute("DELETE FROM cc_workers WHERE worker_id = ?", (worker_id,))
    conn.commit()
    conn.close()


def _set_q_staff_active(db_path, worker_id: int, active: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE q_staff SET is_active = ? WHERE worker_id = ?",
        (active, worker_id),
    )
    conn.commit()
    conn.close()


# ════════════════════════════════════════════
# 基本表示
# ════════════════════════════════════════════

class TestStaffDetailRender:
    def test_200_for_seeded_worker(self, app_env):
        # conftest seed の worker_id=1 (山田太郎) は q_staff に居る
        _seed_cert(app_env["db_path"], cert_id=600, worker_id=1, cert_no="第DET600号")
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # 個票ヘッダ: 氏名と所属
            assert "山田太郎" in r.text
            assert "A班" in r.text
            # cert カードに数値や cert_no が出る
            assert "第DET600号" in r.text
            assert "玉掛け技能講習" in r.text
        finally:
            _delete_cert(app_env["db_path"], 600)

    def test_summary_stat_cards_render(self, app_env):
        """個票上部のサマリ stat-cards (保有数 / 有効 / 期限近接 / 期限切れ) を確認。"""
        # 1 件だけ seed し、bucket=safe (>180 日) になるはず
        _seed_cert(
            app_env["db_path"], cert_id=601, worker_id=1,
            cert_no="第SUM601号", expires="2099-12-31",
        )
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # h3 (氏名) のあとに stat-cards 4 つの表示文言が並ぶ
            assert "保有数" in r.text
            assert "有効" in r.text
            assert "期限近接" in r.text
            assert "期限切れ" in r.text
        finally:
            _delete_cert(app_env["db_path"], 601)

    def test_404_for_nonexistent_worker_id(self, app_env):
        r = app_env["client"].get("/qualifications/staff/99999")
        assert r.status_code == 404

    def test_404_for_cc_worker_without_q_staff_entry(self, app_env):
        """cc_workers にはいるが q_staff に居ない人 → 404。"""
        _add_cc_worker_only(app_env["db_path"], worker_id=850, name="日報専用人")
        try:
            r = app_env["client"].get("/qualifications/staff/850")
            assert r.status_code == 404
        finally:
            _cleanup_cc_worker(app_env["db_path"], 850)

    def test_inactive_q_staff_still_shows_with_banner(self, app_env):
        """q_staff.is_active=0 でも個票は閲覧できる (履歴目的)。無効化バナーが出る。"""
        _seed_cert(app_env["db_path"], cert_id=602, worker_id=2, cert_no="第HIS602号")
        _set_q_staff_active(app_env["db_path"], worker_id=2, active=0)
        try:
            r = app_env["client"].get("/qualifications/staff/2")
            assert r.status_code == 200
            # cert は引き続き見える (履歴)
            assert "第HIS602号" in r.text
            # 無効化バナーの文言
            assert "資格者マスタで無効化済み" in r.text
        finally:
            _set_q_staff_active(app_env["db_path"], worker_id=2, active=1)
            _delete_cert(app_env["db_path"], 602)


# ════════════════════════════════════════════
# bucket → border 強調
# ════════════════════════════════════════════

class TestBucketBorderHighlight:
    def test_expired_card_has_danger_border(self, app_env):
        # 過去の有効期限 = expired bucket
        _seed_cert(
            app_env["db_path"], cert_id=610, worker_id=1,
            cert_no="第EXP610号", expires="2020-01-01",
        )
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # 期限切れカードは border-danger を持つ
            assert "border-danger" in r.text
            # 関連バッジ
            assert "期限切れ" in r.text
        finally:
            _delete_cert(app_env["db_path"], 610)


# ════════════════════════════════════════════
# 一覧画面のリンク差替え検証
# ════════════════════════════════════════════

class TestIndexLinkPointsToStaffDetail:
    def test_index_link_points_to_staff(self, app_env):
        """index 画面の作業員名リンクが /qualifications/staff/{id} を指していること。"""
        # cert を 1 件 seed して index に表示させる
        _seed_cert(
            app_env["db_path"], cert_id=620, worker_id=1,
            cert_no="第LNK620号", expires="2020-01-01",  # expired → default attention に出る
        )
        try:
            r = app_env["client"].get("/qualifications/?status=all")
            assert r.status_code == 200
            # 新リンク
            assert 'href="/qualifications/staff/1"' in r.text
            # 旧 /workers/{id} は (この index 描画では) 既に出ない
            assert 'href="/qualifications/workers/1"' not in r.text
        finally:
            _delete_cert(app_env["db_path"], 620)


# ════════════════════════════════════════════
# 認可 (qualifications.general 必須 / no-perm 403)
# ════════════════════════════════════════════

class TestStaffDetailPermission:
    def test_no_perm_user_gets_403(self, app_env):
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        no_perm = {
            "id": "u-nop", "username": "u", "display_name": "U",
            "is_admin": 0, "permissions": {}, "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: no_perm
        try:
            client = TestClient(app)
            r = client.get("/qualifications/staff/1")
            assert r.status_code == 403
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior

    def test_general_user_passes(self, app_env):
        """qualifications.general だけ持つユーザでも個票は閲覧できる。"""
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        gen = {
            "id": "u-gen", "username": "u", "display_name": "U", "is_admin": 0,
            "permissions": {"qualifications": "general"},
            "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: gen
        try:
            client = TestClient(app)
            r = client.get("/qualifications/staff/1")
            assert r.status_code == 200
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior


# ════════════════════════════════════════════
# プレビューモーダルの表示制御
# ════════════════════════════════════════════

class TestFilePreviewVisibility:
    def test_manager_sees_preview_trigger_button(self, app_env):
        """manager にはプレビューモーダルの trigger ボタンが描画される。"""
        _seed_cert(
            app_env["db_path"], cert_id=630, worker_id=1,
            cert_no="第MGR630号", with_file=True,
        )
        try:
            # admin (= manager 含む) でアクセス (conftest デフォルト)
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # モーダル本体と JS が同梱される
            assert 'id="filePreviewModal"' in r.text
            # トリガーボタンのクラス
            assert "preview-trigger" in r.text
        finally:
            _delete_cert(app_env["db_path"], 630)

    def test_missing_file_renders_as_warning_badge(self, app_env):
        """物理ファイルが staging に無い時、preview-trigger ボタンではなく
        「(欠損)」バッジが描画される (404 detail JSON が画面に出るのを防ぐ)。"""
        # cert は seed するが、staging に対応するファイルは作らない
        _seed_cert(
            app_env["db_path"], cert_id=632, worker_id=1,
            cert_no="第MISS632号", with_file=True,
        )
        # 物理ファイルが無いことを念のため確認
        physical = app_env["staging_root"] / "test-job-id" / "sample.pdf"
        assert not physical.exists()
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # クリック可能な preview-trigger **ボタンクラス** は出ない
            # (JS 側のセレクタ文字列はマッチするので、属性として検出する)
            assert 'class="btn btn-outline-primary preview-trigger"' not in r.text
            # 欠損バッジが出る
            assert '(欠損)' in r.text
            assert 'このファイルはサーバ上に存在しません' in r.text
            # 印刷ボタンは廃止済み
            assert 'print-cert' not in r.text
            # ダウンロードボタンも、ファイル欠損時は描画しない
            assert 'download="sample.pdf"' not in r.text
        finally:
            _delete_cert(app_env["db_path"], 632)

    def test_existing_file_renders_preview_and_download(self, app_env):
        """物理ファイルが staging にある時、プレビュー / ダウンロードボタンが
        正しく描画される (印刷ボタンは廃止)。"""
        _seed_cert(
            app_env["db_path"], cert_id=633, worker_id=1,
            cert_no="第EXIST633号", with_file=True,
        )
        # staging の物理ファイルを作成
        job_dir = app_env["staging_root"] / "test-job-id"
        job_dir.mkdir(parents=True, exist_ok=True)
        physical = job_dir / "sample.pdf"
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # プレビュートリガーが出る
            assert 'class="btn btn-outline-primary preview-trigger"' in r.text
            # ダウンロードリンク (download 属性付き) が出る
            assert 'download="sample.pdf"' in r.text
            # 印刷ボタンはテンプレートから削除済み
            assert 'print-cert' not in r.text
            assert 'bi-printer' not in r.text
            # 欠損バッジは出ない
            assert '(欠損)' not in r.text
            # ファイル提供エンドポイントが 200 で返ること
            r2 = app_env["client"].get(
                "/qualifications/files/test-job-id/sample.pdf"
            )
            assert r2.status_code == 200
        finally:
            _delete_cert(app_env["db_path"], 633)
            physical.unlink(missing_ok=True)

    def test_serve_staged_file_returns_404_when_missing(self, app_env):
        """物理ファイル欠損時は 404 を返す (例外でアプリは死なない)。"""
        r = app_env["client"].get(
            "/qualifications/files/no-such-job/no-such-file.pdf"
        )
        assert r.status_code == 404
        body = r.json()
        assert body.get("detail") == "ファイルが見つかりません"

    def test_serve_staged_file_inline_by_default(self, app_env):
        """既定 (download=false) では Content-Disposition: inline を返す
        (ブラウザ内プレビュー用)。"""
        job_dir = app_env["staging_root"] / "test-job-id"
        job_dir.mkdir(parents=True, exist_ok=True)
        physical = job_dir / "sample.pdf"
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get(
                "/qualifications/files/test-job-id/sample.pdf"
            )
            assert r.status_code == 200
            cd = r.headers.get("content-disposition", "")
            assert cd.startswith("inline"), cd
        finally:
            physical.unlink(missing_ok=True)

    def test_serve_staged_file_attachment_when_download(self, app_env):
        """``?download=true`` 指定時は Content-Disposition: attachment を返す
        (明示的なダウンロード導線用)。"""
        job_dir = app_env["staging_root"] / "test-job-id"
        job_dir.mkdir(parents=True, exist_ok=True)
        physical = job_dir / "sample.pdf"
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get(
                "/qualifications/files/test-job-id/sample.pdf?download=true"
            )
            assert r.status_code == 200
            cd = r.headers.get("content-disposition", "")
            assert cd.startswith("attachment"), cd
        finally:
            physical.unlink(missing_ok=True)

    def test_download_link_uses_download_query(self, app_env):
        """個票テンプレートのダウンロードリンクは ``?download=true`` を付与する
        (preview と分離)。"""
        _seed_cert(
            app_env["db_path"], cert_id=634, worker_id=1,
            cert_no="第DLQ634号", with_file=True,
        )
        job_dir = app_env["staging_root"] / "test-job-id"
        job_dir.mkdir(parents=True, exist_ok=True)
        physical = job_dir / "sample.pdf"
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # ダウンロードボタンに ?download=true が付く
            assert (
                '/qualifications/files/test-job-id/sample.pdf?download=true'
                in r.text
            )
        finally:
            _delete_cert(app_env["db_path"], 634)
            physical.unlink(missing_ok=True)

    def test_preview_trigger_data_url_uses_download_false(self, app_env):
        """モーダルプレビュー用の preview-trigger ボタンの data-url 属性は、
        絶対 URL ではなくサイトルート相対パス + ?download=false を持つ。

        JS は ``getAttribute('data-url')`` をそのまま iframe.src に流し込むので、
        この値が絶対 URL (http://...:8000/) だとリバースプロキシ越しの環境で
        ポートを取りこぼし ERR_CONNECTION_REFUSED の原因になる。"""
        _seed_cert(
            app_env["db_path"], cert_id=635, worker_id=1,
            cert_no="第MOD635号", with_file=True,
        )
        job_dir = app_env["staging_root"] / "test-job-id"
        job_dir.mkdir(parents=True, exist_ok=True)
        physical = job_dir / "sample.pdf"
        physical.write_bytes(b"%PDF-1.4 dummy")
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            assert (
                'data-url="/qualifications/files/test-job-id/sample.pdf?download=false"'
                in r.text
            )
            # 絶対 URL の data-url は出ない
            assert 'data-url="http://' not in r.text
            assert 'data-url="https://' not in r.text
        finally:
            _delete_cert(app_env["db_path"], 635)
            physical.unlink(missing_ok=True)

    def test_general_user_does_not_see_preview_trigger(self, app_env):
        """general にはプレビュー trigger は出ない (証跡ファイル配信が manager 限定のため)。"""
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        _seed_cert(
            app_env["db_path"], cert_id=631, worker_id=1,
            cert_no="第GEN631号", with_file=True,
        )
        gen = {
            "id": "u-gen", "username": "u", "display_name": "U", "is_admin": 0,
            "permissions": {"qualifications": "general"},
            "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: gen
        try:
            client = TestClient(app)
            r = client.get("/qualifications/staff/1")
            assert r.status_code == 200
            # general はモーダル trigger を持たない
            assert "preview-trigger" not in r.text
            assert 'id="filePreviewModal"' not in r.text
            # ファイル名自体は表示される (どんな証跡が紐付いているかは見える)
            assert "sample.pdf" in r.text
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior
            _delete_cert(app_env["db_path"], 631)


# ════════════════════════════════════════════
# 削除ボタン (論理削除)
# ════════════════════════════════════════════

class TestStaffDetailDeleteButton:
    def test_manager_sees_delete_form_per_cert(self, app_env):
        """manager にはカード毎に「削除」ボタン (POST /delete/<cert_id>) が描画される。"""
        _seed_cert(
            app_env["db_path"], cert_id=640, worker_id=1,
            cert_no="第DEL640号", with_file=False,
        )
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            assert 'action="/qualifications/delete/640"' in r.text
            # 確認ダイアログ文言が onsubmit にある
            assert "本当に削除しますか" in r.text
        finally:
            _delete_cert(app_env["db_path"], 640)

    def test_general_user_does_not_see_delete_form(self, app_env):
        """general 権限では削除フォームは描画されない (manager gate)。"""
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        _seed_cert(
            app_env["db_path"], cert_id=641, worker_id=1,
            cert_no="第GEN641号", with_file=False,
        )
        gen = {
            "id": "u-gen", "username": "u", "display_name": "U", "is_admin": 0,
            "permissions": {"qualifications": "general"},
            "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: gen
        try:
            client = TestClient(app)
            r = client.get("/qualifications/staff/1")
            assert r.status_code == 200
            assert 'action="/qualifications/delete/641"' not in r.text
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior
            _delete_cert(app_env["db_path"], 641)

    def test_archived_cert_hides_delete_button(self, app_env):
        """archived な cert は staff_detail にそもそも出ない (=削除フォームも出ない)。
        archived は ``_fetch_worker_certificates`` の WHERE で除外されるため。"""
        import sqlite3
        _seed_cert(
            app_env["db_path"], cert_id=642, worker_id=1,
            cert_no="第ARC642号", with_file=False,
        )
        # 直接 archived に
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("UPDATE q_certificates SET status='archived' WHERE cert_id=?", (642,))
        conn.commit()
        conn.close()
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            # archived は除外されカード自体が出ない
            assert "第ARC642号" not in r.text
            assert 'action="/qualifications/delete/642"' not in r.text
        finally:
            _delete_cert(app_env["db_path"], 642)

    def test_delete_form_carries_next_back_to_staff_detail(self, app_env):
        """削除フォームには ``next=/qualifications/staff/<worker_id>`` の隠し
        フィールドが含まれ、削除完了後も staff_detail にとどまれる。"""
        _seed_cert(
            app_env["db_path"], cert_id=643, worker_id=1,
            cert_no="第NXT643号", with_file=False,
        )
        try:
            r = app_env["client"].get("/qualifications/staff/1")
            assert r.status_code == 200
            assert 'name="next"' in r.text
            assert 'value="/qualifications/staff/1"' in r.text
        finally:
            _delete_cert(app_env["db_path"], 643)
