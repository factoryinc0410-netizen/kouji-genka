"""
契約条件書 PDF 動的生成モジュール — ReportLab Platypus ベース

TermsData を受け取り、A4 縦 1 ページの契約条件書 PDF を生成する。
BaseBuilder を継承しているため、空白保持・自動縮小・罫線描画は基盤層に委譲し、
このモジュールは「契約条件書固有のレイアウト組立」に専念する。

出力構造:
  1. タイトル「契約条件書」
  2. ヘッダ表（工事名 / 現場代理人）
  3. 費用負担項目ヘッダ（区分 / 費用負担会社[元方|下請] / 備考）
  4. 10 セクション × 項目行テーブル — ✓ は ☑ で描画
  5. 注記行「※以上の条件で契約致します。(ﾚ点の部分）」
  6. サインブロック（元請 / 下請 — Excel 原文通り左右非対称）

1ページに収まらない場合は KeepInFrame(mode="shrink") で自動圧縮する。
"""
from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm

from . import config
from .base_builder import BaseBuilder
from .terms_models import TermsData, TermsItem, TermsParty, TermsSection

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TermsBuilder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TermsBuilder(BaseBuilder):
    """契約条件書専用ビルダ（A4 縦、1ページ強制）。"""

    PAGE_SIZE = A4
    IS_LANDSCAPE = False
    DOC_TITLE = "契約条件書"
    LOGICAL_FONT_NAME = "TermsMincho"

    def __init__(self, output_path: Path, font_path: str | None = None):
        # マージンは config から取得
        layout = config.TERMS_LAYOUT
        self.MARGIN_MM = {
            "top":    layout["margin_top_mm"],
            "bottom": layout["margin_bottom_mm"],
            "left":   layout["margin_left_mm"],
            "right":  layout["margin_right_mm"],
        }
        self.DEFAULT_FONT_SIZE = layout.get("font_size_item", 8.5)

        super().__init__(output_path, font_path)

        # TermsBuilder 固有スタイルを追加
        self.styles.update(self._make_terms_styles())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # スタイル拡張
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _make_terms_styles(self) -> dict[str, ParagraphStyle]:
        """契約条件書用の追加スタイル。"""
        layout = config.TERMS_LAYOUT
        fn = self.font_name

        def _ps(name, *, size, align=0, leading_add=2):
            return ParagraphStyle(
                name, fontName=fn, fontSize=size,
                leading=size + leading_add, alignment=align, wordWrap="CJK",
            )

        return {
            "terms_title":       _ps("terms_title", size=layout["font_size_title"], align=1),
            "terms_header_lbl":  _ps("terms_header_lbl", size=layout["font_size_header"], align=0),
            "terms_header_val":  _ps("terms_header_val", size=layout["font_size_header"], align=0),
            "terms_col_head":    _ps("terms_col_head", size=layout["font_size_col_head"], align=1),
            "terms_section":     _ps("terms_section", size=layout["font_size_section"], align=0),
            "terms_item":        _ps("terms_item", size=layout["font_size_item"], align=0),
            "terms_check":       _ps("terms_check", size=layout["font_size_item"] + 1.0, align=1),
            "terms_biko":        _ps("terms_biko", size=layout["font_size_biko"], align=0),
            "terms_note":        _ps("terms_note", size=layout["font_size_header"], align=2),
            "terms_sign_lbl":    _ps("terms_sign_lbl", size=layout["font_size_sign_label"], align=0),
            "terms_sign_val":    _ps("terms_sign_val", size=layout["font_size_sign_value"], align=0),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 列幅計算
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _main_col_widths(self) -> list[float]:
        """メインテーブルの 6 列幅 (pt)。合計 = content_width。"""
        layout = config.TERMS_LAYOUT
        widths_mm = layout["col_widths_mm"]
        avail_w, _ = self.get_content_area()
        total_mm = sum(widths_mm)
        scale = avail_w / (total_mm * mm)
        return [w * mm * scale for w in widths_mm]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Flowable 構築
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def build_story(self, data: TermsData) -> list:
        """TermsData から Flowable 群を組み立てる。

        全体を KeepInFrame(shrink) で包んで 1 ページに強制収容する。
        """
        layout = config.TERMS_LAYOUT

        inner: list = []

        # 1) タイトル
        inner.append(self.make_paragraph("契 約 条 件 書", "terms_title"))
        inner.append(self.spacer(2.0))

        # 2) ヘッダ表（工事名 / 現場代理人）
        inner.append(self._build_header_table(data))
        inner.append(self.spacer(2.0))

        # 3 + 4) メインテーブル（列ヘッダ + 10 セクション × 項目行）
        inner.append(self._build_main_table(data))
        inner.append(self.spacer(1.5))

        # 5) 注記行
        inner.append(self.make_paragraph(data.note_line, "terms_note"))
        inner.append(self.spacer(1.5))

        # 6) サインブロック（左右非対称）
        inner.append(self._build_signblock(data))

        # KeepInFrame で 1 ページに圧縮
        keeper = self.wrap_in_keepinframe(
            inner,
            mode=layout.get("keep_in_frame_mode", "shrink"),
        )
        return [keeper]

    # ── 2) ヘッダ表 ─────────────────────────────────────
    def _build_header_table(self, data: TermsData):
        layout = config.TERMS_LAYOUT
        avail_w, _ = self.get_content_area()

        # 4 列: 工事名ラベル / 工事名値 / 現場代理人ラベル / 現場代理人値
        # 20% / 45% / 15% / 20%
        col_w = [avail_w * 0.16, avail_w * 0.48, avail_w * 0.16, avail_w * 0.20]

        fs = layout["font_size_header"]
        rows = [[
            self.make_paragraph("工事名：", "terms_header_lbl",
                                fit_width_pt=col_w[0], fit_base_size=fs,
                                bold=True, key_hint="hdr_lbl"),
            self.make_paragraph(data.koji_kenmei, "terms_header_val",
                                fit_width_pt=col_w[1], fit_base_size=fs,
                                key_hint="hdr_koji"),
            self.make_paragraph("現場代理人", "terms_header_lbl",
                                fit_width_pt=col_w[2], fit_base_size=fs,
                                bold=True, key_hint="hdr_lbl2"),
            self.make_paragraph(data.genba_dairinin, "terms_header_val",
                                fit_width_pt=col_w[3], fit_base_size=fs,
                                key_hint="hdr_daiin"),
        ]]
        h = layout["row_height_header_mm"] * mm
        return self.make_table(
            rows=rows, col_widths=col_w, row_heights=[h],
            draw_grid=False,
            style_commands=[
                ("LINEBELOW", (0, 0), (-1, 0),
                 layout["grid_line_width"], colors.black),
            ],
        )

    # ── 3 + 4) メインテーブル ───────────────────────────────
    def _build_main_table(self, data: TermsData):
        """
        列構成 (6 列):
          0: A 大分類 / 1: 余白 / 2: 明細ラベル / 3: 元方✓ / 4: 下請✓ / 5: 備考

        行構成:
          Row 0-1: 列ヘッダ (2 段: 費用負担会社 / 元方・下請)
          以降: セクション（大分類）行 + 項目行
        """
        layout = config.TERMS_LAYOUT
        col_widths = self._main_col_widths()

        rows: list[list] = []
        row_heights: list[float] = []
        style_cmds: list[tuple] = []

        # ── 列ヘッダ 2 段 ──
        h_head = layout["row_height_header_mm"] * mm
        rows.append([
            self.make_paragraph("費用負担項目", "terms_col_head"),
            "",
            self.make_paragraph("区　分", "terms_col_head"),
            self.make_paragraph("費用負担会社", "terms_col_head"),
            "",
            self.make_paragraph("備　考", "terms_col_head"),
        ])
        row_heights.append(h_head)

        rows.append([
            "",
            "",
            "",
            self.make_paragraph("元方", "terms_col_head"),
            self.make_paragraph("下請", "terms_col_head"),
            "",
        ])
        row_heights.append(h_head * 0.7)

        # ヘッダ行の SPAN
        style_cmds.extend([
            # 大分類ヘッダを 2 段縦に SPAN
            ("SPAN", (0, 0), (1, 1)),     # A: 費用負担項目（2段縦 + 余白列結合）
            ("SPAN", (2, 0), (2, 1)),     # C: 区分
            ("SPAN", (3, 0), (4, 0)),     # D-E: 費用負担会社（1段目、2列横結合）
            ("SPAN", (5, 0), (5, 1)),     # F: 備考
            ("BACKGROUND", (0, 0), (-1, 1), colors.Color(0.92, 0.92, 0.92)),
            ("ALIGN", (0, 0), (-1, 1), "CENTER"),
            ("VALIGN", (0, 0), (-1, 1), "MIDDLE"),
        ])

        # ── 10 セクション ──
        h_section = layout["row_height_section_mm"] * mm
        h_item = layout["row_height_item_mm"] * mm
        check_ch = layout["check_mark_char"]

        def _ck(flag: bool) -> str:
            return check_ch if flag else ""

        for section in data.sections:
            # セクション行（大分類）— A 列にタイトルのみ表示
            if section.number < 10:
                # 通常セクション: A列にタイトル表示、他の列は項目で使う
                section_row_idx = len(rows)
                # この行自体にも「空の明細」は持たせず、項目を次行から展開する
                # → A列が縦に全項目をまたぐ SPAN を使う
                # 構成: 最初のアイテムの行と A 列を SPAN 結合

                # まず、A列タイトルを 1 行目に配置
                first_item = section.items[0] if section.items else None

                if section.layout == "wide":
                    # wide: B:D = label, E & F = check, G = biko
                    # rows 生成: A=title(1件目のみ), B=(空), C=label(span B-C相当 → 1-2 SPAN), D=✓, E=✓, F=biko
                    for idx, it in enumerate(section.items):
                        a_val = section.title if idx == 0 else ""
                        rows.append([
                            self.make_paragraph(a_val, "terms_section", bold=True),
                            "",
                            self.make_paragraph(it.label, "terms_item",
                                                fit_width_pt=col_widths[2],
                                                key_hint=f"s{section.number}_lbl"),
                            self.make_paragraph(_ck(it.motokata_checked), "terms_check"),
                            self.make_paragraph(_ck(it.shitauke_checked), "terms_check"),
                            self.make_paragraph(it.biko, "terms_biko",
                                                fit_width_pt=col_widths[5],
                                                key_hint=f"s{section.number}_biko"),
                        ])
                        row_heights.append(h_item)
                    # A 列を全項目で SPAN
                    n = len(section.items)
                    if n > 1:
                        style_cmds.append(("SPAN",
                                           (0, section_row_idx),
                                           (0, section_row_idx + n - 1)))
                elif section.layout == "single":
                    # single (セクション10): A=大分類＋項目ラベル, C=空/チェックのみ
                    it = section.items[0]
                    rows.append([
                        self.make_paragraph(section.title, "terms_section", bold=True),
                        "",
                        "",
                        self.make_paragraph(_ck(it.motokata_checked), "terms_check"),
                        self.make_paragraph(_ck(it.shitauke_checked), "terms_check"),
                        self.make_paragraph(it.biko, "terms_biko",
                                            fit_width_pt=col_widths[5],
                                            key_hint=f"s10_biko"),
                    ])
                    row_heights.append(h_section)
                else:
                    # std: C=label, E=✓, F=✓, G=biko
                    for idx, it in enumerate(section.items):
                        a_val = section.title if idx == 0 else ""
                        rows.append([
                            self.make_paragraph(a_val, "terms_section", bold=True),
                            "",
                            self.make_paragraph(it.label, "terms_item",
                                                fit_width_pt=col_widths[2],
                                                key_hint=f"s{section.number}_lbl"),
                            self.make_paragraph(_ck(it.motokata_checked), "terms_check"),
                            self.make_paragraph(_ck(it.shitauke_checked), "terms_check"),
                            self.make_paragraph(it.biko, "terms_biko",
                                                fit_width_pt=col_widths[5],
                                                key_hint=f"s{section.number}_biko"),
                        ])
                        row_heights.append(h_item)
                    n = len(section.items)
                    if n > 1:
                        style_cmds.append(("SPAN",
                                           (0, section_row_idx),
                                           (0, section_row_idx + n - 1)))
            else:
                # section.number == 10: single レイアウト
                if section.items:
                    it = section.items[0]
                    rows.append([
                        self.make_paragraph(section.title, "terms_section", bold=True),
                        "",
                        "",
                        self.make_paragraph(_ck(it.motokata_checked), "terms_check"),
                        self.make_paragraph(_ck(it.shitauke_checked), "terms_check"),
                        self.make_paragraph(it.biko, "terms_biko",
                                            fit_width_pt=col_widths[5],
                                            key_hint="s10_biko"),
                    ])
                    row_heights.append(h_section)

        # 共通スタイル: B列(余白)は A と結合して 1 つの大分類列として見せる
        # ただし ReportLab では「全行で A-B SPAN」は動的行数だと一括できないため、
        # B列は 0 幅ではないが視覚的に連結させる（LINEAFTER を消す）
        # → 代わりに B列の LINEAFTER を消し、A-B 間だけ罫線を除去する
        for r_idx in range(2, len(rows)):
            # B列と A列を視覚連結：A→B間の縦線を消す
            style_cmds.append((
                "LINEAFTER", (0, r_idx), (0, r_idx),
                0, colors.white,
            ))
            # B列の横罫線も薄く
            style_cmds.append((
                "BACKGROUND", (1, r_idx), (1, r_idx), colors.white,
            ))

        style_cmds.extend([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (3, 2), (4, -1), "CENTER"),    # ✓ 列は中央
            ("ALIGN", (0, 2), (0, -1), "LEFT"),       # 大分類は左
            ("ALIGN", (2, 2), (2, -1), "LEFT"),       # 明細は左
            ("ALIGN", (5, 2), (5, -1), "LEFT"),       # 備考は左
            ("TOPPADDING", (0, 0), (-1, -1), 0.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
        ])

        return self.make_table(
            rows=rows,
            col_widths=col_widths,
            row_heights=row_heights,
            style_commands=style_cmds,
            grid_width=layout["grid_line_width"],
            outer_width=layout["outer_line_width"],
        )

    # ── 6) サインブロック（左右非対称） ───────────────────────
    def _build_signblock(self, data: TermsData):
        """Excel 原文通り、左右で住所/商号/氏名の順序が違うレイアウトを再現する。

        左（元請）: 住所 / 商号 / 氏名   (行順)
        右（下請）: 商号 / 住所 / 氏名   (行順)
        """
        layout = config.TERMS_LAYOUT
        avail_w, _ = self.get_content_area()

        # 左右列の割合: 左(ラベル+値) | 右(ラベル+値) = ほぼ均等
        left_label_w = layout["signblock_left_label_mm"] * mm
        left_value_w = layout["signblock_left_value_mm"] * mm
        right_label_w = layout["signblock_right_label_mm"] * mm
        right_value_w = layout["signblock_right_value_mm"] * mm

        # 正規化: content 幅に収める
        total = left_label_w + left_value_w + right_label_w + right_value_w
        if total > avail_w:
            scale = avail_w / total
            left_label_w *= scale
            left_value_w *= scale
            right_label_w *= scale
            right_value_w *= scale

        col_widths = [left_label_w, left_value_w, right_label_w, right_value_w]

        fs_label = layout["font_size_sign_label"]
        fs_value = layout["font_size_sign_value"]

        # ── 見出し行 ──
        mgroup = data.motouke_group_name or "（元請負人）"
        rows: list[list] = []

        # 行 0: 元請グループ名（左側 col 0-1 に表示）
        rows.append([
            "",
            self.make_paragraph(mgroup, "terms_sign_val",
                                fit_width_pt=left_value_w, fit_base_size=fs_value,
                                key_hint="sign_mgroup"),
            "",
            self.make_paragraph("（下請負人）", "terms_sign_val",
                                fit_width_pt=right_value_w, fit_base_size=fs_value,
                                bold=True, key_hint="sign_sgroup"),
        ])

        # 行 1: 代表構成員
        rows.append([
            self.make_paragraph(data.daikyo_koseiin_label, "terms_sign_lbl",
                                fit_width_pt=left_label_w, fit_base_size=fs_label,
                                key_hint="sign_koseiin"),
            "",
            "",
            "",
        ])

        # 行 2: 左=住所, 右=商号
        rows.append([
            self.make_paragraph("住　　所", "terms_sign_lbl",
                                fit_width_pt=left_label_w, fit_base_size=fs_label,
                                key_hint="sign_l_addr_lbl"),
            self.make_paragraph(data.motouke.address, "terms_sign_val",
                                fit_width_pt=left_value_w, fit_base_size=fs_value,
                                key_hint="sign_l_addr"),
            self.make_paragraph("商号又は名称", "terms_sign_lbl",
                                fit_width_pt=right_label_w, fit_base_size=fs_label,
                                key_hint="sign_r_comp_lbl"),
            self.make_paragraph(data.shitauke.company, "terms_sign_val",
                                fit_width_pt=right_value_w, fit_base_size=fs_value,
                                key_hint="sign_r_comp"),
        ])

        # 行 3: 左=商号, 右=住所
        rows.append([
            self.make_paragraph("商号又は名称", "terms_sign_lbl",
                                fit_width_pt=left_label_w, fit_base_size=fs_label,
                                key_hint="sign_l_comp_lbl"),
            self.make_paragraph(data.motouke.company, "terms_sign_val",
                                fit_width_pt=left_value_w, fit_base_size=fs_value,
                                key_hint="sign_l_comp"),
            self.make_paragraph("住　　所", "terms_sign_lbl",
                                fit_width_pt=right_label_w, fit_base_size=fs_label,
                                key_hint="sign_r_addr_lbl"),
            self.make_paragraph(data.shitauke.address, "terms_sign_val",
                                fit_width_pt=right_value_w, fit_base_size=fs_value,
                                key_hint="sign_r_addr"),
        ])

        # 行 4: 左右とも氏名
        rows.append([
            self.make_paragraph("氏　　名", "terms_sign_lbl",
                                fit_width_pt=left_label_w, fit_base_size=fs_label,
                                key_hint="sign_l_name_lbl"),
            self.make_paragraph(data.motouke.name, "terms_sign_val",
                                fit_width_pt=left_value_w, fit_base_size=fs_value,
                                key_hint="sign_l_name"),
            self.make_paragraph("氏　　名", "terms_sign_lbl",
                                fit_width_pt=right_label_w, fit_base_size=fs_label,
                                key_hint="sign_r_name_lbl"),
            self.make_paragraph(data.shitauke.name, "terms_sign_val",
                                fit_width_pt=right_value_w, fit_base_size=fs_value,
                                key_hint="sign_r_name"),
        ])

        h_sign = layout["row_height_sign_mm"] * mm
        row_heights = [h_sign] * len(rows)

        return self.make_table(
            rows=rows,
            col_widths=col_widths,
            row_heights=row_heights,
            draw_grid=False,
            style_commands=[
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                # サインブロック全体を薄い枠で囲む
                ("BOX", (0, 0), (-1, -1),
                 layout["grid_line_width"], colors.black),
                # 左右を分ける縦線
                ("LINEAFTER", (1, 0), (1, -1),
                 layout["grid_line_width"] * 0.7, colors.Color(0.5, 0.5, 0.5)),
            ],
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  関数スタイル API（後方互換）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_terms_pdf(
    data: TermsData,
    output_path: Path,
    font_path: str | None = None,
) -> None:
    """契約条件書 PDF を生成する。

    Parameters
    ----------
    data : TermsData
        extract_terms_data() で抽出済みデータ。
    output_path : Path
        出力先 PDF ファイルパス。
    font_path : str | None
        日本語フォントファイルのパス。None の場合は config.FONT_FALLBACKS を使用。
    """
    builder = TermsBuilder(output_path, font_path=font_path)
    builder.build(data)
    logger.info(
        "契約条件書PDF生成完了: %s (sections=%d, source=%s)",
        output_path, len(data.sections), data.source_sheet,
    )
