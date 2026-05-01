"""
Web アプリケーション設定 — 環境変数 (.env) から読み込み

すべてのパス設定は .env ファイルで上書き可能。
skills モジュールからも参照されるため、プロジェクト全体の設定の一元管理を担う。
"""
import os
import secrets
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# .env をプロジェクトルートから読み込む
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ── アプリケーション設定 ──────────────────────────────────────
SECRET_KEY: str = os.getenv("SECRET_KEY", secrets.token_hex(32))
SESSION_MAX_AGE: int = int(os.getenv("SESSION_MAX_AGE", "28800"))  # 8時間

# ── サーバー設定 ──────────────────────────────────────────────
HOST: str = os.getenv("HOST", "127.0.0.1")
PORT: int = int(os.getenv("PORT", "8000"))

# ── データベース ──────────────────────────────────────────────
_WEB_APP_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH: Path = Path(os.getenv("DATABASE_PATH", str(_WEB_APP_DIR / "data" / "app.db")))

# ── ファイルストレージ ────────────────────────────────────────
UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", str(_WEB_APP_DIR / "uploads")))
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(_WEB_APP_DIR / "outputs")))

# ── COM 一時ディレクトリ（ASCIIパス必須・Windows専用） ────────
# デフォルト: Windows なら C:\tmp_chukumon、それ以外はシステム一時ディレクトリ
_DEFAULT_COM_TEMP = (
    r"C:\tmp_chukumon" if os.name == "nt"
    else str(Path(tempfile.gettempdir()) / "tmp_chukumon")
)
COM_TEMP_DIR: Path = Path(os.getenv("COM_TEMP_DIR", _DEFAULT_COM_TEMP))

# ── フォント設定 ─────────────────────────────────────────────
# PDF スタンプに使用する日本語フォントパス
# デフォルト: Windows の MS 明朝（他 OS では .env で明示指定が必要）
_DEFAULT_FONT_PATH = (
    r"C:\Windows\Fonts\msmincho.ttc" if os.name == "nt"
    else ""
)
FONT_PATH: str = os.getenv("FONT_PATH", _DEFAULT_FONT_PATH)

# ── LibreOffice 設定（Excel→PDF 変換用） ────────────────────
# Session 0 環境で Excel COM の代替として使用する
# デフォルト: Windows 標準インストールパス
_DEFAULT_LIBREOFFICE_PATH = (
    r"C:\Program Files\LibreOffice\program\soffice.exe" if os.name == "nt"
    else "soffice"  # Linux/macOS は PATH から探す
)
LIBREOFFICE_PATH: str = os.getenv("LIBREOFFICE_PATH", _DEFAULT_LIBREOFFICE_PATH)

# LibreOffice 変換タイムアウト（秒）
LIBREOFFICE_TIMEOUT: int = int(os.getenv("LIBREOFFICE_TIMEOUT", "120"))

# ── アップロード制限 ──────────────────────────────────────────
MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

# ── クリーンアップ ────────────────────────────────────────────
CLEANUP_AGE_HOURS: int = int(os.getenv("CLEANUP_AGE_HOURS", "72"))

# ── ディレクトリ自動作成 ──────────────────────────────────────
for _dir in (DATABASE_PATH.parent, UPLOAD_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
