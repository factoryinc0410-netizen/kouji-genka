"""
GUI 確認画面モジュール — Tkinter ベースの抽出データ確認・編集ウィンドウ

Excel から抽出した業者データを一覧表で表示し、
ユーザーが「変更回数」を入力してから PDF 生成に進めるようにする。

使い方:
    from .gui_confirm import show_confirm_dialog
    result = show_confirm_dialog(vendor_list)
    if result is None:
        # ユーザーがキャンセルした
    else:
        # result は変更回数が付与された vendor_list
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional


# 一覧に表示する列定義: (vendor_data のキー, 表示ヘッダー)
_DISPLAY_COLUMNS: list[tuple[str, str]] = [
    ("vendor_company", "業者名"),
    ("kingaku_ukeoi", "請負金額"),
    ("kouki_start", "工期（自）"),
    ("kouki_end", "工期（至）"),
]


def _format_kouki(vendor: dict, prefix: str) -> str:
    """工期の年月日を結合して表示用文字列にする。"""
    y = vendor.get(f"{prefix}_year") or ""
    m = vendor.get(f"{prefix}_month") or ""
    d = vendor.get(f"{prefix}_day") or ""
    if y or m or d:
        return f"R{y}.{m}.{d}"
    return ""


def show_confirm_dialog(
    vendor_list: list[dict[str, str | None]],
) -> Optional[list[dict[str, str | None]]]:
    """
    抽出データの確認ダイアログを表示する。
    """
    if not vendor_list:
        return vendor_list

    result: Optional[list[dict]] = None

    root = tk.Tk()
    root.title("注文書作成 — 抽出データ確認")
    root.resizable(True, True)

    # ── ウィンドウサイズ・位置 ──
    win_w, win_h = 850, 400
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - win_w) // 2
    y = (screen_h - win_h) // 2
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    # ── 説明ラベル ──
    # ※変更回数の説明文を削除しました
    header = ttk.Label(
        root,
        text="抽出データを確認してください。",
        padding=(10, 8),
    )
    header.pack(fill=tk.X)

    # ── Treeview（一覧表） ──
    # ※変更回数列を削除しました
    columns = [col_id for col_id, _ in _DISPLAY_COLUMNS]
    tree = ttk.Treeview(root, columns=columns, show="headings", height=12)

    for col_id, col_label in _DISPLAY_COLUMNS:
        tree.heading(col_id, text=col_label)
        width = 200 if col_id == "vendor_company" else 130
        tree.column(col_id, width=width, anchor=tk.W)

    # スクロールバー
    vsb = ttk.Scrollbar(root, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)

    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
    vsb.pack(side=tk.LEFT, fill=tk.Y, pady=5)

    # ── データ投入 ──
    for vendor in vendor_list:
        values = []
        for col_id, _ in _DISPLAY_COLUMNS:
            if col_id == "kouki_start":
                values.append(_format_kouki(vendor, "kouki_start"))
            elif col_id == "kouki_end":
                values.append(_format_kouki(vendor, "kouki_end"))
            else:
                values.append(vendor.get(col_id) or "")
        
        # ※変更回数の初期値投入を削除しました
        tree.insert("", tk.END, values=values)

    # ── 変更回数の編集機能（ダブルクリック等）はすべて削除 ──

    # ── ボタンフレーム ──
    btn_frame = ttk.Frame(root, padding=10)
    btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

    def _on_generate() -> None:
        nonlocal result
        # extractor.py で抽出したデータをそのまま次へ渡す
        result = vendor_list
        root.destroy()

    def _on_cancel() -> None:
        if messagebox.askyesno("確認", "キャンセルしますか？\nPDF生成は行われません。"):
            root.destroy()

    ttk.Button(btn_frame, text="この内容でPDFを生成する", command=_on_generate).pack(side=tk.RIGHT, padx=5)
    ttk.Button(btn_frame, text="キャンセル", command=_on_cancel).pack(side=tk.RIGHT, padx=5)

    # ── ウィンドウ閉じるボタン(X)もキャンセル扱い ──
    root.protocol("WM_DELETE_WINDOW", _on_cancel)

    root.mainloop()
    return result