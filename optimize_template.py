"""
テンプレートPDFの画像最適化スクリプト（使い捨て）

約款PDFのスキャン画像（840DPI）を 300DPI に縮小し、
ファイルサイズを大幅に削減する。
元ファイルは _original.pdf としてバックアップ済みの前提。
"""
from pathlib import Path

import fitz  # PyMuPDF

TEMPLATES_DIR = Path(__file__).parent / "skills" / "order_docs" / "templates"

# 対象: 約款PDF（12.9MBの巨大ファイル）
TARGET_NAME = "020528　下請契約約款　Ｒ2.4月～.pdf"
BACKUP_NAME = "020528　下請契約約款　Ｒ2.4月～_original.pdf"

# 目標DPI（印刷品質を維持しつつ軽量化）
TARGET_DPI = 300


def optimize_scanned_pdf(src: Path, backup: Path) -> None:
    original_size = backup.stat().st_size
    print(f"元ファイルサイズ: {original_size:,} bytes ({original_size / 1024 / 1024:.1f} MB)")

    doc = fitz.open(str(backup))
    new_doc = fitz.open()

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]

            # ページをラスタライズして新しいPDFページとして再構築
            # matrix で目標DPIに合わせる（元が ~840DPI なので縮小率を計算）
            mat = fitz.Matrix(TARGET_DPI / 72, TARGET_DPI / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)

            # JPEG圧縮でPNG+deflateよりはるかに小さくなる
            img_bytes = pix.tobytes(output="jpeg", jpg_quality=85)

            # 新しいページを作成（元のページサイズを維持）
            new_page = new_doc.new_page(
                width=page.rect.width,
                height=page.rect.height,
            )
            new_page.insert_image(
                new_page.rect,
                stream=img_bytes,
            )

            print(f"  Page {page_num + 1}: {pix.width}x{pix.height} @ {TARGET_DPI}DPI (JPEG 85%)")

        # 保存
        new_doc.save(
            str(src),
            garbage=4,
            deflate=True,
        )
    finally:
        new_doc.close()
        doc.close()

    new_size = src.stat().st_size
    reduction = (1 - new_size / original_size) * 100
    print(f"\n結果: {original_size:,} → {new_size:,} bytes ({reduction:.1f}% 削減)")
    print(f"       {original_size / 1024 / 1024:.1f} MB → {new_size / 1024 / 1024:.1f} MB")


def main():
    print("=" * 60)
    print("約款PDF 画像最適化 (300DPI JPEG)")
    print("=" * 60)

    src = TEMPLATES_DIR / TARGET_NAME
    backup = TEMPLATES_DIR / BACKUP_NAME

    if not backup.exists():
        print(f"エラー: バックアップが見つかりません: {backup}")
        return

    optimize_scanned_pdf(src, backup)

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
