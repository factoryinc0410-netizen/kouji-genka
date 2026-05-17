"""資格者証管理ルーター

このルーターは段階的に育てる:

Phase 1:
  - GET  /qualifications/                  → 確定済み一覧 + 期限サマリ
  - GET  /qualifications/pending           → 未確定 (draft) 一覧
  - GET  /qualifications/upload            → アップロード画面 (admin)
  - POST /qualifications/upload            → ファイル保存 + ジョブ作成 (admin)

Phase 2:
  - GET/POST /qualifications/jobs/<job_id>/classify   (admin)
  - GET/POST /qualifications/<cert_id>/review         (admin)
  - POST     /qualifications/<cert_id>/delete         (admin)

権限モデル (RBAC):
  - 閲覧系 (一覧 / 検索 / CSV / PDF / 作業員別ビュー / pending):
        ``RequirePermission("qualifications", "general")``
  - 書込系 (upload / classify / edit / delete / restore / manual-add /
        staging ファイル配信): ``RequirePermission("qualifications", "manager")``
  - ``is_admin=True`` のユーザーは ``has_permission`` のショートカットで素通し。

期限ステータス分類 (180/60/30 日):
  - >180 日           → 'safe'
  - 180〜61 日       → 'far'
  - 60〜31 日        → 'soon'
  - 30〜1 日         → 'urgent'
  - ≤0 日            → 'expired'
  - renewal_required=0 か expires_on=NULL → 'no_renewal'
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
# request.form() が返すのは Starlette の UploadFile (FastAPI の UploadFile は
# そのサブクラスだが、form() は親クラスを返すので isinstance チェックには
# Starlette 側を使う必要がある)。
from starlette.datastructures import UploadFile as StarletteUploadFile

import csv
import io
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse

from web_app.core.config import (
    QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
    QUALIFICATIONS_MAX_FILE_MB,
)
from web_app.core.database import get_db
from web_app.core.dependencies import RequirePermission
from web_app.core.safe_files import safe_file_response
from web_app.core.templates import templates as _templates
from web_app.services.job_queue import job_queue
from skills.qualifications.schema import OCRResponse
from skills.qualifications.storage import (
    QUALIFICATIONS_STAGING_ROOT,
    is_allowed_extension,
    list_staged_files,
    sanitize_filename,
    staging_dir_for,
)

logger = logging.getLogger("web_app.qualifications")

router = APIRouter(prefix="/qualifications", tags=["qualifications"])

# 機能名 (user_permissions.feature_name と一致) — テンプレート active_tab/ skill_key
# 双方の意味で使う単一定数。
_SKILL_KEY = "qualifications"

# RBAC: Depends 用シングルトン (モジュールレベルで 1 度だけ生成)。
# 閲覧系 (一覧 / 検索 / CSV / PDF / 作業員別ビュー / pending) は general、
# 書込系 (upload / classify 確定 / edit / delete / restore / manual-add /
# staging ファイル配信) は manager。
# is_admin=True のユーザーは has_permission のショートカットで素通し。
_RequireGeneral = RequirePermission(_SKILL_KEY, "general")
_RequireManager = RequirePermission(_SKILL_KEY, "manager")


# ────────────────────────────────────────────
# 期限ステータス判定
# ────────────────────────────────────────────

def _expiry_bucket(expires_on: str | None, renewal_required: int) -> str:
    """有効期限から表示用ステータスを決める。

    expires_on は ISO 8601 (YYYY-MM-DD) もしくは NULL。
    renewal_required=0 のものは更新不要扱い。
    """
    if not renewal_required or not expires_on:
        return "no_renewal"
    try:
        exp = date.fromisoformat(expires_on)
    except ValueError:
        # 不正フォーマットは「不明」扱い。Phase 2 で validator が直すので一旦 expired 寄せ。
        return "expired"
    days = (exp - date.today()).days
    if days <= 0:
        return "expired"
    if days <= 30:
        return "urgent"
    if days <= 60:
        return "soon"
    if days <= 180:
        return "far"
    return "safe"


# ────────────────────────────────────────────
# データ取得ヘルパ
# ────────────────────────────────────────────

async def _fetch_confirmed(
    db,
    *,
    q: str | None = None,
    category: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """確定済み資格者証を作業員・資格マスタと JOIN して取得する。

    SQL レベルで適用するフィルタ:
      - ``q``                : worker_name / qual_name / certificate_no を LIKE で部分一致
      - ``category``         : q_qualifications.category の完全一致
      - ``include_archived`` : true なら archived も含める (一覧の「アーカイブ含む」表示用)

    state フィルタ (bucket ベース) は呼び出し側で ``_apply_status_filter`` を使って
    Python 側でかける (有効期限の比較が今日依存のため SQL より素直)。
    """
    if include_archived:
        where = ["c.status IN ('confirmed','archived')"]
    else:
        where = ["c.status = 'confirmed'"]
    params: list = []

    q_clean = (q or "").strip()
    if q_clean:
        where.append(
            "(w.worker_name LIKE ? OR ql.name LIKE ? OR c.certificate_no LIKE ?)"
        )
        like = f"%{q_clean}%"
        params.extend([like, like, like])

    cat_clean = (category or "").strip()
    if cat_clean:
        where.append("ql.category = ?")
        params.append(cat_clean)

    sql = f"""
        SELECT  c.cert_id, c.certificate_no, c.issuer,
                c.issued_on, c.expires_on, c.renewal_required,
                c.notes, c.status, c.ocr_confidence,
                w.worker_id, w.worker_name, w.group_name,
                ql.qual_id, ql.name AS qual_name, ql.category AS qual_category
          FROM  q_certificates c
          JOIN  cc_workers       w  ON  w.worker_id = c.worker_id
          JOIN  q_qualifications ql ON ql.qual_id   = c.qual_id
         WHERE  {' AND '.join(where)}
         ORDER BY c.expires_on IS NULL, c.expires_on, w.worker_name
    """
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["bucket"] = _expiry_bucket(r["expires_on"], r["renewal_required"])
    return rows


# 状態フィルタ key → bucket 集合のマッピング。
# 'all' は「フィルタなし」を明示するキー。
# 'attention' は index 画面の **デフォルト表示** で使う「期限切迫(30日以内) + 期限切れ」の合成バケット。
# テンプレート側の <select> 値および index 画面のサマリカードと対応。
_STATUS_FILTER_BUCKETS: dict[str, frozenset[str]] = {
    "safe":       frozenset({"safe"}),
    "warning":    frozenset({"far", "soon", "urgent"}),
    "expired":    frozenset({"expired"}),
    "no_renewal": frozenset({"no_renewal"}),
    "attention":  frozenset({"urgent", "expired"}),
}

# デフォルト表示で使うフィルタ key (1ヶ月以内 + 期限切れ)。
# 利用者の最頻ニーズに合わせ、何もクエリが無い時はこのフィルタが効いた状態で開く。
_DEFAULT_STATUS_FILTER = "attention"


def _apply_status_filter(rows: list[dict], status: str | None) -> list[dict]:
    """残日数バケット (>180/180-61/...) によるフィルタを Python 側でかける。"""
    if not status or status == "all":
        return rows
    allowed = _STATUS_FILTER_BUCKETS.get(status)
    if allowed is None:
        return rows  # 未知の値は無視
    return [r for r in rows if r["bucket"] in allowed]


async def _fetch_categories(db) -> list[str]:
    """フィルタドロップダウン用に q_qualifications.category の DISTINCT 一覧を返す。"""
    cur = await db.execute(
        """
        SELECT DISTINCT category
          FROM q_qualifications
         WHERE is_active = 1 AND category != ''
         ORDER BY category
        """
    )
    return [row[0] for row in await cur.fetchall() if row[0]]


# bucket key → 日本語ラベル。CSV/集計表示で共通利用する。
_BUCKET_LABELS: dict[str, str] = {
    "safe":       "有効",
    "far":        "期限近接 (180日以内)",
    "soon":       "期限近接 (60日以内)",
    "urgent":     "期限近接 (30日以内)",
    "expired":    "期限切れ",
    "no_renewal": "更新不要",
}


async def count_alerts(db) -> dict[str, int]:
    """確定済み資格者証のうち、期限近接 (warning) と期限切れ (expired) の件数を返す。

    portal などの**外部画面**から件数バッジ表示用に呼び出される公開ヘルパ。
    archived / draft は除外し、status='confirmed' のみカウントする。
    """
    cur = await db.execute(
        """
        SELECT expires_on, renewal_required
          FROM q_certificates
         WHERE status = 'confirmed'
        """
    )
    warning = expired = 0
    for row in await cur.fetchall():
        bucket = _expiry_bucket(row[0], row[1])
        if bucket == "expired":
            expired += 1
        elif bucket in ("far", "soon", "urgent"):
            warning += 1
    return {"warning": warning, "expired": expired}


async def _fetch_upload_jobs(db) -> list[dict]:
    """進行中・確認待ちの ``q_upload_jobs`` を取得し、OCR 結果をパースして返す。

    戻り値の各 dict は q_upload_jobs の生カラムに加え:
      - ``short_id``           : job_id の先頭 8 文字
      - ``candidates``         : list[dict]  classify_json から展開した候補
      - ``overall_confidence`` : float       OCRResponse.overall_confidence
    を持つ。
    """
    cur = await db.execute(
        """
        SELECT  job_id, user_id, file_count, status,
                classify_json, error_message,
                created_at, updated_at
          FROM  q_upload_jobs
         WHERE  status != 'done'
         ORDER BY created_at DESC
        """
    )
    jobs = [dict(r) for r in await cur.fetchall()]

    for j in jobs:
        j["short_id"] = j["job_id"][:8]
        j["candidates"] = []
        j["overall_confidence"] = 0.0

        if not j["classify_json"]:
            continue
        try:
            response = OCRResponse.model_validate_json(j["classify_json"])
        except Exception:
            # 壊れた JSON を出さないため defensive: ログに残しても画面は破綻させない
            logger.exception(
                "classify_json のパースに失敗 job=%s", j["short_id"],
            )
            continue

        j["overall_confidence"] = response.overall_confidence
        for c in response.candidates:
            # フィールド単位の信頼度を平均して 1 候補の自信度にまとめる
            fcs = [
                v for v in c.field_confidences.model_dump().values() if v is not None
            ]
            avg = sum(fcs) / len(fcs) if fcs else 0.0
            j["candidates"].append({
                "qualification_name": c.qualification_name,
                "category":           c.category,
                "worker_name":        c.worker_name,
                "issued_on":          c.issued_on,
                "expires_on":         c.expires_on,
                "renewal_required":   c.renewal_required,
                "confidence":         avg,
            })
    return jobs


def _group_certs_by_qualification(rows: list[dict]) -> list[dict]:
    """フラットな cert リストを資格ごとにグルーピングしてドリルダウン用に変形。

    仕様:
      - cert ごとに 1 行 (renewal で複数枚ある場合も全て表示する → 履歴
        として失われない / CSV/PDF の件数とも一致)。
      - 各グループは ``cert_count`` (cert 数) と ``worker_count`` (distinct
        worker 数) を持つ。表示は「N 件 (M 名)」形式。
      - cert は「対応すべき順」: expired → urgent → soon → far → safe →
        no_renewal、同 bucket 内では expires_on 昇順 → worker_name 昇順。
      - グループは cert_count 降順 → qual_name 昇順 (関心の集中する資格を上に)。

    入力 ``rows`` は ``_fetch_confirmed`` の戻り値を想定 (各要素に
    ``qual_id, qual_name, qual_category, worker_id, worker_name, group_name,
    cert_id, certificate_no, expires_on, status, bucket`` を持つ)。
    """
    bucket_order = {
        "expired": 0, "urgent": 1, "soon": 2, "far": 3, "safe": 4, "no_renewal": 5,
    }
    groups: dict[int, dict] = {}
    for c in rows:
        qid = c["qual_id"]
        g = groups.setdefault(qid, {
            "qual_id": qid,
            "qual_name": c["qual_name"],
            "qual_category": c.get("qual_category"),
            "certs": [],
            "_worker_ids": set(),
        })
        g["certs"].append(c)
        g["_worker_ids"].add(c["worker_id"])

    out: list[dict] = []
    for g in groups.values():
        g["certs"].sort(key=lambda c: (
            bucket_order.get(c.get("bucket"), 99),
            c.get("expires_on") or "9999-12-31",
            c.get("worker_name") or "",
        ))
        g["cert_count"]    = len(g["certs"])
        g["worker_count"]  = len(g["_worker_ids"])
        g["expired_count"] = sum(1 for c in g["certs"] if c.get("bucket") == "expired")
        g["warning_count"] = sum(
            1 for c in g["certs"]
            if c.get("bucket") in ("urgent", "soon", "far")
        )
        del g["_worker_ids"]
        out.append(g)
    out.sort(key=lambda g: (-g["cert_count"], g["qual_name"]))
    return out


def _summarize(rows: list[dict]) -> dict[str, int]:
    """期限サマリ用の件数集計（confirmed のみカウント）。"""
    summary = {"total": len(rows), "safe": 0, "warning": 0, "expired": 0, "no_renewal": 0}
    for r in rows:
        b = r["bucket"]
        if b == "safe":
            summary["safe"] += 1
        elif b in ("far", "soon", "urgent"):
            summary["warning"] += 1
        elif b == "expired":
            summary["expired"] += 1
        elif b == "no_renewal":
            summary["no_renewal"] += 1
    return summary


# ────────────────────────────────────────────
# Phase 1: お知らせ (要対応アクション) — クエリ B
# ────────────────────────────────────────────
#
# ポータル上部の「お知らせ」枠に出す対象データ。expired と urgent (≤30日)
# のみを抽出し、days_remaining 順に並べる。bucket 計算は SQL の julianday
# で一括処理 (アプリ側ループを省く)。
#
# q_staff.is_active=1 のスタッフだけに絞る (= 無効化済みスタッフの cert は
# お知らせにも出さない)。renewal_required=0 (更新不要) もアラート対象外。

async def _fetch_notifications(db, *, limit: int = 20) -> list[dict]:
    """お知らせ枠用: 期限切れ + 30日以内の cert を作業員/資格と一緒に返す。

    抽出ロジックは ``qualifications_alerts`` モジュールに集約。メール通知
    (``email_service.collect_expiring_certs``) と完全に同一の条件
    (q_staff.is_active=1 を含む) で問い合わせる。
    """
    from web_app.services.qualifications_alerts import fetch_alert_rows_async
    return await fetch_alert_rows_async(db, limit=limit)


async def _count_notifications(db) -> dict[str, int]:
    """お知らせ枠ヘッダ用の集計 (expired / urgent ≤30日 の件数)。

    全数を返す (UI 上「N 件中先頭 20 件を表示中」と出したい時に使える)。
    抽出ロジックは fetch_alert_rows_async と共通 → メールと完全一致。
    """
    from web_app.services.qualifications_alerts import (
        fetch_alert_rows_async, summarize_alerts,
    )
    rows = await fetch_alert_rows_async(db)  # limit なし = 全件
    return summarize_alerts(rows)


# ────────────────────────────────────────────
# Phase 2: 作業員一覧 (スタッフ軸) 集計 — クエリ A
# ────────────────────────────────────────────
#
# q_staff active な各スタッフごとに保有 cert を集計。bucket 別件数と、
# 最早期限 (renewal_required=1 の中で最古の expires_on) を 1 SQL で算出する。
# 並びは「警告 (expired+warning) 多い人を上に」→ 業務上の対応優先度に一致。

async def _fetch_staff_aggregation(
    db,
    *,
    q: str = "",
    group: str = "",
) -> list[dict]:
    """作業員一覧タブ用 — 1 SQL でスタッフごとの保有 cert 統計を返す。

    フィルタ:
      - ``q``     : 氏名 / 所属の部分一致
      - ``group`` : 所属の完全一致
    並び: 警告件数降順 → 所属/氏名昇順 (対応必要な人を先頭に)。
    """
    where: list[str] = ["qs.is_active = 1"]
    params: list = []

    q_clean = (q or "").strip()
    if q_clean:
        where.append("(cc.worker_name LIKE ? OR cc.group_name LIKE ?)")
        like = f"%{q_clean}%"
        params.extend([like, like])

    grp_clean = (group or "").strip()
    if grp_clean:
        where.append("cc.group_name = ?")
        params.append(grp_clean)

    where_sql = " AND ".join(where)
    sql = f"""
        WITH cert_buckets AS (
            SELECT
                c.worker_id, c.cert_id, c.expires_on, c.renewal_required,
                CASE
                    WHEN c.renewal_required = 0 OR c.expires_on IS NULL
                        THEN 'no_renewal'
                    WHEN julianday(c.expires_on) - julianday('now','localtime') <= 0
                        THEN 'expired'
                    WHEN julianday(c.expires_on) - julianday('now','localtime') <= 30
                        THEN 'urgent'
                    WHEN julianday(c.expires_on) - julianday('now','localtime') <= 60
                        THEN 'soon'
                    WHEN julianday(c.expires_on) - julianday('now','localtime') <= 180
                        THEN 'far'
                    ELSE 'safe'
                END AS bucket
              FROM q_certificates c
             WHERE c.status = 'confirmed'
        )
        SELECT
            cc.worker_id, cc.worker_name, cc.group_name, cc.role,
            COUNT(cb.cert_id)                                       AS total_certs,
            COALESCE(SUM(CASE WHEN cb.bucket='expired' THEN 1 ELSE 0 END), 0) AS expired_count,
            -- warning_count は ``_STATUS_FILTER_BUCKETS["warning"]`` (180日以内 = far+soon+urgent)
            -- と揃える。staff 行の数値セルから ``?view=certs&q=<name>&status=warning`` への
            -- ドリルダウンで件数が一致する必要があるため、ここの集計と URL クエリの bucket 集合が
            -- ズレてはいけない。
            COALESCE(SUM(CASE WHEN cb.bucket IN ('urgent','soon','far') THEN 1 ELSE 0 END), 0) AS warning_count,
            COALESCE(SUM(CASE WHEN cb.bucket='safe'       THEN 1 ELSE 0 END), 0) AS safe_count,
            COALESCE(SUM(CASE WHEN cb.bucket='no_renewal' THEN 1 ELSE 0 END), 0) AS no_renewal_count,
            MIN(CASE WHEN cb.renewal_required = 1 AND cb.expires_on IS NOT NULL
                     THEN cb.expires_on END)                        AS earliest_expiry
          FROM q_staff qs
          JOIN cc_workers cc ON cc.worker_id = qs.worker_id
          LEFT JOIN cert_buckets cb ON cb.worker_id = cc.worker_id
         WHERE {where_sql}
         GROUP BY cc.worker_id, cc.worker_name, cc.group_name, cc.role
         ORDER BY (expired_count + warning_count) DESC,
                  cc.group_name, cc.worker_name
    """
    cur = await db.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def _fetch_staff_groups(db) -> list[str]:
    """作業員タブの所属フィルタ用 dropdown。q_staff active 限定の DISTINCT。"""
    cur = await db.execute(
        """
        SELECT DISTINCT cc.group_name
          FROM q_staff qs
          JOIN cc_workers cc ON cc.worker_id = qs.worker_id
         WHERE qs.is_active = 1
           AND cc.group_name IS NOT NULL
           AND cc.group_name != ''
         ORDER BY cc.group_name
        """
    )
    return [row[0] for row in await cur.fetchall() if row[0]]


# ────────────────────────────────────────────
# ルート
# ────────────────────────────────────────────

async def _count_active_jobs(db) -> int:
    """サブナビバッジ表示用に、未完了のアップロードジョブ数を返す。"""
    cur = await db.execute(
        "SELECT COUNT(*) FROM q_upload_jobs WHERE status != 'done'"
    )
    row = await cur.fetchone()
    return row[0] if row else 0


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    view: str | None = None,  # 旧 URL 互換 (?view=staff|certs) — 受理のみ、分岐には使わない
    q: str = "",
    status: str | None = None,
    category: str = "",
    include_archived: int = 0,
    staff_q: str = "",
    staff_group: str = "",
    user: dict = Depends(_RequireGeneral),
):
    """資格者証管理ポータル — お知らせ + 2 カラム (資格者一覧 / 資格証一覧)。

    PC では左右 2 カラム同時表示、モバイルでは縦積み。タブ切替は廃止し、
    両側のデータを常に同時に取得・描画する。

    URL クエリ:
      cert 側  : q / status / category / include_archived
      staff 側 : staff_q / staff_group
      view パラメータは旧 URL 互換のため受理するが、分岐には使用しない。
    """
    include_archived_b = bool(include_archived)

    db = await get_db()
    try:
        # ── お知らせ (常時) ─────────────────────────────────
        notifications = await _fetch_notifications(db)
        notif_counts = await _count_notifications(db)
        pending_count = await _count_active_jobs(db)

        # cert 側の effective_status 計算 (お知らせ実装後はデフォルト 'all')。
        if status is None:
            effective_status = "all"
        else:
            effective_status = status.strip() or "all"

        # ── 資格証一覧 (cert 軸) ─────────────────────────────
        all_certs = await _fetch_confirmed(db)
        certificates = await _fetch_confirmed(
            db, q=q, category=category, include_archived=include_archived_b,
        )
        certificates = _apply_status_filter(certificates, effective_status)
        cert_groups = _group_certs_by_qualification(certificates)
        categories = await _fetch_categories(db)

        # ── 資格者一覧 (staff 軸) ────────────────────────────
        staff_rows = await _fetch_staff_aggregation(
            db, q=staff_q, group=staff_group,
        )
        staff_groups = await _fetch_staff_groups(db)
    finally:
        await db.close()

    filters = {
        "q": q.strip(),
        "status": effective_status if effective_status != "all" else "",
        "category": category.strip(),
        "include_archived": include_archived_b,
        "staff_q": staff_q.strip(),
        "staff_group": staff_group.strip(),
    }
    is_filtered_certs = bool(
        filters["q"] or filters["status"] or filters["category"]
        or filters["include_archived"]
    )
    is_filtered_staff = bool(filters["staff_q"] or filters["staff_group"])

    return _templates.TemplateResponse(
        request,
        "qualifications/index.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "index",
            # ── お知らせ ──
            "notifications": notifications,
            "notif_counts": notif_counts,
            # ── certs カラム ──
            "certificates": certificates,           # 後方互換 (CSV/PDF link クエリ用に保持)
            "cert_groups": cert_groups,             # ドリルダウン用のグループ化済みデータ
            "summary": _summarize(all_certs),
            "categories": categories,
            "is_filtered": is_filtered_certs,
            "total_count": len(all_certs),
            "filtered_count": len(certificates),
            # ── staff カラム ──
            "staff_rows": staff_rows,
            "staff_groups": staff_groups,
            "is_filtered_staff": is_filtered_staff,
            # ── 共通 ──
            "filters": filters,
            "pending_count": pending_count,
        },
    )


# CSV のヘッダー行 (Excel 互換のため UTF-8 BOM を別途付与)。
_CSV_HEADER: tuple[str, ...] = (
    "作業員", "所属", "資格名", "カテゴリ",
    "交付番号", "交付機関", "交付日", "有効期限",
    "残日数", "状態",
)


def _days_until(expires_on: str | None) -> str:
    """有効期限までの残日数を文字列で返す (なし/不正は空)。"""
    if not expires_on:
        return ""
    try:
        return str((date.fromisoformat(expires_on) - date.today()).days)
    except ValueError:
        return ""


@router.get("/export")
async def export_csv(
    q: str = "",
    status: str = "",
    category: str = "",
    user: dict = Depends(_RequireGeneral),
):
    """確定済み一覧を CSV でダウンロードする。

    クエリパラメータは index と完全に同じ意味で、現在の絞り込み条件を維持できる。
    StreamingResponse で行ごとに送出するため、件数が多くてもメモリを抱え込まない。
    """
    db = await get_db()
    try:
        rows = await _fetch_confirmed(db, q=q, category=category)
        rows = _apply_status_filter(rows, status)
    finally:
        await db.close()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        # UTF-8 BOM (Excel が自動で UTF-8 と認識するため)
        buf.write("﻿")
        writer.writerow(_CSV_HEADER)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for r in rows:
            writer.writerow([
                r["worker_name"],
                r["group_name"] or "",
                r["qual_name"],
                r["qual_category"] or "",
                r["certificate_no"] or "",
                r["issuer"] or "",
                r["issued_on"] or "",
                r["expires_on"] or "",
                _days_until(r["expires_on"]) if r["renewal_required"] else "",
                _BUCKET_LABELS.get(r["bucket"], r["bucket"]),
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"qualifications_{timestamp}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ────────────────────────────────────────────
# PDF エクスポート (Phase 3.3) — 印刷向け一覧
# ────────────────────────────────────────────

# CSS は CDN ではなくテンプレ側で最小スタイルを記述する。
# Playwright は networkidle まで待つので、外部 CSS を引いても OK だが
# オフライン環境でも動かしやすいよう自前 CSS で最小限にとどめる。

async def _html_to_pdf(html: str) -> bytes:
    """HTML 文字列を A4 landscape PDF バイト列に変換する (Playwright)。

    既存の skills/order_docs/html_pdf_builder.py と同じ手順
    (set_content → emulate_media('print') → page.pdf) を踏襲した薄いラッパ。
    PDF オプションは @page CSS でテンプレ側から制御できるよう
    prefer_css_page_size=True を有効化する。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            await page.emulate_media(media="print")
            return await page.pdf(
                format="A4",
                landscape=True,
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "10mm", "bottom": "10mm",
                        "left": "10mm", "right": "10mm"},
            )
        finally:
            await browser.close()


# 状態フィルタ key → 日本語ラベル (PDF タイトル下のサマリ表示用)。
_STATUS_LABELS_JA: dict[str, str] = {
    "safe":       "有効 (>180日)",
    "warning":    "期限近接 (180日以内)",
    "expired":    "期限切れ",
    "no_renewal": "更新不要",
}


def _build_filter_label(filters: dict) -> str:
    """印刷ヘッダ向けに、現在のフィルタ条件を 1 行で日本語化する。"""
    parts: list[str] = []
    if filters.get("q"):
        parts.append(f"キーワード「{filters['q']}」")
    if filters.get("status"):
        parts.append(_STATUS_LABELS_JA.get(filters["status"], filters["status"]))
    if filters.get("category"):
        parts.append(f"カテゴリ「{filters['category']}」")
    if filters.get("include_archived"):
        parts.append("アーカイブ含む")
    return " / ".join(parts) if parts else "全件"


@router.get("/export/pdf")
async def export_pdf(
    request: Request,
    q: str = "",
    status: str = "",
    category: str = "",
    include_archived: int = 0,
    preview: int = 0,
    user: dict = Depends(_RequireGeneral),
):
    """印刷向け一覧の PDF / HTML プレビュー。

    クエリ:
      - 一覧画面と同じ ``q`` / ``status`` / ``category`` / ``include_archived``
      - ``preview=1`` で HTML をそのまま返す (PDF 化スキップ — ブラウザで確認用)

    PDF はメモリ上で生成し ``StreamingResponse`` で返却する。Content-Disposition は
    ``attachment`` で明示的にダウンロードを促す。プレビュー時は HTML として返し、
    ブラウザのプリント機能と組み合わせて使えるようにする。
    """
    include_archived_b = bool(include_archived)
    db = await get_db()
    try:
        rows = await _fetch_confirmed(
            db, q=q, category=category, include_archived=include_archived_b,
        )
        rows = _apply_status_filter(rows, status)
    finally:
        await db.close()

    filters = {
        "q": q.strip(),
        "status": status.strip(),
        "category": category.strip(),
        "include_archived": include_archived_b,
    }
    context = {
        "request": request,
        "user": user,
        "certificates": rows,
        "summary": _summarize(rows),
        "total_count": len(rows),
        "filters": filters,
        "filter_label": _build_filter_label(filters),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # HTML プレビュー: TemplateResponse で Content-Type=text/html
    if preview:
        return _templates.TemplateResponse(
            request, "qualifications/print.html", context,
        )

    # PDF 生成: 同じテンプレートを文字列として描画
    html = _templates.env.get_template("qualifications/print.html").render(**context)
    pdf_bytes = await _html_to_pdf(html)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"qualifications_{timestamp}.pdf"
    logger.info(
        "qualifications PDF エクスポート: rows=%d filters=%s user=%s",
        len(rows), filters, user.get("username"),
    )
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/pending", response_class=HTMLResponse)
async def pending(request: Request, user: dict = Depends(_RequireGeneral)):
    """未確定一覧 — q_upload_jobs ベース。

    OCR 後の確認待ち / 進行中 / エラーのジョブをカード形式で表示する。
    """
    db = await get_db()
    try:
        jobs = await _fetch_upload_jobs(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/pending.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "pending",
            "jobs": jobs,
            "pending_count": len(jobs),
        },
    )


# ────────────────────────────────────────────
# アップロード (admin)
# ────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, user: dict = Depends(_RequireManager)):
    """アップロード画面を返す。admin のみ。"""
    return _templates.TemplateResponse(
        request,
        "qualifications/upload.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "upload",
            "max_file_mb": QUALIFICATIONS_MAX_FILE_MB,
            "max_files": QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
        },
    )


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    user: dict = Depends(_RequireManager),
):
    """multipart で複数ファイルを受け取り、ステージングへ保存して
    ``q_upload_jobs`` に status='pending' のジョブを作成する。

    後続の OCR/classify ステップは Phase 2 で worker に乗せる。本エンドポイントは
    あくまで「ステージング完了」を返すだけ。失敗時は途中で書き出したファイルを
    削除して整合性を保つ。
    """
    # ── 入力検証 ──
    if not files:
        return JSONResponse({"ok": False, "error": "ファイルが選択されていません。"}, status_code=400)
    if len(files) > QUALIFICATIONS_MAX_FILES_PER_UPLOAD:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    f"一度にアップロードできるのは "
                    f"{QUALIFICATIONS_MAX_FILES_PER_UPLOAD} 枚までです。"
                ),
            },
            status_code=400,
        )

    max_bytes = QUALIFICATIONS_MAX_FILE_MB * 1024 * 1024

    # ── ファイル拡張子・サイズの先行検証（DB 書き込み前にエラーを返す） ──
    contents: list[tuple[str, bytes]] = []
    for f in files:
        original_name = f.filename or "unnamed"
        if not is_allowed_extension(original_name):
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"非対応のファイル形式です: {original_name}（PDF/JPG/PNG のみ）",
                },
                status_code=400,
            )
        data = await f.read()
        if len(data) == 0:
            return JSONResponse(
                {"ok": False, "error": f"空ファイルです: {original_name}"},
                status_code=400,
            )
        if len(data) > max_bytes:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"{original_name} がサイズ上限 "
                        f"{QUALIFICATIONS_MAX_FILE_MB} MB を超えています。"
                    ),
                },
                status_code=400,
            )
        contents.append((original_name, data))

    # ── ジョブ ID とステージング ──
    job_id = uuid.uuid4().hex
    staging = staging_dir_for(job_id)

    saved_paths: list[str] = []
    try:
        # ファイル名衝突を避けるため、必要に応じてサフィックスを付ける
        used_names: set[str] = set()
        for original_name, data in contents:
            base = sanitize_filename(original_name)
            unique = base
            n = 1
            while unique in used_names:
                stem, _, ext = base.rpartition(".")
                unique = f"{stem}_{n}.{ext}" if ext else f"{base}_{n}"
                n += 1
            used_names.add(unique)
            target = staging / unique
            target.write_bytes(data)
            saved_paths.append(str(target))

        # ── DB レコード作成 ──
        db = await get_db()
        try:
            await db.execute(
                """
                INSERT INTO q_upload_jobs (job_id, user_id, file_count, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (job_id, user["id"], len(contents)),
            )
            await db.commit()
        finally:
            await db.close()

        # ── ワーカーキューに投入 ──
        # ワーカーが落ちている (TestClient で lifespan 未起動など) 場合でも
        # キューに残るだけで失敗にはならない。次回起動時に restore_pending_jobs が拾う。
        job_queue.put(("qualifications", job_id))
    except Exception as e:
        # 部分的に書き出したファイルを片付けて再送出
        logger.exception("アップロード処理でエラー発生 job_id=%s", job_id)
        shutil.rmtree(staging, ignore_errors=True)
        return JSONResponse(
            {"ok": False, "error": f"サーバーエラー: {e.__class__.__name__}"},
            status_code=500,
        )

    logger.info(
        "qualifications upload accepted: job_id=%s files=%d user=%s",
        job_id, len(contents), user.get("username"),
    )
    return JSONResponse(
        {
            "ok": True,
            "job_id": job_id,
            "file_count": len(contents),
            "status": "pending",
            "saved_files": [p.rsplit("/", 1)[-1] for p in saved_paths],
            "next_url": f"/qualifications/classify/{job_id}",
        }
    )


# ────────────────────────────────────────────
# Classify (OCR 結果の確認・修正・確定) — Phase 2.5
# ────────────────────────────────────────────

# ファイルプレビュー (左ペイン) で参照される拡張子。
# safe_file_response が path 検証を行うので拡張子チェックは UI 補助のみ。
_PREVIEW_MIME_HINT: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}


def _resolve_media_type(filename: str) -> str:
    """ファイル名から Content-Type を決定する (プレビュー強制用)。

    優先順位:
      1. ``_PREVIEW_MIME_HINT`` の固定マップ (PDF / 画像群)
      2. Python ``mimetypes.guess_type`` の推定
      3. それでも判定できなければ ``application/octet-stream``

    重要: プレビュー目的では「正しい Content-Type を必ず付ける」ことが
    最優先。Content-Type が無いとブラウザは保存ダイアログを出してしまう。
    """
    suffix = ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""
    fixed = _PREVIEW_MIME_HINT.get(suffix)
    if fixed:
        return fixed
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _fetch_workers(db) -> list[dict]:
    """資格者マスタ (q_staff) で active なスタッフを返す (作業員ドロップダウン用)。

    Option 1 設計: 物理的な氏名/所属は cc_workers に置きつつ、可視性の判定は
    q_staff.is_active=1 で行う。q_staff に未登録の作業員 (= 日報専用) は
    資格管理側のドロップダウンには現れない。

    在職フラグ (cc_workers.is_active=1) も二重で見ることで、退職処理が
    日報側で行われた場合も自然に消える。
    """
    cur = await db.execute(
        """
        SELECT cc.worker_id, cc.worker_name, cc.group_name
          FROM q_staff qs
          JOIN cc_workers cc ON cc.worker_id = qs.worker_id
         WHERE qs.is_active = 1
           AND cc.is_active = 1
         ORDER BY cc.group_name, cc.worker_name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


async def _fetch_qualifications_master(db) -> list[dict]:
    """q_qualifications マスタを取得 (資格名のオートコンプリート用)。"""
    cur = await db.execute(
        """
        SELECT qual_id, name, category, default_valid_years
          FROM q_qualifications
         WHERE is_active = 1
         ORDER BY display_order, name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


def _shape_candidates_for_form(response: OCRResponse) -> list[dict]:
    """OCRResponse → form 表示用の dict リストに整形する。"""
    out: list[dict] = []
    for c in response.candidates:
        fcs = [v for v in c.field_confidences.model_dump().values() if v is not None]
        avg = sum(fcs) / len(fcs) if fcs else 0.0
        out.append({
            "qualification_name": c.qualification_name or "",
            "category":           c.category or "",
            "worker_name":        c.worker_name or "",
            "certificate_no":     c.certificate_no or "",
            "issuer":             c.issuer or "",
            "issued_on":          c.issued_on or "",
            "expires_on":         c.expires_on or "",
            "renewal_required":   bool(c.renewal_required),
            "page_indices":       c.page_indices,
            "confidence":         avg,
        })
    return out


@router.get("/jobs/{job_id}/status")
async def job_status(
    job_id: str,
    user: dict = Depends(_RequireManager),
):
    """ジョブの現在ステータスを JSON で返す (アップロード後のポーリング用)。

    upload.html の解析中 UI が ``setInterval`` で叩き、``status`` が
    ``await_review`` (= OCR 完了) になったら classify 画面へ自動遷移する。
    エラー時は ``error_message`` に整形済みメッセージが入る。

    レスポンス形:
      ``{"job_id": "...", "status": "pending|ocr|await_review|error|done",
        "error_message": null | "...",
        "next_url": "/qualifications/classify/<id>" | null}``
      存在しないジョブは 404。
    """
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT job_id, status, error_message FROM q_upload_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
    finally:
        await db.close()
    if row is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    job = dict(row)
    next_url = (
        f"/qualifications/classify/{job_id}"
        if job["status"] == "await_review" else None
    )
    return JSONResponse({
        "job_id": job["job_id"],
        "status": job["status"],
        "error_message": job.get("error_message"),
        "next_url": next_url,
    })


@router.get("/classify/{job_id}", response_class=HTMLResponse)
async def classify_page(
    request: Request,
    job_id: str,
    user: dict = Depends(_RequireManager),
):
    """OCR 結果の確認・修正画面を返す。admin のみ。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM q_upload_jobs WHERE job_id = ?", (job_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        job = dict(row)

        if job["status"] not in ("await_review", "ocr"):
            # ocr 中は OCR 完了待ちなので少しだけ許容、それ以外は pending に戻す
            return RedirectResponse(
                url="/qualifications/pending", status_code=303,
            )

        # OCR 結果をパース (壊れた JSON でも落ちないように)
        candidates: list[dict] = []
        overall_confidence = 0.0
        if job["classify_json"]:
            try:
                response = OCRResponse.model_validate_json(job["classify_json"])
                candidates = _shape_candidates_for_form(response)
                overall_confidence = response.overall_confidence
            except Exception:
                logger.exception("classify_json パース失敗 job=%s", job_id[:8])

        # ファイルプレビュー用: ステージングのファイル一覧
        # URL は ``/`` で始まるサイトルート相対パスで返す。ブラウザが
        # 現在のページ origin (scheme/host/port) で解決するため、iframe の
        # src がポート番号を取りこぼす事故が起きない。
        #
        # アップロード時 (``upload_files``) は ``staging_dir_for(job_id)`` で
        # ``QUALIFICATIONS_STAGING_ROOT/<job_id>/`` 配下にファイルを書き出す。
        # ここでも同じルートを参照する ``list_staged_files`` を使うので、
        # 物理ディレクトリは確実に一致する。万一 staging ディレクトリが消えて
        # いた場合 (cleanup レース等) は WARNING ログを出して空配列を返し、
        # 画面は「ステージングにファイルがありません」表示で破綻させない。
        staging_dir = QUALIFICATIONS_STAGING_ROOT / job_id
        if not staging_dir.is_dir():
            logger.warning(
                "qualifications classify: staging dir not found job=%s path=%s",
                job_id[:8], staging_dir,
            )
        files = []
        for p in list_staged_files(job_id):
            files.append({
                "name": p.name,
                "url":  f"/qualifications/files/{job_id}/{p.name}",
                "is_image": p.suffix.lower() in (".png", ".jpg", ".jpeg"),
                "is_pdf":   p.suffix.lower() == ".pdf",
            })

        workers       = await _fetch_workers(db)
        quals_master  = await _fetch_qualifications_master(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/classify.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "pending",
            "job": job,
            "files": files,
            "candidates": candidates,
            "overall_confidence": overall_confidence,
            "workers": workers,
            "qualifications_master": quals_master,
        },
    )


@router.get(
    "/files/{job_id}/{filename}",
    name="qualifications_serve_staged_file",
)
async def serve_staged_file(
    job_id: str,
    filename: str,
    download: bool = False,
    user: dict = Depends(_RequireManager),
):
    """staging ディレクトリのファイルを安全に配信する。

    プレビュー / ダウンロードを ``?download=`` クエリで切り替える:
      - ``?download=false`` (既定): ``Content-Disposition: inline`` を返し、
        ブラウザで PDF / 画像をそのまま表示する (モーダル / iframe / 別タブ プレビュー用)
      - ``?download=true``         : ``Content-Disposition: attachment`` を返し、
        ブラウザに保存ダイアログを出す (明示的なダウンロード導線用)

    safe_file_response が path 解決を行うため、``..`` 等のトラバーサル試行は 404 になる。
    物理ファイルが存在しない場合も 404 を返すが、HTTPException 経由なのでアプリ
    プロセスは死なず、運用上の追跡用に WARNING ログを残す。
    """
    base = QUALIFICATIONS_STAGING_ROOT / job_id
    target = base / filename
    exists = target.is_file()
    # プレビュー時は inline、明示ダウンロードは attachment
    disposition = "attachment" if download else "inline"
    # Content-Type を必ず付与する (None だとブラウザが application/octet-stream
    # に倒れて保存ダイアログを出すため、プレビューが破綻する)
    media_type = _resolve_media_type(filename)

    # 診断: 物理パス解決の結果を記録する。プレビュー失敗時の調査用に
    # journalctl で「どのパスを見にいったか / 実体が在るか / どんな
    # Content-Type / Disposition で配信したか」が一目で分かるように残す。
    if exists:
        logger.info(
            "qualifications file serve: job=%s file=%s path=%s "
            "media_type=%s disposition=%s",
            job_id[:8], filename, target, media_type, disposition,
        )
    else:
        logger.warning(
            "qualifications staging file not found: job=%s file=%s path=%s "
            "(DB レコードは残っているが物理ファイルが欠損)",
            job_id[:8], filename, target,
        )

    # ── X-Frame-Options を SAMEORIGIN に上書き ──
    # 既定 middleware (web_app/main.py) が全レスポンスに X-Frame-Options: DENY を
    # 付ける。これだと自社ドメインの ``<embed>`` / ``<iframe>`` でも PDF が
    # ブラウザに「フレーム埋め込み拒否」と解釈されレンダリングされない。
    # ファイル配信は同一 origin からの埋め込みプレビューに限り許可する。
    return safe_file_response(
        target, base_dir=base,
        filename=filename, media_type=media_type,
        content_disposition_type=disposition,
        extra_headers={"X-Frame-Options": "SAMEORIGIN"},
    )


async def _ensure_qualification(
    db, name: str, category: str | None = None,
) -> int:
    """資格マスタに ``name`` が無ければ追加し qual_id を返す。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="資格名は必須です")
    cur = await db.execute(
        "SELECT qual_id FROM q_qualifications WHERE name = ?", (name,),
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    # 新規追加
    cur = await db.execute(
        """
        INSERT INTO q_qualifications (name, category, renewal_required)
        VALUES (?, ?, 1)
        """,
        (name, (category or "").strip() or ""),
    )
    qual_id = cur.lastrowid
    logger.info("資格マスタに新規追加: %s (qual_id=%d)", name, qual_id)
    return int(qual_id)


@router.post("/classify/{job_id}")
async def classify_submit(
    request: Request,
    job_id: str,
    user: dict = Depends(_RequireManager),
):
    """フォーム送信を受け、各候補を q_certificates(status='confirmed') に確定登録する。"""
    form = await request.form()
    try:
        n_candidates = int(form.get("n_candidates", "0"))
    except ValueError:
        n_candidates = 0
    if n_candidates < 1:
        raise HTTPException(status_code=400, detail="登録する候補がありません")

    db = await get_db()
    created: list[int] = []
    try:
        # ジョブ存在 + 状態確認
        cur = await db.execute(
            "SELECT status, classify_json FROM q_upload_jobs WHERE job_id = ?",
            (job_id,),
        )
        job_row = await cur.fetchone()
        if job_row is None:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        if job_row["status"] not in ("await_review", "ocr"):
            raise HTTPException(
                status_code=400,
                detail=f"このジョブは確定済みです (status={job_row['status']})",
            )

        # staging のファイル群を「相対パス -> 絶対パス相当の文字列」に正規化。
        # original_files_json に書き込むのは ``qualifications/<job>/<file>`` 形式の
        # 相対パス。後段でファイル名 (basename) と双方向に引けるよう dict 化する。
        staged = list_staged_files(job_id)
        staged_rel_by_name: dict[str, str] = {
            p.name: str(p.relative_to(QUALIFICATIONS_STAGING_ROOT.parent))
            for p in staged
        }

        for i in range(n_candidates):
            qual_name = (form.get(f"qualification_name_{i}", "") or "").strip()
            category  = (form.get(f"category_{i}", "") or "").strip()
            try:
                worker_id = int(form.get(f"worker_id_{i}", "") or "0")
            except ValueError:
                worker_id = 0
            certificate_no = (form.get(f"certificate_no_{i}", "") or "").strip() or None
            issuer         = (form.get(f"issuer_{i}", "") or "").strip() or None
            issued_on      = (form.get(f"issued_on_{i}", "") or "").strip() or None
            expires_on     = (form.get(f"expires_on_{i}", "") or "").strip() or None
            renewal_required = (form.get(f"renewal_required_{i}", "") == "1")

            if worker_id <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"候補 {i + 1}: 作業員を選択してください",
                )

            # ── 候補 (= 確定後の cert) ごとの紐付けファイルを決定 ──
            # form は ``source_files_{i}`` を multi-value で送る (basename のみ)。
            # 値は staged にあるファイルだけホワイトリストで通し、無効なもの
            # (パストラバーサル試行 / 存在しない名前) は黙って捨てる。
            # 有効選択が 0 件なら 400 で拒否する: 「全 cert に全ファイルが紐付き、
            # staff_detail で他資格の原本まで表示される」過去バグの再発防止。
            selected = form.getlist(f"source_files_{i}") if hasattr(form, "getlist") else []
            valid_rel = [
                staged_rel_by_name[name]
                for name in selected
                if name in staged_rel_by_name
            ]
            if not valid_rel:
                raise HTTPException(
                    status_code=400,
                    detail=f"候補 {i + 1}: 証跡ファイルを 1 つ以上選択してください",
                )
            cert_files_json = _json_dumps(valid_rel)

            qual_id = await _ensure_qualification(db, qual_name, category)
            cur = await db.execute(
                """
                INSERT INTO q_certificates (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, renewal_required,
                    status, original_files_json,
                    ocr_raw_json, ocr_confidence, ocr_model,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?,
                        ?, NULL, NULL, ?, datetime('now','localtime'), datetime('now','localtime'))
                """,
                (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, 1 if renewal_required else 0,
                    cert_files_json,
                    job_row["classify_json"], user["id"],
                ),
            )
            created.append(int(cur.lastrowid))

        # ジョブを done に
        await db.execute(
            "UPDATE q_upload_jobs SET status='done', updated_at=datetime('now','localtime') "
            "WHERE job_id = ?",
            (job_id,),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications classify 確定: job=%s certs=%d user=%s",
        job_id[:8], len(created), user.get("username"),
    )
    # 一覧 (確定済み) にリダイレクト
    return RedirectResponse(url="/qualifications/", status_code=303)


# ────────────────────────────────────────────
# アップロードジョブの削除 — status in ('error','await_review') を物理削除
# ────────────────────────────────────────────
#
# OCR/分類で失敗 (error) したジョブと、OCR 完了で確認待ち (await_review) のまま
# 破棄したいジョブの両方を pending 画面から片付ける。誤って進行中ジョブ
# (pending/ocr/classifying) や確定済み (done) を消さないよう、ガードで弾く。
# DB 行と staging ディレクトリの両方を片付ける。

async def _delete_upload_job(job_id: str, user: dict) -> dict:
    """q_upload_jobs を物理削除する共通ロジック (POST / DELETE 両エンドポイント
    から呼ばれる)。manager 認可は呼び出し側 Depends で済ませる前提。

    削除を許可するのは「これ以上自動的に進まない / 確認待ちの宙ぶらりん」状態:
      - ``error``         : エラーで停止しているジョブ
      - ``await_review``  : OCR 完了で確認待ちだが破棄したいジョブ (classify から)

    エラーマトリクス:
      - 存在しない job_id                → HTTPException 404
      - 進行中 (pending/ocr/classifying) → HTTPException 400 (誤削除防止)
      - 確定済み (done)                  → HTTPException 400 (履歴保護)
    成功時は staging ディレクトリ (qualifications/<job_id>/) も丸ごと掃除する。
    戻り値は ``{"ok": True, "job_id": ..., "deleted_status": ...}``。
    """
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT status FROM q_upload_jobs WHERE job_id = ?", (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.info(
                "qualifications upload job 削除拒否: job=%s 理由=not_found user=%s",
                job_id[:8], user.get("username"),
            )
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        prev_status = row["status"]
        if prev_status not in ("error", "await_review"):
            logger.warning(
                "qualifications upload job 削除拒否: job=%s status=%s "
                "理由=invalid_state user=%s",
                job_id[:8], prev_status, user.get("username"),
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"このジョブは削除できる状態ではありません "
                    f"(現在の状態: {prev_status}) — "
                    f"エラー / 確認待ち以外のジョブは削除を許可していません"
                ),
            )
        await db.execute(
            "DELETE FROM q_upload_jobs WHERE job_id = ?", (job_id,),
        )
        await db.commit()
    finally:
        await db.close()

    # ── staging ディレクトリの物理削除 ──
    # 失敗してもリクエストは成功扱いにする (DB 行は既に消えており、孤児ファイルが
    # 残るだけで機能影響はない)。ただし「なぜ消せなかったか」は運用ログに残す。
    staging = QUALIFICATIONS_STAGING_ROOT / job_id
    if staging.exists():
        # ファイル単位で削除して、permission denied 等の詳細を握りつぶさない。
        for child in list(staging.rglob("*")):
            if child.is_file() or child.is_symlink():
                try:
                    child.unlink()
                except OSError as e:
                    logger.warning(
                        "qualifications upload job: 物理ファイル削除失敗 job=%s "
                        "path=%s err=%s (孤児として残置)",
                        job_id[:8], child, e,
                    )
        # 空ディレクトリを削除 (中身が消えていない場合は失敗するが ignore)
        try:
            shutil.rmtree(staging, ignore_errors=False)
        except OSError as e:
            logger.warning(
                "qualifications upload job: staging ディレクトリ削除失敗 job=%s "
                "path=%s err=%s",
                job_id[:8], staging, e,
            )

    logger.info(
        "qualifications upload job 削除: job=%s prev_status=%s user=%s",
        job_id[:8], prev_status, user.get("username"),
    )
    return {"ok": True, "job_id": job_id, "deleted_status": prev_status}


@router.post("/jobs/{job_id}/delete")
async def delete_upload_job_form(
    job_id: str,
    user: dict = Depends(_RequireManager),
):
    """ジョブ削除 (HTML form 互換): 成功時 ``/qualifications/pending`` へ 303。

    HTML <form> は DELETE メソッドを発行できないため、JS を使わない classify.html
    の form-submit からはこちらを叩く。実体は ``_delete_upload_job``。
    """
    await _delete_upload_job(job_id, user)
    return RedirectResponse(url="/qualifications/pending", status_code=303)


@router.delete("/jobs/{job_id}")
async def delete_upload_job_api(
    job_id: str,
    user: dict = Depends(_RequireManager),
):
    """ジョブ削除 (RESTful): pending.html の JS から AJAX で叩く。

    成功時は JSON ``{"ok": true, "job_id": ..., "deleted_status": ...}`` を返す。
    エラーは ``_delete_upload_job`` 側で HTTPException として上がるので、
    FastAPI が 400/404 + JSON detail に変換する。
    """
    result = await _delete_upload_job(job_id, user)
    return JSONResponse(result)


def _json_dumps(value) -> str:
    """JSON dumps (順序保持・日本語そのまま)。Python 標準で十分。"""
    import json as _json
    return _json.dumps(value, ensure_ascii=False)


# ────────────────────────────────────────────
# 作業員別ビュー (Phase 3.3) — 1 人分の保有資格をまとめる個票
# ────────────────────────────────────────────

async def _fetch_worker(db, worker_id: int) -> dict | None:
    """cc_workers から 1 件取得する (個票ヘッダ用)。"""
    cur = await db.execute(
        """
        SELECT worker_id, worker_name, group_name, is_active
          FROM cc_workers
         WHERE worker_id = ?
        """,
        (worker_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _fetch_worker_certificates(db, worker_id: int) -> list[dict]:
    """指定作業員の status='confirmed' 資格者証一覧を取得する。

    並びは「カテゴリ → display_order → 資格名」で、同じカテゴリの資格を
    まとめて閲覧しやすくする。各行に bucket と original_files を付与する。
    """
    cur = await db.execute(
        """
        SELECT  c.cert_id, c.certificate_no, c.issuer,
                c.issued_on, c.expires_on, c.renewal_required,
                c.notes, c.status, c.original_files_json,
                c.ocr_confidence, c.created_at, c.updated_at,
                ql.qual_id, ql.name AS qual_name,
                ql.category AS qual_category, ql.display_order
          FROM  q_certificates c
          JOIN  q_qualifications ql ON ql.qual_id = c.qual_id
         WHERE  c.worker_id = ? AND c.status = 'confirmed'
         ORDER BY ql.category, ql.display_order, ql.name,
                  c.expires_on IS NULL, c.expires_on
        """,
        (worker_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["bucket"] = _expiry_bucket(r["expires_on"], r["renewal_required"])
        r["original_files"] = _parse_original_files(r["original_files_json"])
    return rows


@router.get("/workers/{worker_id}", response_class=HTMLResponse)
async def worker_view(
    request: Request,
    worker_id: int,
    user: dict = Depends(_RequireGeneral),
):
    """作業員 1 人分の保有資格をまとめた個票画面。

    - 上部: 作業員情報カード + サマリ stat-cards
    - 下部: 該当作業員の資格者証一覧 (status='confirmed' のみ)
    - 存在しない worker_id は 404
    """
    db = await get_db()
    try:
        worker = await _fetch_worker(db, worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="作業員が見つかりません")
        certificates = await _fetch_worker_certificates(db, worker_id)
        pending_count = await _count_active_jobs(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/worker_view.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "index",
            "worker": worker,
            "certificates": certificates,
            "summary": _summarize(certificates),
            "pending_count": pending_count,
        },
    )


# ────────────────────────────────────────────
# 編集 / 削除 (Phase 3.1)
# ────────────────────────────────────────────

async def _fetch_certificate(db, cert_id: int) -> dict | None:
    """1 件の q_certificates を作業員・資格マスタと JOIN して取得する。"""
    cur = await db.execute(
        """
        SELECT  c.*,
                w.worker_name, w.group_name,
                ql.name AS qual_name, ql.category AS qual_category
          FROM  q_certificates c
          JOIN  cc_workers       w  ON  w.worker_id = c.worker_id
          JOIN  q_qualifications ql ON ql.qual_id   = c.qual_id
         WHERE  c.cert_id = ?
        """,
        (cert_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _cleanup_orphan_files(db, current_cert_id: int, paths: list[str]) -> None:
    """edit でファイル差し替えが起きた後、旧 staging ファイルを物理削除する。

    安全策:
      - 他の (current_cert_id 以外) cert がまだ同じパスを参照していれば削除しない
        (OCR ジョブの staging ディレクトリは複数 cert に共有されることがある)。
      - パスが想定外形式 (``qualifications/<dir>/<file>``) のときはスキップ。
      - 物理削除に失敗しても処理は継続する (DB は既に更新済みなので、孤児ファイルが
        残るだけで機能影響はない)。

    削除対象は ``QUALIFICATIONS_STAGING_ROOT/<dir>/<file>`` のみ。``..`` を含むパスは
    pathlib の resolve でルート外に出ないことを確認してから削除する。
    """
    for raw in paths:
        path_str = str(raw)
        # 共有チェック: 同じパスを参照する他の cert が居ないか
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM q_certificates "
            "WHERE cert_id != ? AND original_files_json LIKE ?",
            (current_cert_id, f"%{path_str}%"),
        )
        row = await cur.fetchone()
        if row and row["c"]:
            logger.info(
                "qualifications edit: 旧ファイル %s は他の cert に共有されているため削除スキップ",
                path_str,
            )
            continue

        # パス形式の検証
        parts = path_str.split("/")
        if len(parts) < 3 or parts[0] != "qualifications":
            continue
        job_dir, filename = parts[1], parts[-1]
        try:
            staging_root = QUALIFICATIONS_STAGING_ROOT.resolve()
            target = (QUALIFICATIONS_STAGING_ROOT / job_dir / filename).resolve()
            target.relative_to(staging_root)  # ルート外なら ValueError
        except (OSError, ValueError):
            logger.warning(
                "qualifications edit: 旧ファイルパスが不正 — 削除スキップ: %s",
                path_str,
            )
            continue

        try:
            if target.is_file():
                target.unlink()
            # 親ディレクトリが空なら削除 (孤児フォルダを残さない)
            try:
                parent = target.parent
                if parent != staging_root and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        except OSError as e:
            logger.warning(
                "qualifications edit: 旧ファイル削除に失敗 (孤児として残置): %s — %s",
                target, e,
            )


def _parse_original_files(original_files_json: str | None) -> list[dict]:
    """``original_files_json`` (例: ``["qualifications/<jid>/<name>", ...]``) を
    プレビュー用の {name, url, exists} dict に変換する。

    パスが想定外なら url=None / exists=False を返してテンプレート側で
    「ファイル欠損」表示にする。物理ファイルの実体存在もここで検証し、
    削除済み・移動済みの場合はクリック不可の表示に倒す
    (リンクを踏んで 404 detail JSON が画面に出るのを防ぐ)。

    URL は ``/qualifications/files/<job_id>/<filename>`` の形のサイトルート
    相対パスで返す。ブラウザが現在のページ origin で解決するので、iframe や
    ``<a>`` がポート番号を取りこぼす事故は起きない。
    """
    if not original_files_json:
        return []
    import json as _json
    try:
        paths = _json.loads(original_files_json)
    except Exception:
        return []
    out: list[dict] = []
    for p in paths:
        # パスは "qualifications/<job_id>/<filename>" の形を期待
        parts = str(p).split("/")
        if len(parts) >= 3 and parts[0] == "qualifications":
            job_id, filename = parts[1], parts[-1]
            # 物理実体の存在確認: staging に残っているか
            try:
                physical = QUALIFICATIONS_STAGING_ROOT / job_id / filename
                exists = physical.is_file()
            except (OSError, ValueError):
                exists = False
            out.append({
                "name": filename,
                "url":  f"/qualifications/files/{job_id}/{filename}" if exists else None,
                "exists": exists,
            })
        else:
            out.append({"name": str(p), "url": None, "exists": False})
    return out


@router.get("/edit/{cert_id}", response_class=HTMLResponse)
async def edit_page(
    request: Request,
    cert_id: int,
    user: dict = Depends(_RequireManager),
):
    """1 件の確定済み資格者証を編集する画面。admin のみ。"""
    db = await get_db()
    try:
        cert = await _fetch_certificate(db, cert_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="資格者証が見つかりません")
        if cert["status"] == "archived":
            raise HTTPException(status_code=410, detail="この資格者証は削除済みです")
        workers = await _fetch_workers(db)
        quals_master = await _fetch_qualifications_master(db)
    finally:
        await db.close()

    original_files = _parse_original_files(cert.get("original_files_json"))

    return _templates.TemplateResponse(
        request,
        "qualifications/edit.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "index",
            "cert": cert,
            "workers": workers,
            "qualifications_master": quals_master,
            "original_files": original_files,
        },
    )


@router.post("/edit/{cert_id}")
async def edit_submit(
    request: Request,
    cert_id: int,
    user: dict = Depends(_RequireManager),
):
    """編集フォーム送信を受けて q_certificates を更新する。

    資格名が既存マスタに無ければ ``q_qualifications`` を自動で追加する
    (classify と同じ ``_ensure_qualification`` を流用)。

    ``multipart/form-data`` で ``file`` フィールドが送信された場合は、
    新しい原本ファイルとして staging に保存し ``original_files_json`` を
    差し替える。空送信 (ファイル未選択) の場合は既存ファイルを保持する。
    """
    form = await request.form()

    qual_name = (form.get("qualification_name", "") or "").strip()
    category  = (form.get("category", "") or "").strip()
    try:
        worker_id = int(form.get("worker_id", "") or "0")
    except ValueError:
        worker_id = 0
    certificate_no = (form.get("certificate_no", "") or "").strip() or None
    issuer         = (form.get("issuer", "") or "").strip() or None
    issued_on      = (form.get("issued_on", "") or "").strip() or None
    expires_on     = (form.get("expires_on", "") or "").strip() or None
    notes          = (form.get("notes", "") or "").strip() or None
    renewal_required = (form.get("renewal_required", "") == "1")

    if worker_id <= 0:
        raise HTTPException(status_code=400, detail="作業員を選択してください")
    if not qual_name:
        raise HTTPException(status_code=400, detail="資格名は必須です")
    if not issued_on:
        raise HTTPException(status_code=400, detail="交付日は必須です")

    # ── 任意: 添付ファイル差し替え ──
    # form.get("file") は多重定義時 (input が無い OR 空送信) で None / 空 UploadFile になる。
    # filename が空文字列の場合は「ファイル未選択」として無視する。
    upload = form.get("file")
    new_file_data: tuple[str, bytes] | None = None
    if isinstance(upload, StarletteUploadFile) and (upload.filename or "").strip():
        original_name = upload.filename or "unnamed"
        if not is_allowed_extension(original_name):
            raise HTTPException(
                status_code=400,
                detail=f"非対応のファイル形式です: {original_name}（PDF/JPG/PNG のみ）",
            )
        data = await upload.read()
        if len(data) == 0:
            raise HTTPException(
                status_code=400, detail=f"空ファイルです: {original_name}",
            )
        max_bytes = QUALIFICATIONS_MAX_FILE_MB * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{original_name} がサイズ上限 "
                    f"{QUALIFICATIONS_MAX_FILE_MB} MB を超えています"
                ),
            )
        new_file_data = (original_name, data)

    # ── ファイル差し替え (DB 更新前に行うことで失敗時のロールバックを単純化) ──
    new_original_paths: list[str] | None = None
    new_staging_dir = None
    if new_file_data is not None:
        original_name, data = new_file_data
        replace_id = f"edit_{uuid.uuid4().hex}"
        new_staging_dir = staging_dir_for(replace_id)
        try:
            base_name = sanitize_filename(original_name)
            target = new_staging_dir / base_name
            target.write_bytes(data)
            new_original_paths = [f"qualifications/{replace_id}/{base_name}"]
        except Exception:
            shutil.rmtree(new_staging_dir, ignore_errors=True)
            raise

    db = await get_db()
    old_paths_to_cleanup: list[str] = []
    try:
        # 存在確認 + 旧ファイルパス取得 (差し替え時のクリーンアップに使う)
        cur = await db.execute(
            "SELECT status, original_files_json FROM q_certificates "
            "WHERE cert_id = ?",
            (cert_id,),
        )
        row = await cur.fetchone()
        if row is None:
            if new_staging_dir is not None:
                shutil.rmtree(new_staging_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="資格者証が見つかりません")
        if row["status"] == "archived":
            if new_staging_dir is not None:
                shutil.rmtree(new_staging_dir, ignore_errors=True)
            raise HTTPException(status_code=410, detail="削除済みの資格者証は編集できません")

        # 差し替え対象なら旧パスを記録 (commit 後に物理ファイルを削除)
        if new_original_paths is not None and row["original_files_json"]:
            try:
                import json as _json
                old_paths_to_cleanup = list(_json.loads(row["original_files_json"]))
            except Exception:
                old_paths_to_cleanup = []

        qual_id = await _ensure_qualification(db, qual_name, category)
        if new_original_paths is not None:
            await db.execute(
                """
                UPDATE q_certificates
                   SET worker_id           = ?,
                       qual_id             = ?,
                       certificate_no      = ?,
                       issuer              = ?,
                       issued_on           = ?,
                       expires_on          = ?,
                       renewal_required    = ?,
                       notes               = ?,
                       original_files_json = ?,
                       updated_at          = datetime('now','localtime')
                 WHERE cert_id = ?
                """,
                (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, 1 if renewal_required else 0,
                    notes, _json_dumps(new_original_paths), cert_id,
                ),
            )
        else:
            await db.execute(
                """
                UPDATE q_certificates
                   SET worker_id        = ?,
                       qual_id          = ?,
                       certificate_no   = ?,
                       issuer           = ?,
                       issued_on        = ?,
                       expires_on       = ?,
                       renewal_required = ?,
                       notes            = ?,
                       updated_at       = datetime('now','localtime')
                 WHERE cert_id = ?
                """,
                (
                    worker_id, qual_id, certificate_no, issuer,
                    issued_on, expires_on, 1 if renewal_required else 0,
                    notes, cert_id,
                ),
            )
        await db.commit()

        # ── 旧ファイルの物理削除 (DB commit 成功後に実行) ──
        # 他の cert がまだ同じパスを参照している場合は削除しない (共有 staging ディレクトリ
        # は OCR ジョブで複数候補 → 複数 cert が紐づくケースがある)。
        if old_paths_to_cleanup:
            await _cleanup_orphan_files(db, cert_id, old_paths_to_cleanup)
    finally:
        await db.close()

    logger.info(
        "qualifications certificate 更新: cert_id=%d file_replaced=%s "
        "old_files_cleaned=%d user=%s",
        cert_id, new_original_paths is not None,
        len(old_paths_to_cleanup) if new_original_paths is not None else 0,
        user.get("username"),
    )
    return RedirectResponse(url="/qualifications/", status_code=303)


# ────────────────────────────────────────────
# 手動追加 (Phase 3.3) — OCR をスキップして 1 件だけ即時登録する
# ────────────────────────────────────────────
#
# 使い所:
#   - OCR の信頼度が極端に低く candidates が空になるケース
#   - 紙のコピーが手元になく、手入力で台帳だけ起こしたいケース
#   - classify を回さずに先に 1 件だけ登録したいケース
#
# upload (OCR フロー) との違い:
#   - q_upload_jobs を作らない
#   - 即座に q_certificates(status='confirmed') を作る
#   - ファイル添付は任意。あれば <UPLOAD_DIR>/qualifications/manual_<uuid>/ に保存

@router.get("/manual-add", response_class=HTMLResponse)
async def manual_add_page(
    request: Request,
    user: dict = Depends(_RequireManager),
):
    """手動追加フォームを表示する。admin のみ。"""
    db = await get_db()
    try:
        workers      = await _fetch_workers(db)
        quals_master = await _fetch_qualifications_master(db)
        pending_count = await _count_active_jobs(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/manual_add.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "manual_add",
            "workers": workers,
            "qualifications_master": quals_master,
            "pending_count": pending_count,
            "max_file_mb": QUALIFICATIONS_MAX_FILE_MB,
            "max_files": QUALIFICATIONS_MAX_FILES_PER_UPLOAD,
        },
    )


@router.post("/manual-add")
async def manual_add_submit(
    request: Request,
    user: dict = Depends(_RequireManager),
):
    """手動追加フォームを処理する。

    - OCR/レビューはスキップし、即座に ``status='confirmed'`` で q_certificates へINSERT
    - ファイルは任意。添付されたものはステージングディレクトリに保存し
      ``original_files_json`` にパスを記録する
    - 必須: ``worker_id`` (>0), ``qualification_name``, ``issued_on``
    """
    form = await request.form()

    qual_name = (form.get("qualification_name", "") or "").strip()
    category  = (form.get("category", "") or "").strip()
    try:
        worker_id = int(form.get("worker_id", "") or "0")
    except ValueError:
        worker_id = 0
    certificate_no = (form.get("certificate_no", "") or "").strip() or None
    issuer         = (form.get("issuer", "") or "").strip() or None
    issued_on      = (form.get("issued_on", "") or "").strip() or None
    expires_on     = (form.get("expires_on", "") or "").strip() or None
    notes          = (form.get("notes", "") or "").strip() or None
    renewal_required = (form.get("renewal_required", "") == "1")

    if worker_id <= 0:
        raise HTTPException(status_code=400, detail="作業員を選択してください")
    if not qual_name:
        raise HTTPException(status_code=400, detail="資格名は必須です")
    if not issued_on:
        raise HTTPException(status_code=400, detail="交付日は必須です")

    # ── 任意ファイルの保存 ──
    # form.getlist("files") は Starlette UploadFile を返すので一度集約してから検証する。
    raw_files = [
        f for f in form.getlist("files")
        if isinstance(f, StarletteUploadFile) and (f.filename or "").strip()
    ]

    max_bytes = QUALIFICATIONS_MAX_FILE_MB * 1024 * 1024
    if len(raw_files) > QUALIFICATIONS_MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"添付できるのは {QUALIFICATIONS_MAX_FILES_PER_UPLOAD} 枚までです",
        )

    contents: list[tuple[str, bytes]] = []
    for f in raw_files:
        original_name = f.filename or "unnamed"
        if not is_allowed_extension(original_name):
            raise HTTPException(
                status_code=400,
                detail=f"非対応のファイル形式です: {original_name}（PDF/JPG/PNG のみ）",
            )
        data = await f.read()
        if len(data) == 0:
            raise HTTPException(
                status_code=400, detail=f"空ファイルです: {original_name}",
            )
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{original_name} がサイズ上限 "
                    f"{QUALIFICATIONS_MAX_FILE_MB} MB を超えています"
                ),
            )
        contents.append((original_name, data))

    # ファイルを保存し original_files_json を組み立てる
    original_paths: list[str] = []
    if contents:
        # upload と同じ命名スキーム ("qualifications/<id>/<name>") に揃える。
        # OCR ジョブと区別するため id には ``manual_`` プレフィックスを付ける。
        manual_id = f"manual_{uuid.uuid4().hex}"
        staging = staging_dir_for(manual_id)
        used_names: set[str] = set()
        try:
            for original_name, data in contents:
                base = sanitize_filename(original_name)
                unique = base
                n = 1
                while unique in used_names:
                    stem, _, ext = base.rpartition(".")
                    unique = f"{stem}_{n}.{ext}" if ext else f"{base}_{n}"
                    n += 1
                used_names.add(unique)
                target = staging / unique
                target.write_bytes(data)
                original_paths.append(f"qualifications/{manual_id}/{unique}")
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    db = await get_db()
    try:
        qual_id = await _ensure_qualification(db, qual_name, category)
        cur = await db.execute(
            """
            INSERT INTO q_certificates (
                worker_id, qual_id, certificate_no, issuer,
                issued_on, expires_on, renewal_required,
                notes, status, original_files_json,
                ocr_raw_json, ocr_confidence, ocr_model,
                created_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?,
                    ?, 'confirmed', ?,
                    NULL, NULL, NULL, ?,
                    datetime('now','localtime'), datetime('now','localtime'))
            """,
            (
                worker_id, qual_id, certificate_no, issuer,
                issued_on, expires_on, 1 if renewal_required else 0,
                notes, _json_dumps(original_paths),
                user["id"],
            ),
        )
        cert_id = int(cur.lastrowid)
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications manual-add: cert_id=%d worker=%d qual=%s files=%d user=%s",
        cert_id, worker_id, qual_name, len(contents), user.get("username"),
    )
    return RedirectResponse(url="/qualifications/", status_code=303)


def _safe_next_url(next_url: str | None) -> str:
    """``next`` form の値を open-redirect 対策つきで検証する。

    許可するのは ``/qualifications/`` 配下のサイトルート相対パスのみ。
    プロトコル相対 (``//evil``) や絶対 URL、改行混入 (ヘッダ injection)
    を弾き、それ以外は ``None`` を返す。
    """
    if not next_url:
        return None
    if "\r" in next_url or "\n" in next_url:
        return None
    if next_url.startswith("//"):
        return None
    if not next_url.startswith("/qualifications/"):
        return None
    return next_url


@router.post("/delete/{cert_id}")
async def delete_certificate(
    cert_id: int,
    next: str | None = Form(default=None),
    user: dict = Depends(_RequireManager),
):
    """資格者証を archive する (物理削除はしない)。admin のみ。

    DELETE ではなく ``status='archived'`` への更新で履歴は保持する。
    archived 行は ``_fetch_confirmed`` の WHERE 句で除外されるため、
    一覧 / 編集画面からは消える。

    フォームに ``next`` 隠しフィールドが付いている場合は、そのパス
    (``/qualifications/`` 配下のサイトルート相対のみ許可) にリダイレクトする。
    staff_detail から削除した場合は staff_detail にとどまるためのフック。
    """
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT status FROM q_certificates WHERE cert_id = ?", (cert_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="資格者証が見つかりません")
        # 既に archived でも 200 を返す (idempotent)
        if row["status"] != "archived":
            await db.execute(
                """
                UPDATE q_certificates
                   SET status     = 'archived',
                       updated_at = datetime('now','localtime')
                 WHERE cert_id = ?
                """,
                (cert_id,),
            )
            await db.commit()
            logger.info(
                "qualifications certificate アーカイブ: cert_id=%d user=%s",
                cert_id, user.get("username"),
            )
    finally:
        await db.close()

    redirect_to = _safe_next_url(next) or "/qualifications/"
    return RedirectResponse(url=redirect_to, status_code=303)


# ────────────────────────────────────────────
# 復元 (Phase 3.x) — archived → confirmed
# ────────────────────────────────────────────

@router.post("/{cert_id}/restore")
async def restore_certificate(
    cert_id: int,
    user: dict = Depends(_RequireManager),
):
    """archived された資格者証を ``status='confirmed'`` に戻す。admin のみ。

    delete (アーカイブ) と異なり、こちらは**冪等にしない**。すでに confirmed の
    ものを誤って再復元しないよう、明示的に 400 を返す。
      - 存在しない cert_id → 404
      - 状態が archived 以外  → 400
    成功時は ``?include_archived=1`` 付きで一覧へ戻し、操作直後の状態が
    画面で確認できるようにする。
    """
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT status FROM q_certificates WHERE cert_id = ?", (cert_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="資格者証が見つかりません")
        if row["status"] != "archived":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"この資格者証はアーカイブされていません "
                    f"(現在の状態: {row['status']})"
                ),
            )
        await db.execute(
            """
            UPDATE q_certificates
               SET status     = 'confirmed',
                   updated_at = datetime('now','localtime')
             WHERE cert_id = ?
            """,
            (cert_id,),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications certificate 復元: cert_id=%d user=%s",
        cert_id, user.get("username"),
    )
    # 復元直後のレコードがそのまま見えるよう archived ビューに留める
    return RedirectResponse(
        url="/qualifications/?include_archived=1", status_code=303,
    )


# ════════════════════════════════════════════
# 資格者マスタ（スタッフ管理） — Option 1: q_staff メンバーシップ表
# ════════════════════════════════════════════
#
# 資格管理側で扱う「スタッフ集合」を q_staff で表現する。物理データ
# (氏名/所属) は cc_workers に置いたままなので、日報側との二重管理は無い。
#
# - GET  /qualifications/staff           : 一覧 (q_staff JOIN cc_workers)
# - POST /qualifications/staff/new       : 新規 (cc_workers + q_staff の二段 INSERT)
# - POST /qualifications/staff/<id>/delete : 無効化 (q_staff.is_active=0)
# - GET  /qualifications/staff/import    : 取込候補 (q_staff 未登録の cc_workers)
# - POST /qualifications/staff/import    : 一括取込 (worker_ids[])
#
# 取込のデダップ: q_staff.worker_id は UNIQUE。同じ worker_id に対する
# INSERT は ON CONFLICT 経路で is_active=1 への再有効化として扱う。

async def _fetch_staff_with_certcount(db) -> list[dict]:
    """資格者マスタ一覧を cert 件数つきで返す。"""
    cur = await db.execute(
        """
        SELECT  qs.id           AS staff_id,
                qs.is_active    AS staff_is_active,
                qs.source       AS staff_source,
                qs.created_at   AS staff_created_at,
                cc.worker_id, cc.worker_name, cc.group_name, cc.role,
                cc.is_qualifications_only,
                (SELECT COUNT(*)
                   FROM q_certificates c
                  WHERE c.worker_id = cc.worker_id
                    AND c.status = 'confirmed') AS cert_count
          FROM q_staff qs
          JOIN cc_workers cc ON cc.worker_id = qs.worker_id
         ORDER BY qs.is_active DESC, cc.group_name, cc.worker_name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


async def _fetch_import_candidates(db) -> list[dict]:
    """取込候補: 在職中 cc_workers のうち、active な q_staff 行を持たない人。"""
    cur = await db.execute(
        """
        SELECT cc.worker_id, cc.worker_name, cc.group_name, cc.role,
               cc.is_qualifications_only
          FROM cc_workers cc
          LEFT JOIN q_staff qs
                 ON qs.worker_id = cc.worker_id AND qs.is_active = 1
         WHERE cc.is_active = 1
           AND qs.id IS NULL
         ORDER BY cc.group_name, cc.worker_name
        """
    )
    return [dict(r) for r in await cur.fetchall()]


@router.get("/staff", response_class=HTMLResponse)
async def staff_index(
    request: Request,
    msg: str = "",
    cat: str = "success",
    user: dict = Depends(_RequireManager),
):
    """資格者マスタ一覧。qualifications.manager 必須。

    無効化済みのスタッフも一覧の末尾に並べる (履歴目的)。再有効化は import から
    可能 (UNIQUE 制約 + ON CONFLICT で is_active=1 に戻る)。
    """
    db = await get_db()
    try:
        staff = await _fetch_staff_with_certcount(db)
        # 取込候補数のバッジ表示用
        candidates = await _fetch_import_candidates(db)
    finally:
        await db.close()
    return _templates.TemplateResponse(
        request,
        "qualifications/staff.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "staff",
            "staff": staff,
            "import_candidate_count": len(candidates),
            "msg": msg,
            "cat": cat,
        },
    )


@router.post("/staff/new")
async def staff_new(
    request: Request,
    user: dict = Depends(_RequireManager),
    worker_name: str = Form(...),
    group_name: str = Form(""),
    role: str = Form(""),
):
    """資格管理ネイティブスタッフを 1 件登録する。

    cc_workers と q_staff の **両方** に INSERT する:
      - cc_workers: 氏名/所属/role + is_qualifications_only=1 (日報側に出さない)
      - q_staff:    対応する worker_id を source='native' で登録

    ロールバック: cc_workers の INSERT 後に q_staff の INSERT で
    例外が出た場合に備え、commit はループ末尾で 1 度だけ行う。
    """
    name = (worker_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="氏名は必須です")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="氏名は 120 文字以内で入力してください")

    grp = (group_name or "").strip() or None
    rl  = (role or "").strip() or None

    db = await get_db()
    try:
        cur = await db.execute(
            """
            INSERT INTO cc_workers
                (worker_name, role, group_name, is_qualifications_only)
            VALUES (?, ?, ?, 1)
            """,
            (name, rl, grp),
        )
        worker_id = int(cur.lastrowid)
        await db.execute(
            """
            INSERT INTO q_staff (worker_id, source) VALUES (?, 'native')
            """,
            (worker_id,),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications staff 新規追加: name=%s group=%s user=%s",
        name, grp, user.get("username"),
    )
    return RedirectResponse(
        url=f"/qualifications/staff?msg={name} を登録しました&cat=success",
        status_code=303,
    )


@router.post("/staff/{staff_id}/delete")
async def staff_delete(
    staff_id: int,
    user: dict = Depends(_RequireManager),
):
    """資格者マスタからの無効化 (q_staff.is_active=0)。

    既に active な資格証 (confirmed) を持つスタッフは削除できない (400)。
    archived 資格者証はカウントしない (= 過去履歴は残ってよい)。
    """
    db = await get_db()
    try:
        cur = await db.execute(
            """
            SELECT qs.id, qs.is_active, cc.worker_id, cc.worker_name,
                   (SELECT COUNT(*)
                      FROM q_certificates c
                     WHERE c.worker_id = cc.worker_id
                       AND c.status = 'confirmed') AS active_certs
              FROM q_staff qs
              JOIN cc_workers cc ON cc.worker_id = qs.worker_id
             WHERE qs.id = ?
            """,
            (staff_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません")
        if row["active_certs"] > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"このスタッフは {row['active_certs']} 件の有効な資格者証を保有"
                    f"しているため無効化できません。先に資格者証をアーカイブしてください。"
                ),
            )
        if row["is_active"] == 0:
            # 冪等扱い (既に無効化済み)
            return RedirectResponse(
                url="/qualifications/staff?msg=既に無効化済みです&cat=info",
                status_code=303,
            )
        await db.execute(
            """
            UPDATE q_staff
               SET is_active = 0,
                   updated_at = datetime('now','localtime')
             WHERE id = ?
            """,
            (staff_id,),
        )
        await db.commit()
        worker_name = row["worker_name"]
    finally:
        await db.close()

    logger.info(
        "qualifications staff 無効化: staff_id=%d worker=%s user=%s",
        staff_id, worker_name, user.get("username"),
    )
    return RedirectResponse(
        url=f"/qualifications/staff?msg={worker_name} を無効化しました&cat=warning",
        status_code=303,
    )


@router.get("/staff/import", response_class=HTMLResponse)
async def staff_import_page(
    request: Request,
    user: dict = Depends(_RequireManager),
):
    """日報側 (cc_workers) 由来の取込候補を表示する。

    既に q_staff に active で居る人は候補に出ない。無効化済みの人 (is_active=0)
    は候補に出る (= 再有効化として再取込できる)。
    """
    db = await get_db()
    try:
        candidates = await _fetch_import_candidates(db)
    finally:
        await db.close()
    return _templates.TemplateResponse(
        request,
        "qualifications/staff_import.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "staff",
            "candidates": candidates,
        },
    )


@router.post("/staff/import")
async def staff_import_submit(
    request: Request,
    user: dict = Depends(_RequireManager),
):
    """選択された worker_id 群を q_staff へ一括登録する。

    重複防止: q_staff.worker_id UNIQUE + ON CONFLICT で再有効化に倒す。
      - 未登録    → INSERT (source='imported', is_active=1)
      - 無効化済み → UPDATE is_active=1 (再有効化、source は触らない)
      - 既に有効   → no-op (UPDATE で is_active=1 を上書きするだけ)

    フォームは ``worker_ids`` を複数値で受ける (チェックボックス想定)。
    """
    form = await request.form()
    raw_ids = form.getlist("worker_ids")
    worker_ids: list[int] = []
    for v in raw_ids:
        try:
            wid = int(v)
        except (TypeError, ValueError):
            continue
        if wid > 0:
            worker_ids.append(wid)

    if not worker_ids:
        return RedirectResponse(
            url="/qualifications/staff/import?",
            status_code=303,
        )

    db = await get_db()
    inserted = reactivated = 0
    try:
        # 取込前の現状把握: 既存 q_staff 行 (active/inactive 別)
        placeholders = ",".join("?" for _ in worker_ids)
        cur = await db.execute(
            f"""
            SELECT worker_id, is_active FROM q_staff
             WHERE worker_id IN ({placeholders})
            """,
            worker_ids,
        )
        existing = {r["worker_id"]: r["is_active"] for r in await cur.fetchall()}

        for wid in worker_ids:
            await db.execute(
                """
                INSERT INTO q_staff (worker_id, source)
                VALUES (?, 'imported')
                ON CONFLICT(worker_id) DO UPDATE SET
                    is_active = 1,
                    updated_at = datetime('now','localtime')
                """,
                (wid,),
            )
            prev = existing.get(wid)
            if prev is None:
                inserted += 1
            elif prev == 0:
                reactivated += 1
            # prev == 1 → no-op だが SQL は走る (副作用は updated_at の更新のみ)
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications staff 取込: inserted=%d reactivated=%d total_selected=%d user=%s",
        inserted, reactivated, len(worker_ids), user.get("username"),
    )
    msg = f"{inserted} 名を新規取込、{reactivated} 名を再有効化しました"
    return RedirectResponse(
        url=f"/qualifications/staff?msg={msg}&cat=success", status_code=303,
    )


# ────────────────────────────────────────────
# 作業員別個票ビュー (Phase 5) — Card レイアウト + 証跡モーダル
# ────────────────────────────────────────────
#
# 旧 /workers/<id> (worker_view.html, テーブル) と並行して、
# /staff/<id> (staff_detail.html, カード) を提供する。新規導線 (index.html
# の作業員名) は staff_detail に向ける。worker_view は後方互換で残置。

async def _fetch_staff_for_detail(db, worker_id: int) -> dict | None:
    """個票ヘッダ用: q_staff JOIN cc_workers で 1 件取得。

    q_staff に行が無い (= 資格管理対象でない) cc_workers については個票を
    出さない (404)。is_active=0 のスタッフは「無効化済み」として描画する。
    """
    cur = await db.execute(
        """
        SELECT  cc.worker_id, cc.worker_name, cc.group_name, cc.role,
                cc.is_active            AS cc_is_active,
                cc.is_qualifications_only,
                qs.id                   AS staff_id,
                qs.is_active            AS staff_is_active,
                qs.source               AS staff_source,
                qs.created_at           AS staff_created_at
          FROM  q_staff qs
          JOIN  cc_workers cc ON cc.worker_id = qs.worker_id
         WHERE  qs.worker_id = ?
        """,
        (worker_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


@router.get("/staff/{worker_id:int}", response_class=HTMLResponse)
async def staff_detail(
    request: Request,
    worker_id: int,
    user: dict = Depends(_RequireGeneral),
):
    """1 スタッフの保有資格一覧 (Card レイアウト)。

    - q_staff に登録の無い worker_id は 404
    - q_staff.is_active=0 のスタッフは「無効化済み」バナーを出すが、
      既存の cert は引き続き閲覧可能 (履歴として残す方針)
    - 認可: qualifications.general 以上 (閲覧)。証跡ファイル自体の配信は
      ``serve_staged_file`` 側で manager に gate されるため、preview ボタンは
      manager にだけ見せる (general はファイル名表示のみ)。
    """
    db = await get_db()
    try:
        staff = await _fetch_staff_for_detail(db, worker_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません")
        certificates = await _fetch_worker_certificates(db, worker_id)
        pending_count = await _count_active_jobs(db)
    finally:
        await db.close()

    return _templates.TemplateResponse(
        request,
        "qualifications/staff_detail.html",
        {
            "user": user,
            "skill_key": _SKILL_KEY,
            "active_tab": "index",
            "staff": staff,
            "certificates": certificates,
            "summary": _summarize(certificates),
            "pending_count": pending_count,
        },
    )
