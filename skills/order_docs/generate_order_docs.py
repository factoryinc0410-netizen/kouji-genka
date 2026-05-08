"""
注文書作成スキルモジュール — G-Core プラットフォーム統合用

main_loop.py の _process_vendor を独立モジュール化したもの。
2 つのエントリーポイントを提供する:
  - generate_for_vendor()  : 1 業者分のパイプライン
  - generate_from_excel()  : Excel 一括処理（全業者分）

戻り値は dataclass ベースの構造化データで、
FastAPI 等の API レイヤーからそのまま JSON 化できる。
"""
from __future__ import annotations

import logging
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .extractor import extract_data
from .nairaku_extraction import extract_nairaku_data
from .terms_extraction import extract_terms_data
from .html_pdf_builder import build_breakdown_pdf, build_condition_pdf
from .pdf_stamper import stamp_pdf
from .pdf_merger import merge_pdfs
from .office_com import convert_xls_to_xlsx_if_needed
# from .gui_confirm import show_confirm_dialog

logger = logging.getLogger(__name__)


def _banner(tag: str, message: str) -> None:
    """黒い画面でも埋もれない大文字タグ付きログ。

    print でターミナルに直接出しつつ logger にも流す。
    本番サーバーのログ設定 (INFO 未満 / handler 未登録) に左右されず、
    抽出〜PDF生成〜合冊の各フェーズで何が起きたかを追跡できるようにする。
    """
    line = f">>> [{tag}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    logger.info(line)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  戻り値の型定義
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class DocumentResult:
    """個々のドキュメント（注文書PDF、注文請書PDF等）の処理結果。"""
    doc_type: str          # "chumonsho", "ukesho", "nairaku", "joken", "shinkyuu", "yakkan"
    success: bool = False
    output_path: str | None = None
    error: str | None = None


@dataclass
class OrderDocumentSet:
    """1 業者分の全書類セットの処理結果。"""
    vendor_company: str
    success: bool = False
    documents: list[DocumentResult] = field(default_factory=list)
    merged_chumonsho: str | None = None  # 合冊済み注文書セット
    merged_ukesho: str | None = None     # 合冊済み注文請書セット
    error: str | None = None


@dataclass
class BatchResult:
    """Excel 一括処理の全体結果。"""
    excel_path: str
    koji_kenmei: str | None = None
    total_vendors: int = 0
    success_count: int = 0
    results: list[OrderDocumentSet] = field(default_factory=list)
    error: str | None = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ユーティリティ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sanitize_folder_name(name: str) -> str:
    """フォルダ名に使えない文字を全角に置換する。"""
    table = str.maketrans({
        "/": "／", "\\": "＼", ":": "：", "*": "＊",
        "?": "？", '"': "\u201d", "<": "＜", ">": "＞", "|": "｜",
    })
    return name.translate(table).strip()


def _prepare_work_tmp(work_tmp: Path | None = None) -> Path:
    """一時作業フォルダを作成・クリアして返す。

    Parameters
    ----------
    work_tmp : Path | None
        ジョブ専用の一時フォルダパス。None の場合はデフォルト（CLI互換）を使用。
    """
    tmp = work_tmp or config.DEFAULT_WORK_TMP_DIR
    if tmp.exists():
        shutil.rmtree(str(tmp), ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _cleanup_work_tmp(work_tmp: Path | None = None) -> None:
    """一時作業フォルダを削除する。

    Parameters
    ----------
    work_tmp : Path | None
        ジョブ専用の一時フォルダパス。None の場合はデフォルト（CLI互換）を使用。
    """
    tmp = work_tmp or config.DEFAULT_WORK_TMP_DIR
    if tmp.exists():
        shutil.rmtree(str(tmp), ignore_errors=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1 業者パイプライン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_for_vendor(
    vendor_data: dict[str, str | None],
    excel_path: Path,
    output_dir: Path,
    work_tmp: Path | None = None,
    *,
    vendor_index: int | None = None,
) -> OrderDocumentSet:
    """
    1 業者分の注文書類一式を生成する。

    Parameters
    ----------
    vendor_data : dict
        extractor.extract_data() が返す業者データ辞書。
    excel_path : Path
        元の Excel ファイル（内訳書・契約条件書の COM 変換に使用）。
    output_dir : Path
        最終成果物の出力先ディレクトリ。
    work_tmp : Path | None
        一時作業フォルダ。None の場合は自動で作成・管理する。
    vendor_index : int | None
        業者番号（1-indexed）。契約条件書シートの識別に使用する。
        None の場合、契約条件書生成はスキップされる。
        generate_from_excel() から呼ばれる場合は常に設定される。

    Returns
    -------
    OrderDocumentSet
        処理結果。各ドキュメントの成否を個別に確認できる。
    """
    company = str(vendor_data.get("vendor_company", "不明"))
    safe_company = _sanitize_folder_name(company)
    result = OrderDocumentSet(vendor_company=company)

    manage_tmp = work_tmp is None
    if manage_tmp:
        work_tmp = _prepare_work_tmp()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        shodaku_stamped = None

        # ── 内訳書データの先行抽出（JV 判定を表紙スタンプに反映するため） ──
        # 「下請代金内訳書」シートの「(代表構成員)」ラベル有無で JV/非 JV を
        # 判定する。同じ NairakuData をあとで breakdown PDF 生成にも使い
        # 回し、抽出コストの二重計上を避ける。
        #
        # 非 JV のまま進めた場合の副作用:
        #   - _extract_from_first_joken の正規表現バグで daikyo_koseiin に
        #     「）」等のゴミが入っていると、旧実装では表紙に
        #     「【代表構成員】」ラベルが誤って打たれていた。
        # ここで is_jv フラグを取り、下の chumon_data 組み立てで明示的に
        # 除去することでその経路を塞ぐ。
        nairaku_sheet = vendor_data.get("nairaku_sheet")
        nairaku_data = None
        is_jv = False
        if nairaku_sheet:
            try:
                nairaku_data = extract_nairaku_data(excel_path, nairaku_sheet)
                is_jv = bool(nairaku_data.header.is_jv)
                vendor_data["jv_name"] = nairaku_data.header.jv_name
                logger.info(
                    "  JV 判定: is_jv=%s, jv_name='%s' (sheet='%s')",
                    is_jv, nairaku_data.header.jv_name, nairaku_sheet,
                )
            except Exception:
                logger.error(
                    "内訳書シートの先行抽出失敗（JV 判定は非 JV として継続）: %s",
                    company, exc_info=True,
                )
                nairaku_data = None

        # ── 注文書・注文請書用データ ──
        # JV (共同企業体) のときのみ、表紙の元請負人欄に
        #   上段: 元請負人名称 (= JV 名)
        #   下段: 【代表構成員】ラベル
        # をスタンプする。非 JV では両方ともスキップし、
        # 単独企業の素の表紙のまま出力する。
        chumon_data = dict(vendor_data)
        if is_jv:
            chumon_data["daikyo_koseiin"] = "【代表構成員】"
        else:
            chumon_data.pop("daikyo_koseiin", None)
            chumon_data.pop("motouke_company", None)

        # ── 0) スタンプ: 承諾書 ──
        shodaku_stamped = work_tmp / f"{safe_company}_shodaku.pdf"
        doc_result = _stamp_document(
            "shodaku", chumon_data,
            config.FOLDER_TEMPLATE / config.PDF_TEMPLATES["shodaku"],
            shodaku_stamped,
            config.PDF_STAMP_MAP["shodaku"],
        )
        result.documents.append(doc_result)

        # ── 1) スタンプ: 注文書 ──
        chumonsho_stamped = work_tmp / f"{safe_company}_chumonsho.pdf"
        doc_result = _stamp_document(
            "chumonsho", chumon_data,
            config.FOLDER_TEMPLATE / config.PDF_TEMPLATES["chumonsho"],
            chumonsho_stamped,
            config.PDF_STAMP_MAP["chumonsho"],
        )
        result.documents.append(doc_result)

        # ── 2) スタンプ: 注文請書 ──
        ukesho_stamped = work_tmp / f"{safe_company}_ukesho.pdf"
        doc_result = _stamp_document(
            "ukesho", chumon_data,
            config.FOLDER_TEMPLATE / config.PDF_TEMPLATES["ukesho"],
            ukesho_stamped,
            config.PDF_STAMP_MAP["ukesho"],
        )
        result.documents.append(doc_result)

        # ── 3a) Route B: 契約条件書 → HTML/CSS + Playwright 動的 PDF 生成 ──
        # v2.1: 旧 PDF スタンプ方式 (_stamp_document + _stamp_joken_checkboxes) を
        # 廃止し、html_pdf_builder.build_condition_pdf() に統一。
        # チェックボックスはテンプレート側 ({% if ... %}☑{% else %}□{% endif %}) で
        # 直接レンダリングされるため、後処理の再描画ロジックは不要になった。
        joken_pdf = work_tmp / f"{safe_company}_joken.pdf"
        joken_sheet = vendor_data.get("joken_sheet")

        if not joken_sheet:
            result.documents.append(DocumentResult(
                doc_type="joken", success=False,
                error="契約条件書シート未検出",
            ))
        elif vendor_index is None:
            # 直接 generate_for_vendor() を呼び出す外部コードでは vendor_index
            # が未指定のケースがあり得る（extract_terms_data が必要とする）。
            result.documents.append(DocumentResult(
                doc_type="joken", success=False,
                error=(
                    "vendor_index 未指定のため契約条件書を生成できません。"
                    "generate_from_excel() 経由で呼び出してください。"
                ),
            ))
        else:
            try:
                terms_data = extract_terms_data(excel_path, vendor_index)
                if not terms_data.sections:
                    result.documents.append(DocumentResult(
                        doc_type="joken", success=False,
                        error=f"契約条件書セクションが 0 件 (sheet={joken_sheet})",
                    ))
                else:
                    build_condition_pdf(terms_data, joken_pdf)
                    result.documents.append(DocumentResult(
                        doc_type="joken", success=True,
                        output_path=str(joken_pdf),
                    ))
            except Exception:
                err = traceback.format_exc()
                logger.error(
                    "契約条件書 HTML/Playwright 生成失敗: %s",
                    company, exc_info=True,
                )
                result.documents.append(DocumentResult(
                    doc_type="joken", success=False, error=err,
                ))

        # ── 3b) Route B: 内訳書 → HTML/CSS + Playwright 動的 PDF 生成 ──
        # Excel からデータを構造化抽出し、Jinja2 で breakdown.html をレンダ、
        # Playwright (headless Chromium) で PDF 化する。
        # LibreOffice Headless 方式の課題（チェックボックス消失・環境依存）、
        # および ReportLab Platypus 実装の保守性を解消。
        #
        # nairaku_data は上部で JV 判定のために先行抽出済み。ここでは
        # それをそのまま再利用し、同じシートを 2 回開かない。
        nairaku_pdf = work_tmp / f"{safe_company}_nairaku.pdf"

        if not nairaku_sheet:
            result.documents.append(DocumentResult(
                doc_type="nairaku", success=False,
                error="内訳書シート未検出",
            ))
        elif nairaku_data is None:
            # 先行抽出で例外が出たケース（詳細は上の logger.error を参照）
            result.documents.append(DocumentResult(
                doc_type="nairaku", success=False,
                error="内訳書データの抽出に失敗しました（ログ参照）",
            ))
        else:
            try:
                if nairaku_data.rows:
                    build_breakdown_pdf(
                        nairaku_data, nairaku_pdf,
                        vendor_data=vendor_data,
                    )
                    result.documents.append(DocumentResult(
                        doc_type="nairaku", success=True,
                        output_path=str(nairaku_pdf),
                    ))
                else:
                    result.documents.append(DocumentResult(
                        doc_type="nairaku", success=False,
                        error="内訳書データが 0 行です",
                    ))
            except Exception:
                err = traceback.format_exc()
                logger.error("内訳書 HTML/Playwright 生成失敗: %s", company, exc_info=True)
                result.documents.append(DocumentResult(
                    doc_type="nairaku", success=False, error=err,
                ))

        # ── 4) スタンプ: 新旧対照表 ──
        # Word COM を廃止し、注文書/請書と同じ PDF スタンプ方式に変更
        # JV判定は内訳書由来の is_jv フラグを使用する（motouke_company は
        # サンプルによっては None になり文字列マッチが失敗するため信頼できない）
        shinkyuu_pdf = work_tmp / f"{safe_company}_shinkyuu.pdf"
        shinkyuu_stamp_map = config.PDF_STAMP_MAP.get("shinkyuu", [])
        if shinkyuu_stamp_map:
            shinkyuu_data = dict(vendor_data)
            if not is_jv:
                shinkyuu_data.pop("motouke_company", None)
                logger.info("新旧対照表: motouke_company スキップ（非JVのため）")
            doc_result = _stamp_document(
                "shinkyuu", shinkyuu_data,
                config.FOLDER_TEMPLATE / config.PDF_TEMPLATES["shinkyuu"],
                shinkyuu_pdf,
                shinkyuu_stamp_map,
            )
            result.documents.append(doc_result)
        else:
            result.documents.append(DocumentResult(
                doc_type="shinkyuu", success=False,
                error="PDF_STAMP_MAP に 'shinkyuu' が未定義",
            ))

        # ── 5) 約款 PDF コピー ──
        yakkan_src = config.FOLDER_TEMPLATE / config.PDF_TEMPLATES["yakkan"]
        yakkan_pdf = work_tmp / f"{safe_company}_yakkan.pdf"
        if yakkan_src.exists():
            shutil.copy2(str(yakkan_src), str(yakkan_pdf))
            result.documents.append(DocumentResult(
                doc_type="yakkan", success=True,
                output_path=str(yakkan_pdf),
            ))
        else:
            result.documents.append(DocumentResult(
                doc_type="yakkan", success=False,
                error=f"約款PDF未配置: {yakkan_src}",
            ))

        # ── 6) 合冊 ──        
        # 業者ごとに「変更回数」を取得して判定する
        # 空欄、None、または "0" の場合は当初（変更なし）とみなす
        raw_kaisuu = str(vendor_data.get("henkou_kaisuu") or "").strip()
        is_henkou = raw_kaisuu not in ["", "0", "None", "False"]

        merge_name_map = {
            "shodaku": shodaku_stamped if not is_henkou else None,  # 当初(henkou=0)なら常にセット対象
            "chumonsho": chumonsho_stamped,
            "ukesho": ukesho_stamped,
            "yakkan": yakkan_pdf,
            "shinkyuu": shinkyuu_pdf,  # JVや変更の有無に関わらず、生成されていれば常に合冊
            "nairaku": nairaku_pdf,
            "joken": joken_pdf,
        }
        

        def _collect(order_keys: list[str]) -> list[Path]:
            return [merge_name_map[k] for k in order_keys if merge_name_map.get(k) and merge_name_map[k].exists()]

        merge_errors: list[str] = []

        # 注文書セット合冊
        chumonsho_paths = _collect(config.MERGE_ORDER_CHUMONSHO)
        if chumonsho_paths:
            merged_path = output_dir / f"注文書_{safe_company}.pdf"
            try:
                merge_pdfs(chumonsho_paths, merged_path)
                result.merged_chumonsho = str(merged_path)
            except Exception:
                logger.error("注文書セット合冊失敗: %s", company, exc_info=True)
                merge_errors.append(f"注文書合冊: {traceback.format_exc()}")

        # 注文請書セット合冊
        ukesho_paths = _collect(config.MERGE_ORDER_UKESHO)
        if ukesho_paths:
            merged_path = output_dir / f"注文請書_{safe_company}.pdf"
            try:
                merge_pdfs(ukesho_paths, merged_path)
                result.merged_ukesho = str(merged_path)
            except Exception:
                logger.error("注文請書セット合冊失敗: %s", company, exc_info=True)
                merge_errors.append(f"注文請書合冊: {traceback.format_exc()}")

        if merge_errors and not (result.merged_chumonsho or result.merged_ukesho):
            result.error = "\n---\n".join(merge_errors)

        # 全体の成否判定: 合冊が少なくとも 1 つ成功していれば success
        result.success = bool(result.merged_chumonsho or result.merged_ukesho)
        logger.info("業者処理完了: %s (success=%s)", company, result.success)

    except Exception:
        result.error = traceback.format_exc()
        result.success = False
        logger.exception("業者処理中にエラーが発生しました: %s", company)

    finally:
        if manage_tmp:
            _cleanup_work_tmp()

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  個別ドキュメント生成ヘルパー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _stamp_document(
    doc_type: str,
    vendor_data: dict[str, str | None],
    template_path: Path,
    output_path: Path,
    stamp_map_list: list[dict],
) -> DocumentResult:
    """PDF スタンプ処理を実行し、DocumentResult を返す。"""
    try:
        if not template_path.exists():
            return DocumentResult(
                doc_type=doc_type, success=False,
                error=f"テンプレート未配置: {template_path}",
            )
        stamp_pdf(template_path, output_path, vendor_data, stamp_map_list)
        return DocumentResult(
            doc_type=doc_type, success=True,
            output_path=str(output_path),
        )
    except Exception:
        err = traceback.format_exc()
        logger.error("スタンプ失敗 (%s): %s", doc_type, err)
        return DocumentResult(doc_type=doc_type, success=False, error=err)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Excel 一括処理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_from_excel(
    excel_path: Path,
    output_dir: Path | None = None,
    *,
    use_gui: bool = False,
    confirmed_vendors: list[dict] | None = None,
    work_tmp_base: Path | None = None,
) -> BatchResult:
    """
    Excel 依頼書から全業者分の注文書類を一括生成する。

    Parameters
    ----------
    excel_path : Path
        注文書作成依頼書 (.xlsx) のパス。
    output_dir : Path | None
        出力先ディレクトリ。None の場合は
        config.FOLDER_DONE / 【工事名】 を自動生成する。
    use_gui : bool
        True の場合、抽出後に Tkinter 確認画面を表示して
        ユーザーに変更回数の入力を求める。デフォルトは False（従来互換）。
    confirmed_vendors : list[dict] | None
        Web 版確認画面で確定済みの業者データリスト。
        指定された場合、extract_data() をスキップしてこのデータを使用する。
    work_tmp_base : Path | None
        ジョブ専用の一時作業フォルダ。None の場合はデフォルトパスを使用（CLI互換）。
        Web ワーカーからは COM_TEMP_DIR / job_id / work が渡される。

    Returns
    -------
    BatchResult
        全業者分の処理結果。
    """
    batch = BatchResult(excel_path=str(excel_path))

    try:
        # ── 0a) 入力ファイルの存在検証 ──
        if not Path(excel_path).exists():
            batch.error = f"Excelファイルが見つかりません: {excel_path}"
            logger.error(batch.error)
            return batch

        # ── 0b) .xls → .xlsx 自動変換 ──
        excel_path = convert_xls_to_xlsx_if_needed(excel_path)

        # ── 0c) Pre-flight: openpyxl で試験的に開き、破損/保護を検知 ──
        # 業者ループの途中で落ちるより、ここで即座に失敗を返すほうが
        # ユーザー体験とログの可読性が大幅に向上する。
        try:
            import openpyxl  # 遅延 import（依存関係を local に留める）
            _preflight_wb = openpyxl.load_workbook(
                str(excel_path), data_only=True, read_only=True,
            )
            _preflight_wb.close()
        except Exception as exc:
            batch.error = (
                f"Excelファイルを開けません（破損・パスワード保護・"
                f"非対応フォーマットの可能性）: {type(exc).__name__}: {exc}"
            )
            logger.error(batch.error, exc_info=True)
            return batch

        # ── 1) Excel データ抽出（確認済みデータがあればスキップ） ──
        if confirmed_vendors:
            vendor_list = confirmed_vendors
            logger.info("確認済みデータを使用（%d 社）", len(vendor_list))
        else:
            vendor_list = extract_data(excel_path)

        if not vendor_list:
            batch.error = "業者データが 0 件です。依頼書の内容を確認してください。"
            return batch

        # ── 1.5) GUI 確認画面（現在は無効化中） ──
        # 元はここで Tkinter ベースの show_confirm_dialog (skills/order_docs/
        # gui_confirm.py) を呼び、業者一覧をユーザーに確認させていた。
        # Linux サーバ運用では Tkinter が使えず import 時点で失敗するため、
        # 28 行目の import 文ごとコメントアウトして呼び出しを停止している
        # (詳細は skills/order_docs/CLAUDE.md §3.3 OS 共通の注意 を参照)。
        #
        # 復活させる場合は use_gui=True 経路で lazy import + 例外時 fallback を
        # 実装し、確認ステップを再度組み込むこと。それまでは use_gui の値に関わらず
        # 抽出済み vendor_list をそのまま採用して続行する。
        if use_gui and not confirmed_vendors:
            # 確認ステップは行わず素通り。
            pass

        batch.total_vendors = len(vendor_list)
        batch.koji_kenmei = vendor_list[0].get("koji_kenmei")

        # ── 2) 出力先決定 ──
        if output_dir is None:
            koji = str(batch.koji_kenmei or "不明工事")
            safe_koji = _sanitize_folder_name(koji)
            output_dir = config.FOLDER_DONE / f"【{safe_koji}】"
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── 3) 一時フォルダ準備（ジョブ専用パスが渡されていればそれを使用） ──
        work_tmp = _prepare_work_tmp(work_tmp_base)

        try:
            # ── 4) 業者ループ ──
            for idx, vendor_data in enumerate(vendor_list):
                company = vendor_data.get("vendor_company", "(不明)")
                logger.info("業者 %d/%d: %s", idx + 1, len(vendor_list), company)

                vendor_result = generate_for_vendor(
                    vendor_data, excel_path, output_dir,
                    work_tmp=work_tmp,
                    vendor_index=idx + 1,  # extract_terms_data は 1-indexed
                )
                batch.results.append(vendor_result)
                if vendor_result.success:
                    batch.success_count += 1

        finally:
            _cleanup_work_tmp(work_tmp_base)

        logger.info(
            "一括処理完了: %d/%d 社成功",
            batch.success_count, batch.total_vendors,
        )

    except Exception:
        batch.error = traceback.format_exc()
        logger.exception("一括処理エラー: %s", excel_path)

    return batch
