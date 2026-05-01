"""
PDF スタンプモジュール — PyMuPDF (fitz) で空 PDF にテキストを配置する。

対応機能:
- point 型       : insert_text で固定座標に描画
- rect 型        : insert_textbox で矩形内に描画（自動縮小ロジック付き）
- 縦中央揃え     : rect 型は矩形の縦方向中心にテキストを配置
- 回転ページ対応 : derotation_matrix による座標変換
- 金額フィールド : カンマ区切り円表記へ自動フォーマット
- カスタムフォント: stamp_map の fontpath で TTF/OTF を動的登録
- 斜体 (italic)  : is_italic=True で shear 行列による右傾き描画
- 太字 (bold)    : is_bold=True で render_mode=2（塗りつぶし＋縁取り）による擬似ボールド
- デバッグ枠     : output_path に "プレビュー" が含まれる場合、rect を赤枠で描画
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import platform

from . import config

logger = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────
_KINGAKU_PREFIX  = "kingaku_"   # 金額フィールド判定プレフィックス
_MIN_FONTSIZE    = 4.0          # 自動縮小の下限 (pt)
_SHRINK_STEP     = 0.5          # 自動縮小の刻み幅 (pt)
_DEFAULT_FONT    = "japan"      # fontpath 未指定時のデフォルト
_MEASURE_HEIGHT  = 10_000.0     # 高さ計測用ダミー矩形の仮高さ (pt)
_DEBUG_BORDER_COLOR = (1.0, 0.0, 0.0)   # デバッグ枠の色（赤）
_DEBUG_BORDER_WIDTH = 0.5               # デバッグ枠の線幅 (pt)

# 斜体用 shear 行列（右に 0.3 傾ける）
_ITALIC_MATRIX = fitz.Matrix(1, 0, 0.3, 1, 0, 0)

# 太字（擬似ボールド）
_BOLD_RENDER_MODE        = 2     # fill + stroke で太く見せる (PyMuPDF render_mode)
_BOLD_BORDER_WIDTH_DEFAULT = 0.15  # bold_width 未指定時のデフォルト縁取り線幅 (pt)
_NORMAL_RENDER_MODE      = 0     # 通常: fill のみ
_NORMAL_BORDER_WIDTH     = 1     # 通常時のデフォルト border_width


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内部ユーティリティ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _format_kingaku(value: str) -> str:
    """
    金額文字列をカンマ区切り円表記に変換する。
    例: "1000000" → "￥1,000,000-"
    数値変換に失敗した場合はそのまま返す。
    """
    cleaned = re.sub(r"[￥¥\\,、\-ー円]", "", value.strip())
    try:
        num = int(float(cleaned))
        return f"￥{num:,}"
    except (ValueError, OverflowError):
        logger.debug("金額フォーマット変換失敗（そのまま出力）: '%s'", value)
        return value


def _register_font(
page: fitz.Page,
    fontpath: str,
    registry: dict[int, dict[str, str]],
    page_num: int,
) -> str:
    """
    カスタムフォントをページに登録し、登録済みの fontname を返す。
    """
    # --- OS判定とフォントパスの自動修正 ---
    if platform.system() == "Linux":
        # Windowsのパス形式が含まれている、またはファイルが存在しない場合はLinux用へ
        if "Windows" in fontpath or ":" in fontpath or not Path(fontpath).exists():
            fontpath = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    # --- ここまで ---

    page_cache = registry.setdefault(page_num, {})

    if fontpath in page_cache:
        return page_cache[fontpath]

    if not Path(fontpath).exists():
        logger.warning("カスタムフォントが見つかりません: %s → japan を使用", fontpath)
        page_cache[fontpath] = _DEFAULT_FONT
        return _DEFAULT_FONT

    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", Path(fontpath).stem)[:20]
    fontname = f"cf_{stem}"

    try:
        page.insert_font(fontname=fontname, fontfile=fontpath)
        page_cache[fontpath] = fontname
        logger.debug("カスタムフォント登録: %s → %s", Path(fontpath).name, fontname)
    except Exception:
        logger.warning("カスタムフォント登録失敗: %s → japan を使用", fontpath, exc_info=True)
        fontname = _DEFAULT_FONT
        page_cache[fontpath] = fontname

    return fontname


def _tmp_fontname(tmp_page: fitz.Page, fontpath: Optional[str], fontname: str) -> str:
    """
    ダミーページ用フォント名を解決する。
    カスタムフォントを tmp_page に登録して fontname を返す。
    失敗時は _DEFAULT_FONT にフォールバック。
    """
    if not fontpath or fontname == _DEFAULT_FONT:
        return fontname
    try:
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", Path(fontpath).stem)[:20]
        tmp_fn = f"cf_{stem}"
        tmp_page.insert_font(fontname=tmp_fn, fontfile=fontpath)
        return tmp_fn
    except Exception:
        return _DEFAULT_FONT


def _determine_fontsize(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fontsize: float,
    fontname: str,
    fontpath: Optional[str],
    align: int,
    rotation: int,
) -> float:
    """
    テキストが rect に収まる最大フォントサイズを決定して返す。
    収まらない場合は _SHRINK_STEP ずつ縮小し、_MIN_FONTSIZE を下限とする。
    morph はレイアウト計算に影響しないため省略する。
    """
    current_size = fontsize

    while current_size >= _MIN_FONTSIZE:
        tmp_doc = fitz.open()
        try:
            tmp_page = tmp_doc.new_page(width=page.rect.width, height=page.rect.height)
            fn = _tmp_fontname(tmp_page, fontpath, fontname)
            rc = tmp_page.insert_textbox(
                rect, text,
                fontsize=current_size,
                fontname=fn,
                align=align,
                rotate=rotation,
            )
            if rc >= 0:
                break
        finally:
            tmp_doc.close()

        current_size -= _SHRINK_STEP

    if current_size < _MIN_FONTSIZE:
        current_size = _MIN_FONTSIZE
        logger.warning("自動縮小が下限 (%.1fpt) に到達: text='%s'", _MIN_FONTSIZE, text[:30])

    return current_size


def _measure_text_height(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fontsize: float,
    fontname: str,
    fontpath: Optional[str],
    align: int,
    rotation: int,
) -> float:
    """
    指定フォントサイズでテキストが実際に占める縦幅（pt）を計測して返す。

    幅を rect に固定し、高さだけ _MEASURE_HEIGHT の仮 rect でレンダリングし、
    戻り値の残余スペースから使用高さを逆算する。

    日本語フォントは ascender/descender が大きく行高さが fontsize より
    かなり大きくなる。insert_textbox の返値がその実測値として最も正確。
    remaining が負（仮 10000pt 矩形でもオーバーフロー）は想定外なので
    フォントメトリクスによるフォールバックを使う。
    """
    measure_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + _MEASURE_HEIGHT)
    tmp_doc = fitz.open()
    try:
        tmp_page = tmp_doc.new_page(
            width=max(page.rect.width, rect.x1 + 1),
            height=rect.y0 + _MEASURE_HEIGHT + 1,
        )
        fn = _tmp_fontname(tmp_page, fontpath, fontname)
        remaining = tmp_page.insert_textbox(
            measure_rect, text,
            fontsize=fontsize,
            fontname=fn,
            align=align,
            rotate=rotation,
        )

        if remaining >= 0:
            # 正常: 使用高さ = 仮高さ − 残余スペース
            used = _MEASURE_HEIGHT - remaining
        else:
            # 異常（10000pt でもオーバーフロー）: フォントメトリクスで近似
            logger.warning("_measure_text_height: remaining<0 (%.1f) → フォントメトリクスで近似", remaining)
            used = _line_height_from_font(fontpath, fontname, fontsize)

        # 最低でも1行分の高さは確保（0以下になる浮動小数点誤差ガード）
        return max(used, _line_height_from_font(fontpath, fontname, fontsize) * 0.5)

    except Exception:
        logger.debug("テキスト高さ計測失敗（フォントメトリクスで近似）: text='%s'", text[:20], exc_info=True)
        return _line_height_from_font(fontpath, fontname, fontsize)
    finally:
        tmp_doc.close()


def _line_height_from_font(
    fontpath: Optional[str],
    fontname: str,
    fontsize: float,
) -> float:
    """
    フォントの ascender / descender メトリクスから1行分の高さ (pt) を返す。
    日本語フォント（MS明朝等）はこの値が fontsize * 1.3 〜 1.5 になる。
    取得に失敗した場合は fontsize * 1.2 を返す。
    """
    try:
        if fontpath and Path(fontpath).exists():
            fnt = fitz.Font(fontfile=fontpath)
        else:
            # 組み込みフォントは fontname で取得。"japan" 等は非対応なので helv で代替
            try:
                fnt = fitz.Font(fontname=fontname)
            except Exception:
                fnt = fitz.Font("helv")
        return fontsize * (fnt.ascender - fnt.descender)
    except Exception:
        return fontsize * 1.2


def _vcenter_rect(rect: fitz.Rect, used_height: float) -> fitz.Rect:
    """
    矩形の縦方向中央にテキストが配置されるよう、上端をオフセットした rect を返す。
    used_height が rect の高さを超える場合はオフセットなし（上端そのまま）。
    """
    v_offset = max(0.0, (rect.height - used_height) / 2.0)
    return fitz.Rect(rect.x0, rect.y0 + v_offset, rect.x1, rect.y1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  描画コア
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _draw_circled_label(
    page: fitz.Page,
    center: fitz.Point,
    label: str,
    radius: float,
    fontname: str,
    fontsize: float,
    color: tuple = (0, 0, 0),
    line_width: float = 0.6,
    rotate: int = 90,
) -> None:
    """
    円で囲まれた1文字ラベルを描画する（例: ○増、○減）。

    Parameters
    ----------
    center   : 円の中心座標（物理座標）
    label    : 円内に描画する1文字
    radius   : 円の半径 (pt)
    fontname : 登録済みフォント名
    fontsize : ラベルの文字サイズ (pt)
    color    : 線・文字の色 (R, G, B) 各 0.0〜1.0
    line_width : 円の線幅 (pt)
    rotate   : テキスト回転角度（90=右90°回転）
    """
    # 円を描画
    page.draw_circle(center, radius, color=color, width=line_width)

    # 文字を円の中央に配置（insert_text でピンポイント座標指定）
    # rotate=90 で右90°回転した状態で、視覚的に円の中心に来るようオフセット
    text_pt = fitz.Point(
        center.x - fontsize * -0.3,
        center.y + fontsize * 0.55,
    )
    page.insert_text(
        text_pt,
        label,
        fontsize=fontsize,
        fontname=fontname,
        color=color,
        rotate=rotate,
    )


def _draw_debug_border(page: fitz.Page, rect: fitz.Rect) -> None:
    """デバッグ用の赤い枠線を rect に描画する。"""
    try:
        page.draw_rect(
            rect,
            color=_DEBUG_BORDER_COLOR,
            width=_DEBUG_BORDER_WIDTH,
        )
    except Exception:
        logger.debug("デバッグ枠描画失敗", exc_info=True)


def _stamp_rect(
    key: str,
    page: fitz.Page,
    visual_rect: fitz.Rect,
    phys_rect: fitz.Rect,
    derotation: fitz.Matrix,
    text: str,
    fontsize: float,
    fontname: str,
    fontpath: Optional[str],
    align: int,
    page_rotation: int,
    morph: Optional[tuple],
    render_mode: int,
    border_width: float,
    debug_mode: bool,
) -> None:
    """
    rect 型の描画処理をすべて担う。

    ポイント: /Rotate=90 等のPDF回転対応
    - フォントサイズ判定・高さ計測・縦中央オフセット算出 は visual_rect（視覚座標）で行う
    - 最終的な insert_textbox には derotation を適用した物理座標の rect を渡す
    - これにより rotate=90 のページでも正確に visual 高さを計測できる

    Parameters
    ----------
    key           : stamp_map_list のキー名（デバッグ print に使用）
    visual_rect   : config.py で指定した視覚座標の rect（計測・オフセット計算の基準）
    phys_rect     : visual_rect * derotation した物理 rect（デバッグ枠表示に使用）
    derotation    : page.derotation_matrix（draw_visual_rect を物理座標に変換するため）
    align         : 0=左, 1=中央, 2=右  ← fitz.TEXT_ALIGN_* と同値
    page_rotation : page.rotation（insert_textbox の rotate 引数に使用）
    render_mode   : 0=通常(fill), 2=擬似ボールド(fill+stroke)
    border_width  : render_mode=2 時の縁取り線幅 (pt)
    debug_mode    : True のとき phys_rect を赤枠で描画する
    """
    # ── 1) デバッグ枠（物理 rect を赤枠で可視化） ──
    if debug_mode:
        _draw_debug_border(page, phys_rect)

    # ── 2) フォントサイズ確定（視覚座標・rotate=0 で計測） ──
    #      visual_rect は視覚空間の正しいアスペクト比を持つため
    #      回転ページでも正確なオーバーフロー検出ができる
    final_size = _determine_fontsize(
        page, visual_rect, text, fontsize, fontname, fontpath, align, rotation=0
    )
    if final_size != fontsize:
        logger.debug("フォント自動縮小: %.1f → %.1f pt (key='%s')", fontsize, final_size, key)

    # ── 3) テキストの視覚的な高さを計測（視覚座標・rotate=0） ──
    used_height = _measure_text_height(
        page, visual_rect, text, final_size, fontname, fontpath, align, rotation=0
    )

    # ── 4) 縦中央オフセットを視覚座標で算出 ──
    #      v_offset = (視覚枠の高さ - テキストの視覚高さ) / 2
    v_offset = max(0.0, (visual_rect.height - used_height) / 2.0)

    # ── デバッグ print（常に出力） ──
    print(
        f"[vcenter] key='{key}' | "
        f"枠の高さ(visual)={visual_rect.height:.2f}pt | "
        f"文字の高さ(visual)={used_height:.2f}pt | "
        f"v_offset={v_offset:.2f}pt"
    )

    # ── 5) 視覚空間でオフセット適用 → 物理座標に変換 ──
    #      y0 を v_offset 分下にずらした視覚 rect を derotation で物理座標に変換
    draw_visual_rect = fitz.Rect(
        visual_rect.x0,
        visual_rect.y0 + v_offset,
        visual_rect.x1,
        visual_rect.y1,            # 下端は変えない
    )
    draw_phys_rect = draw_visual_rect * derotation

    # ── 6) 本番ページへ描画（物理座標・ページ回転を反映） ──
    page.insert_textbox(
        draw_phys_rect,
        text,
        fontsize=final_size,
        fontname=fontname,
        align=align,                 # 0=左, 1=中央, 2=右
        rotate=page_rotation,        # ページの /Rotate 値でテキストを回転
        morph=morph,
        render_mode=render_mode,     # 0=通常 / 2=擬似ボールド
        border_width=border_width,   # 縁取り線幅 (render_mode=2 時に有効)
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン関数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def stamp_pdf(
    template_path: Path,
    output_path: Path,
    data_dict: dict[str, str | None],
    stamp_map_list: list[dict],
) -> Path:
    """
    PDF テンプレートに data_dict の内容をスタンプして保存する。

    stamp_map_list の各エントリで利用できるオプションキー
    ──────────────────────────────────────────────────
    fontpath  : str   カスタムフォントの絶対ファイルパス。省略時は "japan"。
    is_italic : bool  True の場合、shear 行列で文字を右に傾けて描画する。
    is_bold   : bool  True の場合、render_mode=2 で塗りつぶし＋縁取りの擬似ボールド描画。
    align     : int   0=左寄せ(デフォルト) / 1=中央寄せ / 2=右寄せ

    デバッグモード（output_path に "プレビュー" を含む場合のみ）
    ──────────────────────────────────────────────────
    - rect 型の矩形を赤い枠線（0.5pt）で可視化する。

    Parameters
    ----------
    template_path  : 空の PDF テンプレートファイル。
    output_path    : スタンプ済み PDF の出力先。
    data_dict      : フィールド名 → 表示文字列。None / 空文字はスキップ。
    stamp_map_list : config.PDF_STAMP_MAP["chumonsho"] 等のスタンプ定義リスト。

    Returns
    -------
    Path : 出力先パス。
    """
    if not template_path.exists():
        raise FileNotFoundError(f"PDF テンプレートが見つかりません: {template_path}")

    # デバッグモード: output_path のファイル名に "プレビュー" を含む場合
    debug_mode = "プレビュー" in Path(output_path).name
    if debug_mode:
        logger.info("デバッグモード ON: rect に赤枠を描画します")

    logger.info("PDF スタンプ開始: %s → %s", template_path.name, output_path.name)

    doc = fitz.open(str(template_path))

    # カスタムフォント登録キャッシュ: {page_num: {fontpath: fontname}}
    font_registry: dict[int, dict[str, str]] = {}

    try:
        for entry in stamp_map_list:
            key        = entry.get("key", "")
            page_num   = entry.get("page", 0)
            stamp_type = entry.get("type", "point")

            # ── データ存在チェック ──
            text = data_dict.get(key)
            if text is None or str(text).strip() == "":
                continue
            text = str(text).strip()

            # ── 金額フォーマット ──
            if key.startswith(_KINGAKU_PREFIX):
                text = _format_kingaku(text)

            # ── ページ範囲チェック ──
            if page_num >= len(doc):
                logger.warning(
                    "ページ範囲外: key=%s, page=%d (全%dページ)",
                    key, page_num, len(doc),
                )
                continue

            page      = doc[page_num]
            fontsize  = float(entry.get("size", 11))
            align     = int(entry.get("align", 0))   # 0=左, 1=中央, 2=右
            fontpath  = entry.get("fontpath")                    # str or None
            is_italic = bool(entry.get("is_italic", False))
            is_bold   = bool(entry.get("is_bold",   False))

            # ── 太字パラメータを解決 ──
            # is_bold=True の場合: bold_width の個別指定があればその値、なければデフォルト 0.15
            # is_bold=False の場合: render_mode=0（通常）、border_width=1（デフォルト）
            render_mode  = _BOLD_RENDER_MODE if is_bold else _NORMAL_RENDER_MODE
            border_width = (
                float(entry.get("bold_width", _BOLD_BORDER_WIDTH_DEFAULT))
                if is_bold
                else _NORMAL_BORDER_WIDTH
            )

            # ── フォント名を解決 ──
            if fontpath:
                fontname = _register_font(page, str(fontpath), font_registry, page_num)
            else:
                fontname = entry.get("fontname", _DEFAULT_FONT)

            # ── 回転対応 ──
            derotation    = page.derotation_matrix
            page_rotation = page.rotation

            try:
                # ────────────────────────────────────────
                #  point 型: insert_text で固定座標に描画
                # ────────────────────────────────────────
                if stamp_type == "point":
                    x = float(entry.get("x", 0))
                    y = float(entry.get("y", 0))
                    phys_point = fitz.Point(x, y) * derotation

                    # 斜体: pivot = テキスト起点, matrix = shear 行列
                    morph = (phys_point, _ITALIC_MATRIX) if is_italic else None

                    page.insert_text(
                        phys_point,
                        text,
                        fontsize=fontsize,
                        fontname=fontname,
                        rotate=page_rotation,
                        morph=morph,
                        render_mode=render_mode,    # 0=通常 / 2=擬似ボールド
                        border_width=border_width,  # 縁取り線幅 (render_mode=2 時に有効)
                    )

                # ────────────────────────────────────────
                #  rect 型: 縦中央 + 自動縮小 + デバッグ枠
                # ────────────────────────────────────────
                elif stamp_type == "rect":
                    r = entry.get("rect", (0, 0, 0, 0))
                    visual_rect = fitz.Rect(r)

                    # 空 rect（プレースホルダー）はスキップ
                    if visual_rect.is_empty or visual_rect.width <= 0 or visual_rect.height <= 0:
                        logger.debug("rect が未設定（空）のためスキップ: key=%s, rect=%s", key, r)
                        continue

                    phys_rect = visual_rect * derotation

                    # 斜体: pivot = 物理 rect の左上, matrix = shear 行列
                    morph = (phys_rect.top_left, _ITALIC_MATRIX) if is_italic else None

                    _stamp_rect(
                        key=key,
                        page=page,
                        visual_rect=visual_rect,
                        phys_rect=phys_rect,
                        derotation=derotation,
                        text=text,
                        fontsize=fontsize,
                        fontname=fontname,
                        fontpath=str(fontpath) if fontpath else None,
                        align=align,
                        page_rotation=page_rotation,
                        morph=morph,
                        render_mode=render_mode,
                        border_width=border_width,
                        debug_mode=debug_mode,
                    )

                    # ── 金額フィールドの左横に○増/○減を描画 ──
                    if key.startswith(_KINGAKU_PREFIX) and key != "kingaku_direction":
                        direction = data_dict.get("kingaku_direction")
                        if direction:
                            # MS明朝フォントを登録（囲み文字用）
                            _ms_mincho = config.MS_MINCHO
                            circle_fontname = _register_font(
                                page, _ms_mincho, font_registry, page_num,
                            )
                            # 円のサイズ: 金額フォントサイズに合わせる
                            circle_radius = fontsize * 0.55
                            circle_fontsize = fontsize * 0.7
                            # 視覚座標で rect の左横に配置
                            circle_center_visual = fitz.Point(
                                visual_rect.x0 - circle_radius - -7,
                                (visual_rect.y0 + visual_rect.y1) / 2 - 2,
                            )
                            circle_center_phys = circle_center_visual * derotation
                            _draw_circled_label(
                                page=page,
                                center=circle_center_phys,
                                label=direction,
                                radius=circle_radius,
                                fontname=circle_fontname,
                                fontsize=circle_fontsize,
                            )

                else:
                    logger.warning("未知の stamp type: '%s' (key=%s)", stamp_type, key)

            except Exception:
                logger.warning("スタンプ描画失敗: key=%s", key, exc_info=True)

        # ── 保存 ──
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.subset_fonts()
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
        logger.info("PDF スタンプ完了: %s", output_path.name)
        return output_path

    finally:
        doc.close()
