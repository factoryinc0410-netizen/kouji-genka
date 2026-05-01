"""
内訳書データモデル — extractor と nairaku_builder の橋渡し

Excel から抽出した内訳書データを構造化し、
ReportLab の Table コンポーネントへの変換メソッドを提供する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  数値フォーマットヘルパー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt_number(value: float | None, decimal: int = 0) -> str:
    """数値をカンマ区切り文字列に変換する。

    - None → ""
    - 負数 → "△ 1,234" （建設業界の慣例表記）
    - decimal=0 → 整数表示、decimal>0 → 小数点以下を維持

    Parameters
    ----------
    value : float | None
        フォーマット対象の数値。
    decimal : int
        小数点以下の桁数。0 なら整数に丸める。
    """
    if value is None:
        return ""
    if decimal > 0:
        abs_val = abs(value)
        formatted = f"{abs_val:,.{decimal}f}"
    else:
        int_val = int(math.floor(abs(value) + 0.5))
        formatted = f"{int_val:,}"
    if value < 0:
        return f"△ {formatted}"
    return formatted


def _fmt_qty(value: float | None) -> str:
    """数量フォーマット: 小数第1位まで表示。整数なら .0 を付ける。"""
    if value is None:
        return ""
    if value < 0:
        return f"△ {abs(value):,.1f}"
    return f"{value:,.1f}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  データクラス定義
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class NairakuHeaderInfo:
    """内訳書のヘッダ情報（タイトル下〜列ヘッダ上の領域）。"""
    koji_kenmei: str = ""           # 工事名
    contract_date: str = ""         # 契約年月日（和暦: '令和8年1月28日' 形式）
    kouki: str = ""                 # 工期（表示用文字列）

    # ── JV (共同企業体) 判定フラグ ──────────────────────────
    # Excel の「下請代金内訳書」シートから動的検出する:
    #   - is_jv=True  : (元請負人) 近傍に「(代表構成員)」ラベルが存在
    #   - jv_name     : (元請負人) 右隣セルが「特定建設工事共同企業体」を含む場合のみ
    # いずれも非 JV 時は False / 空文字。
    # テンプレート／スタンパはこれらで描画を切り替える。
    is_jv: bool = False
    jv_name: str = ""

    # 元請人
    # motouke_group_name は後方互換のため残置。新規コードは jv_name を参照すること。
    motouke_group_name: str = ""    # 共同企業体名（JV のときのみ値あり。jv_name と同一）
    motouke_address: str = ""       # 元請負人（JV のときは代表構成員）住所
    motouke_company: str = ""       # 元請負人（JV のときは代表構成員）商号又は名称
    motouke_name: str = ""          # 元請負人（JV のときは代表構成員）氏名
    # 下請負人
    shitauke_address: str = ""
    shitauke_company: str = ""
    shitauke_name: str = ""


@dataclass
class NairakuRow:
    """内訳書の1行（明細行・カテゴリ行・合計行・注記行・フッター値行）。

    row_type の種別:
      - "category"    : 工種（大分類ヘッダ、太字、数値なし）
      - "item"        : 明細行（数量・単価・金額あり）
      - "subtotal"    : 合計行（直接工事費計、純工事費計、下請金額 etc.）
      - "note"        : 純粋な注記文（※で始まる前置き文。colspan で1行描画）
      - "footer_item" : 値付きフッター行（労務費・法定福利費。A-O 列の item と同じ構造）
      - "spacer"      : Excel 上の意図的な空白行（Excelの余白設計を反映）
      - "pad"         : 【廃止予定】ページ末尾を埋めるためのパッド行。
                        v1.1.0 で強制パディングは廃止され、通常の抽出
                        パイプラインでは出力されない。外部フローとの
                        互換性のためのみ残置。
    """
    row_type: str = "item"
    indent: int = 0               # A列の先頭空白によるインデント段数 (0, 1, 2)

    # A〜D 列: テキスト項目
    koji_shu: str = ""            # A列: 工種
    shubetsu: str = ""            # B列: 種別
    saibetsu: str = ""            # C列: 細別・規格
    tani: str = ""                # D列: 単位

    # E 列: 元請契約数量
    motouke_suryo: float | None = None

    # F〜H 列: 当初（下請契約）
    suryo: float | None = None         # F: 数量
    tanka: float | None = None         # G: 単価
    kingaku: float | None = None       # H: 金額

    # I〜K 列: 変更
    henkou_suryo: float | None = None
    henkou_tanka: float | None = None
    henkou_kingaku: float | None = None

    # L〜N 列: 増減
    zougen_suryo: float | None = None
    zougen_tanka: float | None = None
    zougen_kingaku: float | None = None

    # O 列: 備考
    biko: str = ""

    is_bold: bool = False          # 太字フラグ（スタイル判定用）

    # ── セル結合情報（15 列分, Excel merged_cells の忠実再現） ────────────
    # Excel の ws.merged_cells.ranges から算出した水平方向の結合情報。
    # 各要素は該当列（0-indexed, A=0 … O=14）の状態を表す:
    #   - 1   : 結合なし（単独セル）
    #   - >=2 : このセルから右方向に n セル結合されている（colspan=n で描画）
    #   - 0   : 左隣のセルからの結合範囲に覆われており、描画不要（hidden）
    #
    # デフォルトは全列非結合 ([1]*15)。extractor は _compute_col_spans()
    # でこのリストを埋め、テンプレートは visible_cells() 経由で参照する。
    # 不変条件: sum(s for s in col_spans if s >= 1) == 15。
    col_spans: list[int] = field(default_factory=lambda: [1] * 15)

    def to_table_row(self, has_henkou: bool = True) -> list[str]:
        """テンプレート用の15要素セル配列に変換する。

        - None → 空文字
        - 金額・単価 → カンマ区切り整数（負数は △ 表記）
        - 数量 → 小数第1位まで（負数は △ 表記）
        - 返り値は常に **15 要素** [A, B, C, D, E, F, G, H, I, J, K, L, M, N, O]。
          has_henkou 引数は後方互換のため残すが、列数は常に 15 固定。
          Excel の A-O 列配置をそのまま保持し、空白セルは空文字として返す。

        Returns
        -------
        list[str]
            15要素 [A, B, C, D, E, F, G, H, I, J, K, L, M, N, O]
        """
        # Spacer / Pad 行: 全セル空文字で返す（行高さのみ保持）
        #   spacer = Excel 上の意図的空白行
        #   pad    = 57 行ページレイアウトを満たすためのフッター直前パッド
        # 15 列固定（Excel の A-O 列に対応）— has_henkou=False でも列は維持する。
        if self.row_type in ("spacer", "pad"):
            return [""] * 15

        # A列: 工種
        # koji_shu は extractor 側で Excel の先頭空白（全角 U+3000）を
        # そのまま保持している（`_cell_str_preserve`）。したがって
        # インデントを二重付与しないよう、ここでは追加処理をしない。
        a_text = self.koji_shu

        # 合計行・注記行では 0 値を抑制（表示上は空欄にする）
        suppress_zero = self.row_type in ("subtotal", "note")

        def _fq(v: float | None) -> str:
            if suppress_zero and v is not None and v == 0.0:
                return ""
            return _fmt_qty(v)

        def _fn(v: float | None) -> str:
            if suppress_zero and v is not None and v == 0.0:
                return ""
            return _fmt_number(v)

        # 15 要素固定で Excel の A-O 列配置を保持
        return [
            a_text,                                   # A: 工種
            self.shubetsu,                            # B: 種別
            self.saibetsu,                            # C: 細別・規格
            self.tani,                                # D: 単位
            _fq(self.motouke_suryo),                  # E: 元請契約数量
            _fq(self.suryo),                          # F: 当初数量
            _fn(self.tanka),                          # G: 当初単価
            _fn(self.kingaku),                        # H: 当初金額
            _fq(self.henkou_suryo),                   # I: 変更数量
            _fn(self.henkou_tanka),                   # J: 変更単価
            _fn(self.henkou_kingaku),                 # K: 変更金額
            _fq(self.zougen_suryo),                   # L: 増減数量
            _fn(self.zougen_tanka),                   # M: 増減単価
            _fn(self.zougen_kingaku),                 # N: 増減金額
            self.biko,                                # O: 備考
        ]

    def visible_cells(self, has_henkou: bool = True) -> list[dict]:
        """描画対象のセルだけを ``{"col", "value", "span"}`` で返す。

        Excel の結合情報 (``col_spans``) を反映し、結合で隠れるセル
        (``col_spans[i] == 0``) は結果から除外する。テンプレート側は
        この返り値をそのままループするだけで、colspan / display:none を
        意識せず忠実な結合を再現できる。

        Returns
        -------
        list[dict]
            描画対象セルのリスト。各要素は
            ``{"col": int(0..14), "value": str, "span": int(>=1)}``。
            順序は列インデックスの昇順。col_spans が未設定 or 要素数不足の
            場合は全 15 列を span=1 で返す（後方互換）。
        """
        values = self.to_table_row(has_henkou=has_henkou)
        result: list[dict] = []
        for i in range(15):
            span = self.col_spans[i] if i < len(self.col_spans) else 1
            if span == 0:
                # 左隣セルの結合範囲に覆われているセル。レンダリングしない。
                continue
            result.append({
                "col": i,
                "value": values[i],
                "span": span,
            })
        return result


@dataclass
class NairakuData:
    """内訳書の全データ。"""
    header: NairakuHeaderInfo = field(default_factory=NairakuHeaderInfo)
    rows: list[NairakuRow] = field(default_factory=list)
    has_henkou: bool = False        # 変更契約列 (I〜N) にデータがあるか
