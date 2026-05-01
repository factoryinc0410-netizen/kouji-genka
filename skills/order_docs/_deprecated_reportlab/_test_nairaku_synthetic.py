"""合成データによる内訳書レイアウトテスト (オフライン)

実行環境に reportlab が存在するときに
    python -m skills.order_docs._test_nairaku_synthetic
で実行する。

検証ポイント:
  - 長い氏名・住所 → ヘッダー値列の拡張 & 自動縮小
  - 先頭空白 / 中間空白の保持
  - 空白行 (spacer) が高さを保ったまま再現される
  - C 列 (細別・規格) が長い細別で膨らむ
  - 金額列は固定幅を維持
"""
from __future__ import annotations

import sys
from pathlib import Path

# パッケージとして実行することを想定
from skills.order_docs.nairaku_builder import build_nairaku_pdf
from skills.order_docs.nairaku_models import (
    NairakuData,
    NairakuHeaderInfo,
    NairakuRow,
)


def _make_header() -> NairakuHeaderInfo:
    return NairakuHeaderInfo(
        koji_kenmei="令和6年度 〇〇県道××号線改築工事 第1工区 (実証試験用長大件名)",
        contract_date="令和6年4月1日",
        kouki="令和6年4月2日  から  令和7年3月20日  まで",
        motouke_address="東京都千代田区霞が関1丁目2番3号 霞が関ビルディング 15階",
        motouke_company="株式会社元請建設工業東京本社プロジェクト管理部",
        motouke_name="代表取締役社長 山田  太郎",
        shitauke_address="神奈川県横浜市西区みなとみらい二丁目3番1号 ランドマークタワー 32階",
        shitauke_company="株式会社サンプル建設工業東京支店プロジェクト開発部",
        shitauke_name="支店長  佐藤  花子",
    )


def _make_rows() -> list[NairakuRow]:
    rows: list[NairakuRow] = []

    # 工種（カテゴリ）
    rows.append(NairakuRow(row_type="category", koji_shu="土工", is_bold=True))

    # 明細: 先頭空白インデント
    rows.append(NairakuRow(
        row_type="item", indent=2,
        koji_shu="\u3000\u3000掘削工",
        shubetsu="", saibetsu="機械掘削 土砂 BH 0.8m3級 (長大規格名テスト ダブルドラム式)",
        tani="m3", motouke_suryo=1200.0,
        suryo=1200.0, tanka=850.0, kingaku=1020000.0,
    ))

    # 明細: 中間空白を含む
    rows.append(NairakuRow(
        row_type="item", indent=2,
        koji_shu="\u3000\u3000土　工 (中間スペース)",
        shubetsu="残土処理", saibetsu="場内運搬 平均200m",
        tani="m3", motouke_suryo=800.0,
        suryo=800.0, tanka=430.0, kingaku=344000.0,
    ))

    # Spacer (Excel の意図的空白行を再現)
    rows.append(NairakuRow(row_type="spacer"))

    # 次のカテゴリ
    rows.append(NairakuRow(row_type="category", koji_shu="舗装工", is_bold=True))

    rows.append(NairakuRow(
        row_type="item", indent=2,
        koji_shu="\u3000\u3000表層工",
        shubetsu="アスファルト舗装", saibetsu="密粒度アスコン t=5cm 再生材使用",
        tani="m2", motouke_suryo=3500.0,
        suryo=3500.0, tanka=1250.0, kingaku=4375000.0,
    ))

    # 小計
    rows.append(NairakuRow(
        row_type="subtotal", koji_shu="直接工事費計",
        kingaku=5739000.0, is_bold=True,
    ))

    # Spacer
    rows.append(NairakuRow(row_type="spacer"))

    # 注記
    rows.append(NairakuRow(
        row_type="note", koji_shu="(労務費内訳については別紙のとおり)",
    ))

    return rows


def main() -> int:
    data = NairakuData(
        header=_make_header(),
        rows=_make_rows(),
        has_henkou=False,
    )

    out = Path("skills/order_docs/_test_nairaku_synthetic.pdf").resolve()
    build_nairaku_pdf(data, out)
    print(f"[OK] PDF written: {out}")
    print(f"     size = {out.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
