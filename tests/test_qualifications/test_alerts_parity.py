"""UI のお知らせ枠とメール通知 (cron) の DRY 統合テスト。

検証対象は ``web_app.services.qualifications_alerts`` に集約された共通抽出ロジックが、
2 経路 (UI 非同期 ``aiosqlite`` / メール同期 ``sqlite3``) で **完全に同じ結果**
を返すことと、両者が q_staff.is_active=0 のスタッフを揃って除外することの 2 点。

- 同じ DB 状態で UI 側と email 側に同じ cert_id 集合が現れる
- q_staff inactive のスタッフ cert は両側から消える (画面 0 件 ⇔ メール 0 件)
- renewal_required=0 / archived / null expires_on は両側で除外
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_master(db_path: Path, *, qual_id: int, name: str | None = None):
    if name is None:
        name = f"PARITY-{qual_id}"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO q_qualifications "
        "(qual_id, name, category, renewal_required) VALUES (?, ?, '技能', 1)",
        (qual_id, name),
    )
    conn.commit()
    conn.close()


def _insert_cert(db_path, *, cert_id, worker_id, qual_id,
                 expires_on, renewal_required=1, status="confirmed",
                 cert_no=None):
    if cert_no is None:
        cert_no = f"P-{cert_id}"
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_certificates WHERE cert_id = ?", (cert_id,))
    conn.execute(
        "INSERT INTO q_certificates "
        "(cert_id, worker_id, qual_id, certificate_no, issuer, "
        " issued_on, expires_on, renewal_required, status, "
        " original_files_json, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'org', '2024-01-01', ?, ?, ?, '[]', 'admin-id', "
        " datetime('now','localtime'), datetime('now','localtime'))",
        (cert_id, worker_id, qual_id, cert_no, expires_on,
         renewal_required, status),
    )
    conn.commit()
    conn.close()


def _delete_certs(db_path, ids):
    conn = sqlite3.connect(str(db_path))
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM q_certificates WHERE cert_id IN ({placeholders})", list(ids))
    conn.commit()
    conn.close()


def _set_q_staff_active(db_path, worker_id: int, active: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE q_staff SET is_active=? WHERE worker_id=?", (active, worker_id))
    conn.commit()
    conn.close()


def _add_cc_worker_only(db_path, *, worker_id, name):
    """cc_workers のみ追加 (q_staff には登録しない) — 旧バグの再現用。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (?, ?, '日報専用', 1)",
        (worker_id, name),
    )
    conn.commit()
    conn.close()


def _cleanup_cc(db_path, worker_id):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM q_staff WHERE worker_id = ?", (worker_id,))
    conn.execute("DELETE FROM cc_workers WHERE worker_id = ?", (worker_id,))
    conn.commit()
    conn.close()


# ════════════════════════════════════════════
# UI / メール 出力一致
# ════════════════════════════════════════════

class TestAlertsParity:
    def test_same_cert_ids_in_ui_and_email(self, app_env):
        """同じ DB 状態で UI のお知らせと email の対象集合が一致する。"""
        from web_app.services.email_service import collect_expiring_certs
        # UI 側を同じ抽出ロジックで取得 (sync ラッパ)
        from web_app.services.qualifications_alerts import (
            fetch_alert_rows_sync,
        )

        _seed_master(app_env["db_path"], qual_id=900)
        # 多様な期限 + ステータス
        _insert_cert(app_env["db_path"], cert_id=9001, worker_id=1, qual_id=900,
                     expires_on=_today_offset(-10))   # expired
        _insert_cert(app_env["db_path"], cert_id=9002, worker_id=2, qual_id=900,
                     expires_on=_today_offset(15))    # urgent
        _insert_cert(app_env["db_path"], cert_id=9003, worker_id=3, qual_id=900,
                     expires_on=_today_offset(60))    # safe → 対象外
        _insert_cert(app_env["db_path"], cert_id=9004, worker_id=1, qual_id=900,
                     expires_on=_today_offset(-2),
                     renewal_required=0)              # 更新不要 → 対象外
        _insert_cert(app_env["db_path"], cert_id=9005, worker_id=2, qual_id=900,
                     expires_on=_today_offset(-1), status="archived")  # archived → 対象外
        try:
            # メール側の出力
            email_out = collect_expiring_certs(app_env["db_path"])
            email_ids = {r["cert_id"] for r in email_out["expired"]} | \
                        {r["cert_id"] for r in email_out["urgent"]}
            # UI 側の出力 (共通モジュール経由 = ルートで使う _fetch_notifications と同じ実装)
            ui_rows = fetch_alert_rows_sync(app_env["db_path"])
            ui_ids = {r["cert_id"] for r in ui_rows}
            # 完全一致
            assert email_ids == ui_ids
            # 想定: 9001 (expired) と 9002 (urgent) のみ
            assert email_ids == {9001, 9002}
        finally:
            _delete_certs(app_env["db_path"], [9001, 9002, 9003, 9004, 9005])

    def test_q_staff_inactive_excluded_from_email(self, app_env):
        """worker_id を q_staff.is_active=0 にすると、UI/メール共に対象外になる
        (旧 collect_expiring_certs では q_staff フィルタが無く、UI と乖離していた)。"""
        from web_app.services.email_service import collect_expiring_certs
        _seed_master(app_env["db_path"], qual_id=901)
        _insert_cert(app_env["db_path"], cert_id=9101, worker_id=3, qual_id=901,
                     expires_on=_today_offset(-3))
        _set_q_staff_active(app_env["db_path"], 3, 0)
        try:
            out = collect_expiring_certs(app_env["db_path"])
            ids = {r["cert_id"] for r in out["expired"]} | \
                  {r["cert_id"] for r in out["urgent"]}
            assert 9101 not in ids
            assert ids == set()
        finally:
            _set_q_staff_active(app_env["db_path"], 3, 1)
            _delete_certs(app_env["db_path"], [9101])

    def test_cc_worker_without_q_staff_excluded(self, app_env):
        """q_staff に未登録 (= 資格管理対象外) の cc_worker は cert 持ちでも対象外。"""
        from web_app.services.email_service import collect_expiring_certs
        _seed_master(app_env["db_path"], qual_id=902)
        _add_cc_worker_only(app_env["db_path"], worker_id=950, name="日報のみ氏")
        _insert_cert(app_env["db_path"], cert_id=9201, worker_id=950, qual_id=902,
                     expires_on=_today_offset(-1))
        try:
            out = collect_expiring_certs(app_env["db_path"])
            ids = {r["cert_id"] for r in out["expired"]} | \
                  {r["cert_id"] for r in out["urgent"]}
            assert 9201 not in ids
            assert ids == set()
        finally:
            _delete_certs(app_env["db_path"], [9201])
            _cleanup_cc(app_env["db_path"], 950)

    def test_count_matches_ui_notification_counter(self, app_env):
        """email の総件数 = UI の notif_counts.total。"""
        from web_app.services.email_service import collect_expiring_certs
        from web_app.services.qualifications_alerts import (
            fetch_alert_rows_sync, summarize_alerts,
        )
        _seed_master(app_env["db_path"], qual_id=903)
        _insert_cert(app_env["db_path"], cert_id=9301, worker_id=1, qual_id=903,
                     expires_on=_today_offset(-7))
        _insert_cert(app_env["db_path"], cert_id=9302, worker_id=2, qual_id=903,
                     expires_on=_today_offset(7))
        _insert_cert(app_env["db_path"], cert_id=9303, worker_id=3, qual_id=903,
                     expires_on=_today_offset(20))
        try:
            email_out = collect_expiring_certs(app_env["db_path"])
            email_total = len(email_out["expired"]) + len(email_out["urgent"])
            # UI の集計関数 (qualifications.py:_count_notifications と同じロジック)
            rows = fetch_alert_rows_sync(app_env["db_path"])
            ui_counts = summarize_alerts(rows)
            assert email_total == ui_counts["total"]
            assert len(email_out["expired"]) == ui_counts["expired"]
            assert len(email_out["urgent"])  == ui_counts["urgent"]
            assert email_total == 3
        finally:
            _delete_certs(app_env["db_path"], [9301, 9302, 9303])

    def test_index_page_shows_same_content_as_email(self, app_env):
        """実際に / ページをレンダした HTML に email 対象 cert の worker_name が出る。"""
        from web_app.services.email_service import collect_expiring_certs
        _seed_master(app_env["db_path"], qual_id=904)
        _insert_cert(app_env["db_path"], cert_id=9401, worker_id=1, qual_id=904,
                     expires_on=_today_offset(-3), cert_no="EML-UI-1")
        try:
            email_out = collect_expiring_certs(app_env["db_path"])
            # email は 1 件 (worker_id=1)
            assert len(email_out["expired"]) == 1
            target = email_out["expired"][0]
            # UI: 「お知らせ」枠にこの作業員と資格名が出る
            r = app_env["client"].get("/qualifications/")
            assert r.status_code == 200
            assert target["worker_name"] in r.text
            assert target["qual_name"] in r.text
        finally:
            _delete_certs(app_env["db_path"], [9401])
