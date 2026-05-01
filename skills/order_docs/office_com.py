"""
Office COM 操作モジュール — Excel / Word をバックグラウンド操作する。

安全設計:
- com_lock による排他制御でスレッド間の COM 競合を防止
- try…except…finally で app.Quit() を確実に実行しゾンビプロセスを防止
- Word は ASCII 一時パスにコピーして日本語パス読み込みエラーを回避
- 一時ファイルは finally で必ず削除

OS 互換性（v2.3.18 で対応）:
- 本モジュールは Windows 専用機能（COM 経由 Excel/Word 操作）を提供する。
- Linux/macOS では `import win32com.client` 自体が失敗するため、
  モジュールトップでの import を廃止し、各関数内で遅延 import に変更した。
- Linux 起動時にこのモジュールを `from .office_com import ...` しても
  ImportError は発生しない。実際に COM 関数を呼んだときだけ
  明示的な RuntimeError を返す（呼び出し元で .xls 入力時のみ到達する）。
"""
from __future__ import annotations

import logging
import shutil
import sys
import uuid
from pathlib import Path

from . import config
from .com_lock import acquire_com_lock

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  win32com 遅延ロード
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_win32com():
    """win32com.client を遅延 import して返す。

    Linux/macOS では pywin32 がインストールされていないため import が失敗する。
    その場合は呼び出し元に分かりやすい RuntimeError を投げる。

    Returns
    -------
    module : win32com.client
        Windows のみ正常に返る。
    """
    if sys.platform != "win32":
        raise RuntimeError(
            "Office COM 機能は Windows 専用です。"
            f"現在のプラットフォーム: {sys.platform}。"
            ".xls ファイルは事前に .xlsx に変換してアップロードしてください。"
        )
    try:
        import win32com.client  # type: ignore[import-not-found]
        return win32com.client
    except ImportError as exc:  # pragma: no cover — Windows でも未インストール時
        raise RuntimeError(
            "pywin32 がインストールされていません。"
            "Windows で COM 機能を使う場合は `pip install pywin32` を実行してください。"
        ) from exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  .xls → .xlsx 自動変換
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def convert_xls_to_xlsx_if_needed(excel_path: Path) -> Path:
    """
    .xls ファイルなら Excel COM で .xlsx に変換して新パスを返す。
    .xlsx / .xlsm 等はそのまま返す（何もしない）。

    Parameters
    ----------
    excel_path : Path
        入力 Excel ファイルのパス。

    Returns
    -------
    Path
        変換後の .xlsx パス、または元のパス。
    """
    if excel_path.suffix.lower() != ".xls":
        return excel_path  # 変換不要

    xlsx_path = excel_path.with_suffix(".xlsx")
    logger.info(".xls 検出 → .xlsx に変換開始: %s", excel_path.name)

    win32com_client = _load_win32com()

    with acquire_com_lock():
        app = None
        wb = None
        try:
            app = win32com_client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            wb = app.Workbooks.Open(str(excel_path.resolve()))
            wb.SaveAs(str(xlsx_path.resolve()), FileFormat=51)  # xlOpenXMLWorkbook
            logger.info(".xls → .xlsx 変換完了: %s", xlsx_path.name)
        except Exception:
            logger.error(".xls → .xlsx 変換失敗: %s", excel_path.name, exc_info=True)
            raise
        finally:
            if wb is not None:
                try:
                    wb.Close(False)
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass

    return xlsx_path


# ── 一時ディレクトリ / ファイル ─────────────────────────────

def _ensure_temp_dir() -> Path:
    """COM 用 ASCII 一時ディレクトリを作成して返す。"""
    d = config.COM_TEMP_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique_ascii_path(src: Path, temp_dir: Path) -> Path:
    """元ファイルを ASCII ファイル名で一時ディレクトリにコピーし、そのパスを返す。"""
    safe_name = f"temp_{uuid.uuid4().hex[:8]}{src.suffix}"
    dest = temp_dir / safe_name
    shutil.copy2(str(src), str(dest))
    return dest


def _safe_remove(path: Path) -> None:
    """ファイルが存在すれば削除する。失敗してもログのみで例外を握り潰す。"""
    try:
        if path.exists():
            path.unlink()
    except Exception:
        logger.debug("一時ファイル削除失敗: %s", path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  A. Excel COM — シート単位 PDF 変換
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def convert_excel_sheets_to_pdf(
    excel_path: Path,
    sheet_names: list[str],
    output_pdf_paths: list[Path],
) -> list[Path]:
    """
    Excel ファイルから指定シートを 1 枚ずつ PDF に変換する。

    Parameters
    ----------
    excel_path : Path
        元の Excel ファイル（.xlsx）の絶対パス。
    sheet_names : list[str]
        PDF 化したいシート名のリスト。
    output_pdf_paths : list[Path]
        出力先 PDF の絶対パスのリスト（sheet_names と同順・同数）。

    Returns
    -------
    list[Path]
        実際に生成できた PDF パスのリスト。
    """
    if len(sheet_names) != len(output_pdf_paths):
        raise ValueError(
            f"sheet_names ({len(sheet_names)}) と "
            f"output_pdf_paths ({len(output_pdf_paths)}) の数が一致しません"
        )

    win32com_client = _load_win32com()

    with acquire_com_lock():
        app = None
        wb = None
        created: list[Path] = []

        try:
            app = win32com_client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False

            wb = app.Workbooks.Open(str(excel_path.resolve()))

            for sheet_name, output_path in zip(sheet_names, output_pdf_paths):
                try:
                    ws = wb.Sheets(sheet_name)
                except Exception:
                    logger.warning(
                        "シートが見つかりません — スキップ: '%s'", sheet_name, exc_info=True
                    )
                    continue

                temp_wb = None
                try:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    # ── 一時ブック分離方式 ──
                    # ws.Copy() を引数なしで呼ぶと、対象シートだけを含む
                    # 新規ブック（一時ブック）が自動生成される。
                    # 物理的に1シートしか存在しないブックからエクスポートするため、
                    # 他シート混入は原理的に不可能。
                    ws.Copy()
                    temp_wb = app.Workbooks(app.Workbooks.Count)
                    # ExportAsFixedFormat: Type=0 → PDF
                    temp_wb.Sheets(1).ExportAsFixedFormat(0, str(output_path.resolve()))
                    created.append(output_path)
                    logger.info("Excel シート→PDF 変換完了: '%s' → %s", sheet_name, output_path.name)
                except Exception:
                    logger.error(
                        "Excel シート PDF 変換失敗: '%s'", sheet_name, exc_info=True
                    )
                finally:
                    # 一時ブックは保存せず閉じる（ゾンビ防止）
                    try:
                        if temp_wb is not None:
                            temp_wb.Close(False)
                    except Exception:
                        logger.debug("一時ブック Close 失敗: '%s'", sheet_name)

            return created

        except Exception:
            logger.error("Excel COM 処理失敗: %s", excel_path.name, exc_info=True)
            raise
        finally:
            # ── 必ず閉じる ──
            try:
                if wb is not None:
                    wb.Close(False)
            except Exception:
                logger.warning("Excel Workbook Close 失敗", exc_info=True)
            try:
                if app is not None:
                    app.Quit()
            except Exception:
                logger.warning("Excel Application Quit 失敗", exc_info=True)
            wb = None
            app = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  B. Word COM — テンプレート → PDF 変換
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def convert_word_to_pdf(
    word_template_path: Path,
    output_pdf_path: Path,
    replace_dict: dict[str, str] | None = None,
) -> Path:
    """
    Word テンプレートを PDF に変換する。
    日本語パス回避のため ASCII 一時パスで作業する。

    Parameters
    ----------
    word_template_path : Path
        Word テンプレート (.docx) のパス。
    output_pdf_path : Path
        出力 PDF の絶対パス。
    replace_dict : dict[str, str] | None
        COM の Find/Replace で置換するマッピング（任意）。

    Returns
    -------
    Path
        出力 PDF パス。
    """
    temp_dir = _ensure_temp_dir()
    temp_word = _unique_ascii_path(word_template_path, temp_dir)

    # ────────────────────────────────────────────────────────
    # TODO: 将来的に XML レベルの文字置換が必要な場合はここに実装する。
    # temp_word (docx) を zipfile で展開 → word/document.xml 内の
    # プレースホルダーを str.replace() → 再 zip する方式。
    # COM の Find/Replace では対応できないケース（表内セルの置換等）用。
    # ────────────────────────────────────────────────────────

    win32com_client = _load_win32com()

    with acquire_com_lock():
        app = None
        doc = None

        try:
            app = win32com_client.DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = False

            doc = app.Documents.Open(str(temp_word.resolve()))

            # ── オプション: COM Find/Replace 置換 ──
            if replace_dict:
                for find_text, replace_text in replace_dict.items():
                    try:
                        find_obj = doc.Content.Find
                        find_obj.ClearFormatting()
                        find_obj.Replacement.ClearFormatting()
                        find_obj.Execute(
                            FindText=find_text,
                            ReplaceWith=replace_text,
                            Replace=2,      # wdReplaceAll
                            Forward=True,
                            Wrap=1,         # wdFindContinue
                        )
                    except Exception:
                        logger.warning(
                            "Word 置換失敗: '%s' → '%s'",
                            find_text,
                            replace_text,
                            exc_info=True,
                        )

            # ── PDF エクスポート (wdExportFormatPDF = 17) ──
            output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            doc.ExportAsFixedFormat(
                str(output_pdf_path.resolve()),
                ExportFormat=17,
            )
            logger.info("Word→PDF 変換完了: %s", output_pdf_path.name)
            return output_pdf_path

        except Exception:
            logger.error("Word COM 処理失敗: %s", word_template_path.name, exc_info=True)
            raise
        finally:
            # ── 必ず閉じる ──
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:
                logger.warning("Word Document Close 失敗", exc_info=True)
            try:
                if app is not None:
                    app.Quit()
            except Exception:
                logger.warning("Word Application Quit 失敗", exc_info=True)
            doc = None
            app = None
            # ── 一時ファイル削除 ──
            _safe_remove(temp_word)
