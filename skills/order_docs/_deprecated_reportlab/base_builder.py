"""
BaseBuilder — ReportLab Platypus ベースの帳票生成共通基盤

サブクラス（NairakuBuilder, TermsBuilder, …）はこのクラスを継承し
build_story() のみを実装すれば、PDF 生成フローが完成する。

提供する共通機能:
  - 日本語フォントの多段フォールバック登録
  - ParagraphStyle のベースセット生成
  - 半角空白 → NBSP 変換（Paragraph での空白詰まりを防止）
  - テキスト幅実測（pdfmetrics.stringWidth）
  - セル単位フォント自動縮小（5pt 下限 + Warning ログ）
  - ParagraphStyle のサイズ別キャッシュ
  - make_paragraph() — 空白保持＋自動縮小＋太字＋Paragraph 生成のオールインワン
  - make_table() — 確実に最後まで黒 0.5pt 以上の GRID を引く Table ファクトリ
  - make_signblock() — サインブロック（ラベル/値ペア）Table 生成
  - ページサイズ／コンテンツ領域の計算
  - SimpleDocTemplate の生成・build 実行

罫線の方針（タスク4 修正）:
  - GRID コマンドは範囲 (0,0)→(-1,-1) で発行し、最終行まで必ず描画
  - 色は colors.black、太さは grid_line_width (既定 0.5pt 以上)
  - サブクラスが style_commands で追加のコマンドを上書き可
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepInFrame,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import config

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  モジュールレベル・フォント登録キャッシュ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 複数ビルダ間でフォント登録を共有（重複登録の防止）
_FONT_REGISTRY: dict[str, str] = {}   # logical_name → resolved_font_name


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BaseBuilder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BaseBuilder(ABC):
    """ReportLab 帳票生成の共通基盤クラス。"""

    # ── クラス変数（サブクラスで override） ──────────────────────
    PAGE_SIZE: tuple[float, float] = A4
    IS_LANDSCAPE: bool = False
    MARGIN_MM: dict[str, float] = {
        "top": 15.0, "bottom": 15.0, "left": 15.0, "right": 15.0,
    }
    DEFAULT_FONT_SIZE: float = 9.0
    DOC_TITLE: str = ""
    DOC_AUTHOR: str = "Factoryskills"
    LOGICAL_FONT_NAME: str = "BaseMincho"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # コンストラクタ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def __init__(self, output_path: Path, font_path: str | None = None):
        self.output_path: Path = Path(output_path)
        self.font_name: str = self._register_fonts(font_path)
        self.styles: dict[str, ParagraphStyle] = self._make_base_styles()

        self._shrink_warn_cache: set[str] = set()
        self._style_cache: dict[tuple, ParagraphStyle] = {}

        # 自動縮小パラメータ（サブクラスが get_layout() で上書き可能）
        defaults = config.BASE_BUILDER_DEFAULTS
        self._shrink_config = {
            "min_size":     defaults.get("auto_shrink_min_size", 5.0),
            "padding_pt":   defaults.get("auto_shrink_padding_pt", 2.0),
            "safety":       defaults.get("auto_shrink_safety_factor", 0.98),
            "preserve_ws":  defaults.get("preserve_whitespace", True),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 抽象メソッド（サブクラスで実装）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @abstractmethod
    def build_story(self, data: Any) -> list:
        """Flowable のリストを返す。サブクラスで実装必須。"""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 公開エントリポイント
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def build(self, data: Any) -> None:
        """PDF ファイルを生成する。"""
        story = self.build_story(data)
        doc = self._make_doc_template()
        doc.build(story)
        logger.info("PDF生成完了: %s (title=%s)", self.output_path, self.DOC_TITLE)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # フォント登録
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _register_fonts(self, font_path: str | None = None) -> str:
        """日本語フォントを登録し、使用可能なフォント名を返す。

        探索順序:
          1. 引数 font_path
          2. config.FONT_FALLBACKS
          3. ReportLab 内蔵 HeiseiMin-W3 CID フォント（最終フォールバック）

        同一論理名での再登録はキャッシュで抑止する。
        """
        logical = self.LOGICAL_FONT_NAME
        if logical in _FONT_REGISTRY:
            return _FONT_REGISTRY[logical]

        candidates: list[Path] = []
        if font_path:
            candidates.append(Path(font_path))
        candidates.extend(config.FONT_FALLBACKS)

        for fp in candidates:
            if not fp or not Path(fp).exists():
                continue
            try:
                fp_str = str(fp)
                if fp_str.lower().endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont(logical, fp_str, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont(logical, fp_str))
                _FONT_REGISTRY[logical] = logical
                logger.info("フォント登録成功: %s → '%s'", fp_str, logical)
                return logical
            except Exception:
                logger.warning("フォント登録失敗（次候補へ）: %s", fp, exc_info=True)

        # 最終フォールバック
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
        except Exception:
            pass  # 既登録の場合は無視
        _FONT_REGISTRY[logical] = "HeiseiMin-W3"
        logger.error(
            "全フォールバック失敗 — HeiseiMin-W3 CID フォントを使用。"
            " config.FONT_FALLBACKS を確認してください。"
        )
        return "HeiseiMin-W3"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ParagraphStyle 生成
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _make_base_styles(self) -> dict[str, ParagraphStyle]:
        """共通 ParagraphStyle セットを返す。

        サブクラスが追加スタイルを必要とする場合は、コンストラクタ末尾で
        self.styles.update({...}) で拡張する想定。
        """
        fn = self.font_name
        size = self.DEFAULT_FONT_SIZE

        def _ps(name, *, align=0, font_size=size, leading_add=2, bold=False):
            return ParagraphStyle(
                name,
                fontName=fn,
                fontSize=font_size,
                alignment=align,
                leading=font_size + leading_add,
                wordWrap="CJK",
            )

        return {
            "title":         _ps("base_title", align=1, font_size=size + 4),
            "header_label":  _ps("base_header_label", align=0),
            "header_value":  _ps("base_header_value", align=0),
            "col_head":      _ps("base_col_head", align=1),
            "data_left":     _ps("base_data_left", align=0),
            "data_center":   _ps("base_data_center", align=1),
            "data_right":    _ps("base_data_right", align=2),
            "subtotal_left": _ps("base_subtotal_left", align=0),
            "subtotal_right": _ps("base_subtotal_right", align=2),
            "note":          _ps("base_note", align=0, font_size=size - 1),
            "sign_label":    _ps("base_sign_label", align=0),
            "sign_value":    _ps("base_sign_value", align=0),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # テキスト処理ユーティリティ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def preserve_whitespace(self, text: str) -> str:
        """半角空白 → NBSP (U+00A0) 変換。全角空白は維持。

        Paragraph が半角空白を折り畳むのを防ぐ。
        """
        if not text or not self._shrink_config.get("preserve_ws", True):
            return text
        return text.replace(" ", "\u00a0")

    def measure_text_width(
        self,
        text: str,
        font_size: float | None = None,
        font_name: str | None = None,
    ) -> float:
        """テキストの描画幅 (pt) を返す。複数行時は最長行の幅。"""
        if not text:
            return 0.0
        fs = font_size if font_size is not None else self.DEFAULT_FONT_SIZE
        fn = font_name or self.font_name
        lines = text.split("\n")
        return max(stringWidth(line, fn, fs) for line in lines)

    def shrink_to_fit(
        self,
        text: str,
        col_width_pt: float,
        base_size: float,
        *,
        font_name: str | None = None,
        key_hint: str = "",
    ) -> float:
        """列幅に収まる最大フォントサイズを返す（下限 5pt）。"""
        cfg = self._shrink_config
        if not text:
            return base_size
        fn = font_name or self.font_name
        available = max(col_width_pt - cfg["padding_pt"], 1.0)
        w = self.measure_text_width(text, base_size, fn)
        if w <= available:
            return base_size
        shrunk = base_size * (available / w) * cfg["safety"]
        min_size = cfg["min_size"]
        if shrunk < min_size:
            cache_key = f"{key_hint}:{text[:40]}"
            if cache_key not in self._shrink_warn_cache:
                self._shrink_warn_cache.add(cache_key)
                logger.warning(
                    "自動縮小が下限 %.1fpt に到達 (要求=%.2fpt, 列幅=%.1fpt): %r",
                    min_size, shrunk, col_width_pt, text[:60],
                )
            return min_size
        return shrunk

    def get_cell_style(
        self,
        base_style: ParagraphStyle,
        font_size: float,
    ) -> ParagraphStyle:
        """(base_style, font_size) キャッシュ付き ParagraphStyle 取得。"""
        key = (base_style.name, round(font_size, 2))
        cached = self._style_cache.get(key)
        if cached is not None:
            return cached
        new_style = ParagraphStyle(
            f"{base_style.name}_s{key[1]}",
            parent=base_style,
            fontSize=font_size,
            leading=font_size + 1.5,
        )
        self._style_cache[key] = new_style
        return new_style

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 高水準ビルダ（サブクラスから呼ぶ）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def make_paragraph(
        self,
        text: str,
        style: ParagraphStyle | str,
        *,
        fit_width_pt: float | None = None,
        fit_base_size: float | None = None,
        bold: bool = False,
        key_hint: str = "",
    ) -> Paragraph:
        """「空白保持 + 自動縮小 + 太字タグ + Paragraph生成」のオールインワン。

        Parameters
        ----------
        text : str
            描画テキスト。空文字なら空の Paragraph を返す。
        style : ParagraphStyle | str
            スタイル or self.styles のキー名。
        fit_width_pt : float | None
            指定時は col 幅に収まるよう自動縮小。
        fit_base_size : float | None
            自動縮小のベースサイズ。None なら style.fontSize。
        bold : bool
            True なら <b>...</b> タグで囲む。
        key_hint : str
            Warning ログの重複抑止キー用ヒント。
        """
        if isinstance(style, str):
            style = self.styles[style]

        if text is None:
            text = ""

        # 自動縮小
        if fit_width_pt is not None and text:
            base_size = fit_base_size if fit_base_size is not None else style.fontSize
            fit_size = self.shrink_to_fit(
                text, fit_width_pt, base_size,
                font_name=style.fontName, key_hint=key_hint,
            )
            if fit_size != base_size:
                style = self.get_cell_style(style, fit_size)

        # 空白保持
        t = self.preserve_whitespace(text)

        # 太字タグ
        if bold and t:
            t = f"<b>{t}</b>"

        return Paragraph(t, style)

    def make_table(
        self,
        rows: list[list],
        col_widths: list[float],
        row_heights: list[float] | None = None,
        *,
        style_commands: list[tuple] | None = None,
        repeat_rows: int = 0,
        split_in_row: int | None = 0,
        grid_color=None,
        grid_width: float | None = None,
        outer_width: float | None = None,
        draw_grid: bool = True,
    ) -> Table:
        """罫線が必ず最後まで描画される Table ファクトリ。

        タスク4 の修正ポイント:
          - GRID コマンドを (0,0)→(-1,-1) で発行し、最終行まで描画
          - 色は colors.black、太さは 0.5pt 以上を強制
          - 外枠 BOX も colors.black で別途引く

        Parameters
        ----------
        rows : list[list]
            テーブルデータ（各セル: Paragraph / 文字列 / 空）。
        col_widths : list[float]
            列幅 (pt)。
        row_heights : list[float] | None
            各行高さ (pt)。Paragraph の自動折返しによる伸長を防ぐため、
            絶対値での指定を推奨。
        style_commands : list[tuple] | None
            追加の TableStyle コマンド（GRID/BOX より後に適用されるので上書き可）。
        repeat_rows : int
            ページ跨ぎ時にヘッダ行として繰り返す行数。
        split_in_row : int | None
            Table に渡す splitInRow（0 = 行途中での分割を禁止）。
        grid_color, grid_width, outer_width : override 用。
        draw_grid : bool
            False のときは GRID を一切引かない（サブクラスがカスタム罫線を引く場合）。
        """
        if grid_color is None:
            grid_color = colors.black
        if grid_width is None:
            grid_width = max(
                config.BASE_BUILDER_DEFAULTS.get("grid_line_width", 0.5),
                0.5,
            )
        if outer_width is None:
            outer_width = config.BASE_BUILDER_DEFAULTS.get("outer_line_width", 0.8)

        table_kwargs = dict(colWidths=col_widths)
        if row_heights is not None:
            table_kwargs["rowHeights"] = row_heights
        if repeat_rows:
            table_kwargs["repeatRows"] = repeat_rows
        if split_in_row is not None:
            table_kwargs["splitInRow"] = split_in_row

        table = Table(rows, **table_kwargs)

        commands: list[tuple] = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]
        if draw_grid:
            # GRID: (0,0) → (-1,-1) で全セル、黒、0.5pt 以上
            commands.append(("GRID", (0, 0), (-1, -1), grid_width, grid_color))
            # 外枠（上から重ねる）
            commands.append(("BOX", (0, 0), (-1, -1), outer_width, grid_color))

        if style_commands:
            commands.extend(style_commands)

        table.setStyle(TableStyle(commands))
        return table

    def make_signblock(
        self,
        entries: list[tuple[str, str]],
        *,
        col_widths: tuple[float, float],
        font_size: float | None = None,
        row_height_pt: float | None = None,
        label_style_key: str = "sign_label",
        value_style_key: str = "sign_value",
        draw_grid: bool = False,
    ) -> Table:
        """サインブロック（ラベル/値の2列ペア）を生成する。

        Parameters
        ----------
        entries : list[tuple[str, str]]
            [("住　所", "長崎県..."), ("商号又は名称", "株式会社..."), ...]
        col_widths : (label_w, value_w) — pt
        draw_grid : bool
            False（既定）で罫線なしのクリーンなサインブロックを生成。
        """
        label_w, value_w = col_widths
        fsize = font_size if font_size is not None \
            else self.styles[value_style_key].fontSize

        rows: list[list] = []
        for label, value in entries:
            p_label = self.make_paragraph(
                label, label_style_key,
                fit_width_pt=label_w, fit_base_size=fsize,
                key_hint="signblock_label",
            )
            p_value = self.make_paragraph(
                value, value_style_key,
                fit_width_pt=value_w, fit_base_size=fsize,
                key_hint="signblock_value",
            )
            rows.append([p_label, p_value])

        row_heights = None
        if row_height_pt is not None:
            row_heights = [row_height_pt] * len(rows)

        return self.make_table(
            rows=rows,
            col_widths=[label_w, value_w],
            row_heights=row_heights,
            draw_grid=draw_grid,
            style_commands=[
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ],
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ページ計算
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_page_size(self) -> tuple[float, float]:
        """IS_LANDSCAPE を考慮した実ページサイズを返す (pt)。"""
        return landscape(self.PAGE_SIZE) if self.IS_LANDSCAPE else self.PAGE_SIZE

    def get_margins_pt(self) -> dict[str, float]:
        """マージンを pt 単位で返す。"""
        return {k: v * mm for k, v in self.MARGIN_MM.items()}

    def get_content_area(self) -> tuple[float, float]:
        """マージンを差し引いた描画領域 (width, height) pt を返す。"""
        w, h = self.get_page_size()
        m = self.get_margins_pt()
        return (w - m["left"] - m["right"], h - m["top"] - m["bottom"])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SimpleDocTemplate 生成
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _make_doc_template(self) -> SimpleDocTemplate:
        m = self.get_margins_pt()
        return SimpleDocTemplate(
            str(self.output_path),
            pagesize=self.get_page_size(),
            topMargin=m["top"],
            bottomMargin=m["bottom"],
            leftMargin=m["left"],
            rightMargin=m["right"],
            title=self.DOC_TITLE,
            author=self.DOC_AUTHOR,
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Flowable ヘルパ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def wrap_in_keepinframe(
        self,
        flowables: list,
        *,
        mode: str = "shrink",
        max_width: float | None = None,
        max_height: float | None = None,
    ) -> KeepInFrame:
        """Flowable 群を KeepInFrame で包み、1ページに強制収容する。

        mode:
          - "shrink"   : 溢れる場合は全体を縮小（TermsBuilder 既定）
          - "truncate" : 溢れる部分を切り捨て
          - "overflow" : 無視して溢れさせる（デバッグ用）
        """
        w, h = self.get_content_area()
        return KeepInFrame(
            maxWidth=max_width or w,
            maxHeight=max_height or h,
            content=flowables,
            mode=mode,
        )

    def spacer(self, height_mm: float) -> Spacer:
        """ミリ単位で Spacer を作る。"""
        return Spacer(1, height_mm * mm)
