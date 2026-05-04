"""
設定モジュール — フォルダパス定義・抽出マッピング・PDF座標・その他設定

パス設定は web_app.core.config（.env 経由）から一元管理される。
このモジュール内にハードコードされた絶対パスは存在しない。
"""
import os
from pathlib import Path

from web_app.core.config import COM_TEMP_DIR as COM_TEMP_DIR              # noqa: F401 — .env 経由
from web_app.core.config import FONT_PATH as _FONT_PATH                   # noqa: F401 — .env 経由
from web_app.core.config import LIBREOFFICE_PATH as LIBREOFFICE_PATH      # noqa: F401 — .env 経由
from web_app.core.config import LIBREOFFICE_TIMEOUT as LIBREOFFICE_TIMEOUT  # noqa: F401 — .env 経由

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  注文書作成スキル バージョン（UI表示・キャッシュバスティング用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 修正をリリースするたびに更新すること。
#
# バージョン命名規約（プロジェクト CLAUDE.md 参照）:
#   - セマンティック・バージョニング (MAJOR.MINOR.PATCH) に従う
#   - 末尾にハイフン繋ぎで変更内容を付記する
#   - スペース・括弧・日本語・大文字は使用禁止（URL 安全な ASCII 小文字のみ）
#   例: "1.3.0-flow-layout", "1.4.1-csv-fix"
#
# 参照先:
# - UI 右下のバッジに「注文書作成 v{ORDER_DOCS_VERSION}」として表示される
# - CSS/JS の読込 URL にも（CORE_VERSION と結合して）付与される
ORDER_DOCS_VERSION: str = "2.3.20-pypdf-deprecation-fix"

# 旧 APP_VERSION は後方互換のためエイリアスとして残置する。
# 新規コードでは必ず ORDER_DOCS_VERSION を参照すること。
APP_VERSION: str = ORDER_DOCS_VERSION

# ── スキルルート（このファイルの親ディレクトリ） ─────────────
SKILL_DIR = Path(__file__).resolve().parent

# ── プロジェクトルート（skills/ の親） ─────────────────────
ROOT_DIR = SKILL_DIR.parent.parent

# ── テンプレートフォルダ（スキル内包型） ───────────────────
FOLDER_TEMPLATE = SKILL_DIR / "templates"

# ── 旧CLI用フォルダ（Web版では未使用・互換性のために残す） ─
FOLDER_INTAKE   = ROOT_DIR / "01_受付"
FOLDER_DONE     = ROOT_DIR / "02_完成"
FOLDER_ERROR    = ROOT_DIR / "03_エラー"

# ── ログ設定 ────────────────────────────────────────────────
LOG_DIR  = ROOT_DIR / "logs"
LOG_FILE = LOG_DIR / "system.log"

# ── ファイル安定化待機（秒） ────────────────────────────────
FILE_STABLE_INTERVAL = 1.0   # ポーリング間隔
FILE_STABLE_TIMEOUT  = 30.0  # 最大待機時間

# ── watchdog 監視間隔（秒） ─────────────────────────────────
WATCH_INTERVAL = 1.0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 以下はプレースホルダー。実際の値を後から埋めていく。
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Excel マッピング（キーワード検索方式） ────────────────
# 行ズレに完全対応: A列・B列のキーワードで行番号を動的に特定する。
# 業者は「列単位（横並び）」で配置されている。
EXCEL_MAP: dict = {
    "sheet_name": "注文書作成依頼書",  # 対象シート名（部分一致で検索）

    # ── 共通項目のキーワード（A列またはB列から検索する文字列） ──
    # 見つかった行の C列（3列目）からデータを取得する
    "common_keywords": {
        "koji_kenmei": "【工事名】",
        "koji_basho":  "【工事場所】",
        "jv_name": "【共同企業体】", 
    },

    # ── 業者ごとの検索キーワード ──
    # これらのキーワードが見つかった行の、各業者の基準列からデータを取得する
    "vendor_keywords": {
        "vendor_company":      "【業者名】",
        "vendor_name":         "【代表者】",
        "vendor_address":      "【住　所】",
        "contract_date_header": "【契約日】",  # この下の行の「当初」行から日付を取得
        "kouki_header":        "【工期】",     # この下の行の「当初」行から工期を取得
        "kingaku_header":      "【発注金額】", # この下の「工事価格」「消費税」「合計」行を探す
        "henkou_kaisuu":       "【変更回数】", # Excelの表記が【】なしの「変更回数」であれば "変更回数" にしてください
    },

    # ── 業者データが存在する基準列（列番号, 1-indexed） ──
    # 1社目=C列(3), 2社目=E列(5), 3社目=G列(7), 4社目=I列(9), 5社目=K列(11)
    "vendor_base_cols": [3, 5, 7, 9, 11],

    # ── 金額サブキーワード ──
    # 「【発注金額】」の下にある行群から、以下の文字列を探して右隣列(+1)の数値を取得
    "kingaku_sub_keywords": {
        "kingaku_koji":  "工事価格",
        "kingaku_zei":   "消費税",
        "kingaku_ukeoi": "合計",
    },
}

# ── PDF スタンプ座標マッピング ──────────────────────────────
# 共通設定：.env の FONT_PATH から読み込み（デフォルト: Windows の MS 明朝）
MS_MINCHO = _FONT_PATH

# ── 注文書スタンプ座標（注文請書と共通） ──────────────────
# ukesho は chumonsho と同一テンプレート構造のため、座標を共有する。
# ここを修正すれば注文書・注文請書の両方に反映される。
_STAMP_CHUMONSHO: list[dict] = [
    # --- 契約日（明朝・中央配置） ---
    {"key": "contract_year",  "page": 0, "type": "rect", "rect": (599, 79, 621, 100), "size": 11, "align": 1, "fontpath": MS_MINCHO},
    {"key": "contract_month", "page": 0, "type": "rect", "rect": (646, 79, 668, 100), "size": 11, "align": 1, "fontpath": MS_MINCHO},
    {"key": "contract_day",   "page": 0, "type": "rect", "rect": (691, 79, 713, 100), "size": 11, "align": 1, "fontpath": MS_MINCHO},

    # --- 業者情報 ---
    {"key": "vendor_address", "page": 0, "type": "rect", "rect": (160, 124, 365, 138), "size": 10, "align": 0, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},
    {"key": "vendor_company", "page": 0, "type": "rect", "rect": (160, 137, 365, 154), "size": 12, "align": 0, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},
    {"key": "vendor_name",    "page": 0, "type": "rect", "rect": (160, 152, 365, 166), "size": 10, "align": 0, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},

    # --- 元請負人名称・代表構成員（座標は仮値 → プレビューで調整） ---
    {"key": "daikyo_koseiin",  "page": 0, "type": "point", "x": 560, "y": 119, "size": 10, "fontpath": MS_MINCHO},

    # --- 共同企業体名（座標は仮値 → プレビューで調整） ---
    {"key": "jv_name",         "page": 0, "type": "point", "x": 560, "y": 105.5, "size": 10, "fontpath": MS_MINCHO},

    # --- 工事内容 ---
    {"key": "koji_kenmei",    "page": 0, "type": "rect", "rect": (130, 359, 350, 394), "size": 11, "align": 0, "fontpath": MS_MINCHO},
    {"key": "koji_basho",     "page": 0, "type": "rect", "rect": (130, 418, 350, 436), "size": 10, "align": 0, "fontpath": MS_MINCHO},

    # --- 変更文言（金額欄の真上に印字。henkou_flag がある場合のみ使用） ---
    {"key": "henkou_flag", "page": 0, "type": "rect", "rect": (375, 279, 489, 295), "size": 10, "align": 1, "fontpath": MS_MINCHO},

    # --- 金額 ---
    {"key": "kingaku_ukeoi",  "page": 0, "type": "rect", "rect": (375, 328, 489, 350), "size": 14, "align": 1, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},
    {"key": "kingaku_koji",   "page": 0, "type": "rect", "rect": (375, 366, 489, 388), "size": 14, "align": 1, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},
    {"key": "kingaku_zei",    "page": 0, "type": "rect", "rect": (375, 415, 489, 437), "size": 14, "align": 1, "fontpath": MS_MINCHO, "is_bold": True, "bold_width": 0.003},

    # --- 工期（中央配置） ---
    {"key": "kouki_start_year",  "page": 0, "type": "rect", "rect": (155, 520, 183, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
    {"key": "kouki_start_month", "page": 0, "type": "rect", "rect": (183, 520, 211, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
    {"key": "kouki_start_day",   "page": 0, "type": "rect", "rect": (211, 520, 239, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
    {"key": "kouki_end_year",    "page": 0, "type": "rect", "rect": (266, 520, 294, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
    {"key": "kouki_end_month",   "page": 0, "type": "rect", "rect": (294, 520, 322, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
    {"key": "kouki_end_day",     "page": 0, "type": "rect", "rect": (322, 520, 350, 544), "size": 14, "align": 1, "fontpath": MS_MINCHO},
]
# 承諾書のスタンプ設定
_STAMP_SHODAKU: list[dict] = [
    # 契約年月日
    {"key": "contract_year",  "page": 0, "type": "rect", "rect": (389, 77, 409, 92),  "size": 11, "align": 1, "fontpath": MS_MINCHO},
    {"key": "contract_month", "page": 0, "type": "rect", "rect": (426, 77, 446, 92), "size": 11, "align": 1, "fontpath": MS_MINCHO},
    {"key": "contract_day",   "page": 0, "type": "rect", "rect": (465, 77, 485, 92), "size": 11, "align": 1, "fontpath": MS_MINCHO},
    
    # JV名・代表構成員（右下）
    {"key": "jv_name",         "page": 0, "type": "rect", "rect": (278, 577, 550, 597), "size": 11, "align": 0, "fontpath": MS_MINCHO},
    {"key": "daikyo_koseiin",  "page": 0, "type": "rect", "rect": (278, 597, 550, 617), "size": 11, "align": 0, "fontpath": MS_MINCHO},

    # 下請業者住所
    {"key": "vendor_address",  "page": 0, "type": "rect", "rect": (278, 690, 550, 710), "size": 12, "align": 0, "fontpath": MS_MINCHO},
    
    # 下請業者名（3箇所）
    {"key": "vendor_company",  "page": 0, "type": "rect", "rect": (200, 173, 329, 196), "size": 12, "align": 1, "fontpath": MS_MINCHO}, # 1箇所目
    {"key": "vendor_company",  "page": 0, "type": "rect", "rect": (332, 534, 461, 557), "size": 12, "align": 1, "fontpath": MS_MINCHO}, # 2箇所目
    {"key": "vendor_company",  "page": 0, "type": "rect", "rect": (278, 670, 550, 690), "size": 12, "align": 0, "fontpath": MS_MINCHO}, # 3箇所目
]

PDF_STAMP_MAP: dict[str, list[dict]] = {
    "shodaku":   _STAMP_SHODAKU,
    "chumonsho": _STAMP_CHUMONSHO,
    "ukesho":    _STAMP_CHUMONSHO,  # 注文書と同一座標を共有
    "shinkyuu": [
        # --- 新旧対照表 (Page 1, rotation=90)・契約日（中央配置） ---
        {"key": "contract_year",  "page": 1, "type": "rect", "rect": (71, 472, 91, 487), "size": 10, "align": 1, "fontpath": MS_MINCHO},
        {"key": "contract_month", "page": 1, "type": "rect", "rect": (100, 472, 120, 487), "size": 10, "align": 1, "fontpath": MS_MINCHO},
        {"key": "contract_day",   "page": 1, "type": "rect", "rect": (127, 472, 147, 487), "size": 10, "align": 1, "fontpath": MS_MINCHO},

        # JV名
        {"key": "jv_name",        "page": 1, "type": "rect", "rect": (403, 457, 545, 473), "size": 9, "align": 0, "fontpath": MS_MINCHO},

        # 下請負人セクション
        {"key": "vendor_address", "page": 1, "type": "rect", "rect": (403, 537, 545, 553), "size": 9,  "align": 0, "fontpath": MS_MINCHO},
        {"key": "vendor_company", "page": 1, "type": "rect", "rect": (403, 555, 545, 572), "size": 11, "align": 0, "fontpath": MS_MINCHO},
        {"key": "vendor_name",    "page": 1, "type": "rect", "rect": (403, 573, 545, 588), "size": 10, "align": 0, "fontpath": MS_MINCHO},
    ],
}

# ── PDFテンプレートファイル名（04_テンプレート 内） ────────
PDF_TEMPLATES: dict[str, str] = {
    "shodaku":    "承諾書（空ファイル）.pdf",
    "chumonsho":  "注文書（空ファイル）.pdf",                # スタンプ用・注文書
    "ukesho":     "注文請書（空ファイル）.pdf",              # スタンプ用・注文請書
    "shinkyuu":   "③新旧対照表（個別工事下請契約約款）.pdf", # スタンプ用・新旧対照表
    "yakkan":     "020528　下請契約約款　Ｒ2.4月～.pdf",    # 合冊に挟む約款（そのまま使用）
    "joken":      "契約条件書（空ファイル）.pdf",            # スタンプ用・契約条件書
}

# ── 契約条件書スタンプ座標（Route A: PDFスタンプ方式） ──────
# テンプレート「契約条件書（空ファイル）.pdf」は 595.2×842.0 pt (A4)。
#
# 座標の求め方（将来の調整用メモ）:
#   1. デバッグモード: 出力ファイル名に「プレビュー」を含めると rect が赤枠で可視化される
#   2. PDF座標系: 左上原点、右方向が +x、下方向が +y（単位: pt, 1pt = 1/72 inch）
#
# 署名欄の構造（PDF テンプレート実測値）:
#   左側（元請）: ラベル A列 x≈53, 値 B列 x≈119
#     「商号又は名称」y≈763  → 値は (119, 763) 付近
#     「住　　所」    y≈788  → 値は (119, 788) 付近
#     「氏　　名」    y≈801  → 値は (119, 801) 付近
#   右側（下請）: ラベル D列 x≈337, 値 E列 x≈395
#     「商号又は名称」y≈763  → 値は (395, 760) 付近
#     「住　　所」    y≈788  → 値は (395, 786) 付近
#     「氏　　名」    y≈801  → 値は (395, 798) 付近
_STAMP_JOKEN: list[dict] = [
    # --- 工事名（「工事名：」ラベル右に印字） ---
    {"key": "koji_kenmei", "page": 0, "type": "rect",
     "rect": (107, 40, 340, 54), "size": 10, "align": 0, "fontpath": MS_MINCHO},

    # --- 現場代理人（「現場代理人」ラベル右に印字） ---
    {"key": "joken_genba_dairinin", "page": 0, "type": "rect",
     "rect": (425, 40, 543, 54), "size": 10, "align": 0, "fontpath": MS_MINCHO},

    # --- 左側（元請）署名欄 ---
    {"key": "joken_left_company", "page": 0, "type": "rect",
     "rect": (110, 760, 330, 775), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    {"key": "joken_left_address", "page": 0, "type": "rect",
     "rect": (110, 786, 330, 799), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    {"key": "joken_left_name", "page": 0, "type": "rect",
     "rect": (110, 798, 330, 812), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    # --- 右側（下請）署名欄 ---
    {"key": "joken_right_company", "page": 0, "type": "rect",
     "rect": (395, 760, 543, 775), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    {"key": "joken_right_address", "page": 0, "type": "rect",
     "rect": (395, 786, 543, 799), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    {"key": "joken_right_name", "page": 0, "type": "rect",
     "rect": (395, 798, 543, 812), "size": 9, "align": 0, "fontpath": MS_MINCHO},

    # --- 備考エリア（備考ヘッダ下、表の右端列） ---
    # 備考は複数行になりうるため、rect を十分な高さで定義
    {"key": "joken_biko", "page": 0, "type": "rect",
     "rect": (446, 88, 543, 615), "size": 7, "align": 0, "fontpath": MS_MINCHO},
]

PDF_STAMP_MAP["joken"] = _STAMP_JOKEN

# ── 契約条件書チェックボックス座標テーブル ──────────────────
# テンプレートPDFには「デフォルトのチェック状態」が画像として焼き付け済み。
# ここでは、デフォルト状態と異なるチェックを上書きする場合の座標を定義する。
#
# 構造: Excel行番号(1-indexed) → PDF Y座標の対応表
# 元方列: x ≈ 370.0 pt / 下請列: x ≈ 398.0 pt
# チェックマーク描画サイズ: 約 8pt
#
# 全55行分の完全な座標定義は、プレビュー機能で実測後に順次追加する。
# 現時点では代表的な行のみ定義する（将来の拡張用テンプレート）。
JOKEN_CHECKBOX_CONFIG: dict = {
    # チェックマーク描画用の共通設定
    "check_char": "✔",           # チェック済みに使う文字
    "check_size": 10,            # チェック文字のフォントサイズ (pt)
    "box_size": 9.0,             # 白塗りつぶし矩形の一辺 (pt)
    "fontpath": MS_MINCHO,

    # 元方列・下請列の X 座標（チェックマーク中心）
    "motokata_x": 370.0,
    "shitauke_x": 398.0,

    # Excel 行番号 (1-indexed) → PDF の Y 座標（チェックボックス上端）
    # テンプレート PDF のテキスト位置から実測した値
    "row_y_map": {
        # --- 1.測量関係費 ---
        6:  89.3,    # 基本測量
        7:  101.3,   # 境界測量
        8:  113.3,   # 現場・丁張測量
        # --- 2.安全関係費 ---
        10: 137.3,   # 工事看板
        11: 149.3,   # 標示板・バリケード
        12: 161.3,   # 仮設電気
        13: 173.3,   # 交通整理員
        14: 185.3,   # 足場設置費
        # --- 3.現場事務所，仮設電力費 ---
        15: 197.3,   # 地代（現場事務所）
        16: 209.3,   # 地代（資材置場等）
        17: 221.3,   # 敷地造成費・復旧費
        18: 233.3,   # 現場事務所設置
        19: 245.3,   # 仮設ハウス備品
        20: 257.3,   # トイレ
        21: 269.3,   # 電話・電気・水道引込撤去
        22: 281.3,   # 電話・電気・水道使用料
        # --- 4.管理費用 ---
        24: 305.3,   # 出来形管理測定
        25: 317.3,   # 出来形管理書類作成
        26: 329.3,   # 品質管理測定
        27: 341.3,   # 品質管理書類作成
        28: 353.3,   # 写真撮影
        29: 365.3,   # 写真管理
        30: 377.3,   # 竣工検査書類作成
        # --- 5.現場環境改善費用 ---
        32: 401.3,   # 材料費
        33: 413.3,   # 設置撤去費
        # --- 6.その他費用 ---
        35: 437.3,   # 諸官庁申請書類作成
        36: 449.3,   # 諸官庁申請費用
        37: 461.3,   # 材料検査費用
        38: 473.3,   # 試験施工費用
        39: 485.3,   # 地下埋設・地上施設調査費
        40: 497.3,   # 地元挨拶，説明会費用
        41: 509.3,   # 会計検査費用
        # --- 6.その他費用（追加行） ---
        42: 521.3,   # （セクション区切り行 — VML制御存在）
        # --- 7.別途協議事項 ---
        43: 533.3,   # 近隣対策，補償費
        44: 545.3,   # 産業廃棄物処理費
        45: 557.3,   # 公害対策費
        46: 569.3,   # （セクション区切り行 — VML制御存在）
        # --- 8.その他 ---
        48: 593.3,   # 出来形及び品質管理基準
        49: 605.2,   # 安全教育訓練への参加
        # --- 9.下請契約の合意形成等 ---
        52: 641.3,   # 下請契約の金額の合意形成
        54: 665.3,   # 見積書を尊重し下請け契約を締結する
        55: 677.3,   # 建設キャリアアップシステムの事業登録
        # --- 署名欄付近 ---
        59: 725.3,   # （署名欄上部）
        60: 737.3,   # ※以上の条件で契約致します（ﾚ点の部分）
    },
}

# ── 合冊順序定義 ─────────────────────────────────────────
# 一時フォルダ内のファイル名サフィックスで順序を制御する。
# 注文書セット・注文請書セットそれぞれに適用。
# キー: 論理名（一時ファイルの命名に使用）
MERGE_ORDER_CHUMONSHO: list[str] = [
    "shodaku",          # 1. スタンプ済み承諾書
    "chumonsho",        # 2. スタンプ済み注文書
    "yakkan",           # 3. 約款PDF
    "shinkyuu",         # 4. 新旧対照表PDF
    "nairaku",          # 5. 内訳書PDF
    "joken",            # 6. 契約条件書PDF
]

MERGE_ORDER_UKESHO: list[str] = [
    "shodaku",          # 1. スタンプ済み承諾書（当初のみ）
    "ukesho",           # 2. スタンプ済み注文請書    
    "yakkan",           # 3. 約款PDF
    "shinkyuu",         # 4. 新旧対照表PDF
    "nairaku",          # 5. 内訳書PDF
    "joken",            # 6. 契約条件書PDF
]

# ── 一時作業フォルダ（デフォルト — CLI 使用時のフォールバック用） ──
# Web ワーカーからはジョブ ID 付きの動的パスが渡されるため、この値は使われない。
DEFAULT_WORK_TMP_DIR = Path(__file__).resolve().parent / "_work_tmp"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書 PDF 動的生成レイアウト設定 (ReportLab Platypus)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 単位はすべてミリメートル (mm)。
# nairaku_builder.py 側で mm→pt (×2.8346) に変換して使用する。
#
# 列の定義順:
#   A:工種 B:種別 C:細別 D:単位 E:元請数量
#   F:当初数量 G:当初単価 H:当初金額
#   I:変更数量 J:変更単価 K:変更金額
#   L:増減数量 M:増減単価 N:増減金額
#   O:備考

NAIRAKU_LAYOUT: dict = {
    # ── 用紙・マージン ──
    # A4横 (297×210mm)。マージンは印刷領域を最大化しつつ余白を確保。
    "page_size": "A4_LANDSCAPE",  # (297mm × 210mm)
    "margin_top_mm":     10.0,
    "margin_bottom_mm":   8.0,
    "margin_left_mm":     8.0,
    "margin_right_mm":    8.0,

    # ── 列幅 (mm) ──
    # has_henkou=True (15列): 変更・増減列を含むフルレイアウト
    # 合計 = 281mm (= 297 - 8 - 8)
    "col_widths_full_mm": [
        22.0,   # A: 工種
        34.0,   # B: 種別
        34.0,   # C: 細別・規格
        8.0,    # D: 単位
        14.0,   # E: 元請契約 数量
        14.0,   # F: 当初 数量
        15.0,   # G: 当初 単価
        20.0,   # H: 当初 金額
        14.0,   # I: 変更 数量
        15.0,   # J: 変更 単価
        20.0,   # K: 変更 金額
        14.0,   # L: 増減 数量
        15.0,   # M: 増減 単価
        20.0,   # N: 増減 金額
        22.0,   # O: 備考
    ],

    # has_henkou=False (9列): 当初契約のみ。余剰幅を A/B/C/O に再配分。
    # 合計 = 281mm
    "col_widths_base_mm": [
        30.0,   # A: 工種
        48.0,   # B: 種別
        48.0,   # C: 細別・規格
        10.0,   # D: 単位
        17.0,   # E: 元請契約 数量
        17.0,   # F: 当初 数量
        18.0,   # G: 当初 単価
        25.0,   # H: 当初 金額
        68.0,   # O: 備考
    ],

    # ── フォントサイズ (pt) ──
    "font_size_title":     14,    # タイトル「下請代金内訳書」
    "font_size_header":     8,    # ヘッダ情報（工事名等）
    "font_size_col_head":   7,    # 列ヘッダ（工種・数量 等）
    "font_size_data":       7,    # データ行
    "font_size_subtotal":   7,    # 合計行
    "font_size_note":       6.5,  # 注記行

    # ── 行高さ (mm) ──
    "row_height_col_head_mm":  5.0,   # 列ヘッダ行
    "row_height_data_mm":      4.5,   # データ行
    "row_height_subtotal_mm":  5.0,   # 合計行
    "row_height_category_mm":  5.0,   # カテゴリ行

    # ── ヘッダ（1ページ目のみ）テーブルの列幅 (mm) ──
    # 左ラベル / 左値 / 中ラベル / 中値 / 右ラベル / 右値
    "header_table_col_widths_mm": [18.0, 80.0, 18.0, 50.0, 18.0, 97.0],

    # ── 罫線（タスク4 修正: 黒 0.5pt 以上で必ず全行まで描画） ──
    "grid_line_width":     0.5,   # 通常罫線の太さ (pt) ※0.5pt 以上を強制
    "outer_line_width":    0.9,   # 外枠線の太さ (pt)
    "subtotal_line_width": 0.8,   # 合計行上辺の太さ (pt)

    # ── 列ヘッダー構造 ──
    # repeatRows に使用する列ヘッダーの行数。現行デザインは 3 段固定。
    "col_header_rows": 3,

    # ── フォント ──
    # ReportLab に登録する論理フォント名。実体ファイルは FONT_FALLBACKS を参照。
    "font_name": "NairakuMincho",

    # ── 列ヘッダー SPAN 定義 ──
    # 各エントリ: {"text": 表示テキスト, "start": (col,row), "end": (col,row)}
    # ReportLab の SPAN は左上セルの内容のみ描画するため、text は start セルに配置する。
    # start/end は 0-origin、row は列ヘッダー内のローカル行番号（0,1,2）。
    # text が "" の要素は SPAN のみ張る（結合だけ）。
    #
    # 列定義 (has_henkou=True / 15列):
    #   0:工種 1:種別 2:細別 3:単位 4:元請契約
    #   5〜13: 下請契約 (当初 F-G-H / 変更 I-J-K / 増減 L-M-N)
    #   14:備考
    "col_header_spans_full": [
        # Row0 (大見出し)
        {"text": "工種",       "start": (0, 0),  "end": (0, 2)},
        {"text": "種別",       "start": (1, 0),  "end": (1, 2)},
        {"text": "細別・規格", "start": (2, 0),  "end": (2, 2)},
        {"text": "単位",       "start": (3, 0),  "end": (3, 2)},
        {"text": "元請<br/>契約", "start": (4, 0), "end": (4, 1)},
        {"text": "下請契約",   "start": (5, 0),  "end": (13, 0)},
        {"text": "備考",       "start": (14, 0), "end": (14, 2)},
        # Row1 (中見出し)
        {"text": "当初",         "start": (5, 1),  "end": (7, 1)},
        {"text": "変更金額",     "start": (8, 1),  "end": (10, 1)},
        {"text": "変更(増減)",   "start": (11, 1), "end": (13, 1)},
    ],
    # Row2 (末端ヘッダー: SPAN なしでそのまま描画する列ラベル)
    "col_header_row2_full": [
        "", "", "", "",
        "数量",            # 4: 元請契約 数量
        "数量", "単価", "金額",   # 5-7: 当初
        "数量", "単価", "金額",   # 8-10: 変更
        "数量", "単価", "金額",   # 11-13: 増減
        "",
    ],

    # 列定義 (has_henkou=False / 9列):
    #   0:工種 1:種別 2:細別 3:単位 4:元請契約
    #   5〜7: 下請契約(当初) F-G-H
    #   8:備考
    "col_header_spans_base": [
        {"text": "工種",       "start": (0, 0), "end": (0, 2)},
        {"text": "種別",       "start": (1, 0), "end": (1, 2)},
        {"text": "細別・規格", "start": (2, 0), "end": (2, 2)},
        {"text": "単位",       "start": (3, 0), "end": (3, 2)},
        {"text": "元請<br/>契約", "start": (4, 0), "end": (4, 1)},
        {"text": "下請契約",   "start": (5, 0), "end": (7, 0)},
        {"text": "備考",       "start": (8, 0), "end": (8, 2)},
        {"text": "当初",       "start": (5, 1), "end": (7, 1)},
    ],
    "col_header_row2_base": [
        "", "", "", "",
        "数量",
        "数量", "単価", "金額",
        "",
    ],

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 動的レイアウト・自動縮小設定（v2.1 新規）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # セル単位自動縮小の下限フォントサイズ (pt)。
    # この値まで縮小された場合は Warning ログを出力する。
    "auto_shrink_min_size": 5.0,

    # セル内の padding (pt): 自動縮小時に col_width - padding を収容幅として扱う
    "auto_shrink_padding_pt": 2.0,

    # 自動縮小時の安全マージン係数（stringWidth 実測値より少し小さめにする）
    "auto_shrink_safety_factor": 0.98,

    # 中間空白を NBSP (U+00A0) に変換して Paragraph で詰められないようにするか
    "preserve_whitespace": True,

    # ── ヘッダーテーブル動的列幅 ──
    # ラベル列（0,2,4: 工事名 / (元請人) / (下請負人)）の最小幅 (mm)
    "header_label_min_mm": 13.0,
    # 値列（1,3,5: 工事情報 / 元請情報 / 下請情報）の最小幅 (mm)
    "header_value_min_mm": 28.0,

    # ── データ表動的列幅 ──
    # テキスト列の最小幅 (mm)。需要が不足してもこの値を下回らない。
    "data_text_col_min_mm": {
        "A": 16.0,   # 工種
        "B": 22.0,   # 種別
        "C": 22.0,   # 細別・規格
        "O": 16.0,   # 備考
    },
    # C 列（細別・規格）への傾斜配分ウェイト（確定事項 #2）
    "data_col_c_weight": 1.5,

    # ── Spacer 行（空白行） ──
    # spacer 行の行高さ (mm)。Excel の意図的な余白を再現。
    "row_height_spacer_mm": 4.5,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BaseBuilder 既定値（全帳票共通のデフォルト）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BaseBuilder を継承する各ビルダが共通で参照する既定値。
# 個別帳票のレイアウト (NAIRAKU_LAYOUT, TERMS_LAYOUT) 側でオーバーライド可能。
BASE_BUILDER_DEFAULTS: dict = {
    # フォント
    "font_name": "BaseMincho",

    # 自動縮小
    "auto_shrink_min_size": 5.0,
    "auto_shrink_padding_pt": 2.0,
    "auto_shrink_safety_factor": 0.98,

    # 空白保持
    "preserve_whitespace": True,

    # 罫線（タスク4 修正: 色=colors.black、太さは 0.5pt 以上を強制）
    "grid_line_width": 0.5,
    "outer_line_width": 0.8,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約条件書 PDF 動的生成レイアウト設定 (ReportLab Platypus)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TERMS_LAYOUT: dict = {
    # ── 用紙・マージン ──
    "page_size":         "A4_PORTRAIT",  # A4 縦
    "margin_top_mm":     12.0,
    "margin_bottom_mm":   8.0,
    "margin_left_mm":    15.0,
    "margin_right_mm":   15.0,

    # ── フォントサイズ (pt) ──
    "font_size_title":      14.0,
    "font_size_header":      9.0,
    "font_size_col_head":    8.5,
    "font_size_section":     9.0,   # セクションタイトル（A列 1.測量関係費 等）
    "font_size_item":        8.5,   # 明細行
    "font_size_biko":        7.5,   # 備考
    "font_size_sign_label":  8.5,
    "font_size_sign_value":  9.5,

    # ── 列幅 (mm) — 合計 180mm（A4 portrait 210mm - 左右 15mm × 2） ──
    # レイアウト: [A=大分類, B=（空/padding）, C+D=明細ラベル, E=元方, F=下請, G=備考]
    #            idx:   0         1                2              3       4       5
    "col_widths_mm": [
        27.0,   # A: 大分類
        3.0,    # B: 余白
        78.0,   # C+D: 明細（merge 結果）
        15.0,   # E: 元方 ✓
        15.0,   # F: 下請 ✓
        42.0,   # G: 備考
    ],

    # ── 行高さ (mm) ──
    "row_height_header_mm":  6.0,
    "row_height_section_mm": 6.0,
    "row_height_item_mm":    5.0,
    "row_height_sign_mm":    6.5,

    # ── チェックマーク ──
    # 抽出した ✓ を視認性の高い記号に置換
    "check_mark_char":   "\u2611",   # ☑ (U+2611: BALLOT BOX WITH CHECK)
    "uncheck_mark_char": "\u2610",   # ☐ (U+2610: BALLOT BOX)

    # ── サインブロック ──
    # 元請（左）と下請（右）の列幅 (mm)
    "signblock_left_label_mm":   22.0,
    "signblock_left_value_mm":   68.0,
    "signblock_right_label_mm":  22.0,
    "signblock_right_value_mm":  68.0,

    # ── 罫線（タスク4 修正: 色=black、0.5pt 以上） ──
    "grid_line_width":  0.5,
    "outer_line_width": 0.8,

    # ── 自動縮小 ──
    "auto_shrink_min_size":       5.0,
    "auto_shrink_padding_pt":     2.0,
    "auto_shrink_safety_factor":  0.98,
    "preserve_whitespace":        True,

    # ── KeepInFrame（1ページ強制） ──
    "keep_in_frame_mode": "shrink",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  フォントフォールバックチェーン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# nairaku_builder._register_fonts() がこのリストを先頭から順に試行する。
# すべて失敗した場合、最終手段として ReportLab 内蔵 HeiseiMin-W3 CID フォントを使う
# （この最終フォールバックはコード側で担保する）。
#
# 新しいフォントを追加する場合は、TTF/TTC のフルパスをこのリストに追加するだけでよい。
# サーバー環境によってインストール済みフォントが異なるため、
# 複数候補を登録しておくことで起動失敗を防ぐ。
#
# v2.3.18: OS 別にチェーンを切り替え、さくらVPS等の Linux サーバでも
# 日本語フォントを確実に検出できるようにした。MS_MINCHO は .env の FONT_PATH
# 経由で OS によらず最優先候補として尊重される（未指定なら空文字なのでスキップ）。
if os.name == "nt":
    # ── Windows ──
    FONT_FALLBACKS: list[Path] = [
        MS_MINCHO,                                       # 第一候補: .env の FONT_PATH（既定: MS明朝 TTC）
        Path(r"C:\Windows\Fonts\yumin.ttf"),             # 第二候補: 游明朝
        Path(r"C:\Windows\Fonts\meiryo.ttc"),            # 第三候補: メイリオ
        Path(r"C:\Windows\Fonts\msgothic.ttc"),          # 第四候補: MSゴシック
    ]
else:
    # ── Linux / macOS ──
    # Debian/Ubuntu パッケージ:
    #   apt install fonts-noto-cjk fonts-ipafont fonts-ipaexfont
    # macOS は標準でヒラギノが /System/Library/Fonts に入っている。
    FONT_FALLBACKS: list[Path] = [
        MS_MINCHO,  # 第一候補: .env の FONT_PATH（Linux でも明示指定があれば最優先）
        # ── Noto CJK (Linux 標準: fonts-noto-cjk) ──
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc"),   # RHEL 系
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),    # RHEL 系
        # ── IPA / IPAex フォント (Linux: fonts-ipafont, fonts-ipaexfont) ──
        Path("/usr/share/fonts/opentype/ipaexfont-mincho/ipaexm.ttf"),
        Path("/usr/share/fonts/opentype/ipafont-mincho/ipam.ttf"),
        Path("/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf"),
        Path("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"),
        # ── macOS（ヒラギノ） ──
        Path("/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Excel スキャン上限（抽出系マジックナンバーの一元管理）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 依頼書の構造が変わり、より大きな範囲を走査する必要が出てきた場合は
# この辞書を更新するだけで extractor 側の挙動を変更できる。
EXCEL_SCAN_LIMITS: dict = {
    # シート全体スキャン（業者検出・データ検出用）
    "sheet_scan_max_row": 70,
    "sheet_scan_max_col": 15,

    # 小スキャン（キーワード検索などヘッダー領域のみ）
    "header_scan_max_row": 100,

    # 内訳書（nairaku）シート専用
    "nairaku_max_row_fallback": 120,   # ws.max_row が None のときの上限
    "nairaku_header_scan_rows": 10,    # 上部ヘッダー情報の走査行数
    "nairaku_keyword_scan_rows": 200,  # 工事名等キーワード検索範囲

    # ベンダー基準列検出時の走査範囲
    "vendor_header_max_scan_row": 30,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書: 動的疑似結合 (Dynamic Pseudo-Merge) の設定
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 下請代金内訳書の A 列（工種）・B 列（種別）・C 列（細別規格）について、
# Excel 側で結合されていなくても「内容の有無と文字数」から HTML 側で
# 擬似的に colspan を張る補正機能。隣接する空セルに長い文字がはみ出して
# 見える現象を防ぐ。
#
# NAIRAKU_AUTO_MERGE_THRESHOLD:
#   動的疑似結合が発動する「長い」の判定しきい値（strip 後の文字数）。
#   3 ルールすべてで「対象セルの文字数 >= この値」が発動の必須条件であり、
#   これを下回る短いテキストでは一切結合せず独立セルとして罫線を残す。
#
#     ルール (a): len(A.strip()) >= threshold AND B空 AND C空 → [3,0,0]
#     ルール (b): len(A.strip()) >= threshold AND B空 AND C有 → [2,0,1]
#     ルール (c): len(B.strip()) >= threshold AND C空           → [1,2,0]
#
#   既定値 8 は「工種名・種別名が 7 文字以下であれば固定列幅に収まり、
#   右隣のセルに視覚的にはみ出さない」という経験則に基づく。
#   運用で調整が必要になれば、6〜10 の範囲で変更するのが安全。
NAIRAKU_AUTO_MERGE_THRESHOLD: int = 15
