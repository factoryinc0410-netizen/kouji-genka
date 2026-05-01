"""
Factoryskills — ローカル起動スクリプト（サーバ不使用）

処理フロー:
  1. ネイティブ Windows ファイル選択ダイアログで Excel 依頼書を選ばせる
  2. Tkinter GUI で抽出データ（業者一覧・変更回数）を確認・編集
  3. PDF を生成
  4. 出力フォルダをエクスプローラで開く

サーバ／ブラウザ／localhost は使用しません。完全にローカル動作。

実行方法:
    .venv\\Scripts\\pythonw.exe start_local.py     # コンソール非表示（推奨）
    .venv\\Scripts\\python.exe  start_local.py     # コンソール表示（デバッグ用）
"""
from __future__ import annotations

import io
import logging
import os
import sys
import traceback
from pathlib import Path


# ── UTF-8 強制（Windows コンソール対策） ─────────────────────
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass


# ── プロジェクトルートを sys.path に追加 ─────────────────────
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _pick_excel_file(initial_dir: Path) -> Path | None:
    """ネイティブファイル選択ダイアログで Excel を選ばせる。"""
    import tkinter as tk
    from tkinter import filedialog

    # 不可視のルートウィンドウを作成（ファイルダイアログだけ表示するため）
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # ダイアログを最前面に

    path_str = filedialog.askopenfilename(
        parent=root,
        title="注文書作成依頼書（Excel）を選択してください",
        filetypes=[
            ("Excel ファイル", "*.xlsx *.xls"),
            ("すべてのファイル", "*.*"),
        ],
        initialdir=str(initial_dir) if initial_dir.exists() else str(Path.home()),
    )
    root.destroy()

    if not path_str:
        return None
    return Path(path_str)


def _show_error(title: str, message: str) -> None:
    """エラーメッセージを表示する。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        # 最終手段: 標準エラー
        print(f"[{title}] {message}", file=sys.stderr)


def _show_info(title: str, message: str) -> None:
    """完了メッセージを表示する。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        print(f"[{title}] {message}")


def main() -> int:
    # ── ロギング設定（GUI モードでも file handler は有効） ──
    logs_dir = HERE / "logs"
    logs_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "start_local.log", encoding="utf-8"),
        ],
    )

    log = logging.getLogger("start_local")
    log.info("=" * 60)
    log.info("Factoryskills ローカル起動")
    log.info("=" * 60)

    # ── 1. Excel ファイル選択 ──────────────────────────────
    desktop_candidates = [
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Desktop",
    ]
    initial_dir = next((d for d in desktop_candidates if d.exists()), Path.home())

    excel_path = _pick_excel_file(initial_dir)
    if excel_path is None:
        log.info("ユーザーがキャンセル（ファイル未選択）")
        return 0

    if not excel_path.exists():
        _show_error("エラー", f"ファイルが見つかりません:\n{excel_path}")
        return 1

    log.info(f"選択された Excel: {excel_path}")

    # ── 2. 生成パイプライン実行（GUI モード） ──────────────
    try:
        from skills.order_docs.generate_order_docs import generate_from_excel
    except Exception as e:
        log.exception("モジュール読み込みエラー")
        _show_error(
            "起動エラー",
            f"モジュールの読み込みに失敗しました:\n{e}\n\n"
            f"詳細: {traceback.format_exc()[:800]}",
        )
        return 2

    try:
        result = generate_from_excel(
            excel_path=excel_path,
            output_dir=None,      # config.FOLDER_DONE / 【工事名】 を自動決定
            use_gui=True,         # ← これで gui_confirm.py のネイティブ GUI が起動
        )
    except Exception as e:
        log.exception("生成処理で例外発生")
        _show_error(
            "処理エラー",
            f"PDF 生成中に予期しないエラーが発生しました:\n{e}\n\n"
            f"ログ: {logs_dir / 'start_local.log'}",
        )
        return 3

    # ── 3. 結果処理 ────────────────────────────────────────
    if result.error:
        # ユーザーが GUI 確認画面でキャンセルした場合はエラーダイアログを出さない
        if "キャンセル" in result.error:
            log.info("GUI 確認画面でキャンセル")
            return 0
        log.error(f"バッチエラー: {result.error}")
        _show_error("処理エラー", f"処理に失敗しました:\n{result.error[:500]}")
        return 4

    # ── 出力先フォルダを特定 ──
    output_dir: Path | None = None
    for r in result.results:
        if r.merged_chumonsho:
            output_dir = Path(r.merged_chumonsho).parent
            break
        if r.merged_ukesho:
            output_dir = Path(r.merged_ukesho).parent
            break

    # ── 結果ダイアログ ──
    success = result.success_count
    total = result.total_vendors
    lines = [
        f"処理完了: {success} / {total} 社成功",
        "",
    ]
    for r in result.results:
        mark = "✅" if r.success else "❌"
        lines.append(f"  {mark} {r.vendor_company}")
    if output_dir:
        lines.append("")
        lines.append(f"出力先: {output_dir}")

    log.info(f"成功 {success}/{total}; 出力先={output_dir}")
    _show_info("Factoryskills — 完了", "\n".join(lines))

    # ── 4. 出力フォルダをエクスプローラ／ファイラで開く ──
    # os.startfile は Windows 専用 API のため、サーバ運用（Linux/macOS）では
    # 別系統のコマンドにフォールバックする。Linux はサーバ運用想定で
    # 通常 GUI 自体がないので、警告ログのみで終了する。
    if output_dir and output_dir.exists():
        if sys.platform == "win32":
            try:
                os.startfile(str(output_dir))  # type: ignore[attr-defined]
                log.info("出力フォルダをエクスプローラで開きました")
            except Exception as e:
                log.warning(f"フォルダを開けませんでした: {e}")
        elif sys.platform == "darwin":
            # macOS: Finder で開く
            try:
                import subprocess
                subprocess.Popen(["open", str(output_dir)])
                log.info("出力フォルダを Finder で開きました")
            except Exception as e:
                log.warning(f"フォルダを開けませんでした: {e}")
        else:
            # Linux: GUI 環境で動いているなら xdg-open、サーバなら何もしない
            try:
                import shutil as _shutil
                import subprocess
                if _shutil.which("xdg-open") and os.environ.get("DISPLAY"):
                    subprocess.Popen(["xdg-open", str(output_dir)])
                    log.info("出力フォルダを xdg-open で開きました")
                else:
                    log.info(
                        "Linux 環境のため出力フォルダの自動展開はスキップ: %s",
                        output_dir,
                    )
            except Exception as e:
                log.warning(f"フォルダを開けませんでした: {e}")

    return 0 if success == total else 5


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # ← 予期しない落下も GUI で報告
        try:
            _show_error(
                "致命的エラー",
                f"ランチャーが異常終了しました:\n{e}\n\n{traceback.format_exc()[:1000]}",
            )
        except Exception:
            pass
        sys.exit(99)
