"""
内訳書 PDF 動的生成モジュール — ReportLab Platypus ベース (BaseBuilder 継承版)

NairakuData を受け取り、以下の構造の PDF を生成する:
  - 1ページ目のみ: タイトル + ヘッダー情報（工事名, 元請/下請情報）
  - 全ページ共通:   列ヘッダー（3段構成、repeatRows で自動繰り返し）
  - データ行:       category / item / subtotal / note を row_type で描き分け
  - 自動改ページ:   Platypus が行数に応じて自動処理

タスク4 の主要修正:
  - BaseBuilder を継承し、フォント登録・自動縮小・空白保持・make_paragraph/make_table
    などの共通処理を基盤層に委譲。
  - GRID 罫線バグを修正: (0,0)→(-1,-1) 範囲で colors.black、grid_line_width を 0.5pt
    以上で必ず最終行まで描画する。

列幅・フォントサイズ・罫線太さなどの設定値は config.NAIRAKU_LAYOUT に
ミリメートル単位で一元管理されている。
"""
from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph

from . import config
from .base_builder import BaseBuilder
from .nairaku_models import NairakuData, NairakuRow

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NairakuBuilder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NairakuBuilder(BaseBuilder):
    """内訳書専用ビルダ（A4 横、複数ページ対応）。"""

    PAGE_SIZE = A4
    IS_LANDSCAPE = True
    DOC_TITLE = "下請代金内訳書"
    LOGICAL_FONT_NAME = "NairakuMincho"

    def __init__(
        self,
        output_path: Path,
        font_path: str | None = None,
    ):
        layout = config.NAIRAKU_LAYOUT
        self.MARGIN_MM = {
            "top":    layout["margin_top_mm"],
            "bottom": layout["margin_bottom_mm"],
            "left":   layout["margin_left_mm"],
            "right":  layout["margin_right_mm"],
        }
        self.DEFAULT_FONT_SIZE = layout.get("font_size_data", 7.0)

        super().__init__(output_path, font_path)

        # 内訳書固有スタイルで base styles を上書き/拡張
        self.styles.update(self._make_nairaku_styles())

        # 内訳書固有の自動縮小パラメータ（config 側に個別設定があれば優先）
        self._shrink_config["min_size"] = layout.get(
            "auto_shrink_min_size", self._shrink_config["min_size"]
        )
        self._shrink_config["padding_pt"] = layout.get(
            "auto_shrink_padding_pt", self._shrink_config["padding_pt"]
        )
        self._shrink_config["safety"] = layout.get(
            "auto_shrink_safety_factor", self._shrink_config["safety"]
        )
        self._shrink_config["preserve_ws"] = layout.get(
            "preserve_whitespace", self._shrink_config["preserve_ws"]
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # スタイル生成
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _make_nairaku_styles(self) -> dict[str, ParagraphStyle]:
        """内訳書用 ParagraphStyle セット。"""
        layout = config.NAIRAKU_LAYOUT
        fn = self.font_name

        def _ps(name, *, size, align=0, leading_add=2, space_after=0):
            return ParagraphStyle(
                name,
                fontName=fn,
                fontSize=size,
                alignment=align,
                leading=size + leading_add,
                wordWrap="CJK",
                spaceAfter=space_after,
            )

        return {
            "title":          _ps("nairaku_title", size=layout["font_size_title"],
                                  align=1, space_after=2 * mm),
            "header_label":   _ps("nairaku_header_label", size=layout["font_size_header"]),
            "header_value":   _ps("nairaku_header_value", size=layout["font_size_header"]),
            "col_head":       _ps("nairaku_col_head", size=layout["font_size_col_head"], align=1),
            "data_left":      _ps("nairaku_data_left", size=layout["font_size_data"], align=0),
            "data_center":    _ps("nairaku_data_center", size=layout["font_size_data"], align=1),
            "data_right":     _ps("nairaku_data_right", size=layout["font_size_data"], align=2),
            "subtotal_left":  _ps("nairaku_sub_left", size=layout["font_size_subtotal"], align=0),
            "subtotal_right": _ps("nairaku_sub_right", size=layout["font_size_subtotal"], align=2),
            "note":           _ps("nairaku_note", size=layout["font_size_note"], align=0),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 列幅算出
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_col_widths_static(
        self,
        has_henkou: bool,
        available_width_pt: float,
    ) -> list[float]:
        """config の mm 定義を available_width_pt に正規化した列幅 (pt)。"""
        layout = config.NAIRAKU_LAYOUT
        key = "col_widths_full_mm" if has_henkou else "col_widths_base_mm"
        widths_mm: list[float] = layout[key]

        total_mm = sum(widths_mm)
        if total_mm <= 0:
            n = len(widths_mm)
            return [available_width_pt / n] * n

        scale = available_width_pt / (total_mm * mm)
        return [w * mm * scale for w in widths_mm]

    def _compute_col_widths_dynamic(
        self,
        has_henkou: bool,
        available_width_pt: float,
        data_rows: list[NairakuRow],
    ) -> list[float]:
        """データ表の列幅を動的に算出する。

        方針:
          - 数値列 (E〜N) と D(単位) は config 値を保持（絶対固定）
          - テキスト列 A/B/C/O は需要に応じて拡張
          - C 列（細別・規格）は需要に data_col_c_weight (=1.5) のウェイト
          - テキスト列合計 = available - 固定列合計
          - 必要幅が残余を超える場合は config 値そのまま（→ セル単位の自動縮小に委ねる）
        """
        layout = config.NAIRAKU_LAYOUT
        base_widths = self._compute_col_widths_static(has_henkou, available_width_pt)
        font_name = self.font_name

        if has_henkou:
            text_col_keys = [("A", 0), ("B", 1), ("C", 2), ("O", 14)]
        else:
            text_col_keys = [("A", 0), ("B", 1), ("C", 2), ("O", 8)]

        text_cols = [idx for _, idx in text_col_keys]
        min_mm = layout["data_text_col_min_mm"]
        min_widths_pt = {idx: min_mm[key] * mm for key, idx in text_col_keys}

        padding = 4.0
        demand: dict[int, float] = {idx: 0.0 for idx in text_cols}

        size_item = layout["font_size_data"]
        size_sub = layout["font_size_subtotal"]
        size_note = layout["font_size_note"]

        def _size_for(rt: str) -> float:
            if rt == "subtotal":
                return size_sub
            if rt == "note":
                return size_note
            return size_item

        for nrow in data_rows:
            if nrow.row_type == "spacer":
                continue
            row_cells = nrow.to_table_row(has_henkou=has_henkou)
            fsize = _size_for(nrow.row_type)
            for idx in text_cols:
                if idx >= len(row_cells):
                    continue
                cell_text = row_cells[idx] or ""
                w = self.measure_text_width(cell_text, fsize, font_name)
                if w > demand[idx]:
                    demand[idx] = w

        needed = {
            idx: max(demand[idx] + padding, min_widths_pt[idx])
            for idx in text_cols
        }

        c_weight = layout.get("data_col_c_weight", 1.5)
        weights = {idx: 1.0 for idx in text_cols}
        for key, idx in text_col_keys:
            if key == "C":
                weights[idx] = c_weight

        fixed_total = sum(
            base_widths[i] for i in range(len(base_widths)) if i not in text_cols
        )
        text_available = available_width_pt - fixed_total

        weighted_need_sum = sum(needed[idx] * weights[idx] for idx in text_cols)
        raw_need_sum = sum(needed[idx] for idx in text_cols)

        result = list(base_widths)

        if raw_need_sum <= text_available:
            slack = text_available - raw_need_sum
            weight_sum = sum(weights[idx] for idx in text_cols)
            for idx in text_cols:
                bonus = slack * (weights[idx] / weight_sum) if weight_sum > 0 else 0
                result[idx] = needed[idx] + bonus
        else:
            if weighted_need_sum > 0:
                for idx in text_cols:
                    share = text_available * (needed[idx] * weights[idx] / weighted_need_sum)
                    result[idx] = max(share, min_widths_pt[idx])
            else:
                per = text_available / len(text_cols)
                for idx in text_cols:
                    result[idx] = per

        for idx in text_cols:
            if result[idx] < min_widths_pt[idx]:
                result[idx] = min_widths_pt[idx]

        total = sum(result)
        if total > 0 and abs(total - available_width_pt) > 0.1:
            fixed_actual = sum(
                result[i] for i in range(len(result)) if i not in text_cols
            )
            text_actual = sum(result[i] for i in text_cols)
            adjust_target = available_width_pt - fixed_actual
            if text_actual > 0 and adjust_target > 0:
                ratio = adjust_target / text_actual
                for idx in text_cols:
                    result[idx] = max(result[idx] * ratio, min_widths_pt[idx])

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ヘッダ（1ページ目のみ）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_header_col_widths(
        self,
        header_texts: list[list[str]],
        font_size: float,
        available_width_pt: float,
    ) -> list[float]:
        """ヘッダテーブル 6 列の動的列幅を算出する。"""
        layout = config.NAIRAKU_LAYOUT
        label_min = layout["header_label_min_mm"] * mm
        value_min = layout["header_value_min_mm"] * mm
        padding = 4.0

        col_max_width: list[float] = [0.0] * 6
        for row in header_texts:
            for ci, text in enumerate(row):
                w = self.measure_text_width(text or "", font_size, self.font_name)
                if w > col_max_width[ci]:
                    col_max_width[ci] = w

        widths = [0.0] * 6
        for i in (0, 2, 4):
            widths[i] = max(col_max_width[i] + padding, label_min)

        label_total = sum(widths[i] for i in (0, 2, 4))
        value_available = max(available_width_pt - label_total, value_min * 3)

        value_demand = [max(col_max_width[i] + padding, value_min) for i in (1, 3, 5)]
        demand_sum = sum(value_demand)

        if demand_sum <= 0:
            for i in (1, 3, 5):
                widths[i] = value_available / 3
        else:
            for idx, ci in enumerate((1, 3, 5)):
                widths[ci] = max(
                    value_available * value_demand[idx] / demand_sum,
                    value_min,
                )

        total = sum(widths)
        if total > 0 and abs(total - available_width_pt) > 0.1:
            ratio = available_width_pt / total
            widths = [w * ratio for w in widths]

        return widths

    def _build_header_flowables(
        self,
        data: NairakuData,
        available_width_pt: float,
    ) -> list:
        """1ページ目に配置するタイトル＋ヘッダー情報 Flowable リスト。"""
        layout = config.NAIRAKU_LAYOUT
        h = data.header
        base_size = layout["font_size_header"]

        flowables: list = []

        # タイトル
        flowables.append(Paragraph("下　請　代　金　内　訳　書", self.styles["title"]))

        # 生テキスト（計測・描画共通）
        raw_texts = [
            [
                "工 事 名",
                h.koji_kenmei,
                "(元 請 人)",
                f"住所　{h.motouke_address}",
                "(下請負人)",
                f"住所　{h.shitauke_address}",
            ],
            [
                "契約年月日",
                h.contract_date,
                "",
                f"商号　{h.motouke_company}",
                "",
                f"商号　{h.shitauke_company}",
            ],
            [
                "工　　期",
                h.kouki,
                "",
                f"氏名　{h.motouke_name}",
                "",
                f"氏名　{h.shitauke_name}",
            ],
        ]

        header_col_widths = self._compute_header_col_widths(
            raw_texts, base_size, available_width_pt,
        )

        # セル単位の自動縮小 + 空白保持で Paragraph を生成
        header_data: list[list] = []
        for r_idx, row in enumerate(raw_texts):
            drawn_row = []
            for ci, text in enumerate(row):
                style_key = "header_label" if ci % 2 == 0 else "header_value"
                drawn_row.append(self.make_paragraph(
                    text, style_key,
                    fit_width_pt=header_col_widths[ci],
                    fit_base_size=base_size,
                    key_hint=f"header_r{r_idx}c{ci}",
                ))
            header_data.append(drawn_row)

        # ヘッダ表は罫線なし（タイトル下のシンプルなレイアウト）
        header_table = self.make_table(
            rows=header_data,
            col_widths=header_col_widths,
            draw_grid=False,
            style_commands=[
                ("FONTNAME", (0, 0), (-1, -1), self.font_name),
                ("FONTSIZE", (0, 0), (-1, -1), base_size),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ],
        )

        flowables.append(header_table)
        flowables.append(self.spacer(2.0))

        return flowables

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 列ヘッダ（3段構成）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_col_header_config(
        self, has_henkou: bool,
    ) -> tuple[list[dict], list[str]]:
        """config から列ヘッダ定義を取得する。"""
        layout = config.NAIRAKU_LAYOUT
        if has_henkou:
            return layout["col_header_spans_full"], layout["col_header_row2_full"]
        return layout["col_header_spans_base"], layout["col_header_row2_base"]

    def _build_col_header_rows(self, has_henkou: bool) -> list[list]:
        """列ヘッダ 3 行分のデータグリッドを構築する。"""
        layout = config.NAIRAKU_LAYOUT
        header_rows = layout["col_header_rows"]
        col_count = len(
            layout["col_widths_full_mm" if has_henkou else "col_widths_base_mm"]
        )
        spans_def, row2_labels = self._get_col_header_config(has_henkou)

        style = self.styles["col_head"]
        def P(text):
            return Paragraph(text, style)

        grid: list[list] = [
            [P("") for _ in range(col_count)] for _ in range(header_rows)
        ]

        for entry in spans_def:
            start_col, start_row = entry["start"]
            if 0 <= start_row < header_rows and 0 <= start_col < col_count:
                grid[start_row][start_col] = P(entry["text"])

        if header_rows >= 3:
            for col, label in enumerate(row2_labels):
                if col < col_count and label:
                    grid[2][col] = P(label)

        return grid

    def _build_col_header_spans(self, has_henkou: bool) -> list[tuple]:
        """列ヘッダの SPAN TableStyle コマンドリスト。"""
        spans_def, _ = self._get_col_header_config(has_henkou)
        commands: list[tuple] = []
        for entry in spans_def:
            commands.append(("SPAN", tuple(entry["start"]), tuple(entry["end"])))
        return commands

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # データ行構築
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _col_idx_biko(has_henkou: bool) -> int:
        return 14 if has_henkou else 8

    @staticmethod
    def _col_idx_last_num(has_henkou: bool) -> int:
        return 13 if has_henkou else 7

    def _build_data_row(
        self,
        nrow: NairakuRow,
        has_henkou: bool,
        col_widths: list[float] | None = None,
    ) -> list:
        """NairakuRow を Paragraph のリストに変換する。"""
        layout = config.NAIRAKU_LAYOUT
        raw = nrow.to_table_row(has_henkou=has_henkou)
        biko_idx = self._col_idx_biko(has_henkou)
        last_num_idx = self._col_idx_last_num(has_henkou)

        # Spacer 行: 空セルのみ
        if nrow.row_type == "spacer":
            return ["" for _ in raw]

        if nrow.row_type == "note":
            base_size = layout["font_size_note"]
        elif nrow.row_type == "subtotal":
            base_size = layout["font_size_subtotal"]
        else:
            base_size = layout["font_size_data"]

        result = []
        for i, cell_text in enumerate(raw):
            if not cell_text:
                result.append("")
                continue

            # スタイル選択
            if nrow.row_type == "note":
                style_key = "note"
            elif nrow.row_type == "subtotal":
                if i <= 2:
                    style_key = "subtotal_left"
                elif 4 <= i <= last_num_idx:
                    style_key = "subtotal_right"
                else:
                    style_key = "subtotal_left"
            elif i == 3:
                style_key = "data_center"
            elif 4 <= i <= last_num_idx:
                style_key = "data_right"
            elif i == biko_idx:
                style_key = "data_left"
            else:
                style_key = "data_left"

            fit_w = None
            if col_widths and i < len(col_widths):
                fit_w = col_widths[i]

            result.append(self.make_paragraph(
                cell_text,
                style_key,
                fit_width_pt=fit_w,
                fit_base_size=base_size,
                bold=nrow.is_bold,
                key_hint=f"{nrow.row_type}_c{i}",
            ))

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TableStyle 構築
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _build_table_style_commands(
        self,
        data_rows: list[NairakuRow],
        has_henkou: bool,
        total_rows: int,
    ) -> list[tuple]:
        """row_type に基づく TableStyle コマンドを動的生成する。

        タスク4 修正:
          - GRID は make_table の既定（(0,0)→(-1,-1), colors.black, 0.5pt）に委ねる。
          - ここでは SPAN, BACKGROUND, 行タイプ別の追加罫線（subtotal 上辺の太線等）、
            spacer 行の罫線消去（白線で上書き）のみを積む。
        """
        layout = config.NAIRAKU_LAYOUT
        grid_lw = max(layout.get("grid_line_width", 0.5), 0.5)
        subtotal_lw = max(layout.get("subtotal_line_width", 0.8), 0.5)
        last_col = self._col_idx_biko(has_henkou)

        commands: list[tuple] = []

        # ── セル共通パディング ──
        commands.append(("VALIGN", (0, 0), (-1, -1), "MIDDLE"))
        commands.append(("TOPPADDING", (0, 0), (-1, -1), 0.5))
        commands.append(("BOTTOMPADDING", (0, 0), (-1, -1), 0.5))
        commands.append(("LEFTPADDING", (0, 0), (-1, -1), 2))
        commands.append(("RIGHTPADDING", (0, 0), (-1, -1), 2))

        # ── 列ヘッダ (row 0〜2): 背景色 ──
        commands.append(("BACKGROUND", (0, 0), (-1, 2), colors.Color(0.93, 0.93, 0.93)))
        commands.append(("ALIGN", (0, 0), (-1, 2), "CENTER"))
        commands.append(("VALIGN", (0, 0), (-1, 2), "MIDDLE"))

        # 列ヘッダの SPAN
        commands.extend(self._build_col_header_spans(has_henkou))

        # ── 行タイプ別スタイル ──
        header_rows = layout["col_header_rows"]
        for i, nrow in enumerate(data_rows):
            row_idx = header_rows + i

            if nrow.row_type == "spacer":
                # spacer 行: Excel 上の意図的な空白を再現
                # make_table が引く GRID を白線で上書きして罫線を消す
                commands.append((
                    "LINEBELOW", (0, row_idx), (-1, row_idx),
                    grid_lw, colors.white,
                ))
                commands.append((
                    "LINEABOVE", (0, row_idx), (-1, row_idx),
                    grid_lw, colors.white,
                ))
                for col in range(last_col + 1):
                    commands.append((
                        "LINEAFTER", (col, row_idx), (col, row_idx),
                        grid_lw, colors.white,
                    ))
                    commands.append((
                        "LINEBEFORE", (col, row_idx), (col, row_idx),
                        grid_lw, colors.white,
                    ))
                continue

            if nrow.row_type == "category":
                # カテゴリ行: 背景色 + A〜C結合
                commands.append((
                    "BACKGROUND",
                    (0, row_idx), (-1, row_idx),
                    colors.Color(0.95, 0.95, 0.95),
                ))
                if last_col >= 2:
                    commands.append(("SPAN", (0, row_idx), (2, row_idx)))

            elif nrow.row_type == "subtotal":
                # 合計行: 上辺太線 + 淡背景 + A〜C結合
                commands.append((
                    "LINEABOVE",
                    (0, row_idx), (-1, row_idx),
                    subtotal_lw, colors.black,
                ))
                commands.append((
                    "BACKGROUND",
                    (0, row_idx), (-1, row_idx),
                    colors.Color(0.96, 0.96, 0.96),
                ))
                if last_col >= 2:
                    commands.append(("SPAN", (0, row_idx), (2, row_idx)))

            elif nrow.row_type == "note":
                # 注記行: 上辺を淡い灰色で + A〜C結合
                commands.append((
                    "LINEABOVE",
                    (0, row_idx), (-1, row_idx),
                    grid_lw * 0.6, colors.Color(0.7, 0.7, 0.7),
                ))
                if last_col >= 2:
                    commands.append(("SPAN", (0, row_idx), (2, row_idx)))

        return commands

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 行高さ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_row_heights(self, data_rows: list[NairakuRow]) -> list[float]:
        """列ヘッダ + データ行の行高さ (pt) リスト。"""
        layout = config.NAIRAKU_LAYOUT
        h_head = layout["row_height_col_head_mm"] * mm
        h_data = layout["row_height_data_mm"] * mm
        h_sub = layout["row_height_subtotal_mm"] * mm
        h_cat = layout["row_height_category_mm"] * mm
        h_spacer = layout.get("row_height_spacer_mm", 4.5) * mm

        heights = [h_head] * layout["col_header_rows"]

        for nrow in data_rows:
            if nrow.row_type == "category":
                heights.append(h_cat)
            elif nrow.row_type == "subtotal":
                heights.append(h_sub)
            elif nrow.row_type == "spacer":
                heights.append(h_spacer)
            else:
                heights.append(h_data)

        return heights

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Story 構築
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def build_story(self, data: NairakuData) -> list:
        """NairakuData から Flowable 群を組み立てる。"""
        layout = config.NAIRAKU_LAYOUT
        has_henkou = data.has_henkou

        avail_w, _ = self.get_content_area()

        # 列幅（動的算出）
        col_widths = self._compute_col_widths_dynamic(
            has_henkou, avail_w, data.rows,
        )

        story: list = []

        # 1) ヘッダ（1ページ目のみ）
        story.extend(self._build_header_flowables(data, avail_w))

        # 2) 列ヘッダ（3行）
        table_data = self._build_col_header_rows(has_henkou)

        # 3) データ行
        for nrow in data.rows:
            table_data.append(
                self._build_data_row(nrow, has_henkou, col_widths=col_widths)
            )

        # 4) 行高さ
        row_heights = self._compute_row_heights(data.rows)

        # 5) TableStyle 追加コマンド（SPAN, BACKGROUND, 行タイプ別罫線）
        total_rows = len(table_data)
        style_commands = self._build_table_style_commands(
            data.rows, has_henkou, total_rows,
        )

        # 6) Table 生成（make_table が GRID を (0,0)→(-1,-1) 黒 0.5pt で自動描画）
        main_table = self.make_table(
            rows=table_data,
            col_widths=col_widths,
            row_heights=row_heights,
            repeat_rows=layout["col_header_rows"],
            split_in_row=0,
            grid_width=max(layout.get("grid_line_width", 0.5), 0.5),
            outer_width=max(layout.get("outer_line_width", 0.9), 0.5),
            style_commands=style_commands,
        )

        story.append(main_table)

        return story


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  関数スタイル API（後方互換）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_nairaku_pdf(
    data: NairakuData,
    output_path: Path,
    font_path: str | None = None,
    vendor_data: dict[str, str | None] | None = None,
) -> None:
    """内訳書データから PDF を動的生成する（後方互換ラッパ）。

    Parameters
    ----------
    data : NairakuData
        extract_nairaku_data() で抽出済みのデータ。
    output_path : Path
        出力先 PDF ファイルパス。
    font_path : str | None
        日本語フォントファイルのパス。None の場合は config.FONT_FALLBACKS を使用。
    vendor_data : dict | None
        extract_data() で抽出済みの業者データ。
        内訳書シートの数式参照が未解決の場合に、ここから契約日・工期等を補完する。
    """
    # ── ヘッダ情報の補完（数式参照が未解決の場合の対策） ──
    if vendor_data:
        h = data.header
        if not h.contract_date or h.contract_date == h.shitauke_company:
            y = vendor_data.get("contract_year") or ""
            m = vendor_data.get("contract_month") or ""
            d = vendor_data.get("contract_day") or ""
            if y and m and d:
                h.contract_date = f"令和{y}年{m}月{d}日"
                logger.info("契約年月日を vendor_data から補完: %s", h.contract_date)

    builder = NairakuBuilder(output_path, font_path=font_path)
    builder.build(data)
    logger.info(
        "内訳書PDF生成完了: %s (%d 行, has_henkou=%s)",
        output_path, len(data.rows), data.has_henkou,
    )
