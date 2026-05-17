"""資格者証の期限アラート — UI / メール バッチ共通の抽出ロジック (DRY)。

設計方針:
  - 「対応必要 cert」を判定する **単一の SQL** をここに置く。
  - 同じ条件で UI (アプリ内非同期 ``aiosqlite``) と cron バッチ
    (同期 ``sqlite3``) の両方から呼び出せる thin な API ラッパを 2 種提供。
  - 「今日」「閾値」をすべて Python 側で計算してバインド変数に渡すことで、
    ``julianday('now','localtime')`` と ``date.today()`` の暗黙ズレ
    (秒精度小数日 vs 真夜中) を排除する。

包含条件 (UI のお知らせとメール通知で完全に同一):
  - ``q_certificates.status = 'confirmed'`` (archived / draft 除外)
  - ``q_certificates.renewal_required = 1`` (更新不要は除外)
  - ``q_certificates.expires_on IS NOT NULL`` (期限不明は除外)
  - ``q_certificates.expires_on <= today + threshold_days``
  - 作業員が **active な q_staff メンバ** (= 資格者マスタ) である
    ``q_staff.is_active = 1``。q_staff に未登録の cc_workers は対象外
    (= UI からも見えない人にメールも送らない)

公開 API:
  - ``ALERT_THRESHOLD_DAYS``               : 既定 30 日
  - ``fetch_alert_rows_async(db, ...)``    : aiosqlite 用 (UI ルート向け)
  - ``fetch_alert_rows_sync(db_path, ...)``: sqlite3 用 (cron バッチ向け)
  - ``summarize_alerts(rows)``             : ``{expired, urgent, total}``
  - ``group_alert_rows(rows)``             : ``{"expired": [...], "urgent": [...]}``
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

ALERT_THRESHOLD_DAYS: int = 30


# ────────────────────────────────────────────
# 単一のクエリ定義 — 改変は両経路の挙動を同時に変える
# ────────────────────────────────────────────

# バインド順序: (today_iso, today_iso, threshold_iso)
#   - (1) days_remaining 計算用
#   - (2) bucket 判定 (expires_on <= today → 'expired')
#   - (3) WHERE 句のしきい値カット
_ALERT_SQL: str = """
    SELECT
        c.cert_id, c.certificate_no, c.issued_on, c.expires_on,
        c.renewal_required, c.notes,
        cc.worker_id, cc.worker_name, cc.group_name, cc.role,
        ql.qual_id, ql.name AS qual_name, ql.category AS qual_category,
        CAST(julianday(c.expires_on) - julianday(?) AS INTEGER) AS days_remaining,
        CASE
            WHEN c.expires_on <= ? THEN 'expired'
            ELSE 'urgent'
        END AS bucket
      FROM  q_certificates c
      JOIN  cc_workers       cc ON cc.worker_id = c.worker_id
      JOIN  q_qualifications ql ON ql.qual_id   = c.qual_id
      JOIN  q_staff          qs ON qs.worker_id = cc.worker_id AND qs.is_active = 1
     WHERE  c.status            = 'confirmed'
       AND  c.renewal_required  = 1
       AND  c.expires_on IS NOT NULL
       AND  c.expires_on        <= ?
     ORDER BY c.expires_on, cc.worker_name
"""


def _alert_params(
    *, today: date | None = None, threshold_days: int = ALERT_THRESHOLD_DAYS,
) -> tuple[str, str, str]:
    """バインド変数を組み立てる。両経路でこの関数経由で渡す = ズレ無し。"""
    today = today or date.today()
    today_iso = today.isoformat()
    threshold_iso = (today + timedelta(days=threshold_days)).isoformat()
    return today_iso, today_iso, threshold_iso


# ────────────────────────────────────────────
# async API (UI 用 — aiosqlite)
# ────────────────────────────────────────────

async def fetch_alert_rows_async(
    db,
    *,
    limit: int | None = None,
    today: date | None = None,
    threshold_days: int = ALERT_THRESHOLD_DAYS,
) -> list[dict]:
    """UI 経路向け。``db`` は ``await get_db()`` で得る aiosqlite Connection。

    ``limit`` を渡すと SQL 末尾に ``LIMIT ?`` を付加する。お知らせ枠の
    『先頭 20 件』表示用。
    """
    params = list(_alert_params(today=today, threshold_days=threshold_days))
    sql = _ALERT_SQL
    if limit is not None:
        sql = sql + f"\n     LIMIT {int(limit)}"
    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────
# sync API (cron バッチ用 — sqlite3)
# ────────────────────────────────────────────

def fetch_alert_rows_sync(
    db_path: str | Path,
    *,
    today: date | None = None,
    threshold_days: int = ALERT_THRESHOLD_DAYS,
) -> list[dict]:
    """cron バッチ向け。同期 sqlite3 を内部で開閉する。"""
    params = _alert_params(today=today, threshold_days=threshold_days)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(_ALERT_SQL, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────
# 集計 / グルーピング ヘルパ (両経路の共通変換)
# ────────────────────────────────────────────

def summarize_alerts(rows: list[dict]) -> dict[str, int]:
    """``[{bucket: 'expired'|'urgent', ...}, ...]`` から件数を集計。"""
    expired = sum(1 for r in rows if r.get("bucket") == "expired")
    urgent  = sum(1 for r in rows if r.get("bucket") == "urgent")
    return {"expired": expired, "urgent": urgent, "total": expired + urgent}


def group_alert_rows(rows: list[dict]) -> dict[str, list[dict]]:
    """email_service の旧 API 互換: ``{"expired": [...], "urgent": [...]}``。"""
    expired: list[dict] = []
    urgent: list[dict] = []
    for r in rows:
        if r.get("bucket") == "expired":
            expired.append(r)
        elif r.get("bucket") == "urgent":
            urgent.append(r)
    return {"expired": expired, "urgent": urgent}
