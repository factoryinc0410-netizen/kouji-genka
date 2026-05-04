"""pytest 共通設定 — sys.path 調整・サンプル Excel・出力ディレクトリの提供。

このファイルは:
  1. プロジェクトルートを sys.path に挿入し `from skills.* / from web_app.*`
     の絶対 import を可能にする。
  2. テスト全体で共有する fixture を集約する。
     - `project_root`: リポジトリルート (Path)
     - `sample_excel`: サンプル Excel パス（見つからない場合は skip）
     - `pdf_html_dir`: 内訳書/契約条件書の HTML→PDF 出力ディレクトリ
     - `pdf_integration_dir`: 統合テストの合冊 PDF 出力ディレクトリ
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ────────────────────────────────────────────
# sys.path: tests/ の親 (プロジェクトルート) を先頭に挿入
# ────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ────────────────────────────────────────────
# サンプル Excel 探索
# ────────────────────────────────────────────
# 既存スクリプトが想定していた配置 + 現環境で実在するアップロード由来の
# original.xlsx をフォールバックとして列挙する。
_SAMPLE_EXCEL_CANDIDATES: tuple[Path, ...] = (
    Path(__file__).resolve().parent / "注文書作成依頼（サンプルデータ版）.xlsx",
    Path(__file__).resolve().parent / "fixtures" / "sample_chumonsho.xlsx",
    _PROJECT_ROOT / "web_app" / "uploads" / "bca0f2ec0a0c452ab5b1316ab62d13da" / "original.xlsx",
)


def _resolve_sample_excel() -> Path | None:
    for cand in _SAMPLE_EXCEL_CANDIDATES:
        if cand.exists():
            return cand
    return None


@pytest.fixture(scope="session")
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_excel() -> Path:
    """サンプル Excel パスを返す。見つからない場合は skip する。

    fixture を要求したテストは Excel が無い環境で自動的にスキップされる
    （CI 等でサンプルが配置されていなくても他のテストは実行される）。
    """
    path = _resolve_sample_excel()
    if path is None:
        pytest.skip(
            "サンプル Excel が見つかりません。"
            f"探索パス: {[str(p) for p in _SAMPLE_EXCEL_CANDIDATES]}"
        )
    return path


@pytest.fixture(scope="session")
def sample_extracted_json(sample_excel: Path) -> Path | None:
    """サンプル Excel に対応する extracted_vendors.json があれば返す（無ければ None）。"""
    candidate = sample_excel.parent / "extracted_vendors.json"
    return candidate if candidate.exists() else None


@pytest.fixture(scope="session")
def pdf_html_dir() -> Path:
    return Path(__file__).resolve().parent / "_test_html_pdf"


@pytest.fixture(scope="session")
def pdf_integration_dir() -> Path:
    return Path(__file__).resolve().parent / "_test_integration"


# ────────────────────────────────────────────
# E2E 専用の出力ディレクトリ（毎回作り直し）
# ────────────────────────────────────────────
@pytest.fixture
def html_pdf_outdir(tmp_path: Path) -> Path:
    out = tmp_path / "html_pdf"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture
def integration_outdir(tmp_path: Path) -> Path:
    out = tmp_path / "integration"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ────────────────────────────────────────────
# Chromium インストール検出 → requires_chromium マーカー付きを自動 skip
# ────────────────────────────────────────────
def _chromium_available() -> bool:
    """Playwright Chromium が起動可能な状態かを軽量にチェックする。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False

    # Playwright のキャッシュディレクトリに chromium- のサブディレクトリが存在するか
    # （browser をフル起動せず、インストール済み判定だけで十分）
    cache_candidates = [
        Path.home() / ".cache" / "ms-playwright",
        Path("/ms-playwright"),
    ]
    for d in cache_candidates:
        if d.exists() and any(p.name.startswith("chromium-") for p in d.iterdir()):
            return True
    return False


_CHROMIUM_OK = _chromium_available()


def pytest_collection_modifyitems(config, items):
    """`requires_chromium` マーカー付きのテストは Chromium 未インストール時に skip する。"""
    if _CHROMIUM_OK:
        return
    skip_marker = pytest.mark.skip(
        reason="Playwright Chromium が見つかりません "
        "(`python -m playwright install chromium` を実行してください)"
    )
    for item in items:
        if "requires_chromium" in item.keywords:
            item.add_marker(skip_marker)
