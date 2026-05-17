"""GET/POST /qualifications/manual-add (Phase 3.3) のテスト。

OCR をスキップして 1 件だけ即時登録するフロー:
  - 必須: worker_id (>0), qualification_name, issued_on
  - 即座に status='confirmed' で q_certificates にINSERT
  - 任意ファイルは <UPLOAD_DIR>/qualifications/manual_<uuid>/ に保存
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path


# ────────────────────────────────────────────
# データ準備ヘルパ
# ────────────────────────────────────────────

def _seed_qualification_master(
    db_path: Path,
    *,
    qual_id: int = 70,
    name: str = "玉掛け技能講習",
    category: str = "技能講習",
    default_valid_years: int | None = None,
) -> None:
    """テスト用の資格マスタを 1 件投入する (冪等)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT OR REPLACE INTO q_qualifications
            (qual_id, name, category, renewal_required, default_valid_years)
        VALUES (?, ?, ?, 1, ?)
        """,
        (qual_id, name, category, default_valid_years),
    )
    conn.commit()
    conn.close()


def _get_cert_by_certno(db_path: Path, cert_no: str) -> dict | None:
    """certificate_no で 1 件取得 (テスト識別子としてユニークにする)。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_certificates WHERE certificate_no = ?", (cert_no,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_qual_by_name(db_path: Path, name: str) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM q_qualifications WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ────────────────────────────────────────────
# GET /manual-add — フォーム表示
# ────────────────────────────────────────────

class TestManualAddGet:
    def test_renders_form(self, app_env):
        r = app_env["client"].get("/qualifications/manual-add")
        assert r.status_code == 200
        # フォームの主要要素
        assert 'name="worker_id"' in r.text
        assert 'name="qualification_name"' in r.text
        assert 'name="issued_on"' in r.text
        assert 'name="expires_on"' in r.text
        assert 'name="files"' in r.text
        # multipart 必須
        assert 'enctype="multipart/form-data"' in r.text

    def test_renders_workers_dropdown(self, app_env):
        """conftest でシード済みの作業員 (山田太郎/佐藤花子/鈴木一郎) が表示される。"""
        r = app_env["client"].get("/qualifications/manual-add")
        assert "山田太郎" in r.text
        assert "佐藤花子" in r.text
        assert "鈴木一郎" in r.text

    def test_renders_qualification_master_datalist(self, app_env):
        """資格マスタが datalist に展開される。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=80, name="フォークリフト運転技能講習",
            category="技能講習", default_valid_years=5,
        )
        r = app_env["client"].get("/qualifications/manual-add")
        assert "フォークリフト運転技能講習" in r.text
        # data-valid-years 属性も埋め込まれる (JS の自動補完で使う)
        assert 'data-valid-years="5"' in r.text


# ────────────────────────────────────────────
# POST /manual-add — バリデーション
# ────────────────────────────────────────────

class TestManualAddValidation:
    def test_missing_worker(self, app_env):
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "0",
                "qualification_name": "玉掛け技能講習",
                "issued_on": "2024-04-01",
            },
        )
        assert r.status_code == 400

    def test_invalid_worker_value(self, app_env):
        """非数値の worker_id も 400 (フォーム保護)。"""
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "abc",
                "qualification_name": "玉掛け技能講習",
                "issued_on": "2024-04-01",
            },
        )
        assert r.status_code == 400

    def test_missing_qualification_name(self, app_env):
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "",
                "issued_on": "2024-04-01",
            },
        )
        assert r.status_code == 400

    def test_missing_issued_on(self, app_env):
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "玉掛け技能講習",
                "issued_on": "",
            },
        )
        assert r.status_code == 400


# ────────────────────────────────────────────
# POST /manual-add — 正常登録
# ────────────────────────────────────────────

class TestManualAddSuccess:
    def test_minimal_required_fields(self, app_env):
        """必須項目のみで status='confirmed' として登録される。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=90, name="酸欠特別教育", category="特別教育",
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "酸欠特別教育",
                "category": "特別教育",
                "certificate_no": "MA-MIN-001",
                "issued_on": "2024-04-01",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

        cert = _get_cert_by_certno(app_env["db_path"], "MA-MIN-001")
        assert cert is not None
        assert cert["status"] == "confirmed"
        assert cert["worker_id"] == 1
        assert cert["issued_on"] == "2024-04-01"
        assert cert["expires_on"] is None
        # 任意項目は省略 → DB 上 NULL
        assert cert["issuer"] is None
        # original_files_json は空配列で保存 (ファイル添付なし)
        assert json.loads(cert["original_files_json"]) == []
        # OCR 関連は NULL
        assert cert["ocr_model"] is None
        assert cert["ocr_confidence"] is None
        # 登録者
        assert cert["created_by"] == "admin-id"

    def test_with_optional_fields(self, app_env):
        """任意項目 (有効期限・備考・更新フラグ) も格納される。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=91, name="フォークリフト運転技能講習",
            category="技能講習", default_valid_years=5,
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "2",
                "qualification_name": "フォークリフト運転技能講習",
                "category": "技能講習",
                "certificate_no": "MA-OPT-001",
                "issuer": "○○技能講習センター",
                "issued_on": "2024-06-01",
                "expires_on": "2029-06-01",
                "renewal_required": "1",
                "notes": "現場 X 専属",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert_by_certno(app_env["db_path"], "MA-OPT-001")
        assert cert["worker_id"] == 2
        assert cert["expires_on"] == "2029-06-01"
        assert cert["renewal_required"] == 1
        assert cert["issuer"] == "○○技能講習センター"
        assert cert["notes"] == "現場 X 専属"
        assert cert["status"] == "confirmed"

    def test_renewal_unchecked_means_no_renewal(self, app_env):
        """renewal_required を未送信なら 0 で格納される (チェックを外した状態)。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=92, name="その他資格テスト1",
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "3",
                "qualification_name": "その他資格テスト1",
                "certificate_no": "MA-NORE-001",
                "issued_on": "2024-04-01",
                # renewal_required は送らない
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        cert = _get_cert_by_certno(app_env["db_path"], "MA-NORE-001")
        assert cert["renewal_required"] == 0

    def test_uses_existing_master_when_name_matches(self, app_env):
        """既存マスタ名と一致するなら新しい qual 行は作らない。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=93, name="高所作業車運転技能講習",
            category="技能講習",
        )
        # 登録前のマスタ件数
        conn = sqlite3.connect(str(app_env["db_path"]))
        before = conn.execute(
            "SELECT COUNT(*) FROM q_qualifications WHERE name = '高所作業車運転技能講習'"
        ).fetchone()[0]
        conn.close()
        assert before == 1

        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "高所作業車運転技能講習",
                "certificate_no": "MA-EXIST-001",
                "issued_on": "2024-04-01",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        # マスタ件数は増えていない
        conn = sqlite3.connect(str(app_env["db_path"]))
        after = conn.execute(
            "SELECT COUNT(*) FROM q_qualifications WHERE name = '高所作業車運転技能講習'"
        ).fetchone()[0]
        conn.close()
        assert after == 1

        cert = _get_cert_by_certno(app_env["db_path"], "MA-EXIST-001")
        assert cert["qual_id"] == 93

    def test_creates_new_master_when_name_unknown(self, app_env):
        """マスタにない資格名を入力すると q_qualifications に行が増える。"""
        new_name = "新資格テスト_手動追加_001"
        # 事前条件: マスタに無い
        assert _get_qual_by_name(app_env["db_path"], new_name) is None

        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": new_name,
                "category": "その他",
                "certificate_no": "MA-NEW-001",
                "issued_on": "2024-04-01",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        # マスタが追加された
        master = _get_qual_by_name(app_env["db_path"], new_name)
        assert master is not None
        assert master["category"] == "その他"

        # cert がそれを指している
        cert = _get_cert_by_certno(app_env["db_path"], "MA-NEW-001")
        assert cert["qual_id"] == master["qual_id"]

    def test_appears_in_index_immediately(self, app_env):
        """登録直後に /qualifications/ 一覧に表示される (status=confirmed なので)。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=94, name="索引表示テスト資格",
        )
        app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "索引表示テスト資格",
                "certificate_no": "MA-INDEX-001",
                "issued_on": "2024-04-01",
            },
        )
        r = app_env["client"].get("/qualifications/?status=all")
        assert r.status_code == 200
        assert "MA-INDEX-001" in r.text
        assert "索引表示テスト資格" in r.text


# ────────────────────────────────────────────
# POST /manual-add — ファイル添付
# ────────────────────────────────────────────

class TestManualAddFiles:
    def test_succeeds_without_files(self, app_env):
        """ファイル添付なしでも 303 で正常登録される。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=100, name="ファイルなしテスト資格",
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "ファイルなしテスト資格",
                "certificate_no": "MA-NOFILE-001",
                "issued_on": "2024-04-01",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        cert = _get_cert_by_certno(app_env["db_path"], "MA-NOFILE-001")
        assert cert is not None
        assert json.loads(cert["original_files_json"]) == []

    def test_saves_attached_image(self, app_env):
        """添付された画像は staging に保存され、original_files_json に記録される。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=101, name="画像添付テスト資格",
        )
        # 1x1 pixel PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "画像添付テスト資格",
                "certificate_no": "MA-IMG-001",
                "issued_on": "2024-04-01",
            },
            files={"files": ("sample.png", io.BytesIO(png_bytes), "image/png")},
            follow_redirects=False,
        )
        assert r.status_code == 303

        cert = _get_cert_by_certno(app_env["db_path"], "MA-IMG-001")
        files = json.loads(cert["original_files_json"])
        assert len(files) == 1
        # パスは "qualifications/manual_<uuid>/sample.png" の形
        assert files[0].startswith("qualifications/manual_")
        assert files[0].endswith("/sample.png")

        # 実ファイルが staging に存在する
        rel = files[0]  # "qualifications/manual_xxx/sample.png"
        # staging_root は <UPLOAD_DIR>/qualifications。rel は qualifications/ から始まるので
        # parent (UPLOAD_DIR) と結合する
        on_disk = app_env["staging_root"].parent / rel
        assert on_disk.exists()
        assert on_disk.read_bytes() == png_bytes

    def test_rejects_disallowed_extension(self, app_env):
        """非対応拡張子は 400 で拒否され、DB レコードも作られない。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=102, name="拡張子拒否テスト",
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "拡張子拒否テスト",
                "certificate_no": "MA-BADEXT-001",
                "issued_on": "2024-04-01",
            },
            files={"files": ("malware.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
        )
        assert r.status_code == 400
        # DB にレコードが作られていない
        assert _get_cert_by_certno(app_env["db_path"], "MA-BADEXT-001") is None

    def test_rejects_empty_file(self, app_env):
        """空ファイルは 400。"""
        _seed_qualification_master(
            app_env["db_path"], qual_id=103, name="空ファイル拒否テスト",
        )
        r = app_env["client"].post(
            "/qualifications/manual-add",
            data={
                "worker_id": "1",
                "qualification_name": "空ファイル拒否テスト",
                "certificate_no": "MA-EMPTY-001",
                "issued_on": "2024-04-01",
            },
            files={"files": ("empty.png", io.BytesIO(b""), "image/png")},
        )
        assert r.status_code == 400
        assert _get_cert_by_certno(app_env["db_path"], "MA-EMPTY-001") is None
