"""
工事日報集計スキル — 設定
"""
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path("web_app/outputs/construction_cost")

# 日報Excel シート読み込み設定
DAILY_SHEET_HEADER_ROW = 0      # 0-indexed: 1行目がヘッダ
DAILY_SHEET_SUFFIX = "日"

# 日別シートの列名（8列構成）
DAILY_COLUMNS = ["作業員名", "現場名", "開始時間", "終了時間", "休憩時間", "潜水作業", "船舶作業", "備考欄"]

# 基本時間の上限（これを超えた分が残業）
BASIC_HOURS_LIMIT = 7.5

# 除外キーワード（現場名に含まれていたら除外）
EXCLUDE_SITE_KEYWORDS = ["休日", "有休", "欠勤"]
