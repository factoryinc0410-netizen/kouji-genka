"""契約条件書データモデル — extractor と terms_builder の橋渡し

Excel から抽出した契約条件書データを構造化し、
セクション（大分類）→項目（明細行）の入れ子構造で保持する。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  明細行（項目）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TermsItem:
    """契約条件書の1明細行。

    Attributes
    ----------
    excel_row : int
        Excel 上の元行番号（デバッグ・トレース用）。
    label : str
        明細ラベル（C:D merged セル or B:D merged セルの値）。
    motokata_checked : bool
        元方列 (E) に ✓ が入っているか。
    shitauke_checked : bool
        下請列 (F) に ✓ が入っているか。
    biko : str
        備考欄 (G列) のテキスト。
    layout : str
        "std"    : 通常レイアウト (C:D=ラベル, E=元方, F=下請, G=備考)
        "wide"   : セクション9 用 (B:D=ラベル, E:F=チェック, G=備考)
        "single" : 単一行セクション (セクション10) — A列大分類＋E/F チェック
    """
    excel_row: int = 0
    label: str = ""
    motokata_checked: bool = False
    shitauke_checked: bool = False
    biko: str = ""
    layout: str = "std"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  セクション（大分類）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TermsSection:
    """契約条件書の大分類セクション（例: 「1.測量関係費」）。"""
    number: int = 0                            # 1〜10
    title: str = ""                            # A列の値
    items: list[TermsItem] = field(default_factory=list)
    layout: str = "std"                        # "std" / "wide" / "single"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  サインブロック（元請/下請情報）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TermsParty:
    """契約当事者情報（元請/下請共通構造）。"""
    address: str = ""
    company: str = ""
    name: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  契約条件書全体
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TermsData:
    """契約条件書の全データ。"""
    # ── ヘッダ情報 ──
    koji_kenmei: str = ""                      # 工事名
    genba_dairinin: str = ""                   # 現場代理人氏名

    # ── 項目テーブル（10 セクション） ──
    sections: list[TermsSection] = field(default_factory=list)

    # ── 注記行 ──
    note_line: str = "※以上の条件で契約致します。(ﾚ点の部分）"

    # ── サインブロック ──
    # 「（元請負人）」ラベル行（B61:D61 → 共同企業体名）
    motouke_group_name: str = ""
    # 「（代表構成員）」ラベル（A62）
    daikyo_koseiin_label: str = "（代表構成員）"

    # 左側（元請負人）の住所・商号・氏名
    motouke: TermsParty = field(default_factory=TermsParty)
    # 右側（下請負人）の住所・商号・氏名
    shitauke: TermsParty = field(default_factory=TermsParty)

    # ── トレース用 ──
    source_sheet: str = ""
