"""
工事日報集計スキル — 集計ロジック

集計の流れ:
  1. 日別データ（1日〜31日シート）から現場別・作業員別の労務時間を集計
     → 作業員単価マスタと突き合わせて人件費を算出
  2. 現場別に人件費 = 当月支払を算出

※ 機材・経費の集計は行わない（労務費のみ）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from skills.construction_cost.config import BASIC_HOURS_LIMIT
from skills.construction_cost.reader import normalize_str

logger = logging.getLogger("skills.construction_cost.aggregator")


@dataclass
class SiteCostRow:
    """現場別原価管理表の1行"""
    site_name: str
    site_id: int | None = None
    budget: float = 0.0
    cumulative_before: float = 0.0
    labor_cost: float = 0.0
    monthly_cost: float = 0.0
    cumulative_after: float = 0.0
    remaining: float = 0.0


@dataclass
class WorkerSummaryRow:
    """個人別集計表の1行"""
    worker_name: str
    role: str = ""
    group_name: str = ""
    site_hours: dict = field(default_factory=dict)  # {norm_site: {基本, 残業}}
    site_id_map: dict = field(default_factory=dict)  # {norm_site: site_id}
    total_basic: float = 0.0
    total_overtime: float = 0.0
    days_worked: int = 0
    labor_cost: float = 0.0


@dataclass
class AggregationResult:
    """集計結果"""
    target_month: str
    site_costs: list[SiteCostRow] = field(default_factory=list)
    worker_summaries: list[WorkerSummaryRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _build_normalized_dict(items: list[dict], key_field: str) -> dict[str, dict]:
    """マスタリストを {正規化キー: 元のdict} の辞書に変換する。"""
    result = {}
    for item in items:
        raw_key = item.get(key_field, "")
        norm_key = normalize_str(raw_key)
        if norm_key:
            result[norm_key] = item
    return result


def _fuzzy_lookup(lookup_dict: dict[str, dict], raw_name: str) -> dict | None:
    """正規化名で辞書を検索する。完全一致 → 前方一致 → 部分一致 の順。"""
    norm = normalize_str(raw_name)
    if not norm:
        return None
    if norm in lookup_dict:
        return lookup_dict[norm]
    for key, val in lookup_dict.items():
        if key.startswith(norm) or norm.startswith(key):
            return val
    return None


def aggregate(
    daily_df: pd.DataFrame,
    sites: list[dict],
    workers: list[dict],
    target_month: str,
) -> AggregationResult:
    """日報データとマスタを突き合わせて集計する。

    Args:
        daily_df:  日別シートの統合DataFrame
                   列: [日付, 作業員名, 現場名, 基本, 残業, ...]
        sites:     現場マスタ [{site_name, budget, cumulative, ...}]
        workers:   作業員マスタ [{worker_name, role, daily_rate, overtime_rate, ...}]
        target_month: 対象年月 "YYYY-MM"
    """
    result = AggregationResult(target_month=target_month)

    # ── マスタの正規化辞書を構築 ──────────────────────────
    worker_rate = _build_normalized_dict(workers, "worker_name")
    site_map = _build_normalized_dict(sites, "site_name")

    # norm_site → site_id マッピング
    norm_to_site_id: dict[str, int] = {}
    for item in sites:
        norm_key = normalize_str(item.get("site_name", ""))
        if norm_key and "site_id" in item:
            norm_to_site_id[norm_key] = item["site_id"]

    logger.info("=== 集計開始: %s ===", target_month)
    logger.info("マスタ: 現場 %d件, 作業員 %d名", len(site_map), len(worker_rate))

    # ===================================================
    # 人��費の集計（日別シートのみ）
    # ===================================================
    site_labor: dict[str, float] = {}
    worker_data: dict[str, WorkerSummaryRow] = {}
    unmatched_workers: set[str] = set()

    if not daily_df.empty:
        for _, row in daily_df.iterrows():
            name = str(row["作業員名"])
            site = str(row["現場名"])
            norm_name = normalize_str(name)
            norm_site = normalize_str(site)

            if not norm_name or not norm_site:
                continue

            basic = float(row.get("基本", 0) or 0)
            overtime = float(row.get("残業", 0) or 0)

            # 作業員マスタ照合
            rate_info = worker_rate.get(norm_name)
            if rate_info is None:
                rate_info = _fuzzy_lookup(worker_rate, name)

            if rate_info is None:
                if norm_name not in unmatched_workers:
                    result.errors.append(f"作業員マスタ未登録: 「{name}」")
                    logger.warning("  [未登録] 作業員 '%s' がマスタに見つかりません", name)
                    unmatched_workers.add(norm_name)
                daily_rate = 0.0
                ot_rate = 0.0
                transport = 0.0
                role = ""
                group_name = ""
            else:
                daily_rate = float(rate_info.get("daily_rate", 0) or 0)
                ot_rate = float(rate_info.get("overtime_rate", 0) or 0)
                transport = float(rate_info.get("transport", 0) or 0)
                role = rate_info.get("role", "") or ""
                group_name = rate_info.get("group_name", "") or ""

            # 人件費 = (基本時間÷7.5)×日当 + 残業時間×残業単価 + 交通費
            # ÷7.5 の按分で複数現場入りでも日当が二重計算されない
            day_cost = (basic / BASIC_HOURS_LIMIT * daily_rate) + (overtime * ot_rate) + transport

            site_labor[norm_site] = site_labor.get(norm_site, 0) + day_cost

            # 作業員別集計
            if norm_name not in worker_data:
                worker_data[norm_name] = WorkerSummaryRow(worker_name=name, role=role, group_name=group_name)
            ws = worker_data[norm_name]
            if norm_site not in ws.site_hours:
                ws.site_hours[norm_site] = {"基本": 0.0, "残業": 0.0}
                # norm_site → site_id マッピングを記録
                sid = norm_to_site_id.get(norm_site)
                if sid is None:
                    # fuzzy lookup のフォールバック
                    master = _fuzzy_lookup(site_map, norm_site)
                    if master and "site_id" in master:
                        sid = master["site_id"]
                if sid is not None:
                    ws.site_id_map[norm_site] = sid
            ws.site_hours[norm_site]["基本"] += basic
            ws.site_hours[norm_site]["残業"] += overtime
            ws.total_basic += basic
            ws.total_overtime += overtime
            ws.labor_cost += day_cost

        # 出勤日数
        for norm_name, ws in worker_data.items():
            mask = daily_df["作業員名"].apply(lambda x, n=norm_name: normalize_str(x) == n)
            ws.days_worked = int(daily_df.loc[mask, "日付"].nunique())

    logger.info("人件費集計完了: 現場 %d件, 作業員 %d名", len(site_labor), len(worker_data))
    for site, cost in sorted(site_labor.items()):
        logger.info("  [人件費] %s = %,.0f 円", site, cost)

    # ===================================================
    # 現場別原価管理表の組み立て
    # ===================================================
    all_sites_in_data = set(site_labor.keys())

    for norm_site in sorted(all_sites_in_data):
        labor = site_labor.get(norm_site, 0)
        monthly = labor

        master = site_map.get(norm_site)
        if master is None:
            master = _fuzzy_lookup(site_map, norm_site)

        if master:
            display_name = master.get("site_name", norm_site)
            budget = float(master.get("budget", 0) or 0)
            cum_before = float(master.get("cumulative", 0) or 0)
        else:
            display_name = norm_site
            budget = 0.0
            cum_before = 0.0
            result.errors.append(f"現場マスタ未登録: 「{norm_site}」")
            logger.warning("  [未登録] 現場 '%s' がマスタに見つかりません", norm_site)

        cum_after = cum_before + monthly
        remaining = budget - cum_after

        result.site_costs.append(SiteCostRow(
            site_name=display_name,
            site_id=norm_to_site_id.get(norm_site),
            budget=budget,
            cumulative_before=cum_before,
            labor_cost=labor,
            monthly_cost=monthly,
            cumulative_after=cum_after,
            remaining=remaining,
        ))

        logger.info("  [現場] %s: 人件費=%,.0f | 累計 %,.0f → %,.0f",
                     display_name, labor, cum_before, cum_after)

    result.worker_summaries = sorted(
        worker_data.values(),
        key=lambda w: (w.group_name or "zzz", w.worker_name),
    )

    logger.info("=== 集計完了 ===")
    logger.info("  現場: %d件, 作業員: %d名, 警告: %d件",
                len(result.site_costs), len(result.worker_summaries), len(result.errors))

    return result
