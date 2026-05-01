"""
HTML + Playwright ベースの PDF 生成基盤

Jinja2 でレンダリングした HTML を Playwright (headless Chromium) で PDF 化する。

設計方針:
  - v2.1 で ReportLab 実装を廃止し、本モジュールが唯一の動的 PDF 生成基盤となった。
    旧 ReportLab 実装は `_deprecated_reportlab/` にアーカイブ保管。
  - 非同期 API (async_playwright) を基本とし、同期ラッパ (build_pdf_sync) も提供。
  - テンプレートは skills/order_docs/html_templates/ 配下に配置。
  - PDF 出力オプションは format="A4" 固定・print_background=True をデフォルト。
    縦横や用紙サイズはテンプレート側 @page ルールで制御する。

主クラス:
  - HtmlPdfBuilder: 単発用 (build_pdf / build_pdf_sync)
  - 複数ファイル一括生成は render_and_save() を直接呼ぶか、
    外側で asyncio.gather(*[builder.build_pdf(...)]) として並列化する。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

# ── テンプレートディレクトリ ──
TEMPLATES_DIR = Path(__file__).resolve().parent / "html_templates"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Jinja2 環境（モジュール単位で共有）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_JINJA_ENV: Environment | None = None


def get_jinja_env() -> Environment:
    """Jinja2 Environment を遅延初期化して返す（シングルトン）。"""
    global _JINJA_ENV
    if _JINJA_ENV is None:
        _JINJA_ENV = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html", "htm")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _JINJA_ENV


def render_html(template_name: str, context: dict[str, Any]) -> str:
    """テンプレートを HTML 文字列にレンダリングする。

    Parameters
    ----------
    template_name : str
        html_templates/ 配下のファイル名（例: "condition.html"）
    context : dict
        Jinja2 に渡す変数（通常は {"data": <dataclass>} の形）

    Returns
    -------
    str
        レンダリング済み HTML 文字列。
    """
    env = get_jinja_env()
    template = env.get_template(template_name)
    return template.render(**context)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HtmlPdfBuilder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HtmlPdfBuilder:
    """HTML → PDF 変換器（Playwright headless Chromium）。

    使い方 (同期):
        builder = HtmlPdfBuilder("condition.html")
        builder.build_pdf_sync({"data": terms_data}, output_path)

    使い方 (非同期):
        async def run():
            builder = HtmlPdfBuilder("condition.html")
            await builder.build_pdf({"data": terms_data}, output_path)
        asyncio.run(run())

    一括生成（高速化）:
        async def batch():
            builder = HtmlPdfBuilder("condition.html")
            async with builder.browser_context() as ctx:
                await asyncio.gather(*[
                    builder._render_in_context(ctx, ctx_data, out)
                    for ctx_data, out in jobs
                ])
    """

    # PDF デフォルトオプション
    DEFAULT_PDF_OPTIONS: dict[str, Any] = {
        "format": "A4",
        "print_background": True,
        "prefer_css_page_size": True,  # テンプレート @page を優先
        "margin": {"top": "0", "bottom": "0", "left": "0", "right": "0"},
        # 余白はテンプレート @page 側で制御する（二重設定を避ける）
    }

    def __init__(
        self,
        template_name: str,
        *,
        pdf_options: dict[str, Any] | None = None,
    ):
        self.template_name = template_name
        self.pdf_options: dict[str, Any] = {
            **self.DEFAULT_PDF_OPTIONS,
            **(pdf_options or {}),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # レンダリング
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def render_html(self, context: dict[str, Any]) -> str:
        """コンテキストから HTML 文字列を生成する（テンプレート差し替え用の公開API）。"""
        return render_html(self.template_name, context)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 非同期 PDF 生成
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def build_pdf(
        self,
        context: dict[str, Any],
        output_path: Path,
        *,
        save_html: bool = False,
    ) -> Path:
        """単発で HTML → PDF 変換を実行する（ブラウザ起動〜終了をこのメソッド内で完結）。

        Parameters
        ----------
        context : dict
            Jinja2 に渡す変数。
        output_path : Path
            PDF 出力先。
        save_html : bool
            True のとき、同じパスに .html も書き出す（デバッグ用）。

        Returns
        -------
        Path
            生成された PDF のパス（output_path をそのまま返す）。
        """
        from playwright.async_api import async_playwright

        html = self.render_html(context)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if save_html:
            html_path = output_path.with_suffix(".html")
            html_path.write_text(html, encoding="utf-8")
            logger.info("HTML 保存: %s", html_path)

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                # set_content は内部で相対パス解決が必要ない場面で使用。
                # フォントファイル等を参照する場合は base_url を page.set_content(html, wait_until=..) の
                # ほうではなく、--disable-web-security & add_script_tag を併用する設計もあり得るが、
                # 本テンプレートは OS インストール済みフォントのみを使うため不要。
                await page.set_content(html, wait_until="networkidle")
                await page.emulate_media(media="print")
                await page.pdf(path=str(output_path), **self.pdf_options)
            finally:
                await browser.close()

        logger.info("PDF 生成完了: %s (template=%s)", output_path, self.template_name)
        return output_path

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 同期ラッパ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def build_pdf_sync(
        self,
        context: dict[str, Any],
        output_path: Path,
        *,
        save_html: bool = False,
    ) -> Path:
        """同期 API ラッパ（既存のシンプルなスクリプトから呼びやすいように）。

        既存のイベントループ内から呼ぶ場合は build_pdf() を直接 await すること。
        """
        return asyncio.run(
            self.build_pdf(context, output_path, save_html=save_html)
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 一括生成（ブラウザを再利用して高速化）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def build_pdfs_batch(
        self,
        jobs: list[tuple[dict[str, Any], Path]],
        *,
        save_html: bool = False,
        concurrency: int = 4,
    ) -> list[Path]:
        """複数ジョブを 1 つのブラウザプロセス内で順次/並列に生成する。

        Parameters
        ----------
        jobs : list of (context, output_path)
        save_html : bool
            True のとき各 PDF と同じパスに .html も書き出す。
        concurrency : int
            同時に開くページ数。1 に落とせば完全逐次。

        Returns
        -------
        list[Path]
            生成された PDF のパスリスト（入力 jobs と同順）。
        """
        from playwright.async_api import async_playwright

        results: list[Path | None] = [None] * len(jobs)
        sem = asyncio.Semaphore(max(1, concurrency))

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                async def _one(idx: int, context: dict, out: Path):
                    async with sem:
                        html = self.render_html(context)
                        out = Path(out)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        if save_html:
                            out.with_suffix(".html").write_text(html, encoding="utf-8")
                        page = await browser.new_page()
                        try:
                            await page.set_content(html, wait_until="networkidle")
                            await page.emulate_media(media="print")
                            await page.pdf(path=str(out), **self.pdf_options)
                        finally:
                            await page.close()
                        results[idx] = out
                        logger.info(
                            "PDF 生成 [%d/%d]: %s",
                            idx + 1, len(jobs), out,
                        )

                await asyncio.gather(*[
                    _one(i, ctx, out) for i, (ctx, out) in enumerate(jobs)
                ])
            finally:
                await browser.close()

        return [r for r in results if r is not None]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  高レベル関数 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_condition_pdf(data, output_path: Path, *, save_html: bool = False) -> Path:
    """契約条件書 (TermsData) → HTML テンプレート → PDF。

    Parameters
    ----------
    data : TermsData
        extract_terms_data() で抽出済みのデータ。
    output_path : Path
        PDF 出力先。
    save_html : bool
        True で同名の .html も保存（デバッグ用）。
    """
    builder = HtmlPdfBuilder("condition.html")
    return builder.build_pdf_sync({"data": data}, output_path, save_html=save_html)


async def build_condition_pdf_async(
    data,
    output_path: Path,
    *,
    save_html: bool = False,
) -> Path:
    """契約条件書 → PDF 非同期版。"""
    builder = HtmlPdfBuilder("condition.html")
    return await builder.build_pdf(
        {"data": data}, output_path, save_html=save_html
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内訳書 (下請代金内訳書) 高レベル API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _patch_nairaku_contract_date(data, vendor_data: dict | None) -> None:
    """内訳書ヘッダの contract_date が未解決なら vendor_data から補完する。

    Excel 内訳書シートが他シートの数式を参照しており、data_only=True で
    値が取れないときの救済策。contract_date が未設定または shitauke_company
    と混ざっている場合は、vendor_data の contract_year/month/day から
    「令和X年Y月Z日」を合成する。
    """
    if not vendor_data:
        return
    h = data.header
    if not h.contract_date or h.contract_date == h.shitauke_company:
        y = vendor_data.get("contract_year") or ""
        m = vendor_data.get("contract_month") or ""
        d = vendor_data.get("contract_day") or ""
        if y and m and d:
            h.contract_date = f"令和{y}年{m}月{d}日"
            logger.info("契約年月日を vendor_data から補完: %s", h.contract_date)


def build_breakdown_pdf(
    data,
    output_path: Path,
    *,
    vendor_data: dict | None = None,
    save_html: bool = False,
) -> Path:
    """内訳書 (NairakuData) → HTML テンプレート → PDF。

    Parameters
    ----------
    data : NairakuData
        extract_nairaku_data() で抽出済みのデータ。
    output_path : Path
        PDF 出力先。
    vendor_data : dict | None
        extract_data() で抽出済みの業者辞書。内訳書シートの数式参照が
        未解決の場合に、契約年月日等を補完する。
    save_html : bool
        True で同名 .html も保存（デバッグ用）。
    """
    _patch_nairaku_contract_date(data, vendor_data)
    builder = HtmlPdfBuilder("breakdown.html")
    return builder.build_pdf_sync(
        {"data": data}, output_path, save_html=save_html
    )


async def build_breakdown_pdf_async(
    data,
    output_path: Path,
    *,
    vendor_data: dict | None = None,
    save_html: bool = False,
) -> Path:
    """内訳書 → PDF 非同期版。"""
    _patch_nairaku_contract_date(data, vendor_data)
    builder = HtmlPdfBuilder("breakdown.html")
    return await builder.build_pdf(
        {"data": data}, output_path, save_html=save_html
    )
