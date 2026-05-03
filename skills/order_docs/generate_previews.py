"""
PDFスタンプ座標 プレビュー生成スクリプト

config.py の PDF_STAMP_MAP 座標を使い、サンプルデータを赤色でスタンプした
プレビューPDFを生成する。VS Code で開いて座標を確認・微調整するためのツール。

使い方:
    python -m system_core.generate_previews
"""
from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

# ── プロジェクト構成に合わせたインポート ──
from . import config

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  サンプルデータ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SAMPLE_DATA: dict[str, str] = {
    # 共通
    "koji_kenmei":    "立岩川（ホ）火山砂防工事",
    "koji_basho":     "長崎県島原市北千本木町地内",
    # 契約日
    "contract_year":  "7",
    "contract_month": "12",
    "contract_day":   "1",
    # 業者情報
    "vendor_company": "株式会社サンプル業者",
    "vendor_name":    "代表取締役 山田 太郎",
    "vendor_address": "長崎県長崎市大浦町1丁目1-1",
    # 金額
    "kingaku_ukeoi":  "99999999",
    "kingaku_koji":   "90909090",
    "kingaku_zei":    "9090909",
    "kingaku_direction": "増",  # 増減マーク（○増/○減）プレビュー用
    # 工期
    "kouki_start_year":  "7",
    "kouki_start_month": "12",
    "kouki_start_day":   "8",
    "kouki_end_year":    "10",
    "kouki_end_month":   "3",
    "kouki_end_day":     "31",
    # 元請負人名称・代表構成員
    "motouke_company":   "株式会社ダミー元請",
    "daikyo_koseiin":    "株式会社ダミー構成員",
    "jv_name":           "Factory・○○○○建設工事共同企業体",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  プレビュー生成 (PyMuPDF 直接描画 — pdf_stamper 非依存)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# プレビュー描画色: 赤
RED = (1, 0, 0)
BORDER_GREEN = (0, 0.7, 0)
LABEL_BLUE = (0, 0, 0.8)

# 金額フォーマット用
_KINGAKU_KEYS = {"kingaku_ukeoi", "kingaku_koji", "kingaku_zei"}


def _format_value(key: str, raw: str) -> str:
    """サンプルデータをスタンプ用にフォーマットする。"""
    import re
    if key in _KINGAKU_KEYS:
        cleaned = re.sub(r"[￥¥\\,、\-ー円]", "", raw.strip())
        try:
            num = int(float(cleaned))
            return f"\uffe5{num:,}"
        except ValueError:
            return raw
    return raw


def _generate_one(
    doc_type: str,
    stamp_entries: list[dict],
    template_path: Path,
    output_path: Path,
    sample_data: dict[str, str] | None = None,
) -> None:
    """
    1つのテンプレートに対してプレビューPDFを生成する。
    - 赤文字でサンプルデータをスタンプ
    - 緑の枠線で rect 範囲を表示
    - 青のラベルで key 名を表示
    """
    if not template_path.exists():
        logger.warning("テンプレート未配置: %s", template_path)
        return

    doc = fitz.open(str(template_path))

    for entry in stamp_entries:
        key = entry["key"]
        page_idx = entry.get("page", 0)
        if page_idx >= len(doc):
            logger.warning("page %d がPDFに存在しません (type=%s, key=%s)", page_idx, doc_type, key)
            continue

        page = doc[page_idx]
        fontpath = entry.get("fontpath", config.MS_MINCHO)
        fontsize = entry.get("size", 10)

        # フォント登録
        fontname = f"preview-{key}"
        try:
            page.insert_font(fontname=fontname, fontfile=fontpath)
        except Exception:
            fontname = "helv"

        # サンプルデータ取得
        data = sample_data if sample_data is not None else SAMPLE_DATA
        raw = data.get(key, f"[{key}]")
        text = _format_value(key, raw)

        # デロテーションマトリクス
        rotation = page.rotation
        derotation = page.derotation_matrix

        entry_type = entry.get("type", "point")

        if entry_type == "point":
            vx = entry["x"]
            vy = entry["y"]
            visual_pt = fitz.Point(vx, vy)
            phys_pt = visual_pt * derotation

            # テキスト描画
            page.insert_text(
                phys_pt,
                text,
                fontname=fontname,
                fontsize=fontsize,
                color=RED,
                rotate=rotation,
            )
            # ラベル（key名を小さく表示）
            label_visual = fitz.Point(vx, vy - 4)
            label_phys = label_visual * derotation
            page.insert_text(
                label_phys,
                key,
                fontname="helv",
                fontsize=5,
                color=LABEL_BLUE,
                rotate=rotation,
            )

        elif entry_type == "rect":
            r = entry.get("rect", (0, 0, 0, 0))
            visual_rect = fitz.Rect(r)
            if visual_rect.is_empty or visual_rect.width <= 0:
                continue

            align = entry.get("align", 0)

            # 枠線（緑）— visual座標で描画
            draw_rect = visual_rect * derotation
            page.draw_rect(draw_rect, color=BORDER_GREEN, width=0.5)

            # テキスト描画
            # 計測用: visual 空間で高さ測定
            measure_rect = fitz.Rect(
                visual_rect.x0, visual_rect.y0,
                visual_rect.x1, visual_rect.y0 + 10000,
            )
            text_height = page.insert_textbox(
                measure_rect,
                text,
                fontname=fontname,
                fontsize=fontsize,
                align=align,
                rotate=0,
                render_mode=3,  # invisible
            )
            actual_h = 10000 - abs(text_height)
            box_h = visual_rect.height
            v_offset = max(0, (box_h - actual_h) / 2)

            draw_visual_rect = fitz.Rect(
                visual_rect.x0,
                visual_rect.y0 + v_offset,
                visual_rect.x1,
                visual_rect.y1,
            )
            draw_phys_rect = draw_visual_rect * derotation

            # 太字
            render_mode = 0
            border_width = 0
            if entry.get("is_bold"):
                render_mode = 2
                border_width = entry.get("bold_width", 0.017) * fontsize

            page.insert_textbox(
                draw_phys_rect,
                text,
                fontname=fontname,
                fontsize=fontsize,
                align=align,
                color=RED,
                rotate=rotation,
                render_mode=render_mode,
                border_width=border_width,
            )

            # ── 金額フィールドの左横に○増/○減を描画 ──
            if key.startswith("kingaku_") and key != "kingaku_direction":
                direction = data.get("kingaku_direction")
                if direction:
                    # MS明朝フォントを登録（囲み文字用）
                    _ms_mincho = config.MS_MINCHO
                    circle_fn = f"circle-mincho"
                    try:
                        page.insert_font(fontname=circle_fn, fontfile=_ms_mincho)
                    except Exception:
                        circle_fn = fontname

                    circle_radius = fontsize * 0.55
                    circle_fontsize = fontsize * 0.7
                    circle_center_visual = fitz.Point(
                        visual_rect.x0 - circle_radius - -7,
                        (visual_rect.y0 + visual_rect.y1) / 2-2,
                    )
                    circle_center_phys = circle_center_visual * derotation
                    # 円を描画
                    page.draw_circle(circle_center_phys, circle_radius, color=RED, width=0.6)
                    # 文字を円の中央に配置（insert_text でピンポイント座標）
                    text_pt = fitz.Point(
                        circle_center_phys.x - circle_fontsize * -0.3,
                        circle_center_phys.y + circle_fontsize * 0.55,
                    )
                    page.insert_text(
                        text_pt,
                        direction,
                        fontsize=circle_fontsize,
                        fontname=circle_fn,
                        color=RED,
                        rotate=90,
                    )

            # ラベル（key名を枠の上に表示）
            label_visual = fitz.Point(visual_rect.x0, visual_rect.y0 - 2)
            label_phys = label_visual * derotation
            page.insert_text(
                label_phys,
                key,
                fontname="helv",
                fontsize=5,
                color=LABEL_BLUE,
                rotate=rotation,
            )

    doc.save(str(output_path))
    doc.close()
    logger.info("プレビュー生成: %s", output_path.name)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main() -> None:
    """3種類のプレビューPDFをプロジェクトルートに生成する。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    targets = [
        ("shodaku",   "preview_shodaku.pdf"),
        ("chumonsho", "preview_chumonsho.pdf"),
        ("ukesho",    "preview_ukesho.pdf"),
        ("shinkyuu",  "preview_shinkyuu.pdf"),
    ]

    for doc_type, out_name in targets:
        stamp_entries = config.PDF_STAMP_MAP.get(doc_type, [])
        if not stamp_entries:
            logger.warning("PDF_STAMP_MAP に '%s' が未定義 → スキップ", doc_type)
            continue

        template_name = config.PDF_TEMPLATES.get(doc_type)
        if not template_name:
            logger.warning("PDF_TEMPLATES に '%s' が未定義 → スキップ", doc_type)
            continue

        template_path = config.FOLDER_TEMPLATE / template_name
        output_path = config.ROOT_DIR / out_name

        # 注文書・注文請書は daikyo_koseiin を固定表記に置換したデータを使用
        sample = SAMPLE_DATA
        if doc_type in ("chumonsho", "ukesho") and SAMPLE_DATA.get("daikyo_koseiin"):
            sample = dict(SAMPLE_DATA)
            sample["daikyo_koseiin"] = "【代表構成員】"

        _generate_one(doc_type, stamp_entries, template_path, output_path, sample_data=sample)

    print()
    print("=" * 50)
    print("  プレビューPDF生成完了!")
    print("=" * 50)
    for _, out_name in targets:
        p = config.ROOT_DIR / out_name
        status = "[OK]" if p.exists() else "[--]"
        print(f"  {status} {out_name}")
    print()
    print("  VS Code で開いて座標を確認してください。")
    print("  修正: config.py の PDF_STAMP_MAP を編集")
    print("  再生成: .venv\\Scripts\\python.exe -m skills.order_docs.generate_previews")
    print("=" * 50)


if __name__ == "__main__":
    # ── 内訳書 (HTML) のプレビュー生成を追加 ──
    try:
        from .html_pdf_builder import build_breakdown_pdf
        from .nairaku_models import NairakuData, NairakuHeaderInfo, NairakuRow

        print("内訳書のプレビューを生成中...")
        
        # テスト用のダミーヘッダー
        header = NairakuHeaderInfo(
            koji_kenmei="長崎地区水産流通基盤整備工事（時津線（改良）多以良大橋耐震補強）", # ここに長い工事名が入る
            contract_date="令和8年4月1日",
            kouki="令和8年4月1日～令和9年3月31日",
            motouke_group_name="Factory・長崎西部特定建設工事共同企業体",
            motouke_address="長崎県長崎市多以良町1551番地93",
            motouke_company="株式会社Ｆａｃｔｏｒｙ",
            motouke_name="代表取締役社長　山本　清和",
            shitauke_address=SAMPLE_DATA["vendor_address"],
            shitauke_company=SAMPLE_DATA["vendor_company"],
            shitauke_name=SAMPLE_DATA["vendor_name"]
        )
        
        # ダミーの明細行（1行だけ）
        rows = [NairakuRow(row_type="item", saibetsu="テスト明細", tanka=1000, suryo=1, kingaku=1000)]
        nairaku_data = NairakuData(header=header, rows=rows)

        # PDF出力
        out_breakdown = config.ROOT_DIR / "preview_breakdown.pdf"
        build_breakdown_pdf(nairaku_data, out_breakdown)
        print(f"  -> 生成完了: {out_breakdown}")
        
    except Exception as e:
        print(f"内訳書のプレビュー生成中にエラー: {e}")
    main()
    # ── 契約条件書 (HTML) のプレビュー生成 ──
    # 見本 PDF (契約条件書（見本）.pdf) の内容と完全に揃えたダミーデータ。
    # 1ページに全 10 セクションが収まり、レイアウト・罫線・文字位置を
    # 見本と並べて比較検証できるようにする。
    try:
        import traceback
        from .html_pdf_builder import build_condition_pdf
        from .terms_models import TermsData, TermsSection, TermsItem, TermsParty

        print("契約条件書のプレビューを生成中...")

        def _mk(label: str, mk: bool = False, st: bool = False, biko: str = "") -> TermsItem:
            return TermsItem(
                label=label,
                motokata_checked=mk,
                shitauke_checked=st,
                biko=biko,
                layout="std",
            )

        condition_data = TermsData(
            koji_kenmei="長崎地区水産流通基盤整備工事（沖防波堤（改良）3工区）",
            genba_dairinin="出口 勇人",
            # 見本には JV 名称行が存在しないため空にする
            motouke_group_name="",
            daikyo_koseiin_label="",
            motouke=TermsParty(
                address="長崎県長崎市多以良町1551番地93",
                company="株式会社Factory",
                name="代表取締役社長　山本　清和",
            ),
            shitauke=TermsParty(
                address="長崎県長崎市玉園町2番37号",
                company="株式会社長崎西部建設",
                name="代表取締役　岩本　隆宏",
            ),
            note_line="※以上の条件で契約致します。(ﾚ点の部分）",
            sections=[
                # 1. 測量関係費 (3 items)
                TermsSection(number=1, title="1.測量関係費", layout="std", items=[
                    _mk("基本測量", mk=True),
                    _mk("境界測量", mk=True),
                    _mk("現場・丁張測量(材工共)", mk=True),
                ]),
                # 2. 安全関係費 (5 items)
                TermsSection(number=2, title="2.安全関係費", layout="std", items=[
                    _mk("工事看板(材料)", mk=True),
                    _mk("標識板・バリケード類(材工共)", mk=True),
                    _mk("仮設電気、電力料金", mk=True),
                    _mk("交通整理員（ガードマン）", mk=True),
                    _mk("足場設置費"),
                ]),
                # 3. 現場事務所、仮設電力費 (8 items)
                TermsSection(number=3, title="3.現場事務所、仮設電力費", layout="std", items=[
                    _mk("地代（現場事務所）", mk=True),
                    _mk("地代（資材置場等）", mk=True),
                    _mk("敷地造成費・復旧費", mk=True),
                    _mk("現場事務所設置", mk=True),
                    _mk("仮設ハウス備品", mk=True),
                    _mk("トイレ", mk=True),
                    _mk("電話・電気・水道引込撤去", mk=True),
                    _mk("電話・電気・水道使用料", mk=True),
                ]),
                # 4. 管理費用 (7 items)
                TermsSection(number=4, title="4.管理費用", layout="std", items=[
                    _mk("出来形管理測定", mk=True),
                    _mk("出来形管理書類作成", mk=True),
                    _mk("品質管理測定", mk=True),
                    _mk("品質管理書類作成", mk=True),
                    _mk("写真撮影", mk=True),
                    _mk("写真管理(データ整理)", mk=True),
                    _mk("竣工検査書類作成", mk=True),
                ]),
                # 5. 現場環境改善費用 (2 items)
                TermsSection(number=5, title="5.現場環境改善費用", layout="std", items=[
                    _mk("材料費", mk=True),
                    _mk("設置撤去費", mk=True),
                ]),
                # 6. その他費用 (7 items)
                TermsSection(number=6, title="6.その他費用", layout="std", items=[
                    _mk("諸官庁申請書類作成", mk=True),
                    _mk("諸官庁申請費用", mk=True),
                    _mk("材料検査費用", mk=True),
                    _mk("試験施工費用", mk=True),
                    _mk("地下埋設・地上施設調査費", mk=True),
                    _mk("地元挨拶、説明会費用", mk=True),
                    _mk("会計検査費用", mk=True),
                ]),
                # 7. 別途協議事項 (3 items)
                TermsSection(number=7, title="7.別途協議事項", layout="std", items=[
                    _mk("近隣対策、補償費", mk=True),
                    _mk("産業廃棄物処理費", mk=True),
                    _mk("公害対策費", mk=True),
                ]),
                # 8. その他 (2 items) — 備考あり
                TermsSection(number=8, title="8.その他", layout="std", items=[
                    _mk("出来形及び品質管理基準", mk=True, biko="規格値の50%"),
                    _mk("安全教育訓練への参加", mk=True, st=True, biko="毎月4時間"),
                ]),
                # 9. 適切な下請契約等 (wide / 6 items) — 一部のみチェック
                TermsSection(number=9, title="9.適切な下請契約等", layout="wide", items=[
                    TermsItem(label="下請次数を2次までに制限する", layout="wide"),
                    TermsItem(label="下請契約の金額の合意形成", layout="wide"),
                    TermsItem(label="労務費及び法定福利費を明示した見積書提出",
                              motokata_checked=True, layout="wide"),
                    TermsItem(label="見積書を尊重し下請け契約を締結する",
                              motokata_checked=True, layout="wide"),
                    TermsItem(label="建設キャリアアップシステムの事業者登録",
                              motokata_checked=True, biko="契約工期内に事業者登録",
                              layout="wide"),
                    TermsItem(label="その他", layout="wide"),
                ]),
                # 10. 4週8休等の実施 (single / 1 item)
                # セクションタイトルが A 列を埋め、label は空、biko に "4週8休"
                TermsSection(number=10, title="10.4週8休等の実施", layout="single", items=[
                    TermsItem(label="", motokata_checked=True, shitauke_checked=False,
                              biko="4週8休", layout="single"),
                ]),
            ],
        )

        condition_pdf_path = config.ROOT_DIR / "preview_condition.pdf"
        build_condition_pdf(condition_data, condition_pdf_path, save_html=True)
        print(f"  -> 生成完了: {condition_pdf_path}")

    except Exception as e:
        print(f"契約条件書のプレビュー生成中にエラー: {e}")
        traceback.print_exc()
