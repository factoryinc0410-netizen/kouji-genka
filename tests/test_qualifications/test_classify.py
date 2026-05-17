"""classify GET / POST / file 配信の統合テスト。

共通の ``app_env`` fixture は ``conftest.py`` で定義している。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


from skills.qualifications.schema import (
    Candidate,
    FieldConfidences,
    OCRResponse,
)


def _seed_job(
    db_path: Path,
    staging_root: Path,
    job_id: str,
    *,
    status: str = "await_review",
    candidates_fixture: OCRResponse | None = None,
    file_names: list[str] = ("cert.pdf",),
) -> None:
    """ジョブ + classify_json + staging ファイルを 1 セット用意する。"""
    classify_json = (
        candidates_fixture.model_dump_json() if candidates_fixture else None
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO q_upload_jobs (job_id, user_id, file_count, status, classify_json) "
        "VALUES (?, 'admin-id', ?, ?, ?)",
        (job_id, len(file_names), status, classify_json),
    )
    conn.commit()
    conn.close()

    job_dir = staging_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in file_names:
        (job_dir / name).write_bytes(b"%PDF-1.4 dummy\n%%EOF\n")


# ────────────────────────────────────────────
# GET /classify/{job_id}
# ────────────────────────────────────────────

class TestClassifyGet:
    def test_renders_form_with_candidates(self, app_env):
        """OCR 結果 1 件が candidate 形式でフォームに展開される。"""
        job_id = "test_get_001_aaaa"
        fixture = OCRResponse(
            candidates=[
                Candidate(
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎",
                    issued_on="2024-04-01",
                    renewal_required=False,
                    field_confidences=FieldConfidences(
                        qualification_name=0.95, worker_name=0.99,
                    ),
                )
            ],
            overall_confidence=0.97,
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture, file_names=["cert.pdf"])

        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        # フォーム要素が描画されている
        assert "玉掛け技能講習" in r.text
        assert "2024-04-01" in r.text
        assert 'name="n_candidates" value="1"' in r.text
        # 作業員 select に山田太郎が pre-selected されているはず
        assert 'value="1"' in r.text and "山田太郎" in r.text
        # 左ペインに staging のファイル参照
        assert f"/qualifications/files/{job_id}/cert.pdf" in r.text
        # 信頼度バッジ (高信頼度なので bg-success)
        assert "bg-success" in r.text

    def test_redirect_when_status_not_await_review(self, app_env):
        """pending 状態のジョブはアクセス不可 → /pending にリダイレクト。"""
        job_id = "test_get_pending_002"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].get(
            f"/qualifications/classify/{job_id}", follow_redirects=False
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/pending"

    def test_404_when_job_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/classify/does_not_exist")
        assert r.status_code == 404

    def test_renders_when_no_candidates(self, app_env):
        """候補ゼロのジョブもフォームは表示される (警告 alert 付き)。"""
        job_id = "test_get_empty_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=OCRResponse())
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert "候補を抽出できませんでした" in r.text

    def test_preview_url_uses_download_false_query(self, app_env):
        """左カラムの embed / img / 別タブリンクは ?download=false を付け、
        画面表示時に保存ダイアログが出る事故を防ぐ (Content-Disposition: inline 強制)。

        PDF は ``<embed type="application/pdf">``、画像は ``<img>`` で分岐。"""
        job_id = "test_preview_inline_004"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=OCRResponse(),
                  file_names=["preview.pdf"])
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        # ?download=false 付き相対 URL が出ている
        assert (
            f"/qualifications/files/{job_id}/preview.pdf?download=false"
            in r.text
        )
        # PDF は <embed type="application/pdf"> で描画される
        assert (
            f'<embed src="/qualifications/files/{job_id}/preview.pdf?download=false"'
            in r.text
        )
        assert 'type="application/pdf"' in r.text

    def test_preview_uses_img_tag_for_image_file(self, app_env):
        """画像ファイル (.png/.jpg 等) は <img> で描画される。"""
        job_id = "test_preview_img_004b"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=OCRResponse(),
                  file_names=["scan.png"])
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert (
            f'<img src="/qualifications/files/{job_id}/scan.png?download=false"'
            in r.text
        )

    def test_preview_renders_open_in_new_window_fallback_link(self, app_env):
        """埋め込み (<embed>/<img>) が機能しないブラウザ環境向けに、プレビュー枠の
        直上に「📄 別ウィンドウで開く」リンク (target="_blank") が常時描画される。

        ユーザーがプレビューを目視できない場合の出口を確保する役割。"""
        job_id = "test_preview_fallback_004c"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=OCRResponse(),
                  file_names=["sample.pdf"])
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert "📄 別ウィンドウで開く" in r.text
        assert (
            f'href="/qualifications/files/{job_id}/sample.pdf?download=false"'
            in r.text
        )
        assert 'target="_blank"' in r.text

    def test_renders_delete_button_for_manager(self, app_env):
        """classify 画面に「このジョブを削除」フォーム (POST /jobs/<id>/delete)
        が描画され、確認ダイアログを伴う。"""
        job_id = "test_classify_delete_btn_005"
        fixture = OCRResponse(
            candidates=[Candidate(qualification_name="x", worker_name="山田太郎")],
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture)
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert f'action="/qualifications/jobs/{job_id}/delete"' in r.text
        assert "本当に削除しますか" in r.text


# ────────────────────────────────────────────
# POST /jobs/{job_id}/delete — ジョブ物理削除
# ────────────────────────────────────────────

class TestJobDelete:
    def test_deletes_errored_job(self, app_env):
        """status='error' のジョブは削除できる (既存挙動)。"""
        job_id = "test_delete_err_001"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="error", file_names=["e.pdf"])
        r = app_env["client"].post(
            f"/qualifications/jobs/{job_id}/delete", follow_redirects=False,
        )
        assert r.status_code == 303
        # DB から物理削除
        conn = sqlite3.connect(str(app_env["db_path"]))
        row = conn.execute(
            "SELECT 1 FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        assert row is None
        # staging も掃除されている
        assert not (app_env["staging_root"] / job_id).exists()

    def test_deletes_await_review_job(self, app_env):
        """status='await_review' のジョブも削除できる (classify 画面用、新規挙動)。"""
        job_id = "test_delete_await_002"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
            file_names=["sample.pdf"],
        )
        r = app_env["client"].post(
            f"/qualifications/jobs/{job_id}/delete", follow_redirects=False,
        )
        assert r.status_code == 303
        conn = sqlite3.connect(str(app_env["db_path"]))
        row = conn.execute(
            "SELECT 1 FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        assert row is None
        assert not (app_env["staging_root"] / job_id).exists()

    def test_rejects_in_progress_job(self, app_env):
        """進行中 (pending/ocr/classifying) ジョブは削除拒否 (400)。誤削除防止。"""
        job_id = "test_delete_pending_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].post(f"/qualifications/jobs/{job_id}/delete")
        assert r.status_code == 400
        # ジョブはまだ DB に存在する
        conn = sqlite3.connect(str(app_env["db_path"]))
        row = conn.execute(
            "SELECT 1 FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        assert row is not None

    def test_404_when_job_not_found(self, app_env):
        r = app_env["client"].post("/qualifications/jobs/no_such_job/delete")
        assert r.status_code == 404


# ────────────────────────────────────────────
# DELETE /jobs/{job_id} — RESTful AJAX エンドポイント
# (pending.html の JS から叩かれる)
# ────────────────────────────────────────────

class TestJobDeleteApi:
    def test_delete_method_removes_await_review_job(self, app_env):
        """DELETE で await_review ジョブを削除し JSON を返す。"""
        job_id = "test_del_api_await_001"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
            file_names=["x.pdf"],
        )
        r = app_env["client"].delete(f"/qualifications/jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["job_id"] == job_id
        assert body["deleted_status"] == "await_review"
        # DB から消え、staging も消えている
        conn = sqlite3.connect(str(app_env["db_path"]))
        row = conn.execute(
            "SELECT 1 FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        assert row is None
        assert not (app_env["staging_root"] / job_id).exists()

    def test_delete_method_removes_errored_job(self, app_env):
        """DELETE で error ジョブも削除できる (UI 統一のため)。"""
        job_id = "test_del_api_err_002"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="error")
        r = app_env["client"].delete(f"/qualifications/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json()["deleted_status"] == "error"

    def test_delete_method_rejects_in_progress(self, app_env):
        """DELETE でも進行中ジョブは 400。エラー文言で状態が分かる。"""
        job_id = "test_del_api_pending_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].delete(f"/qualifications/jobs/{job_id}")
        assert r.status_code == 400
        assert "pending" in r.json()["detail"]

    def test_delete_method_404_when_not_found(self, app_env):
        r = app_env["client"].delete("/qualifications/jobs/no_such_id")
        assert r.status_code == 404

    def test_await_review_deletion_removes_all_physical_files(self, app_env):
        """await_review ジョブ削除で staging 内の全物理ファイルが消える。

        ユーザー指示の「物理ファイル削除が確実に行われること」を、
        複数ファイル + ディレクトリ存在チェックで明示的に検証する end-to-end テスト。
        """
        job_id = "test_await_e2e_005"
        # 候補ありの await_review ジョブを seed (複数ファイル)
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
            file_names=["page1.pdf", "page2.pdf", "scan.jpg"],
        )

        # 物理ファイルが存在することを事前確認 (seed が確実に書き出している)
        staging_dir: Path = app_env["staging_root"] / job_id
        assert staging_dir.is_dir()
        files_before = sorted(p.name for p in staging_dir.iterdir())
        assert files_before == ["page1.pdf", "page2.pdf", "scan.jpg"]

        # DELETE 実行
        r = app_env["client"].delete(f"/qualifications/jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["deleted_status"] == "await_review"

        # ── 物理ファイル: 全消失 + 親ディレクトリも消失 ──
        assert not staging_dir.exists(), (
            f"staging ディレクトリが残っている: {staging_dir}"
        )
        # DB 行も消えている
        conn = sqlite3.connect(str(app_env["db_path"]))
        try:
            row = conn.execute(
                "SELECT 1 FROM q_upload_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is None, "DB 行が残っている"

        # 二度目の DELETE は 404 (冪等性ではなく明示的「もう無い」)
        r2 = app_env["client"].delete(f"/qualifications/jobs/{job_id}")
        assert r2.status_code == 404


# ────────────────────────────────────────────
# 未確定一覧 (pending.html) の削除ボタン描画
# ────────────────────────────────────────────

class TestPendingPageDeleteButton:
    def test_await_review_card_has_confirm_and_delete(self, app_env):
        """await_review カードに「確認・確定」リンクと JS 削除ボタンが両方出る。"""
        job_id = "test_pending_await_001"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
        )
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        # data-job-id 属性で行を識別 (JS が DOM 削除に使う)
        assert f'data-job-id="{job_id}"' in r.text
        # 確認・確定 リンク
        assert f'href="/qualifications/classify/{job_id}"' in r.text
        # 削除ボタン本体 (JS コードに含まれる文字列ではなく実 button)
        assert 'class="btn btn-sm btn-danger js-delete-job"' in r.text

    def test_error_card_has_only_delete(self, app_env):
        """error カードには削除ボタンが描画される (await_review との共通フッター)。"""
        job_id = "test_pending_err_002"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="error")
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        # 削除ボタン本体が出る
        assert f'data-job-id="{job_id}"' in r.text
        assert 'class="btn btn-sm btn-danger js-delete-job"' in r.text

    def test_in_progress_card_has_no_action_buttons(self, app_env):
        """pending/ocr 等の進行中ジョブには操作ボタンが描画されない (誤削除防止)。

        他テストで残存した await_review/error ジョブと混在しないように、
        テスト直前に q_upload_jobs を一旦クリアして単独描画を検証する。"""
        import sqlite3
        # 他テストで残った job を一旦掃除して進行中ジョブのみ存在する状態に
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute("DELETE FROM q_upload_jobs")
        conn.commit()
        conn.close()

        job_id = "test_pending_progress_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        # 進行中ジョブが描画されている
        assert f'data-job-id="{job_id}"' in r.text
        # 削除ボタンは描画されない (JS コードに含まれる文字列ではなく
        # 実際の <button class="... js-delete-job"> が無いことを確認)
        assert 'class="btn btn-sm btn-danger js-delete-job"' not in r.text
        # 確認・確定 リンクも無い
        assert "/qualifications/classify/" not in r.text

    def test_pending_page_exposes_csrf_meta_tag(self, app_env):
        """pending.html (= base.html 継承) は ``<meta name="csrf-token">`` を
        出力する。JS はここから token を読み出して X-CSRF-Token ヘッダーに
        乗せるため、メタタグ自体が描画されていることが必須。"""
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        assert 'name="csrf-token"' in r.text

    def test_general_user_does_not_see_delete_button(self, app_env):
        """general ユーザーには削除ボタンが描画されない (manager gate)。"""
        from web_app.main import app
        from web_app.core.dependencies import get_current_user
        from fastapi.testclient import TestClient as _TC

        job_id = "test_pending_gen_004"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
        )
        gen = {
            "id": "u-gen", "username": "u", "display_name": "U", "is_admin": 0,
            "permissions": {"qualifications": "general"},
            "role_permissions": {},
        }
        prior = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: gen
        try:
            r = _TC(app).get("/qualifications/pending")
            assert r.status_code == 200
            # general には削除ボタン本体も削除 JS も出ない (両方とも manager gate)
            assert 'class="btn btn-sm btn-danger js-delete-job"' not in r.text
            assert "js-delete-job" not in r.text
        finally:
            if prior is not None:
                app.dependency_overrides[get_current_user] = prior


# ────────────────────────────────────────────
# 削除パス整合 — テンプレート / fetch / ルート の三者が同じ URL を指す
# ────────────────────────────────────────────

class TestDeletePathConsistency:
    """data-job-id (HTML) → JS fetch URL → DELETE ルート の 3 点が同じ
    ``/qualifications/jobs/<job_id>`` を指していることを構造的に保証する。

    過去に「URL が違う」「ID が undefined」を疑った経緯への回帰防止として、
    レンダリング結果と実 HTTP レスポンスを突き合わせる。"""

    def test_data_job_id_attribute_equals_actual_job_id(self, app_env):
        """テンプレートの data-job-id="..." が、サーバ側の job_id 文字列を
        そのまま (URL エンコード不要の形で) 出していることを検証。
        JS は ``card.getAttribute('data-job-id')`` で取り出して fetch URL に
        埋め込むので、ここがズレていると undefined / 空文字 になる。"""
        job_id = "test_consistency_attr_001"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
        )
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        # 完全一致 (空でない / null でない / 末尾にゴミが入らない)
        assert f'data-job-id="{job_id}"' in r.text
        # 短縮版も同様 (data-job-short)
        assert f'data-job-short="{job_id[:8]}"' in r.text

    def test_js_fetch_url_pattern_matches_route(self, app_env):
        """pending.html の JS が組み立てる fetch URL のパターン
        ``/qualifications/jobs/<id>`` がスクリプトに含まれている (DELETE 用)。

        テンプレ側の文字列リテラルが変わって ``/qualifications/job/`` 等の
        typo になった場合に検知する。"""
        # await_review ジョブを 1 件入れて pending.html を描画
        job_id = "test_consistency_url_002"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
        )
        r = app_env["client"].get("/qualifications/pending")
        assert r.status_code == 200
        # JS は `'/qualifications/jobs/' + encodeURIComponent(jobId)` の形で
        # URL を組み立てるので、この prefix リテラルがそのまま含まれていること
        assert "'/qualifications/jobs/'" in r.text
        # method: 'DELETE' も同居
        assert "method: 'DELETE'" in r.text

    def test_delete_against_constructed_url_succeeds(self, app_env):
        """JS が叩くであろう URL (data-job-id をそのまま埋め込んだ URL) に
        対して実際に HTTP DELETE すると 200 で削除される。

        = 「テンプレ → fetch URL → ルート」が end-to-end で噛み合っている。"""
        job_id = "test_consistency_e2e_003"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            status="await_review", candidates_fixture=OCRResponse(),
            file_names=["e2e.pdf"],
        )
        # pending.html の HTML から data-job-id を抽出 → fetch URL を再現
        html = app_env["client"].get("/qualifications/pending").text
        marker = f'data-job-id="{job_id}"'
        assert marker in html

        # JS と同じ URL 組み立てを Python 側でエミュレートする
        from urllib.parse import quote
        url = "/qualifications/jobs/" + quote(job_id, safe="")
        assert url == f"/qualifications/jobs/{job_id}"  # 英数字のみなので等価

        # 実 DELETE
        r = app_env["client"].delete(url)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["job_id"] == job_id
        assert body["deleted_status"] == "await_review"

    def test_delete_route_registered_at_expected_path(self, app_env):
        """FastAPI のルートテーブルを直接覗いて、DELETE メソッドが
        ``/qualifications/jobs/{job_id}`` に登録されていることを確認する。

        ハンドラ側で ``@router.delete("/jobs/...")`` の引数が typo したり、
        prefix が変わったりした場合に静かに 404/405 を返す事故を、
        実 HTTP を介さず early-fail で検知する。"""
        from web_app.main import app

        # APIRoute だけを抽出 (Mount や WebSocket は対象外)
        delete_routes = [
            r for r in app.routes
            if getattr(r, "path", None) == "/qualifications/jobs/{job_id}"
            and "DELETE" in (getattr(r, "methods", None) or set())
        ]
        # 期待: ちょうど 1 件登録されている
        assert len(delete_routes) == 1, (
            "DELETE /qualifications/jobs/{job_id} が登録されていない "
            f"(検出 {len(delete_routes)} 件)"
        )

        # 同じパスに対して DELETE 以外のメソッドが未登録であることも確認
        # (POST は ``/jobs/{job_id}/delete`` の別パスに分離されているため、
        # ここで他メソッドが混入していたら設計とズレている)
        same_path_methods = set()
        for r in app.routes:
            if getattr(r, "path", None) == "/qualifications/jobs/{job_id}":
                same_path_methods |= (getattr(r, "methods", None) or set())
        assert same_path_methods == {"DELETE"}, (
            f"想定外のメソッドが登録されている: {same_path_methods}"
        )


# ────────────────────────────────────────────
# GET /files/{job_id}/{filename} — preview 配信
# ────────────────────────────────────────────

class TestFilesServe:
    def test_serves_existing_pdf(self, app_env):
        job_id = "test_file_001"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["sample.pdf"])
        r = app_env["client"].get(f"/qualifications/files/{job_id}/sample.pdf")
        assert r.status_code == 200
        assert b"%PDF" in r.content
        assert r.headers["content-type"].startswith("application/pdf")

    def test_404_for_nonexistent_file(self, app_env):
        job_id = "test_file_002"
        _seed_job(app_env["db_path"], app_env["staging_root"], job_id)
        r = app_env["client"].get(f"/qualifications/files/{job_id}/missing.pdf")
        assert r.status_code == 404

    def test_path_traversal_rejected(self, app_env):
        """``..`` を使った base_dir 外への到達を試みると 404。"""
        job_id = "test_file_003"
        _seed_job(app_env["db_path"], app_env["staging_root"], job_id)
        # FastAPI は URL-encoded `..` を path に許容するのでここで試行
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/..%2F..%2Fapp.db"
        )
        # safe_file_response が 404 で弾く (200 では絶対にいけない)
        assert r.status_code == 404

    def test_inline_response_has_correct_content_type_for_pdf(self, app_env):
        """``?download=false`` (既定) で PDF は Content-Type: application/pdf
        を必ず返す (ブラウザにインライン表示させるための強制)。"""
        job_id = "test_ctype_pdf_004"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["sample.pdf"])
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/sample.pdf?download=false"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].startswith("inline")

    def test_inline_response_content_type_for_jpeg(self, app_env):
        """JPEG は Content-Type: image/jpeg を必ず返す (拡張子大小区別なし)。"""
        job_id = "test_ctype_jpg_005"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["photo.JPG"])
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/photo.JPG?download=false"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_preview_response_full_header_contract(self, app_env):
        """プレビュー用 URL (?download=false) は 200 OK + 正しい Content-Type +
        Content-Disposition: inline + ファイル本体を返すことの **総合検証**。

        ユーザー報告の「プレビューが表示されない」の主要原因 (誤った Content-Type
        / attachment disposition / 404) をすべて 1 ケースで確認する。"""
        job_id = "test_preview_contract_006"
        # 実 PDF マジックナンバー入りのバイト列でプレビュー本体を seed
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["plan.pdf"])
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/plan.pdf?download=false"
        )
        # ── 1. ステータス ──
        assert r.status_code == 200, r.text
        # ── 2. Content-Type は拡張子から確実に判定される ──
        assert r.headers["content-type"] == "application/pdf"
        # ── 3. Content-Disposition は inline (attachment ではない) ──
        cd = r.headers["content-disposition"]
        assert cd.startswith("inline"), cd
        assert "attachment" not in cd
        # ── 4. ボディは PDF マジックナンバーで始まる ──
        assert r.content.startswith(b"%PDF"), r.content[:16]
        # ── 5. content-length が 0 でない (= 空配信ではない) ──
        assert int(r.headers.get("content-length", "0")) > 0

    def test_inline_image_response_full_header_contract(self, app_env):
        """画像プレビューも同様に inline + image/* の Content-Type を返す。"""
        job_id = "test_preview_img_contract_007"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["scan.png"])
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/scan.png?download=false"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["content-disposition"].startswith("inline")
        assert int(r.headers.get("content-length", "0")) > 0

    def test_japanese_filename_content_disposition_uses_rfc5987(self, app_env):
        """日本語ファイル名は **二形式** (filename= ASCII フォールバック +
        filename*=UTF-8'' エンコード版) で Content-Disposition に乗る。

        片方しか出さないとブラウザが「壊れたヘッダー」と判定して PDF プラグインに
        渡さない事故が起きる。RFC 5987 / RFC 6266 準拠を強制する。"""
        from urllib.parse import quote
        job_id = "test_jp_filename_008"
        japanese_name = "資格者証_山田太郎.pdf"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=[japanese_name])
        # URL エンコードして fetch (TestClient は path を自動 quote しないので明示)
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/{quote(japanese_name)}?download=false"
        )
        assert r.status_code == 200
        cd = r.headers["content-disposition"]
        # 1) inline disposition
        assert cd.startswith("inline"), cd
        # 2) ASCII フォールバック (filename="...") を含む
        assert 'filename="' in cd, cd
        # 3) RFC 5987 形式 (filename*=UTF-8''<percent-encoded>) を含む
        assert "filename*=UTF-8''" in cd, cd
        # 4) パーセントエンコードされた日本語が乗っている
        encoded = quote(japanese_name, safe="")
        assert encoded in cd, cd

    def test_x_frame_options_allows_same_origin_embedding(self, app_env):
        """ファイル配信レスポンスは ``X-Frame-Options: SAMEORIGIN`` を返す。

        既定 middleware は全レスポンスに ``X-Frame-Options: DENY`` を付けるが、
        これだと自社ドメインの ``<embed>`` / ``<iframe>`` でも PDF が
        「フレーム埋め込み拒否」でレンダリングされない。
        ファイル配信のみ SAMEORIGIN に上書きしてプレビューを成立させる。"""
        job_id = "test_xfo_sameorigin_009"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, file_names=["plan.pdf"])
        r = app_env["client"].get(
            f"/qualifications/files/{job_id}/plan.pdf?download=false"
        )
        assert r.status_code == 200
        # 大小区別なしに比較 (ヘッダ名/値とも RFC 上はケース不問)
        xfo = r.headers.get("x-frame-options", "").upper()
        assert xfo == "SAMEORIGIN", f"X-Frame-Options={xfo!r}"


# ────────────────────────────────────────────
# GET /jobs/{job_id}/status — ポーリング API
# ────────────────────────────────────────────

class TestJobStatusApi:
    def test_returns_status_and_no_next_url_while_pending(self, app_env):
        """OCR 進行中は next_url=null で返る (= ポーリングを継続させる)。"""
        job_id = "test_status_pending_001"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        r = app_env["client"].get(f"/qualifications/jobs/{job_id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["job_id"] == job_id
        assert body["status"] == "pending"
        assert body["next_url"] is None
        assert body["error_message"] is None

    def test_returns_next_url_when_await_review(self, app_env):
        """OCR 完了 (await_review) で classify への next_url が返る。"""
        job_id = "test_status_ready_002"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="await_review",
                  candidates_fixture=OCRResponse())
        r = app_env["client"].get(f"/qualifications/jobs/{job_id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "await_review"
        assert body["next_url"] == f"/qualifications/classify/{job_id}"

    def test_surfaces_error_message(self, app_env):
        """status='error' のジョブは error_message を返す (画面側でアラート表示用)。"""
        import sqlite3
        job_id = "test_status_error_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="pending")
        # error_message を直接 SET (pipeline 経由を避けて検証だけ)
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.execute(
            "UPDATE q_upload_jobs SET status='error', error_message=? "
            "WHERE job_id=?",
            ("OCR エラー (RuntimeError)", job_id),
        )
        conn.commit()
        conn.close()

        r = app_env["client"].get(f"/qualifications/jobs/{job_id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "error"
        assert "OCR エラー" in body["error_message"]
        assert body["next_url"] is None

    def test_404_when_job_not_found(self, app_env):
        r = app_env["client"].get("/qualifications/jobs/no_such_job/status")
        assert r.status_code == 404


# ────────────────────────────────────────────
# POST /classify/{job_id} — 確定登録
# ────────────────────────────────────────────

class TestClassifySubmit:
    def test_creates_certificate_and_marks_done(self, app_env):
        """送信成功で q_certificates 行が作成、ジョブが done に。"""
        job_id = "test_post_001_aaaaaaaaaaaaaaaaa"
        fixture = OCRResponse(
            candidates=[
                Candidate(
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎",
                    issued_on="2024-04-01",
                    renewal_required=False,
                )
            ],
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture)

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "玉掛け技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",   # 山田太郎
                "certificate_no_0": "第12345号",
                "issuer_0": "○○技能講習センター",
                "issued_on_0": "2024-04-01",
                "expires_on_0": "",
                "renewal_required_0": "",  # チェックなし → 更新不要
                # 1 ファイルジョブでも新仕様では source_files_{i} 必須
                "source_files_0": "cert.pdf",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/qualifications/"

        # DB 検証
        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row

        # ジョブが done
        job = dict(conn.execute(
            "SELECT * FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone())
        assert job["status"] == "done"

        # q_certificates が 1 件
        certs = [dict(r) for r in conn.execute(
            "SELECT * FROM q_certificates WHERE created_by='admin-id' "
            "ORDER BY cert_id DESC LIMIT 1"
        ).fetchall()]
        assert len(certs) == 1
        cert = certs[0]
        assert cert["worker_id"] == 1
        assert cert["certificate_no"] == "第12345号"
        assert cert["issued_on"] == "2024-04-01"
        assert cert["renewal_required"] == 0  # 更新不要
        assert cert["status"] == "confirmed"
        assert "玉掛け技能講習.pdf" in cert["original_files_json"] or \
               "cert.pdf" in cert["original_files_json"]

        # q_qualifications も自動追加される
        q = conn.execute(
            "SELECT * FROM q_qualifications WHERE name='玉掛け技能講習'"
        ).fetchone()
        assert q is not None
        conn.close()

    def test_rejects_when_worker_not_selected(self, app_env):
        job_id = "test_post_no_worker_002"
        fixture = OCRResponse(
            candidates=[Candidate(qualification_name="フォークリフト運転技能講習")],
        )
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, candidates_fixture=fixture)

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "フォーク",
                "worker_id_0": "",   # 未選択
                "issued_on_0": "2022-10-15",
            },
        )
        assert r.status_code == 400
        assert "作業員" in r.json()["detail"]

        # DB は変更されていない
        conn = sqlite3.connect(str(app_env["db_path"]))
        job_row = conn.execute(
            "SELECT status FROM q_upload_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        assert job_row[0] == "await_review"
        conn.close()

    def test_rejects_when_already_done(self, app_env):
        """二重登録防止: 既に done のジョブには再 POST できない。"""
        job_id = "test_post_done_003"
        _seed_job(app_env["db_path"], app_env["staging_root"],
                  job_id, status="done")
        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={"n_candidates": "1", "qualification_name_0": "x",
                  "worker_id_0": "1", "issued_on_0": "2024-01-01"},
        )
        assert r.status_code == 400
        assert "確定済み" in r.json()["detail"]


# ════════════════════════════════════════════
# 1 ジョブ複数ファイル × 複数候補での source_files 紐付け
# ════════════════════════════════════════════

class TestClassifySubmitSourceFilesMapping:
    """1 ジョブで 2 ファイル / 2 候補をアップして個別に確定したとき、
    各 cert の ``original_files_json`` が **自分の source_files のみ** を含み、
    staff_detail で他 cert のファイルが混入しないことを担保する。

    過去のバグ: classify_submit が ``list_staged_files`` の全件を全 cert に
    一律で書き込んでいた → 個人ページで他資格の原本まで表示される事故。
    """

    @staticmethod
    def _two_candidate_fixture() -> OCRResponse:
        return OCRResponse(
            candidates=[
                Candidate(
                    qualification_name="玉掛け技能講習",
                    worker_name="山田太郎", issued_on="2024-04-01",
                ),
                Candidate(
                    qualification_name="フォークリフト運転技能講習",
                    worker_name="山田太郎", issued_on="2024-05-10",
                ),
            ],
        )

    def test_each_cert_records_only_its_own_source_file(self, app_env):
        """source_files_0=tamagake.pdf, source_files_1=forklift.pdf を別々に
        指定すると、各 cert の original_files_json は **そのファイル 1 つだけ** になる。"""
        job_id = "src_map_two_files_001"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=self._two_candidate_fixture(),
            file_names=["tamagake.pdf", "forklift.pdf"],
        )

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "2",
                # 候補 0 (玉掛け)
                "qualification_name_0": "玉掛け技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",
                "issued_on_0": "2024-04-01",
                "source_files_0": "tamagake.pdf",
                # 候補 1 (フォークリフト)
                "qualification_name_1": "フォークリフト運転技能講習",
                "category_1": "技能講習",
                "worker_id_1": "1",
                "issued_on_1": "2024-05-10",
                "source_files_1": "forklift.pdf",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT certificate_no, original_files_json, qual_id, "
            "(SELECT name FROM q_qualifications WHERE qual_id=q_certificates.qual_id) "
            "AS qual_name "
            "FROM q_certificates WHERE created_by='admin-id' "
            "ORDER BY cert_id DESC LIMIT 2"
        ).fetchall()]
        conn.close()

        # cert ごとに 1 ファイルだけ、かつ自分のもの
        files_by_qual = {
            r["qual_name"]: json.loads(r["original_files_json"])
            for r in rows
        }
        assert files_by_qual["玉掛け技能講習"] == [
            f"qualifications/{job_id}/tamagake.pdf"
        ], "玉掛けの cert にフォークリフトのファイルが混入"
        assert files_by_qual["フォークリフト運転技能講習"] == [
            f"qualifications/{job_id}/forklift.pdf"
        ], "フォークリフトの cert に玉掛けのファイルが混入"

    def test_one_cert_with_multiple_source_files_works(self, app_env):
        """表裏セットなど 1 cert に複数ファイル指定は許容する。"""
        job_id = "src_map_multi_per_cert_002"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(
                candidates=[Candidate(
                    qualification_name="アーク溶接特別教育",
                    worker_name="山田太郎", issued_on="2024-06-01",
                )],
            ),
            file_names=["front.pdf", "back.pdf", "irrelevant.pdf"],
        )

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            # multi-value は dict 値にリストを入れる (urlencoded で
            # ``source_files_0=front.pdf&source_files_0=back.pdf`` になる)
            data={
                "n_candidates": "1",
                "qualification_name_0": "アーク溶接特別教育",
                "category_0": "特別教育",
                "worker_id_0": "1",
                "issued_on_0": "2024-06-01",
                "source_files_0": ["front.pdf", "back.pdf"],
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row
        cert = dict(conn.execute(
            "SELECT original_files_json FROM q_certificates "
            "WHERE created_by='admin-id' ORDER BY cert_id DESC LIMIT 1"
        ).fetchone())
        conn.close()

        files = json.loads(cert["original_files_json"])
        assert sorted(files) == sorted([
            f"qualifications/{job_id}/front.pdf",
            f"qualifications/{job_id}/back.pdf",
        ])
        assert f"qualifications/{job_id}/irrelevant.pdf" not in files

    def test_invalid_source_file_name_is_rejected_with_400(self, app_env):
        """staging に無いファイル名 (パストラバーサル / タンパリング) は無視
        され、有効選択 0 件として 400 で拒否される。
        過去は「fallback で staging 全件」に倒れていたが、複数 cert 時に
        全ファイル紐付けバグの温床になったため廃止した。"""
        job_id = "src_map_invalid_name_003"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(
                candidates=[Candidate(
                    qualification_name="高所作業車運転技能講習",
                    worker_name="山田太郎", issued_on="2024-07-01",
                )],
            ),
            file_names=["valid.pdf"],
        )

        # fixture が scope="module" で DB を共有するため、テスト前後の差分で検証
        conn = sqlite3.connect(str(app_env["db_path"]))
        n_before = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        conn.close()

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "高所作業車運転技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",
                "issued_on_0": "2024-07-01",
                # ../etc/passwd 等のトラバーサル試行 + 存在しない名前のみ
                "source_files_0": ["../etc/passwd", "ghost.pdf"],
            },
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text
        assert "証跡ファイル" in r.json()["detail"]

        # cert は 1 件も増えていない (= job も await_review のまま)
        conn = sqlite3.connect(str(app_env["db_path"]))
        n_after = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        job_status = conn.execute(
            "SELECT status FROM q_upload_jobs WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        conn.close()
        assert n_after == n_before
        assert job_status == "await_review"

    def test_no_source_files_is_rejected_with_400(self, app_env):
        """``source_files_{i}`` をフォームに含めない旧クライアントは
        400 で拒否される。複数 cert 時に「全ファイル紐付け」事故を
        起こす後方互換 fallback を廃止したため、新フォーム必須。"""
        job_id = "src_map_no_source_files_004"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(
                candidates=[Candidate(
                    qualification_name="酸素欠乏特別教育",
                    worker_name="山田太郎", issued_on="2024-08-01",
                )],
            ),
            file_names=["a.pdf", "b.pdf"],
        )

        conn = sqlite3.connect(str(app_env["db_path"]))
        n_before = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        conn.close()

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "酸素欠乏特別教育",
                "category_0": "特別教育",
                "worker_id_0": "1",
                "issued_on_0": "2024-08-01",
                # source_files_0 を一切含めない
            },
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text
        assert "証跡ファイル" in r.json()["detail"]

        # cert は 1 件も増えていない
        conn = sqlite3.connect(str(app_env["db_path"]))
        n_after = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        conn.close()
        assert n_after == n_before

    def test_partial_invalid_with_one_valid_still_succeeds(self, app_env):
        """無効名と有効名が混在しても、有効な分だけで cert が作られる
        (= 部分的トラバーサル試行は黙って捨てる)。"""
        job_id = "src_map_partial_valid_005"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(
                candidates=[Candidate(
                    qualification_name="ガス溶接技能講習",
                    worker_name="山田太郎", issued_on="2024-09-01",
                )],
            ),
            file_names=["real.pdf"],
        )
        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "1",
                "qualification_name_0": "ガス溶接技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",
                "issued_on_0": "2024-09-01",
                "source_files_0": ["../etc/passwd", "real.pdf", "ghost.pdf"],
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

        conn = sqlite3.connect(str(app_env["db_path"]))
        conn.row_factory = sqlite3.Row
        cert = dict(conn.execute(
            "SELECT original_files_json FROM q_certificates "
            "WHERE created_by='admin-id' ORDER BY cert_id DESC LIMIT 1"
        ).fetchone())
        conn.close()
        files = json.loads(cert["original_files_json"])
        assert files == [f"qualifications/{job_id}/real.pdf"]
        assert all("etc/passwd" not in f for f in files)
        assert all("ghost.pdf" not in f for f in files)

    def test_multi_cert_no_source_files_rejected_before_any_insert(self, app_env):
        """複数候補のうち先頭で 400 が出たら、後続 cert も含めて一切
        DB に書き込まれない (= 元バグの再発防止: 全 cert に全ファイルが
        書き込まれる事故が起きないことを end-to-end で担保)。"""
        job_id = "src_map_multi_reject_006"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=self._two_candidate_fixture(),
            file_names=["tamagake.pdf", "forklift.pdf"],
        )
        conn = sqlite3.connect(str(app_env["db_path"]))
        n_before = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        conn.close()

        r = app_env["client"].post(
            f"/qualifications/classify/{job_id}",
            data={
                "n_candidates": "2",
                "qualification_name_0": "玉掛け技能講習",
                "category_0": "技能講習",
                "worker_id_0": "1",
                "issued_on_0": "2024-04-01",
                # source_files_0 を欠落
                "qualification_name_1": "フォークリフト運転技能講習",
                "category_1": "技能講習",
                "worker_id_1": "1",
                "issued_on_1": "2024-05-10",
                "source_files_1": "forklift.pdf",
            },
            follow_redirects=False,
        )
        assert r.status_code == 400

        conn = sqlite3.connect(str(app_env["db_path"]))
        n_after = conn.execute("SELECT COUNT(*) FROM q_certificates").fetchone()[0]
        job_status = conn.execute(
            "SELECT status FROM q_upload_jobs WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        conn.close()
        assert n_after == n_before, "途中で 400 が出たのに cert が部分的に作成された"
        assert job_status == "await_review", "ジョブが done に倒れている"

    def test_classify_form_renders_source_files_checkbox_for_multi_file_jobs(
        self, app_env,
    ):
        """ファイルが 2 つ以上なら classify GET 画面に
        ``name="source_files_<i>"`` チェックボックスが描画される。"""
        job_id = "src_form_render_005"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=self._two_candidate_fixture(),
            file_names=["one.pdf", "two.pdf"],
        )
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        # 各候補ブロックに自分用の name="source_files_<i>" が出る
        assert 'name="source_files_0"' in r.text
        assert 'name="source_files_1"' in r.text
        # ファイル名が選択肢として描画される
        assert 'value="one.pdf"' in r.text
        assert 'value="two.pdf"' in r.text

    def test_classify_form_uses_hidden_input_for_single_file_jobs(self, app_env):
        """ファイルが 1 つなら hidden で固定して UI を煩わせない。"""
        job_id = "src_form_render_006"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(candidates=[Candidate(
                qualification_name="X 講習", issued_on="2024-01-01",
            )]),
            file_names=["only.pdf"],
        )
        r = app_env["client"].get(f"/qualifications/classify/{job_id}")
        assert r.status_code == 200
        assert (
            '<input type="hidden" name="source_files_0"' in r.text
            and 'value="only.pdf"' in r.text
        )
        # checkbox は描画しない (1 ファイルなので)
        assert 'class="form-check-input source-file-checkbox"' not in r.text

    def test_auto_pair_initial_check_when_counts_match(self, app_env):
        """候補数 == ファイル数 のとき、候補 i のチェックボックスは
        i 番目のファイル (= staging を name 順で並べたときの index i) のみ
        初期 ``checked`` になる。最頻ケース「1 ファイル = 1 cert」を
        手間ゼロで正しく紐付ける UX 補助。"""
        import re

        job_id = "src_auto_pair_match_007"
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=self._two_candidate_fixture(),
            # staging は name 順で並ぶので [a.pdf, b.pdf] になる
            file_names=["a.pdf", "b.pdf"],
        )
        html = app_env["client"].get(
            f"/qualifications/classify/{job_id}"
        ).text

        # 候補 0 ↔ a.pdf, 候補 1 ↔ b.pdf が checked、それ以外は未 check。
        # checkbox 単位で id="src-{i}-{j}" を引いて checked 属性の有無を確認する。
        def _is_checked(i: int, j: int) -> bool:
            m = re.search(
                r'<input[^>]*\bid="src-' + str(i) + '-' + str(j) + r'"[^>]*>',
                html,
            )
            assert m is not None, f"checkbox id=src-{i}-{j} が見つからない"
            return "checked" in m.group(0)

        assert _is_checked(0, 0) is True   # 候補 0 ↔ a.pdf
        assert _is_checked(0, 1) is False  # 候補 0 に b.pdf は付かない
        assert _is_checked(1, 0) is False  # 候補 1 に a.pdf は付かない
        assert _is_checked(1, 1) is True   # 候補 1 ↔ b.pdf

    def test_auto_pair_disabled_when_counts_mismatch(self, app_env):
        """候補数 ≠ ファイル数 のときは初期 check しない (= ユーザーに
        明示選択させる)。表裏セットや 1 ファイルに複数資格などの曖昧
        ケースで誤った自動紐付けを避けるため。"""
        import re

        job_id = "src_auto_pair_mismatch_008"
        # 1 候補 + 2 ファイル (表裏セット想定)
        _seed_job(
            app_env["db_path"], app_env["staging_root"], job_id,
            candidates_fixture=OCRResponse(candidates=[Candidate(
                qualification_name="アーク溶接特別教育",
                worker_name="山田太郎", issued_on="2024-06-01",
            )]),
            file_names=["front.pdf", "back.pdf"],
        )
        html = app_env["client"].get(
            f"/qualifications/classify/{job_id}"
        ).text

        # checkbox は描画されているが、どれも checked が付かない
        matches = re.findall(
            r'<input[^>]*\bid="src-0-\d+"[^>]*>', html,
        )
        assert len(matches) == 2  # ファイル 2 つ分の checkbox
        for m in matches:
            assert "checked" not in m, (
                f"候補数≠ファイル数なのに初期 checked が付いている: {m}"
            )
