"""Factory Platform のアイコン (.ico) を生成"""
import struct

def create_ico():
    """シンプルなオレンジ背景に白F文字の32x32アイコンを生成"""
    size = 32
    # BGRA形式のピクセルデータ
    pixels = bytearray()

    # 色定義 (BGRA)
    bg = (74, 123, 224, 255)    # #E07B4A → BGR: 4A 7B E0
    fg = (255, 255, 255, 255)   # 白
    border = (48, 106, 192, 255) # 少し暗いオレンジ

    # F文字のパターン (16x16 中央部分に配置)
    f_pattern = [
        "................",
        "..FFFFFFFFFFF...",
        "..FFFFFFFFFFF...",
        "..FF............",
        "..FF............",
        "..FF............",
        "..FFFFFFFFF.....",
        "..FFFFFFFFF.....",
        "..FF............",
        "..FF............",
        "..FF............",
        "..FF............",
        "..FF............",
        "..FF............",
        "................",
        "................",
    ]

    for y in range(size):
        for x in range(size):
            # 角丸風の丸み
            corners = [
                (x < 4 and y < 4),
                (x >= size-4 and y < 4),
                (x < 4 and y >= size-4),
                (x >= size-4 and y >= size-4),
            ]
            is_corner = any(corners)
            dist_corner = min(
                ((x-3.5)**2 + (y-3.5)**2)**0.5 if (x < 4 and y < 4) else 999,
                ((x-(size-4.5))**2 + (y-3.5)**2)**0.5 if (x >= size-4 and y < 4) else 999,
                ((x-3.5)**2 + (y-(size-4.5))**2)**0.5 if (x < 4 and y >= size-4) else 999,
                ((x-(size-4.5))**2 + (y-(size-4.5))**2)**0.5 if (x >= size-4 and y >= size-4) else 999,
            )

            if is_corner and dist_corner > 4.5:
                pixels.extend((0, 0, 0, 0))  # 透明
                continue

            # F文字の判定
            fx = x - 8
            fy = y - 8
            is_letter = False
            if 0 <= fx < 16 and 0 <= fy < 16:
                if fx < len(f_pattern[fy]) and f_pattern[fy][fx] == 'F':
                    is_letter = True

            if is_letter:
                pixels.extend(fg)
            elif x == 0 or y == 0 or x == size-1 or y == size-1:
                pixels.extend(border)
            else:
                pixels.extend(bg)

    # BMP形式のICOデータ
    bmp_header_size = 40
    bmp_data_size = size * size * 4
    mask_size = size * ((size + 31) // 32) * 4

    # AND mask (all 0 = fully opaque where alpha allows)
    and_mask = bytearray(mask_size)

    # BITMAPINFOHEADER
    bmp_header = struct.pack('<IiiHHIIiiII',
        bmp_header_size,  # biSize
        size,             # biWidth
        size * 2,         # biHeight (doubled for ICO)
        1,                # biPlanes
        32,               # biBitCount
        0,                # biCompression
        bmp_data_size + mask_size,  # biSizeImage
        0, 0, 0, 0       # biX/YPelsPerMeter, biClrUsed, biClrImportant
    )

    # BMPは下から上
    flipped_pixels = bytearray()
    for y in range(size - 1, -1, -1):
        row_start = y * size * 4
        flipped_pixels.extend(pixels[row_start:row_start + size * 4])

    image_data = bmp_header + bytes(flipped_pixels) + bytes(and_mask)

    # ICO ファイル
    ico_header = struct.pack('<HHH', 0, 1, 1)  # reserved, type=1(ICO), count=1
    data_offset = 6 + 16  # header + 1 entry
    entry = struct.pack('<BBBBHHiI',
        size if size < 256 else 0,  # width
        size if size < 256 else 0,  # height
        0,     # color count
        0,     # reserved
        1,     # color planes
        32,    # bits per pixel
        len(image_data),  # size
        data_offset       # offset
    )

    ico_data = ico_header + entry + image_data

    out_path = r"C:\Users\factory\Factoryskills\chat\factory_platform.ico"
    with open(out_path, 'wb') as f:
        f.write(ico_data)
    print(f"アイコン作成: {out_path}")
    return out_path

if __name__ == "__main__":
    create_ico()
