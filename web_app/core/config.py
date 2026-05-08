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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# 本番 HTTPS 環境では SESSION_COOKIE_SECURE=true を設定する。
# デフォルト False（HTTP のローカル開発を壊さないため）。
SESSION_COOKIE_SECURE: bool = _env_bool("SESSION_COOKIE_SECURE", False)

# ── ブルートフォース対策（アカウントロックアウト） ───────────
# LOGIN_MAX_FAILURES 回連続で失敗すると LOGIN_LOCKOUT_MINUTES 分間ロックする。
# ログイン成功で失敗カウンタはリセットされる。
LOGIN_MAX_FAILURES: int = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_MINUTES: int = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

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

# ── 資格者証管理 (qualifications) ────────────────────────────
# Gemini API キー（未設定なら OCR は自動で無効化される）
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
# OCR 機能の ON/OFF。GEMINI_API_KEY 未設定時は実質無効。
QUALIFICATIONS_OCR_ENABLED: bool = _env_bool("QUALIFICATIONS_OCR_ENABLED", True)
# 1 ファイルあたり最大サイズ (MB)
QUALIFICATIONS_MAX_FILE_MB: int = int(os.getenv("QUALIFICATIONS_MAX_FILE_MB", "20"))
# 1 アップロードジョブで投入可能な最大ファイル数
QUALIFICATIONS_MAX_FILES_PER_UPLOAD: int = int(
    os.getenv("QUALIFICATIONS_MAX_FILES_PER_UPLOAD", "5")
)

# ── クリーンアップ ────────────────────────────────────────────
CLEANUP_AGE_HOURS: int = int(os.getenv("CLEANUP_AGE_HOURS", "72"))
# クリーンアップ実行間隔（秒）— デフォルト 1 時間
CLEANUP_INTERVAL: int = int(os.getenv("CLEANUP_INTERVAL", "3600"))
# ユーザーごとに保持する最大ジョブ数（completed/error）
MAX_JOBS_PER_USER: int = int(os.getenv("MAX_JOBS_PER_USER", "4"))

# ── ワーカー ──────────────────────────────────────────────────
# ジョブ処理タイムアウト（秒）— デフォルト 10 分
JOB_TIMEOUT_SECONDS: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "600"))

# ── 期限アラートメール通知 (qualifications) ───────────────────
# scripts/send_expiration_alerts.py を cron から実行する際に参照される。
# SMTP_SERVER が空のままなら通知は無効化扱い（収集だけ走らせて終了）。
SMTP_SERVER: str = os.getenv("SMTP_SERVER", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
# True の場合 SMTP 接続後 starttls() を呼ぶ。587 ポートでの一般的な構成。
SMTP_USE_TLS: bool = _env_bool("SMTP_USE_TLS", True)
# 送信元アドレス（ヘッダ "From")。
ALERT_EMAIL_FROM: str = os.getenv("ALERT_EMAIL_FROM", "")
# 宛先（ヘッダ "To"）。複数指定はカンマ区切り。
ALERT_EMAIL_TO: str = os.getenv("ALERT_EMAIL_TO", "")

# ── ディレクトリ自動作成 ──────────────────────────────────────
for _dir in (DATABASE_PATH.parent, UPLOAD_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
