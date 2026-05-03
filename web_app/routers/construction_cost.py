"""
工事日報集計ルーター — マスタ管理・集計処理・履歴

※ 機材・経費管理は廃止済み（労務費のみ）
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from web_app.core.database import get_db
from web_app.core.dependencies import get_current_user, require_admin
from web_app.core.safe_files import safe_file_response
from web_app.core.templates import templates as _templates

from skills.construction_cost.reader import read_daily_sheets, normalize_str
from skills.construction_cost.aggregator import aggregate
from skills.construction_cost.writer import (
    write_site_cost_report, write_worker_summary, write_dashboard_export,
)
from skills.construction_cost.template_builder import build_template

logger = logging.getLogger("web_app.construction_cost")

router = APIRouter(prefix="/construction-cost", tags=["construction_cost"])

_WEB_APP_DIR = Path(__file__).resolve().parent.parent
OUTPUT_BASE = _WEB_APP_DIR / "outputs_cc"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

# NOTE: 未確定の集計結果は cc_process_log テーブルに status='draft' で永続化する。
# サーバー再起動やブラウザ切断でもデータが失われない設計。


# ────────────────────────────────────────────
# ヘルパー関数
# ────────────────────────────────────────────

async def _get_sites(db, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM cc_sites"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY site_name"
    cur = await db.execute(sql)
    return [dict(r) for r in await cur.fetchall()]


async def _get_workers(db, active_only: bool = False) -> list[dict]:
    where = " WHERE w.is_active = 1" if active_only else ""
    sql = f"""
        SELECT w.*, COALESCE(g.display_order, 9999) AS group_display_order
        FROM cc_workers w
        LEFT JOIN cc_groups g ON TRIM(w.group_name) = TRIM(g.group_name)
        {where}
        ORDER BY COALESCE(g.display_order, 9999), w.group_name, w.worker_name
    """
    cur = await db.execute(sql)
    return [dict(r) for r in await cur.fetchall()]


async def _get_groups(db, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM cc_groups"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY display_order, group_name"
    cur = await db.execute(sql)
    return [dict(r) for r in await cur.fetchall()]


async def _get_site_group_budgets(db, site_id: int) -> list[dict]:
    cur = await db.execute(
        "SELECT * FROM cc_site_group_budgets WHERE site_id=? ORDER BY id",
        (site_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


# ════════════════════════════════════════════
# ダッシュボード（トップ画面）
# ════════════════════════════════════════════

def _compute_site_group_costs(
    agg_json_list: list[dict],
) -> dict[tuple[int, str], float]:
    """確定済み aggregated_json のリストから (site_id, group) → cost の累計を算出する。

    各 worker の labor_cost を現場別の時間比率で按分して現場×グループ別コストを算出。
    site_id が JSON 内に存在するデータのみ処理する（旧形式はマイグレーション済み前提）。
    """
    site_group_costs: dict[tuple[int, str], float] = {}
    for agg_data in agg_json_list:
        for ws in agg_data.get("worker_summaries", []):
            grp = ws.get("group_name") or "未分類"
            labor = float(ws.get("labor_cost", 0))
            site_hours = ws.get("site_hours", {})
            total_h = sum(
                float(h.get("基本", 0)) + float(h.get("残業", 0))
                for h in site_hours.values()
            )
            if total_h <= 0:
                continue
            for site_name, hours in site_hours.items():
                h = float(hours.get("基本", 0)) + float(hours.get("残業", 0))
                cost = labor * h / total_h
                # site_id が JSON 内に存在する場合のみ処理
                sid = hours.get("site_id") if isinstance(hours, dict) else None
                if sid is None:
                    continue  # site_id 未設定 → スキップ（マイグレーション未済の旧データ）
                key = (sid, grp)
                site_group_costs[key] = site_group_costs.get(key, 0) + cost
    return site_group_costs


async def _resolve_target_month(db, target_month: str | None) -> str:
    """target_month が None の場合、最新確定月（なければ当月）を返す。"""
    if target_month is not None:
        return target_month
    cur = await db.execute("SELECT MAX(target_month) FROM cc_cumulative_history")
    row = await cur.fetchone()
    if row and row[0]:
        return row[0]
    return datetime.now().strftime("%Y-%m")


async def _build_dashboard_data(db, target_month: str | None = None) -> list[dict]:
    """ダッシュボード用のデータを構築する（HTML/Excel 共通）。

    target_month: 表示対象月 "YYYY-MM"。None の場合は最新確定月（なければ当月）を使用。
    集計はカットオフ方式：target_month 以前のデータのみを対象とする（タイムトラベル集計）。
    """
    target_month = await _resolve_target_month(db, target_month)
    sites = await _get_sites(db, active_only=True)

    # ── ① 累計支払い：指定月以前の monthly_cost 合計（initial は後で加算）──
    cur_totals = await db.execute("""
        SELECT site_id,
               COALESCE(SUM(monthly_cost), 0) AS confirmed_total
        FROM cc_cumulative_history
        WHERE target_month <= ?
        GROUP BY site_id
    """, (target_month,))
    totals_map: dict[int, float] = {}
    for row in await cur_totals.fetchall():
        totals_map[row["site_id"]] = row["confirmed_total"]

    # ── ② 当月支払い：指定月ぴったりの monthly_cost ──
    cur_current = await db.execute("""
        SELECT site_id, monthly_cost
        FROM cc_cumulative_history
        WHERE target_month = ?
    """, (target_month,))
    current_cost_map: dict[int, float] = {}
    for row in await cur_current.fetchall():
        current_cost_map[row["site_id"]] = row["monthly_cost"]

    # ── ③ グループ別集計：指定月以前の全確定ログ（累計用） ──
    cur_logs_all = await db.execute("""
        SELECT aggregated_json
        FROM cc_process_log
        WHERE status = 'confirmed' AND aggregated_json IS NOT NULL
          AND target_month <= ?
        ORDER BY target_month
    """, (target_month,))
    all_logs = await cur_logs_all.fetchall()

    # 指定月ぴったりのログ（グループ別「当月支払い」用）
    cur_logs_current = await db.execute("""
        SELECT aggregated_json
        FROM cc_process_log
        WHERE status = 'confirmed' AND aggregated_json IS NOT NULL
          AND target_month = ?
    """, (target_month,))
    current_logs = await cur_logs_current.fetchall()

    # 指定月以前の全グループ別累計コスト
    all_agg_data = [json.loads(l["aggregated_json"]) for l in all_logs]
    all_sg_costs = _compute_site_group_costs(all_agg_data)

    # 指定月ぴったりのグループ別当月コスト（当月支払いのみに使用）
    current_agg_data = [json.loads(l["aggregated_json"]) for l in current_logs]
    current_sg_costs = _compute_site_group_costs(current_agg_data)

    # グループ予算マスタを取得（登録順 = id順）
    cur_gb = await db.execute(
        "SELECT site_id, group_name, budget, initial_cumulative_cost FROM cc_site_group_budgets ORDER BY id"
    )
    gb_map: dict[tuple[int, str], dict] = {}
    for row in await cur_gb.fetchall():
        gb_map[(row["site_id"], row["group_name"])] = dict(row)

    # グループ表示順マスタを取得
    cur_grp_order = await db.execute(
        "SELECT group_name, display_order FROM cc_groups ORDER BY display_order, group_name"
    )
    group_order_map: dict[str, int] = {}
    for row in await cur_grp_order.fetchall():
        group_order_map[row["group_name"]] = row["display_order"]

    # ── ダッシュボード行の組み立て ──
    dashboard_rows = []
    for s in sites:
        sid = s["site_id"]
        # 現場マスタの初期累計を取得し、確定分合計に加算して正確な累計を算出
        initial = float(s.get("initial_cumulative_cost", 0) or 0)
        budget = float(s["budget"] or 0)

        confirmed_total = totals_map.get(sid, 0)
        current_paid = current_cost_map.get(sid, 0)
        total_paid = initial + confirmed_total          # initial_cumulative_cost を必ず加算
        prev_paid = total_paid - current_paid

        # グループ別内訳を構築
        groups = []
        seen_groups: set[str] = set()

        def _grp_sort_key(grp_name: str) -> tuple:
            return (group_order_map.get(grp_name, 9999), grp_name)

        # 実績があるグループ
        # ・g_total   = グループの initial_cumulative_cost + 指定月以前の累計（all_sg_costs）
        # ・g_current = 指定月ぴったりのコスト（current_sg_costs）
        # ・g_prev    = g_total - g_current
        site_grps = [(k, v) for k, v in all_sg_costs.items() if k[0] == sid]
        site_grps.sort(key=lambda x: _grp_sort_key(x[0][1]))
        for (key_id, grp), cost in site_grps:
            seen_groups.add(grp)
            gb_info = gb_map.get((sid, grp), {})
            g_initial = float(gb_info.get("initial_cumulative_cost", 0) or 0)
            g_current = current_sg_costs.get((sid, grp), 0)   # 指定月のみ
            g_total = g_initial + cost                          # initial + 累計
            g_prev = g_total - g_current
            g_budget = float(gb_info.get("budget", 0) or 0)
            groups.append({
                "group_name": grp,
                "budget": g_budget,
                "prev_paid": round(g_prev),
                "current_paid": round(g_current),
                "total_paid": round(g_total),
                "remaining": round(g_budget - g_total) if g_budget else None,
            })

        # 予算設定済みだが実績0のグループを追加（登録順＝gb_map挿入順）
        for (gb_sid, gb_grp), gb_info in gb_map.items():
            if gb_sid == sid and gb_grp not in seen_groups:
                g_budget = float(gb_info.get("budget", 0) or 0)
                g_initial = float(gb_info.get("initial_cumulative_cost", 0) or 0)
                groups.append({
                    "group_name": gb_grp,
                    "budget": g_budget,
                    "prev_paid": round(g_initial),
                    "current_paid": 0,
                    "total_paid": round(g_initial),
                    "remaining": round(g_budget - g_initial) if g_budget else None,
                })

        dashboard_rows.append({
            "site_name": s["site_name"],
            "site_code": s["site_code"],
            "status": s["status"],
            "budget": budget,
            "prev_paid": round(prev_paid),
            "current_paid": round(current_paid),
            "total_paid": round(total_paid),
            "remaining": round(budget - total_paid),
            "latest_month": target_month,
            "groups": groups,
        })

    return dashboard_rows


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    month: str | None = None,
    user: dict = Depends(get_current_user),
):
    """工事日報集計ダッシュボード — 現場別の予算・原価消化状況一覧。"""
    # クエリパラメータ month のバリデーション（YYYY-MM 形式のみ受け付ける）
    target_month: str | None = None
    if month:
        try:
            datetime.strptime(month, "%Y-%m")
            target_month = month
        except ValueError:
            pass

    db = await get_db()
    try:
        target_month = await _resolve_target_month(db, target_month)
        dashboard_rows = await _build_dashboard_data(db, target_month=target_month)
    finally:
        await db.close()

    today = datetime.now()
    today_label = f"{today.year}年{today.month}月{today.day}日"
    current_month = today.strftime("%Y-%m")

    # 月ナビゲーション用：前月・翌月を計算
    year, mon = map(int, target_month.split("-"))
    prev_month = f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"
    next_month = f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"
    target_month_label = f"{year}年{mon}月"
    is_latest_month = target_month >= current_month

    return _templates.TemplateResponse(request, "construction_cost/dashboard.html", {
        "user": user,
        "rows": dashboard_rows,
        "today_label": today_label,
        "target_month": target_month,
        "target_month_label": target_month_label,
        "prev_month": prev_month,
        "next_month": next_month,
        "is_latest_month": is_latest_month,
    })


@router.get("/dashboard/export")
async def dashboard_export(
    request: Request,
    month: str | None = None,
    user: dict = Depends(get_current_user),
):
    """ダッシュボードの内容を階層構造のExcelファイルとしてダウンロードする。"""
    # クエリパラメータ month のバリデーション
    target_month: str | None = None
    if month:
        try:
            datetime.strptime(month, "%Y-%m")
            target_month = month
        except ValueError:
            pass

    db = await get_db()
    try:
        target_month = await _resolve_target_month(db, target_month)
        dashboard_rows = await _build_dashboard_data(db, target_month=target_month)
    finally:
        await db.close()

    buf = write_dashboard_export(dashboard_rows, target_month=target_month)
    filename = f"ダッシュボード_{target_month}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    encoded = quote(filename)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"dashboard.xlsx\"; filename*=UTF-8''{encoded}",
        },
    )


# ════════════════════════════════════════════
# 月別集計ビュー
# ════════════════════════════════════════════

@router.get("/monthly/sites", response_class=HTMLResponse)
async def monthly_sites(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """現場ごとの月別集計ビュー。"""
    db = await get_db()
    try:
        cur = await db.execute("""
            SELECT h.target_month, s.site_code, s.site_name,
                   h.monthly_cost, h.cumulative_before, h.cumulative_after,
                   s.budget
            FROM cc_cumulative_history h
            INNER JOIN cc_sites s ON h.site_id = s.site_id AND s.is_active = 1
            ORDER BY h.target_month DESC, s.site_code
        """)
        rows = [dict(r) for r in await cur.fetchall()]

        # 月ごとにグループ化
        months: dict[str, list[dict]] = {}
        for r in rows:
            m = r["target_month"]
            if m not in months:
                months[m] = []
            months[m].append(r)
    finally:
        await db.close()

    return _templates.TemplateResponse(request, "construction_cost/monthly_sites.html", {
        "user": user,
        "months": months,
    })


@router.get("/monthly/workers", response_class=HTMLResponse)
async def monthly_workers(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """作業員ごとの月別集計ビュー。"""
    db = await get_db()
    try:
        cur = await db.execute("""
            SELECT target_month, aggregated_json
            FROM cc_process_log
            WHERE status = 'confirmed' AND aggregated_json IS NOT NULL
            ORDER BY target_month DESC
        """)
        logs = await cur.fetchall()

        # アクティブな現場ID一覧を取得（削除済み現場を除外するため）
        cur_sites = await db.execute("SELECT site_id FROM cc_sites WHERE is_active = 1")
        active_site_ids = {row["site_id"] for row in await cur_sites.fetchall()}

        months: dict[str, list[dict]] = {}
        for log in logs:
            m = log["target_month"]
            data = json.loads(log["aggregated_json"])
            workers = data.get("worker_summaries", [])
            if not workers or m in months:
                continue

            # 削除済み現場を site_hours から除外し、時間合計を再計算
            filtered_workers = []
            for w in workers:
                site_hours_raw = w.get("site_hours", {})
                filtered_hours = {
                    site: h for site, h in site_hours_raw.items()
                    if isinstance(h, dict) and h.get("site_id") in active_site_ids
                }
                w = dict(w)
                w["site_hours"] = filtered_hours
                w["total_basic"] = round(
                    sum(float(h.get("基本", 0)) for h in filtered_hours.values()), 2
                )
                w["total_overtime"] = round(
                    sum(float(h.get("残業", 0)) for h in filtered_hours.values()), 2
                )
                filtered_workers.append(w)

            months[m] = filtered_workers
    finally:
        await db.close()

    return _templates.TemplateResponse(request, "construction_cost/monthly_workers.html", {
        "user": user,
        "months": months,
    })


# ════════════════════════════════════════════
# グループマスタ管理
# ════════════════════════════════════════════

@router.get("/groups", response_class=HTMLResponse)
async def groups_page(
    request: Request,
    user: dict = Depends(require_admin),
    msg: str = "",
    cat: str = "success",
):
    db = await get_db()
    try:
        groups = await _get_groups(db)
    finally:
        await db.close()
    return _templates.TemplateResponse(request, "construction_cost/groups.html", {
        "user": user, "groups": groups, "msg": msg, "cat": cat,
    })


@router.post("/groups/add")
async def groups_add(
    request: Request,
    user: dict = Depends(require_admin),
    group_name: str = Form(...),
    display_order: int = Form(0),
):
    db = await get_db()
    try:
        try:
            await db.execute(
                "INSERT INTO cc_groups (group_name, display_order) VALUES (?, ?)",
                (group_name.strip(), display_order),
            )
            await db.commit()
        except Exception:
            ref = uuid.uuid4().hex[:8]
            logger.exception("グループ登録エラー (ref=%s)", ref)
            return RedirectResponse(
                url=f"/construction-cost/groups?msg=登録に失敗しました（参照番号: {ref}）&cat=danger",
                status_code=303,
            )
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/groups?msg=グループを追加しました&cat=success",
        status_code=303,
    )


@router.post("/groups/update")
async def groups_update(
    request: Request,
    user: dict = Depends(require_admin),
    group_id: int = Form(...),
    group_name: str = Form(...),
    display_order: int = Form(0),
    is_active: int = Form(1),
):
    db = await get_db()
    try:
        # 旧名を取得して関連テーブルも更新
        cur = await db.execute("SELECT group_name FROM cc_groups WHERE group_id=?", (group_id,))
        old = await cur.fetchone()
        old_name = old["group_name"] if old else None
        new_name = group_name.strip()

        await db.execute(
            "UPDATE cc_groups SET group_name=?, display_order=?, is_active=? WHERE group_id=?",
            (new_name, display_order, is_active, group_id),
        )
        # グループ名変更時は関連テーブルも更新
        if old_name and old_name != new_name:
            await db.execute(
                "UPDATE cc_workers SET group_name=? WHERE group_name=?",
                (new_name, old_name),
            )
            await db.execute(
                "UPDATE cc_site_group_budgets SET group_name=? WHERE group_name=?",
                (new_name, old_name),
            )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/groups?msg=更新しました&cat=success",
        status_code=303,
    )


# ════════════════════════════════════════════
# 現場マスタ管理
# ════════════════════════════════════════════

@router.get("/sites", response_class=HTMLResponse)
async def sites_page(
    request: Request,
    user: dict = Depends(require_admin),
    msg: str = "",
    cat: str = "success",
):
    db = await get_db()
    try:
        sites = await _get_sites(db, active_only=True)
        groups = await _get_groups(db, active_only=True)
        # 各現場のグループ別予算を取得
        site_gb_map = {}
        for s in sites:
            site_gb_map[s["site_id"]] = await _get_site_group_budgets(db, s["site_id"])
        # 各現場の月次履歴を取得（月次データ修正用）
        site_history_map: dict[int, list[dict]] = {}
        cur_hist = await db.execute(
            "SELECT * FROM cc_cumulative_history ORDER BY site_id, target_month"
        )
        for row in await cur_hist.fetchall():
            sid = row["site_id"]
            if sid not in site_history_map:
                site_history_map[sid] = []
            site_history_map[sid].append(dict(row))
    finally:
        await db.close()
    return _templates.TemplateResponse(request, "construction_cost/sites.html", {
        "user": user, "sites": sites, "groups": groups,
        "site_gb_map": site_gb_map, "site_history_map": site_history_map,
        "msg": msg, "cat": cat,
    })


@router.post("/sites/add")
async def sites_add(
    request: Request,
    user: dict = Depends(require_admin),
    site_name: str = Form(...),
    budget: float = Form(0),
    initial_cumulative_cost: float = Form(0),
    status: str = Form("進行中"),
):
    db = await get_db()
    try:
        # 自動採番: 既存の最大番号 + 1（SITE-0001 形式）
        cur = await db.execute(
            "SELECT site_code FROM cc_sites WHERE site_code LIKE 'SITE-%' ORDER BY site_code DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if row:
            try:
                last_num = int(row["site_code"].replace("SITE-", ""))
            except ValueError:
                last_num = 0
        else:
            last_num = 0
        # site_id ベースのフォールバック（既存データが SITE- 形式でない場合）
        cur_max = await db.execute("SELECT MAX(site_id) FROM cc_sites")
        max_id = (await cur_max.fetchone())[0] or 0
        next_num = max(last_num, max_id) + 1
        site_code = f"SITE-{next_num:04d}"

        await db.execute(
            """INSERT INTO cc_sites (site_code, site_name, budget, cumulative, initial_cumulative_cost, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (site_code, site_name.strip(), budget, initial_cumulative_cost, initial_cumulative_cost, status),
        )
        await db.commit()
    except Exception:
        ref = uuid.uuid4().hex[:8]
        logger.exception("現場登録エラー (ref=%s)", ref)
        return RedirectResponse(
            url=f"/construction-cost/sites?msg=登録に失敗しました（参照番号: {ref}）&cat=danger",
            status_code=303,
        )
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/sites?msg=現場を追加しました&cat=success",
        status_code=303,
    )


@router.post("/sites/update")
async def sites_update(
    request: Request,
    user: dict = Depends(require_admin),
    site_id: int = Form(...),
    site_name: str = Form(...),
    initial_cumulative_cost: float = Form(0),
    status: str = Form("進行中"),
):
    db = await get_db()
    try:
        form = await request.form()

        # ── グループ別予算 ──
        gb_groups = form.getlist("gb_group_name")
        gb_budgets = form.getlist("gb_budget")
        gb_initials = form.getlist("gb_initial")

        await db.execute("DELETE FROM cc_site_group_budgets WHERE site_id=?", (site_id,))
        for gn, gb, gi in zip(gb_groups, gb_budgets, gb_initials):
            gn = gn.strip()
            if not gn:
                continue
            await db.execute(
                """INSERT INTO cc_site_group_budgets (site_id, group_name, budget, initial_cumulative_cost)
                   VALUES (?, ?, ?, ?)""",
                (site_id, gn, float(gb or 0), float(gi or 0)),
            )

        # 予算合計 = グループ予算の合計
        cur_sum = await db.execute(
            "SELECT COALESCE(SUM(budget), 0) FROM cc_site_group_budgets WHERE site_id=?",
            (site_id,),
        )
        budget = (await cur_sum.fetchone())[0]

        # ── 月次履歴の修正（トランザクション内で連鎖再計算） ──
        hist_ids = form.getlist("hist_id")
        hist_costs = form.getlist("hist_monthly_cost")

        if hist_ids:
            # monthly_cost を更新
            for hid, hcost in zip(hist_ids, hist_costs):
                await db.execute(
                    "UPDATE cc_cumulative_history SET monthly_cost=? WHERE history_id=?",
                    (float(hcost or 0), int(hid)),
                )

            # この現場の全月を月順で取得し、累計を連鎖再計算
            cur_hist = await db.execute(
                "SELECT history_id, monthly_cost FROM cc_cumulative_history WHERE site_id=? ORDER BY target_month",
                (site_id,),
            )
            hist_rows = await cur_hist.fetchall()
            running = initial_cumulative_cost
            for h in hist_rows:
                before = running
                after = running + h["monthly_cost"]
                await db.execute(
                    "UPDATE cc_cumulative_history SET cumulative_before=?, cumulative_after=? WHERE history_id=?",
                    (before, after, h["history_id"]),
                )
                running = after

        # ── cumulative 再計算 ──
        cur = await db.execute(
            "SELECT COALESCE(SUM(monthly_cost), 0) FROM cc_cumulative_history WHERE site_id=?",
            (site_id,),
        )
        past_total = (await cur.fetchone())[0]
        new_cumulative = initial_cumulative_cost + past_total

        await db.execute(
            """UPDATE cc_sites
               SET site_name=?, budget=?, cumulative=?,
                   initial_cumulative_cost=?, status=?,
                   updated_at=datetime('now','localtime')
               WHERE site_id=?""",
            (site_name.strip(), budget, new_cumulative,
             initial_cumulative_cost, status, site_id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        ref = uuid.uuid4().hex[:8]
        logger.exception("現場更新エラー (site_id=%s, ref=%s)", site_id, ref)
        return RedirectResponse(
            url=f"/construction-cost/sites?msg=更新に失敗しました（参照番号: {ref}）&cat=danger",
            status_code=303,
        )
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/sites?msg=更新しました&cat=success",
        status_code=303,
    )


@router.post("/sites/delete")
async def sites_delete(
    request: Request,
    user: dict = Depends(require_admin),
    site_id: int = Form(...),
):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE cc_sites SET is_active=0, updated_at=datetime('now','localtime') WHERE site_id=?",
            (site_id,),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/sites?msg=無効化しました&cat=warning",
        status_code=303,
    )


# ════════════════════════════════════════════
# 作業員単価マスタ管理
# ════════════════════════════════════════════

@router.get("/workers", response_class=HTMLResponse)
async def workers_page(
    request: Request,
    user: dict = Depends(require_admin),
    msg: str = "",
    cat: str = "success",
):
    db = await get_db()
    try:
        workers = await _get_workers(db, active_only=True)
        groups = await _get_groups(db, active_only=True)
    finally:
        await db.close()
    return _templates.TemplateResponse(request, "construction_cost/workers.html", {
        "user": user, "workers": workers, "groups": groups, "msg": msg, "cat": cat,
    })


@router.post("/workers/add")
async def workers_add(
    request: Request,
    user: dict = Depends(require_admin),
    worker_name: str = Form(...),
    role: str = Form(""),
    group_name: str = Form(""),
    daily_rate: float = Form(0),
    overtime_rate: float = Form(0),
    night_rate: float = Form(0),
    transport: float = Form(0),
):
    db = await get_db()
    try:
        try:
            await db.execute(
                """INSERT INTO cc_workers (worker_name, role, group_name, daily_rate, overtime_rate, night_rate, transport)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (worker_name.strip(), role.strip(), group_name.strip() or None, daily_rate, overtime_rate, night_rate, transport),
            )
            await db.commit()
        except Exception:
            ref = uuid.uuid4().hex[:8]
            logger.exception("作業員登録エラー (ref=%s)", ref)
            return RedirectResponse(
                url=f"/construction-cost/workers?msg=登録に失敗しました（参照番号: {ref}）&cat=danger",
                status_code=303,
            )
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/workers?msg=作業員を追加しました&cat=success",
        status_code=303,
    )


@router.post("/workers/update")
async def workers_update(
    request: Request,
    user: dict = Depends(require_admin),
    worker_id: int = Form(...),
    worker_name: str = Form(...),
    role: str = Form(""),
    group_name: str = Form(""),
    daily_rate: float = Form(0),
    overtime_rate: float = Form(0),
    night_rate: float = Form(0),
    transport: float = Form(0),
):
    db = await get_db()
    try:
        await db.execute(
            """UPDATE cc_workers
               SET worker_name=?, role=?, group_name=?, daily_rate=?, overtime_rate=?, night_rate=?,
                   transport=?, updated_at=datetime('now','localtime')
               WHERE worker_id=?""",
            (worker_name.strip(), role.strip(), group_name.strip() or None, daily_rate, overtime_rate, night_rate, transport, worker_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/workers?msg=更新しました&cat=success",
        status_code=303,
    )


@router.post("/workers/delete")
async def workers_delete(
    request: Request,
    user: dict = Depends(require_admin),
    worker_id: int = Form(...),
):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE cc_workers SET is_active=0, updated_at=datetime('now','localtime') WHERE worker_id=?",
            (worker_id,),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(
        url="/construction-cost/workers?msg=無効化しました&cat=warning",
        status_code=303,
    )


# ════════════════════════════════════════════
# 入力用日報テンプレートダウンロード
# ════════════════════════════════════════════

@router.get("/template/download")
async def template_download(
    request: Request,
    user: dict = Depends(require_admin),
    target_month: str = "",
):
    """DBの最新マスタを反映したプルダウン付き日報テンプレートを生成・ダウンロードする。"""
    # クエリパラメータ target_month のバリデーション（YYYY-MM 形式以外は破棄）
    # build_template への伝搬と Content-Disposition への埋め込みを遮断する
    if target_month:
        try:
            datetime.strptime(target_month, "%Y-%m")
        except ValueError:
            target_month = ""

    db = await get_db()
    try:
        sites = await _get_sites(db, active_only=True)
        workers = await _get_workers(db, active_only=True)
    finally:
        await db.close()

    site_names = sorted(set(
        normalize_str(s["site_name"]) for s in sites if normalize_str(s["site_name"])
    ))
    worker_names = sorted(set(
        normalize_str(w["worker_name"]) for w in workers if normalize_str(w["worker_name"])
    ))

    buf = build_template(
        site_names=site_names,
        worker_names=worker_names,
        target_month=target_month,
    )

    filename = f"日報テンプレート_{target_month}.xlsx" if target_month else "日報テンプレート.xlsx"
    encoded = quote(filename)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"template.xlsx\"; filename*=UTF-8''{encoded}",
        },
    )


# ════════════════════════════════════════════
# 集計処理
# ════════════════════════════════════════════

@router.get("/aggregate", response_class=HTMLResponse)
async def aggregate_page(
    request: Request,
    user: dict = Depends(require_admin),
    msg: str = "",
    cat: str = "success",
):
    return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
        "user": user, "result": None, "log_id": None, "msg": msg, "cat": cat,
    })


@router.post("/aggregate", response_class=HTMLResponse)
async def aggregate_run(
    request: Request,
    user: dict = Depends(require_admin),
    target_month: str = Form(...),
    file: UploadFile = File(...),
):
    """日報Excelをアップロードして集計実行。"""
    db = await get_db()
    try:
        sites = await _get_sites(db, active_only=True)
        workers = await _get_workers(db, active_only=True)

        # 各現場の cumulative を initial_cumulative_cost + 過去確定分で再計算
        for s in sites:
            initial = float(s.get("initial_cumulative_cost", 0) or 0)
            cur_past = await db.execute(
                "SELECT COALESCE(SUM(monthly_cost), 0) FROM cc_cumulative_history WHERE site_id=? AND target_month < ?",
                (s["site_id"], target_month),
            )
            past_total = (await cur_past.fetchone())[0]
            s["cumulative"] = initial + past_total

        contents = await file.read()
        buf = BytesIO(contents)

        try:
            daily_df = read_daily_sheets(buf)
        except Exception as e:
            logger.exception("日報ファイル読み込みエラー")
            return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
                "user": user, "result": None, "log_id": None,
                "msg": f"日報ファイル読み込みエラー: {e}", "cat": "danger",
            })

        # 集計実行（機材引数なし）
        try:
            result = aggregate(daily_df, sites, workers, target_month)
        except Exception as e:
            logger.exception("集計処理エラー")
            return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
                "user": user, "result": None, "log_id": None,
                "msg": f"集計処理エラー: {e}", "cat": "danger",
            })

        out_dir = OUTPUT_BASE / target_month
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            site_path = write_site_cost_report(result, out_dir)
            worker_path = write_worker_summary(result, out_dir)
        except Exception as e:
            logger.exception("集計結果ファイル出力エラー")
            return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
                "user": user, "result": None, "log_id": None,
                "msg": f"集計結果ファイル出力エラー: {e}", "cat": "danger",
            })

        agg_json = json.dumps({
            "site_costs": [
                {
                    "site_name": sc.site_name,
                    "site_id": sc.site_id,
                    "monthly_cost": sc.monthly_cost,
                    "cumulative_before": sc.cumulative_before,
                    "cumulative_after": sc.cumulative_after,
                }
                for sc in result.site_costs
            ],
            "worker_summaries": [
                {
                    "worker_name": ws.worker_name,
                    "role": ws.role,
                    "group_name": ws.group_name,
                    "days_worked": ws.days_worked,
                    "total_basic": round(ws.total_basic, 2),
                    "total_overtime": round(ws.total_overtime, 2),
                    "labor_cost": round(ws.labor_cost),
                    "site_hours": {
                        site: {
                            "基本": round(h["基本"], 2),
                            "残業": round(h["残業"], 2),
                            "site_id": ws.site_id_map.get(site),
                        }
                        for site, h in ws.site_hours.items()
                    },
                }
                for ws in result.worker_summaries
            ],
        }, ensure_ascii=False)

        # 未確定データを DB に draft として永続化（サーバー再起動でも失われない）
        await db.execute(
            """INSERT INTO cc_process_log
               (target_month, file_name, status, aggregated_json, output_site, output_worker, processed_at)
               VALUES (?, ?, 'draft', ?, ?, ?, datetime('now','localtime'))""",
            (target_month, file.filename, agg_json, str(site_path), str(worker_path)),
        )
        await db.commit()
        cur_id = await db.execute("SELECT last_insert_rowid()")
        log_id = str((await cur_id.fetchone())[0])

        return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
            "user": user,
            "result": result,
            "log_id": log_id,
            "target_month": target_month,
            "site_path": site_path.name,
            "worker_path": worker_path.name,
            "msg": f"集計完了（{len(result.site_costs)}現場, {len(result.worker_summaries)}名）",
            "cat": "success",
        })
    except Exception as e:
        logger.exception("集計処理で予期しないエラーが発生しました")
        return _templates.TemplateResponse(request, "construction_cost/aggregate.html", {
            "user": user, "result": None, "log_id": None,
            "msg": f"予期しないエラーが発生しました: {e}", "cat": "danger",
        })
    finally:
        await db.close()


@router.post("/aggregate/confirm")
async def aggregate_confirm(
    request: Request,
    user: dict = Depends(require_admin),
    log_id: str = Form(...),
):
    """集計結果を確認後、累計金額を確定更新する。draft → confirmed に遷移。"""
    db = await get_db()
    try:
        # DB から draft 状態のレコードを取得
        cur = await db.execute(
            "SELECT log_id, target_month, file_name, aggregated_json, output_site, output_worker "
            "FROM cc_process_log WHERE log_id = ? AND status = 'draft'",
            (int(log_id),),
        )
        pending = await cur.fetchone()
        if not pending:
            return RedirectResponse(
                url="/construction-cost/aggregate?msg=集計データが見つかりません。ページを再読み込みするか、再度集計を実行してください。&cat=danger",
                status_code=303,
            )
        pending = dict(pending)

        agg_data = json.loads(pending["aggregated_json"])
        target_month = pending["target_month"]

        cur_all = await db.execute(
            "SELECT site_id, site_name, initial_cumulative_cost FROM cc_sites WHERE is_active = 1"
        )
        all_sites = [dict(r) for r in await cur_all.fetchall()]
        site_by_id = {s["site_id"]: s for s in all_sites}

        for sc in agg_data["site_costs"]:
            site_name = sc["site_name"]
            monthly_cost = sc["monthly_cost"]

            # site_id 必須（新形式JSON前提）
            json_site_id = sc.get("site_id")
            if not json_site_id or json_site_id not in site_by_id:
                logger.warning("累計更新スキップ（site_id 不明または非アクティブ）: %s (site_id=%s)", site_name, json_site_id)
                continue
            site_row = site_by_id[json_site_id]

            site_id = site_row["site_id"]
            initial = float(site_row.get("initial_cumulative_cost", 0) or 0)

            # ベース累計 = 初期累計額 + 過去確定分合計（当月より前）
            cur_past = await db.execute(
                "SELECT COALESCE(SUM(monthly_cost), 0) FROM cc_cumulative_history WHERE site_id=? AND target_month < ?",
                (site_id, target_month),
            )
            past_total = (await cur_past.fetchone())[0]
            cum_before = initial + past_total
            cum_after = cum_before + monthly_cost

            try:
                await db.execute(
                    """INSERT INTO cc_cumulative_history
                       (site_id, target_month, monthly_cost, cumulative_before, cumulative_after)
                       VALUES (?, ?, ?, ?, ?)""",
                    (site_id, target_month, monthly_cost, cum_before, cum_after),
                )
            except Exception:
                await db.rollback()
                return RedirectResponse(
                    url=f"/construction-cost/history?msg={target_month} は既に確定済みの現場があります&cat=danger",
                    status_code=303,
                )

            # cumulative = initial + 全確定分合計（当月含む）
            await db.execute(
                """UPDATE cc_sites SET cumulative=?, updated_at=datetime('now','localtime')
                   WHERE site_id=?""",
                (cum_after, site_id),
            )

        # draft → confirmed に更新（新規INSERT ではなく既存レコードを遷移）
        await db.execute(
            """UPDATE cc_process_log
               SET status = 'confirmed', confirmed_at = datetime('now','localtime')
               WHERE log_id = ?""",
            (pending["log_id"],),
        )
        await db.commit()

    finally:
        await db.close()

    return RedirectResponse(
        url=f"/construction-cost/history?msg={target_month} の累計を確定しました&cat=success",
        status_code=303,
    )


@router.get("/aggregate/download/{target_month}/{filename}")
async def aggregate_download(
    target_month: str,
    filename: str,
    user: dict = Depends(require_admin),
):
    """集計結果Excelのダウンロード。"""
    # safe_file_response がパス検証（OUTPUT_BASE 配下に収まっているか）と
    # 存在チェックをまとめて行い、範囲外・非存在いずれも 404 を返す
    file_path = OUTPUT_BASE / target_month / filename
    return safe_file_response(
        file_path,
        OUTPUT_BASE,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ════════════════════════════════════════════
# 処理履歴
# ════════════════════════════════════════════

@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    user: dict = Depends(get_current_user),
    msg: str = "",
    cat: str = "success",
):
    db = await get_db()
    try:
        # 現場名マップ（site_id → 表示名）
        cur_sites = await db.execute("SELECT site_id, site_name, site_code FROM cc_sites")
        site_name_map: dict[int, str] = {
            r["site_id"]: f"[{r['site_code']}] {r['site_name']}"
            for r in await cur_sites.fetchall()
        }

        # 後続月チェック用：確定済み (site_id, target_month) の全ペア
        cur_hist_months = await db.execute(
            "SELECT site_id, target_month FROM cc_cumulative_history"
        )
        confirmed_pairs: list[tuple[int, str]] = [
            (r["site_id"], r["target_month"]) for r in await cur_hist_months.fetchall()
        ]

        cur = await db.execute(
            "SELECT * FROM cc_process_log ORDER BY processed_at DESC"
        )
        raw_logs = [dict(r) for r in await cur.fetchall()]

        logs = []
        for log in raw_logs:
            # aggregated_json から対象 site_id・site_name を解決
            site_ids: list[int] = []
            site_names: list[str] = []
            if log.get("aggregated_json"):
                try:
                    data = json.loads(log["aggregated_json"])
                    site_ids = [
                        sc["site_id"]
                        for sc in data.get("site_costs", [])
                        if "site_id" in sc
                    ]
                    site_names = [
                        site_name_map.get(sid, f"(ID:{sid})") for sid in site_ids
                    ]
                except Exception:
                    pass

            # 後続月チェック：対象現場のいずれかに target_month より大きい確定月があるか
            tm = log.get("target_month", "")
            has_subsequent = any(
                hm > tm for (hsid, hm) in confirmed_pairs if hsid in site_ids
            )

            log["site_ids"] = site_ids
            log["site_names"] = site_names
            log["has_subsequent"] = has_subsequent
            logs.append(log)

        cur2 = await db.execute(
            """SELECT h.*, s.site_name, s.site_code
               FROM cc_cumulative_history h
               INNER JOIN cc_sites s ON h.site_id = s.site_id AND s.is_active = 1
               ORDER BY h.confirmed_at DESC"""
        )
        histories = [dict(r) for r in await cur2.fetchall()]
    finally:
        await db.close()

    return _templates.TemplateResponse(request, "construction_cost/history.html", {
        "user": user, "logs": logs, "histories": histories, "msg": msg, "cat": cat,
    })


@router.post("/history/{log_id}/rollback")
async def rollback_log(
    log_id: int,
    request: Request,
    user: dict = Depends(require_admin),
):
    """確定済み集計ログのロールバック（確定取り消し）。

    トランザクション内で以下を実行:
      1. cc_cumulative_history から対象月×対象現場レコードを削除
      2. cc_process_log から対象ログを削除
      3. cc_sites.cumulative を再計算して整合性を保つ
    """
    db = await get_db()
    try:
        # 対象ログを取得（確定済みのみ）
        cur = await db.execute(
            "SELECT * FROM cc_process_log WHERE log_id=? AND status='confirmed'",
            (log_id,),
        )
        log = await cur.fetchone()
        if not log:
            return RedirectResponse(
                url=f"/construction-cost/history?msg=ログ(ID:{log_id})が見つかりません&cat=danger",
                status_code=303,
            )

        log = dict(log)
        target_month = log["target_month"]

        # aggregated_json から対象 site_id を特定
        try:
            data = json.loads(log["aggregated_json"])
            site_ids = [
                sc["site_id"]
                for sc in data.get("site_costs", [])
                if "site_id" in sc
            ]
        except Exception:
            site_ids = []

        if not site_ids:
            return RedirectResponse(
                url=f"/construction-cost/history?msg=対象現場が特定できません(ログID:{log_id})&cat=danger",
                status_code=303,
            )

        placeholders = ",".join("?" * len(site_ids))
        try:
            # Step 1: cc_cumulative_history 削除（対象月 × 対象現場）
            await db.execute(
                f"DELETE FROM cc_cumulative_history WHERE target_month=? AND site_id IN ({placeholders})",
                [target_month] + site_ids,
            )

            # Step 2: cc_process_log 削除
            await db.execute(
                "DELETE FROM cc_process_log WHERE log_id=?",
                (log_id,),
            )

            # Step 3: cc_sites.cumulative を再計算（initial + 残存する monthly_cost の合計）
            for sid in site_ids:
                await db.execute(
                    """UPDATE cc_sites
                       SET cumulative = (
                           SELECT initial_cumulative_cost + COALESCE(SUM(h.monthly_cost), 0)
                           FROM cc_cumulative_history h
                           WHERE h.site_id = cc_sites.site_id
                       ),
                       updated_at = datetime('now','localtime')
                       WHERE site_id = ?""",
                    (sid,),
                )

            await db.commit()

        except Exception:
            await db.rollback()
            logger.exception("ロールバック処理中にエラー発生: log_id=%s", log_id)
            return RedirectResponse(
                url=f"/construction-cost/history?msg=取り消し処理に失敗しました(ログID:{log_id})&cat=danger",
                status_code=303,
            )
    finally:
        await db.close()

    return RedirectResponse(
        url=f"/construction-cost/history?msg={target_month}の集計(ログID:{log_id})を取り消しました&cat=success",
        status_code=303,
    )
