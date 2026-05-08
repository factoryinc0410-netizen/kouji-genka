#!/usr/bin/env bash
# ================================================================
#  scripts/backup_db.sh — SQLite DB の日次バックアップ
#
#  概要:
#    web_app/data/app.db を ``backups/db_backup_YYYYMMDD.sqlite`` として
#    コピー保存し、30 日より古い同形式のバックアップを自動削除する。
#
#  使い方:
#    # 実行権限を付与 (初回のみ)
#    chmod +x scripts/backup_db.sh
#
#    # 手動実行
#    ./scripts/backup_db.sh
#
#    # cron 例 (毎日 03:30 に実行)
#    30 3 * * * cd /home/ubuntu/dev-app && ./scripts/backup_db.sh \
#                   >> /var/log/factoryskills/backup_db.log 2>&1
#
#  環境変数 (任意):
#    BACKUP_DB_SRC       バックアップ対象 DB の絶対パス
#                        (デフォルト: <project_root>/web_app/data/app.db)
#    BACKUP_DB_DEST_DIR  バックアップ保存先ディレクトリ
#                        (デフォルト: <project_root>/backups)
#    BACKUP_DB_RETENTION 保持日数 (デフォルト: 30)
#
#  方式:
#    sqlite3 .backup を使う (アプリが書込中でも安全にスナップショットを取れる)。
#    sqlite3 が未インストールの環境では cp フォールバックを使うが、その場合
#    アプリ書込中のコピーは破損リスクがあるので注意。
#
#  終了コード:
#    0  正常 (新規バックアップ作成 + クリーンアップ完了)
#    1  異常 (DB 不在 / コピー失敗 など)
# ================================================================
set -euo pipefail

# ── プロジェクトルート (このスクリプトの 1 つ上) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 設定 (環境変数で上書き可) ──
BACKUP_DB_SRC="${BACKUP_DB_SRC:-${PROJECT_ROOT}/web_app/data/app.db}"
BACKUP_DB_DEST_DIR="${BACKUP_DB_DEST_DIR:-${PROJECT_ROOT}/backups}"
BACKUP_DB_RETENTION="${BACKUP_DB_RETENTION:-30}"

TIMESTAMP="$(date +%Y%m%d)"
DEST_FILE="${BACKUP_DB_DEST_DIR}/db_backup_${TIMESTAMP}.sqlite"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

err() {
    log "ERROR: $*" >&2
}

# ── 事前チェック ──
if [[ ! -f "${BACKUP_DB_SRC}" ]]; then
    err "バックアップ対象 DB が見つかりません: ${BACKUP_DB_SRC}"
    exit 1
fi

mkdir -p "${BACKUP_DB_DEST_DIR}"

# ── バックアップ取得 ──
# sqlite3 .backup はオンラインバックアップ API を使うので、アプリが書き込み中でも
# 整合性のあるスナップショットを取れる。sqlite3 CLI が無ければ cp で代替。
if command -v sqlite3 >/dev/null 2>&1; then
    log "sqlite3 .backup でスナップショット取得: ${BACKUP_DB_SRC} -> ${DEST_FILE}"
    if ! sqlite3 "${BACKUP_DB_SRC}" ".backup '${DEST_FILE}'"; then
        err "sqlite3 .backup に失敗しました"
        exit 1
    fi
else
    log "sqlite3 CLI が見つからないため cp でフォールバック (アプリ書込中は破損リスクあり)"
    if ! cp -p "${BACKUP_DB_SRC}" "${DEST_FILE}"; then
        err "cp に失敗しました"
        exit 1
    fi
fi

# サイズを表示
SIZE_BYTES="$(stat -c %s "${DEST_FILE}" 2>/dev/null || stat -f %z "${DEST_FILE}")"
log "バックアップ完了: ${DEST_FILE} (${SIZE_BYTES} bytes)"

# ── 保持期間を超えた古いバックアップを削除 ──
# db_backup_YYYYMMDD.sqlite 形式のファイルだけを対象 (他のファイルを誤削除しない)。
log "保持期間 ${BACKUP_DB_RETENTION} 日を超えた古いバックアップを削除"
DELETED_COUNT=0
# find -mtime は最終更新日時ベース。GNU find / BSD find 互換のフラグのみ使用。
while IFS= read -r -d '' old; do
    log "  delete: ${old}"
    rm -f -- "${old}"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done < <(
    find "${BACKUP_DB_DEST_DIR}" \
        -maxdepth 1 -type f \
        -name 'db_backup_????????.sqlite' \
        -mtime "+${BACKUP_DB_RETENTION}" \
        -print0
)
log "削除件数: ${DELETED_COUNT}"

log "完了"
exit 0
