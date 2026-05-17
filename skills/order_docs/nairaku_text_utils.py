"""
内訳書 (nairaku) と契約条件書 (joken) のテキスト解析ユーティリティ。

extractor.py から切り出した、Excel I/O を伴わない純粋な
テキスト判定・正規化ロジックを集約する。

ここに置かれる関数は以下の条件を満たす:
  - openpyxl の Worksheet / Workbook を引数に取らない
  - モジュール外部の可変状態 (グローバル変数, I/O) に依存しない
  - 入力値のみで結果が決まる

外部からは `from skills.order_docs.nairaku_text_utils import _is_subtotal_text`
のように直接 import する。
"""
from __future__ import annotations

from .extractor_utils import _normalize


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  シート種別判定キーワード
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_NAIRAKU_KEYWORDS = ["内訳書", "内訳"]
_JOKEN_KEYWORDS = ["契約条件書", "条件書", "契約条件"]


def _classify_sheet_type(sheet_name: str) -> str | None:
    """
    シート名から種別（"nairaku" / "joken"）を判定する。
    判定できない場合は None。
    """
    norm = _normalize(sheet_name)
    # 条件書を先に判定（「条件」は「内訳」と重複しないため順序は自由）
    for kw in _JOKEN_KEYWORDS:
        if _normalize(kw) in norm:
            return "joken"
    for kw in _NAIRAKU_KEYWORDS:
        if _normalize(kw) in norm:
            return "nairaku"
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書セクションキーワード
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 内訳書の合計セクションで使われるキーワード ──
# これらのキーワードを **部分文字列として含む** 行は subtotal として扱う。
# 比較は `_is_subtotal_text` で `_normalize(kw) in _normalize(text)` の形で
# 行うため、以下を自動的に吸収する:
#   - 全半角の揺れ（NFKC 正規化）
#   - 空白の有無（全角/半角/タブ/改行/ゼロ幅まで一括除去）
#   - 全角丸括弧と半角丸括弧の差
# このため「諸経費」だけを登録すれば「諸経費計」「諸経費小計」も自動で
# マッチするが、本番データのラベル揺れに対する **意図のドキュメント**
# として、実在する変種は冗長でも明示的に列挙する。
_NAIRAKU_SUBTOTAL_KEYWORDS: list[str] = [
    "直接工事費",
    "共通仮設費",
    "純工事費",
    "諸経費",                # 本番頻出（v2.7.2 で追加）
    "諸経費計",
    "諸経費小計",
    "現場管理費",
    "法定福利費",
    "工事原価",
    "一般管理費",
    "工事価格",
    "消費税",
    "消費税額",              # 本番頻出（prod v2.4.3 で先行追加 → dev へ取り込み）
    "消費税相当額",          # 本番頻出（v2.7.2 で追加）
    "地方消費税額",          # 本番頻出（prod v2.4.3 で先行追加 → dev へ取り込み）
    "合計",                  # 本番頻出（v2.7.2 で追加）
    "合計額",
    "下請金額",
    "下請代金",
]

# ── 注記セクションのキーワード ──
_NAIRAKU_NOTE_KEYWORDS: list[str] = [
    "注記",
    "労務費",
    "法廷福利費",      # Excelの原文ママ（「法定」の誤字対応）
    "法定福利費",
]

# ── フッター終端キーワード ──
# 内訳書の最終行には必ず下記のフッター項目が記載される想定（ユーザー要件）:
#   ・「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」
#   ・「労務費」
#   ・「法定福利費」  ← これが最後
# このうち「法定福利費」を含む行を検出したら、その行を抽出対象の最終行として
# 処理し、以降の行読み込みを完全に停止（break）する。
# 注: 「法定福利費」は主表内の合計行（subtotal）としても出現するため、
#     チェックは必ず data_end（下請金額行）より下でのみ行うこと。
_NAIRAKU_FOOTER_TERMINATORS: list[str] = [
    "法定福利費",
]

# ── 単一セル「複合フッター」終端キーワード ──
# Excelの版によっては、フッターが 1 つのセルにまとめて記載されるケースがある。
# 例: 「【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、
#         労務費、法定福利費】」
# この種のセルは row_num > data_end の制約なしに検出しても誤マッチしにくい
# （下請金額 / 労務費 / 法定福利費 の 3 語が同一セル内に必ず揃う）ため、
# 下記「3 語すべて含有」を条件とした検出を行い、検出行を最終行として出力した
# 直後に抽出ループを break する。
_NAIRAKU_COMPOSITE_FOOTER_REQUIRED: list[str] = [
    "下請金額",
    "労務費",
    "法定福利費",
]

# ── 列ヘッダ行を検出するキーワード ──
# 内訳書の列ヘッダは以下の 3 段構造（Excel 実測）:
#   Row N  : 「工種 / 種別 / 細別・規格 / 単位 / 元請契約 / 下請契約 / 備考」
#   Row N+1: 「当初 / 変更金額 / 変更(増減)」  ← 中見出し
#   Row N+2: 「数量 / 数量 / 単価 / 金額 / ...」← 末端ヘッダ
# 「工種」「種別」だけでは Row N までしか検出できず、
# Row N+1 / Row N+2 がデータ領域に含まれてしまうため、
# 「数量」（Row N+2 の col E に必ず存在）をキーワードに含めて
# ヘッダ領域の最終行を正しく特定する。
_NAIRAKU_HEADER_KEYWORDS: list[str] = ["工種", "種別", "数量"]

# ── 内訳書の1ページあたり行数（旧仕様・参考値のみ）──
# A4 横 + 12mm 余白 + thead 3行 + 本体 7.5pt + padding 1pt/2pt で、
# 1ページに約 57 行の明細が収まる設計。
# ※ v1.1.0 以降: 強制パディングは廃止（動的行数に移行）。
#   この定数は互換性のため残しているが、apply_nairaku_page_padding()
#   の既定引数以外では参照されない。
NAIRAKU_ROWS_PER_PAGE = 57


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  フッター検出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _normalize_for_footer(text: str) -> str:
    """フッター検出用の緩い正規化。

    `_normalize` に加えて、全角括弧類と記号を取り除き、表記ゆれに
    より強い部分一致マッチを可能にする。
    """
    norm = _normalize(text)
    # 括弧・記号は検出ノイズになりやすいため除去
    for ch in ("【", "】", "『", "』", "（", "）", "(", ")",
               "「", "」", "※", "、", ",", "。", ".", " ", "　"):
        norm = norm.replace(ch, "")
    return norm


def _is_composite_footer_text(text: str) -> bool:
    """単一セルに「下請金額+労務費+法定福利費」を列挙した複合フッターか判定する。

    判定条件（すべて満たす場合に True）:
      1. セル内に「下請金額」の部分文字列が含まれる
      2. セル内に「労務費」が 2 回以上出現
      3. セル内に「法定福利費」が 2 回以上出現

    想定する対象テキスト:
      「【下請金額に含まれる労務費及び法定福利費（事業者負担分）について、
        労務費、法定福利費】」
    この形式はラベル内で「労務費」「法定福利費」がそれぞれ 2 回登場する
    （前半の説明文と末尾の列挙）という特徴を持つ。

    旧式の前置き文（1 回のみ出現するもの、例:
      「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」）
    は本判定では False となり、従来どおり次行以降のフッター 2 行
    （労務費 / 法定福利費）の抽出が継続される。
    """
    if not text:
        return False
    norm = _normalize_for_footer(text)
    if _normalize_for_footer("下請金額") not in norm:
        return False
    return (
        norm.count(_normalize_for_footer("労務費")) >= 2
        and norm.count(_normalize_for_footer("法定福利費")) >= 2
    )


def _row_contains_composite_footer(*cell_texts: str | None) -> bool:
    """行内のいずれかのセルに複合フッター文字列が含まれるか判定する。"""
    for t in cell_texts:
        if t and _is_composite_footer_text(t):
            return True
    return False


def _is_footer_terminator(text: str) -> bool:
    """テキストがフッター終端キーワードを含むか判定する。

    フッターは 3 行構成:
      1. 「※下請金額に含まれる労務費及び法定福利費（事業者負担分）について」（前置き文）
      2. 「労務費」                （値行）
      3. 「法定福利費」             （値行 ← 終端）
    前置き文にも「法定福利費」の文字が含まれるが、そこで break すると
    後続の 2 行が読み飛ばされてしまうため、前置きマーカーを持つ行は
    終端と判定しない。

    主表領域（下請金額の前）での subtotal 「法定福利費」との誤マッチを避けるため、
    呼び出し側で row_num > data_end のガードも必ず行うこと。
    """
    if not text:
        return False
    # 前置き文（「※…について」形式）を除外する
    if "※" in text or "について" in text:
        return False
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in _NAIRAKU_FOOTER_TERMINATORS)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  行種別判定
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _count_indent(text: str) -> int:
    """先頭の全角スペース (U+3000) の数をインデント段数として返す。"""
    count = 0
    for ch in text:
        if ch == "　":
            count += 1
        else:
            break
    return count


def _is_subtotal_text(text: str) -> bool:
    """テキストが合計行のキーワードを **部分一致** で含むか判定する。

    重要仕様:
      - 完全一致ではなく ``_normalize(kw) in _normalize(text)`` の in 判定。
      - 比較対象は両辺ともに `_normalize` 通過後の文字列なので、以下の
        表記揺れに対して堅牢:
          * NFKC 正規化 → 全角英数/カタカナ等を半角化
          * 空白系（半角・全角・タブ・改行・NBSP・ゼロ幅）を一括除去
          * 全角丸括弧/隅付き括弧を半角化
      - 例: 「 合計 」「合　計」「合計額」「（合計）」はいずれも
        キーワード「合計」にマッチする。
      - 例: 「諸経費小計」「諸経費計」はキーワード「諸経費」に
        マッチする（部分一致のため）。

    新しいキーワードを追加するときは `_NAIRAKU_SUBTOTAL_KEYWORDS` を
    更新するだけでよい。判定ロジック側の変更は不要。
    """
    if not text:
        return False
    norm = _normalize(text)
    if not norm:
        return False
    return any(_normalize(kw) in norm for kw in _NAIRAKU_SUBTOTAL_KEYWORDS)


def _is_note_text(text: str) -> bool:
    """テキストが注記行のキーワードを含むか判定する。"""
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in _NAIRAKU_NOTE_KEYWORDS)
