#!/usr/bin/env bash
# ================================================================
#  Factoryskills - Linux (Ubuntu/Debian) セットアップスクリプト
#
#  対象 OS  : Ubuntu 22.04+ / Debian 12+ (さくらVPS 標準)
#  実行方法 : sudo bash deploy_linux/install_linux.sh
#
#  処理内容:
#    1. システムパッケージ更新と必要 apt パッケージ導入
#    2. 日本語フォント導入 (Noto CJK / IPA / IPAex)
#    3. アプリ用ユーザー (factoryskills) 作成
#    4. /opt/factoryskills ディレクトリ作成と所有権設定
#    5. Python venv 作成 + requirements.txt インストール
#    6. Playwright Chromium + システム依存ライブラリ導入
#    7. .env 配置（テンプレートからコピーして編集を促す）
#    8. systemd サービス登録 + 自動起動設定
#
#  冪等性: 既に存在するユーザー/ディレクトリ/サービスは検出してスキップする。
# ================================================================

set -euo pipefail

# ── 設定 ────────────────────────────────────────────────────
APP_USER="factoryskills"
APP_GROUP="factoryskills"
APP_DIR="/opt/factoryskills"
SERVICE_NAME="factoryskills"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# このスクリプトが置かれているディレクトリ（= deploy_linux/）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# プロジェクトルート（= deploy_linux の親）
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

# ── 表示ヘルパ ───────────────────────────────────────────────
log_info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
log_ok()    { echo -e "\033[1;32m[ OK ]\033[0m  $*"; }
log_warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
log_error() { echo -e "\033[1;31m[FAIL]\033[0m  $*" >&2; }

# ── 前提チェック ─────────────────────────────────────────────
if [[ "${EUID}" -ne 0 ]]; then
    log_error "このスクリプトは root 権限で実行してください: sudo bash $0"
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    log_error "apt-get が見つかりません。Ubuntu/Debian 系のみサポートしています。"
    exit 1
fi

log_info "リポジトリルート: ${REPO_ROOT}"
log_info "アプリ配置先     : ${APP_DIR}"
log_info "実行ユーザー     : ${APP_USER}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. システムパッケージ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[1/8] apt パッケージを更新・インストール中..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    ca-certificates \
    curl \
    git \
    sudo
log_ok "基本パッケージ導入完了"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 日本語フォント
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[2/8] 日本語フォントをインストール中..."
apt-get install -y \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-ipafont \
    fonts-ipaexfont
fc-cache -fv >/dev/null 2>&1 || true
log_ok "日本語フォント導入完了 (Noto CJK / IPA / IPAex)"

# 確認: Noto Serif CJK JP が存在するか
if [[ ! -f /usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc ]]; then
    log_warn "NotoSerifCJK-Regular.ttc が想定パスに無い。.env の FONT_PATH を要調整。"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. アプリ用ユーザー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[3/8] アプリ用ユーザー (${APP_USER}) を確認/作成中..."
if id -u "${APP_USER}" >/dev/null 2>&1; then
    log_ok "ユーザー ${APP_USER} は既に存在します — スキップ"
else
    useradd --system --create-home --home-dir "/home/${APP_USER}" \
            --shell /usr/sbin/nologin "${APP_USER}"
    log_ok "ユーザー ${APP_USER} を作成しました"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. /opt/factoryskills の準備
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[4/8] アプリディレクトリを準備中..."
mkdir -p "${APP_DIR}"
mkdir -p "${APP_DIR}/data"
mkdir -p "${APP_DIR}/uploads"
mkdir -p "${APP_DIR}/outputs"
mkdir -p "${APP_DIR}/logs"

# リポジトリの中身をコピー（.venv と .git は除外）
log_info "  → ソースコードを ${APP_DIR} に同期..."
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.venv' \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='data' \
        --exclude='uploads' \
        --exclude='outputs' \
        --exclude='logs' \
        --exclude='.env' \
        "${REPO_ROOT}/" "${APP_DIR}/"
else
    log_warn "rsync が無いため cp で代替（既存ファイルは上書きされません）"
    cp -rn "${REPO_ROOT}/." "${APP_DIR}/"
fi

chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
chmod 755 "${APP_DIR}"
log_ok "ディレクトリ準備完了"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Python venv + 依存パッケージ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[5/8] Python venv を作成し requirements.txt をインストール中..."
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel setuptools
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
log_ok "Python 依存パッケージ導入完了"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Playwright Chromium
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[6/8] Playwright Chromium とシステム依存ライブラリを導入中..."
# --with-deps は内部で apt 経由のシステムライブラリインストールを行うため
# root 権限で playwright を実行する必要がある（playwright-deps は root 必須）。
"${APP_DIR}/.venv/bin/python" -m playwright install-deps chromium
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" -m playwright install chromium
log_ok "Playwright Chromium 導入完了"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. .env の配置
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[7/8] .env を配置中..."
ENV_TEMPLATE="${APP_DIR}/deploy_linux/.env.linux.template"
ENV_TARGET="${APP_DIR}/.env"

if [[ -f "${ENV_TARGET}" ]]; then
    log_warn ".env は既に存在します — 上書きしません: ${ENV_TARGET}"
else
    if [[ -f "${ENV_TEMPLATE}" ]]; then
        cp "${ENV_TEMPLATE}" "${ENV_TARGET}"
        # SECRET_KEY を自動生成して置換
        SECRET_VAL="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET_VAL}|" "${ENV_TARGET}"
        chown "${APP_USER}:${APP_GROUP}" "${ENV_TARGET}"
        chmod 600 "${ENV_TARGET}"
        log_ok ".env を作成し SECRET_KEY を自動生成しました (perm=600)"
    else
        log_error "テンプレートが見つかりません: ${ENV_TEMPLATE}"
        exit 1
    fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. systemd サービス登録
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log_info "[8/8] systemd サービスを登録中..."
SERVICE_SRC="${APP_DIR}/deploy_linux/factoryskills.service"
if [[ ! -f "${SERVICE_SRC}" ]]; then
    log_error "サービスユニットが見つかりません: ${SERVICE_SRC}"
    exit 1
fi

cp "${SERVICE_SRC}" "${SERVICE_FILE}"
chmod 644 "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
log_ok "systemd 登録完了 (自動起動: 有効)"

# 既に起動中なら restart、未起動なら start
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log_info "  → サービス再起動..."
    systemctl restart "${SERVICE_NAME}"
else
    log_info "  → サービス起動..."
    systemctl start "${SERVICE_NAME}"
fi

sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log_ok "サービスが起動しました"
else
    log_error "サービス起動失敗 — journalctl -u ${SERVICE_NAME} -e で原因を確認してください"
    systemctl status "${SERVICE_NAME}" --no-pager || true
    exit 1
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  完了サマリ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo
echo "================================================================"
echo "  Factoryskills セットアップ完了"
echo "================================================================"
echo
echo "  アプリ配置先 : ${APP_DIR}"
echo "  実行ユーザー : ${APP_USER}"
echo "  サービス名   : ${SERVICE_NAME}"
echo "  .env         : ${APP_DIR}/.env (perm=600, SECRET_KEY 自動生成済)"
echo
echo "  ── よく使うコマンド ──"
echo "    状態確認    : sudo systemctl status ${SERVICE_NAME}"
echo "    ログ追跡    : sudo journalctl -u ${SERVICE_NAME} -f"
echo "    再起動      : sudo systemctl restart ${SERVICE_NAME}"
echo "    停止        : sudo systemctl stop ${SERVICE_NAME}"
echo
echo "  ── 動作確認 ──"
echo "    curl http://localhost:8000/health    # 'OK' が返れば成功"
echo
echo "  ── ファイアウォール（さくらVPS パケットフィルタ等）──"
echo "    TCP 8000 番（または 80/443 + nginx 経由）を開放してください"
echo
echo "================================================================"
