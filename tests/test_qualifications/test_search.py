"""GET /qualifications/ の検索・絞り込みパラメータの動作テスト。

サマリは絞り込みの影響を受けず常に全件で計算されること、
キーワード/状態/カテゴリの単独および組み合わせフィルタが正しく動くこと
を確認する。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


# ────────────────────────────────────────────
# テストデータ準備ヘルパ
# ────────────────────────────────────────────

def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_certificates(db_path: Path) -> None:
    """状態バリエーションを網羅したテストデータを 6 件投入する。

    事前に conftest が cc_workers (1=山田太郎/A班, 2=佐藤花子/B班, 3=鈴木一郎/A班)
    をシード済み。
    """
    conn = sqlite3.connect(str(db_path))
    # 資格マスタ (3 種類のカテゴリ)
    conn.executescript(
        """
        DELETE FROM q_certificates;
        DELETE FROM q_qualifications;
        INSERT INTO q_qualifications (qual_id, name, category, renewal_required) VALUES
            (10, '玉掛け技能講習',         '技能講習', 0),
            (11, 'フォークリフト運転技能講習', '技能講習', 0),
            (12, '酸欠特別教育',            '特別教育', 0),
            (13, '危険物取扱者乙種第4類',   '免許',     1);
        """
    )

    # bucket 判定は (renewal_required, expires_on) の組合せで決まる:
    #   renewal=0 or expires=NULL  → no_renewal (どんなに先でも no_renewal 扱い)
    #   renewal=1 + expires>+180   → safe
    #   renewal=1 + expires<=+180  → far/soon/urgent (warning カテゴリ)
    #   renewal=1 + expires<=0     → expired
    rows = [
        # (cert_id, worker_id, qual_id, cert_no, issued, expires, renewal)
        (101, 1, 10, "第A001号", "2024-04-01", _today_offset(2000),  1),  # 山田 / 玉掛け / safe
        (102, 1, 11, "第A002号", "2022-10-01", _today_offset(120),   1),  # 山田 / フォーク / far (warning)
        (103, 2, 10, "第A003号", "2020-04-01", _today_offset(-30),   1),  # 佐藤 / 玉掛け / expired
        (104, 2, 12, "第A004号", "2024-01-01", None,                 0),  # 佐藤 / 酸欠 / no_renewal
        (105, 3, 13, "第A005号", "2024-06-01", _today_offset(15),    1),  # 鈴木 / 危険物 / urgent (warning)
        (106, 3, 12, "第A006号", "2024-02-01", _today_offset(2000),  1),  # 鈴木 / 酸欠 / safe
    ]
    for cert_id, w_id, q_id, cno, issued, expires, renewal in rows:
        conn.execute(
            """
            INSERT INTO q_certificates
                (cert_id, worker_id, qual_id, certificate_no, issuer,
                 issued_on, expires_on, renewal_required, status,
                 original_files_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, '○○協会',
                    ?, ?, ?, 'confirmed', '[]', 'admin-id',
                    datetime('now','localtime'), datetime('now','localtime'))
            """,
            (cert_id, w_id, q_id, cno, issued, expires, renewal),
        )
    conn.commit()
    conn.close()


# ────────────────────────────────────────────
# テスト
# ────────────────────────────────────────────

class TestNoFilter:
    def test_returns_all_when_no_query(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/")
        assert r.status_code == 200
        # 6 件全部が表示される
        for cno in ("第A001号", "第A002号", "第A003号", "第A004号", "第A005号", "第A006号"):
            assert cno in r.text
        # 絞り込みインジケータは出ない
        assert "/ 6 件" not in r.text

    def test_summary_shows_full_count(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/")
        # サマリ: total=6, safe=2, warning=2, expired=1, no_renewal=1
        # ざっくり数値を含む小ブロックがあれば OK
        assert ">6<" in r.text  # total


class TestKeywordFilter:
    def test_match_worker_name(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=山田")
        assert r.status_code == 200
        # 山田の 2 件のみ
        assert "第A001号" in r.text
        assert "第A002号" in r.text
        # 他作業員の証は出ない
        assert "第A003号" not in r.text
        assert "第A005号" not in r.text

    def test_match_qualification_name(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=玉掛け")
        assert r.status_code == 200
        # 玉掛けの 2 件 (山田 + 佐藤)
        assert "第A001号" in r.text
        assert "第A003号" in r.text
        # 玉掛け以外は出ない
        assert "第A002号" not in r.text
        assert "第A004号" not in r.text

    def test_match_certificate_no(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=A005")
        assert r.status_code == 200
        assert "第A005号" in r.text
        assert "第A001号" not in r.text

    def test_no_match(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=存在しない名前")
        assert r.status_code == 200
        assert "該当する資格者証がありません" in r.text


class TestStatusFilter:
    def test_safe_only(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?status=safe")
        # safe = 山田/玉掛け + 鈴木/酸欠
        assert "第A001号" in r.text
        assert "第A006号" in r.text
        # 他は除外
        assert "第A002号" not in r.text  # warning
        assert "第A003号" not in r.text  # expired
        assert "第A004号" not in r.text  # no_renewal

    def test_warning_combines_far_soon_urgent(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?status=warning")
        # warning = 山田/フォーク (120日) + 鈴木/危険物 (15日)
        assert "第A002号" in r.text
        assert "第A005号" in r.text
        # 他は除外
        assert "第A001号" not in r.text  # safe
        assert "第A003号" not in r.text  # expired

    def test_expired_only(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?status=expired")
        assert "第A003号" in r.text
        assert "第A001号" not in r.text

    def test_no_renewal(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?status=no_renewal")
        # no_renewal = 期限切れフラグなし & expires_on=NULL → 佐藤/酸欠 のみ
        assert "第A004号" in r.text
        # 他は除外
        assert "第A001号" not in r.text


class TestCategoryFilter:
    def test_filters_by_category(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?category=技能講習")
        # 技能講習 = 玉掛け 2件 + フォーク 1件 = 3件
        assert "第A001号" in r.text
        assert "第A002号" in r.text
        assert "第A003号" in r.text
        # 特別教育・免許は除外
        assert "第A004号" not in r.text
        assert "第A005号" not in r.text


class TestCombinedFilters:
    def test_keyword_and_status(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=山田&status=warning")
        # 山田 × warning = フォーク 1件
        assert "第A002号" in r.text
        assert "第A001号" not in r.text  # safe

    def test_category_and_status(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?category=技能講習&status=expired")
        # 技能講習 × expired = 佐藤/玉掛け
        assert "第A003号" in r.text
        assert "第A001号" not in r.text


class TestSummaryStability:
    """サマリ集計はフィルタの影響を受けず、常に全件で計算される。"""

    def test_summary_shows_full_count_when_filtered(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=山田")
        # 表示される件数は 2 だが、サマリ total は 6 のまま
        assert "2 / 6 件" in r.text
        # サマリカード内に 6 が出ている
        assert ">6<" in r.text


class TestFilterFormState:
    """フィルタ値が再表示時に保持される (form の selected/value)。"""

    def test_keyword_value_preserved(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?q=test_kw")
        assert 'value="test_kw"' in r.text

    def test_status_select_preserved(self, app_env):
        _seed_certificates(app_env["db_path"])
        r = app_env["client"].get("/qualifications/?status=warning")
        # selected="" は HTML として selected と等価
        assert 'value="warning"' in r.text
        # 期待: option の中で warning が selected
        assert "selected" in r.text
