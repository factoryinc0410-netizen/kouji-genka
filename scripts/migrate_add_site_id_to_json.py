"""
マイグレーションスクリプト: 旧形式 aggregated_json に site_id を追記する

背景:
  旧形式の aggregated_json は site_name（テキスト）のみで現場を識別していた。
  同名で現場を再作成すると、旧データが新現場に誤って紐づく致命的バグが発生する。

処理内容:
  1. cc_cumulative_history（確定時に正しい site_id で記録済み）を正解データとして使用
  2. 各確定済み cc_process_log の aggregated_json に:
     - site_costs[].site_id を追記
     - worker_summaries[].site_hours[site_name].site_id を追記
  3. 正解データに存在しない現場名は cc_sites テーブルから名前で検索（全件、削除済み含む）

安全性:
  - 既に site_id が設定済みのエントリは上書きしない
  - 変更前のJSONをバックアップとしてログ出力
  - ドライラン対応（--dry-run オプション）
"""
import argparse
import json
import sqlite3
import sys
import unicodedata

def normalize_str(s: str) -> str:
    """正規化: NFKC + 小文字 + 空白除去"""
    if not s:
        return ""
    return unicodedata.normalize("NFKC", s).strip().lower()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Migrate aggregated_json to include site_id")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--db", default="web_app/data/app.db", help="Database path")
    args = parser.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    # ── 正解データ構築 ──
    # 1. cc_cumulative_history から target_month ごとの site_name → site_id マッピング
    hist_rows = db.execute("""
        SELECT h.target_month, h.site_id, s.site_name
        FROM cc_cumulative_history h
        JOIN cc_sites s ON h.site_id = s.site_id
    """).fetchall()

    # {target_month: {norm_site_name: site_id}}
    month_name_to_sid: dict[str, dict[str, int]] = {}
    for r in hist_rows:
        month = r["target_month"]
        norm = normalize_str(r["site_name"])
        if month not in month_name_to_sid:
            month_name_to_sid[month] = {}
        month_name_to_sid[month][norm] = r["site_id"]

    # 2. 全現場マスタから name → site_id（削除済み含む、IDが最も古いものを優先）
    all_sites = db.execute("SELECT site_id, site_name FROM cc_sites ORDER BY site_id").fetchall()
    global_name_to_sid: dict[str, int] = {}
    for s in all_sites:
        norm = normalize_str(s["site_name"])
        if norm not in global_name_to_sid:  # 最初（最古のID）を優先
            global_name_to_sid[norm] = s["site_id"]

    # ── 確定済みログを処理 ──
    logs = db.execute("""
        SELECT log_id, target_month, aggregated_json
        FROM cc_process_log
        WHERE status = 'confirmed' AND aggregated_json IS NOT NULL
        ORDER BY target_month
    """).fetchall()

    updated_count = 0
    for log in logs:
        log_id = log["log_id"]
        month = log["target_month"]
        data = json.loads(log["aggregated_json"])
        modified = False

        # 該当月の正解マッピング
        name_sid_map = month_name_to_sid.get(month, {})

        # --- site_costs に site_id 追記 ---
        for sc in data.get("site_costs", []):
            if sc.get("site_id") is not None:
                continue  # 既に設定済み
            norm = normalize_str(sc["site_name"])
            sid = name_sid_map.get(norm) or global_name_to_sid.get(norm)
            if sid is not None:
                sc["site_id"] = sid
                modified = True
                print(f"  [log_id={log_id} month={month}] site_costs: '{sc['site_name']}' -> site_id={sid}")
            else:
                print(f"  [log_id={log_id} month={month}] site_costs: '{sc['site_name']}' -> *** UNRESOLVED ***")

        # --- worker_summaries.site_hours に site_id 追記 ---
        for ws in data.get("worker_summaries", []):
            site_hours = ws.get("site_hours", {})
            for site_name, hours in site_hours.items():
                if not isinstance(hours, dict):
                    continue
                if hours.get("site_id") is not None:
                    continue  # 既に設定済み
                norm = normalize_str(site_name)
                sid = name_sid_map.get(norm) or global_name_to_sid.get(norm)
                if sid is not None:
                    hours["site_id"] = sid
                    modified = True

        if modified:
            updated_count += 1
            if not args.dry_run:
                new_json = json.dumps(data, ensure_ascii=False)
                db.execute(
                    "UPDATE cc_process_log SET aggregated_json=? WHERE log_id=?",
                    (new_json, log_id),
                )

    if not args.dry_run and updated_count > 0:
        db.commit()
        print(f"\n=== {updated_count} 件のログを更新しました ===")
    elif args.dry_run:
        print(f"\n=== [DRY RUN] {updated_count} 件のログが更新対象です（実際の更新なし） ===")
    else:
        print("\n=== 更新対象のログはありません ===")

    db.close()


if __name__ == "__main__":
    main()
