"""
内訳書データ抽出のための補助ヘルパー群。

extractor.py の `extract_nairaku_data` から呼び出される、
ヘッダ/データ終端の検出・セル結合キャッシュ構築・列スパン解決の
ロジックを集約する。

含まれる主な機能:
  - 内訳書の列ヘッダ最終行 / データ領域最終行の自動検出
  - A〜C 列にまたがる結合判定
  - openpyxl の `merged_cells.ranges` を 1 回スキャンしてキャッシュ化
  - 行ごとの col_spans 計算（Excel 結合 + 動的疑似結合の合成）

extractor.py からは re-export することで、既存の internal 呼び出しと
将来的な外部利用に互換性を保つ。

Step 8a での切り出し範囲:
  ここに置いた 7 ヘルパは、extract_nairaku_data 本体とは独立した
  「読み取り専用の前処理 + 計算」のみを担当する。本体（NairakuRow を
  逐次積み上げていくループ）は次のサブステップ (Step 8b) で移動する想定。
"""
from __future__ import annotations

from openpyxl.worksheet.worksheet import Worksheet

from . import config
from .extractor_utils import _cell_str, _normalize
from .nairaku_text_utils import _NAIRAKU_HEADER_KEYWORDS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ヘッダ / データ範囲の検出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _detect_header_end_row(ws: Worksheet, max_scan: int = 15) -> int:
    """内訳書の列ヘッダの最終行を検出する。

    「工種」「種別」などのキーワードを含む行を探し、
    その最後の行番号を返す。見つからなければ 9（デフォルト）。
    """
    header_rows: list[int] = []
    for row in range(1, max_scan + 1):
        for col in range(1, 6):
            val = _cell_str(ws, row, col)
            if val is None:
                continue
            norm = _normalize(val)
            if any(_normalize(kw) in norm for kw in _NAIRAKU_HEADER_KEYWORDS):
                header_rows.append(row)
                break
    if header_rows:
        return max(header_rows)
    return 9


def _detect_data_end_row(
    ws: Worksheet,
    start_row: int,
    max_row: int,
) -> int:
    """内訳書のデータ領域の最終行を検出する。

    「下請金額」「下請代金」行を探し、その行番号を返す。
    見つからなければ max_row をそのまま返す。
    """
    for row in range(start_row, max_row + 1):
        for col in (1, 2, 3):
            val = _cell_str(ws, row, col)
            if val is None:
                continue
            norm = _normalize(val)
            if "下請金額" in norm or "下請代金" in norm:
                return row
    return max_row


def _is_merged_across_abc(ws: Worksheet, row: int) -> bool:
    """指定行の A〜C 列にまたがるセル結合があるか判定する。"""
    for merged_range in ws.merged_cells.ranges:
        if (merged_range.min_row <= row <= merged_range.max_row
                and merged_range.min_col == 1
                and merged_range.max_col >= 3):
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  セル結合キャッシュ + 列スパン算出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# セル結合キャッシュの型エイリアス。
# キー: (row, col) は 1-indexed の Excel 座標。
# 値: span 情報
#   - >=2 : このセルは結合のアンカー（colspan=値 で描画）
#   - 0   : 左側のセルからの結合範囲に覆われており、描画不要
# 「結合なし」のセルはキャッシュに登録しない（dict.get のデフォルト 1 で対応）。
MergedSpanCache = dict[tuple[int, int], int]


def _build_merged_cells_cache(
    ws: Worksheet, max_col: int = 15,
) -> MergedSpanCache:
    """``ws.merged_cells.ranges`` を 1 回だけ走査し、行ごとの結合情報を
    ``(row, col) → span`` の辞書に展開する。

    ここでの前処理により、行ごとの col_spans 算出は O(max_col) の dict
    ルックアップに落ちる（従来は 1 行あたり全結合範囲を線形走査していた）。

    ルール:
      - 結合範囲は ``[1, max_col]`` にクリップする（範囲外は無視）。
      - 単一セル結合 (min_col == max_col) はキャッシュに登録しない。
      - 垂直方向の結合は、通過する全行に対して水平スパン幅を登録する。

    Parameters
    ----------
    ws : Worksheet
        openpyxl のワークシート。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    MergedSpanCache
        ``(row, col)`` をキーとした結合情報辞書。
        結合に関与しないセルはキーが存在しない（ルックアップ時に既定値 1 を使う）。
    """
    cache: MergedSpanCache = {}
    for merged_range in ws.merged_cells.ranges:
        # [1, max_col] にクリップ
        start_col = max(merged_range.min_col, 1)
        end_col = min(merged_range.max_col, max_col)
        if start_col > max_col or end_col < 1 or end_col < start_col:
            continue
        # 単一セル（水平方向に結合なし）はキャッシュ不要
        if start_col == end_col:
            continue
        span_width = end_col - start_col + 1
        # 垂直方向に広がる結合も、各行について同じ水平スパン幅を記録する
        for r in range(merged_range.min_row, merged_range.max_row + 1):
            cache[(r, start_col)] = span_width          # アンカー
            for c in range(start_col + 1, end_col + 1):
                cache[(r, c)] = 0                        # 隠蔽セル
    return cache


def _compute_col_spans(
    cache: MergedSpanCache, row: int, max_col: int = 15,
) -> list[int]:
    """行ごとの col_spans リストを事前構築キャッシュから O(max_col) で取得する。

    返り値 (長さ ``max_col``):
      - ``1``   : 結合なし（単独セル） — キャッシュにキーが存在しない
      - ``n``   : 当該セルから右に n 個の結合 → ``colspan=n`` で描画
      - ``0``   : 左側のセルからの結合範囲に覆われており、描画不要

    Parameters
    ----------
    cache : MergedSpanCache
        ``_build_merged_cells_cache`` で事前構築された結合情報辞書。
    row : int
        1-indexed の Excel 行番号。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    list[int]
        長さ ``max_col`` の結合情報配列。
    """
    return [cache.get((row, col), 1) for col in range(1, max_col + 1)]


def _apply_dynamic_pseudo_merge(
    col_spans: list[int],
    val_a: str | None,
    val_b: str | None,
    val_c: str | None,
    threshold: int | None = None,
) -> list[int]:
    """A/B/C 列の内容に応じた「動的疑似結合」を col_spans に被せて返す。

    下請代金内訳書では、作業者が Excel でセル結合を張り忘れたまま長い
    工種名や種別名を入力しているケースが多く、そのままレンダリングすると
    右隣の空セルに文字が「はみ出て見える」ように罫線が走る。このヘルパー
    は「対象セルの文字数が閾値以上で長い」ときに限り A/B/C 列の colspan
    を自動補正する。閾値未満の短いテキストには一切手を出さず、独立セル
    としての罫線を維持する（これが旧仕様からの最重要な差分）。

    ルール (厳格な文字数ロック方式, 優先順位順に 1 つだけ適用):
      (a) len(A.strip()) >= threshold  AND  B 空  AND  C 空
          → col_spans[0]=3, [1]=0, [2]=0    （A が A:C にまたがる）
      (b) len(A.strip()) >= threshold  AND  B 空  AND  C 有り
          → col_spans[0]=2, [1]=0           （A が A:B にまたがる, C は単独）
      (c) len(B.strip()) >= threshold  AND  C 空  （A の長さは問わない）
          → col_spans[1]=2, [2]=0           （A 単独, B が B:C にまたがる）

      上記いずれにも該当しない（対象セルが空, または閾値未満）場合は
      col_spans をそのまま返す（= 独立セル + 罫線維持）。

    除外条件:
      Excel 側で既に A/B/C のいずれかに結合が設定されている場合
      （= ``col_spans[0..2]`` に 1 以外の値が 1 つでも含まれる場合）は、
      作業者の明示的な意図を尊重してこのロジック全体をスキップする。

    文字数の判定:
      必ず ``.strip()`` で前後空白を除去してから ``len()`` を取る。
      全角/半角とも 1 文字 = 1 カウント。既定閾値は
      ``config.NAIRAKU_AUTO_MERGE_THRESHOLD`` （全角 8 文字）。

    Parameters
    ----------
    col_spans : list[int]
        ``_compute_col_spans()`` の結果。15 要素を想定。
        この関数は新しいリストを返すため、入力は破壊しない。
    val_a, val_b, val_c : str | None
        A/B/C 列の原文。None または空白のみは「空」と見なす。
    threshold : int | None
        「長い」と判定する文字数（これ以上で結合発動）。``None`` の場合は
        ``config.NAIRAKU_AUTO_MERGE_THRESHOLD`` を参照。

    Returns
    -------
    list[int]
        A/B/C 列について疑似結合を反映した新しい col_spans リスト。
        入力の他列 (D..O) は変更しない。いずれのルールも非該当であれば
        入力と等価な（ただし独立した）リストを返す。
    """
    new_spans = list(col_spans)

    # ── 除外: Excel 側で A/B/C のいずれかに既存結合がある ─────────────
    # _compute_col_spans の結果で col_spans[0..2] がすべて 1 のときだけ
    # 動的疑似結合を発動する。1 つでも 1 以外ならユーザー指定を尊重する。
    if (len(new_spans) < 3
            or new_spans[0] != 1
            or new_spans[1] != 1
            or new_spans[2] != 1):
        return new_spans

    if threshold is None:
        threshold = config.NAIRAKU_AUTO_MERGE_THRESHOLD

    # 前後空白を除去してから長さを評価。空白のみは「空」扱い。
    a = (val_a or "").strip()
    b = (val_b or "").strip()
    c = (val_c or "").strip()

    # ルール (a): A が長い & B 空 & C 空 → A を 3 列分にスパン
    if len(a) >= threshold and not b and not c:
        new_spans[0] = 3
        new_spans[1] = 0
        new_spans[2] = 0
    # ルール (b): A が長い & B 空 & C 有り → A を 2 列分にスパン, C は単独
    elif len(a) >= threshold and not b and c:
        new_spans[0] = 2
        new_spans[1] = 0
        # new_spans[2] は 1 のまま
    # ルール (c): B が長い & C 空 → B を 2 列分にスパン（A の長さは問わない）
    elif len(b) >= threshold and not c:
        # new_spans[0] は 1 のまま（A 単独で罫線維持）
        new_spans[1] = 2
        new_spans[2] = 0
    # 上記いずれにも該当しなければ結合せず罫線維持

    return new_spans


def _resolve_col_spans(
    cache: MergedSpanCache,
    row: int,
    val_a: str | None = None,
    val_b: str | None = None,
    val_c: str | None = None,
    max_col: int = 15,
) -> list[int]:
    """行の col_spans を「Excel 結合 + 動的疑似結合」の合成で決定する。

    呼び出し側はこの 1 関数だけを使えば、キャッシュ参照と A/B/C の
    自動補正が両方適用された最終的な col_spans を得られる。
    val_a/val_b/val_c を省略した場合は動的疑似結合は発動しない
    （= Excel の結合情報のみが反映される）。

    Parameters
    ----------
    cache : MergedSpanCache
        ``_build_merged_cells_cache`` で事前構築された結合情報辞書。
    row : int
        1-indexed の Excel 行番号。
    val_a, val_b, val_c : str | None
        疑似結合判定に使う A/B/C 列の値。NairakuRow に実際に格納される
        値（レンダリング対象の値）を渡すこと。
    max_col : int
        考慮する列数（既定は 15 = A..O）。

    Returns
    -------
    list[int]
        最終的な col_spans（長さ max_col）。
    """
    spans = _compute_col_spans(cache, row, max_col)
    return _apply_dynamic_pseudo_merge(spans, val_a, val_b, val_c)
