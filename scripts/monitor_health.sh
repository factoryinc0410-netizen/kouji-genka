#!/usr/bin/env bash
# scripts/monitor_health.sh — liveness probe (Phase G-2).
#
# Performs a GET against $HEALTH_URL (default: http://127.0.0.1:8000/login).
# On any non-200 response (including connection refused), appends a one-line
# record to ~/logs/health_error.log AND posts a notification to a LINE WORKS
# Incoming Webhook (if configured). Stays silent when healthy.
#
# Environment variables:
#   APP_NAME    label used in the log/notification message (default: dev-app)
#   HEALTH_URL  endpoint to probe        (default: http://127.0.0.1:8000/login)
#
# Webhook URL is always read from this script's project root .env:
#     /home/ubuntu/dev-app/.env  →  LINE_WORKS_WEBHOOK_URL=https://...
# (single source of truth — dev and prod monitoring share the same channel).
# If unset, the notification is skipped (the down event is still logged).
#
# Usage:
#     ./scripts/monitor_health.sh
#     APP_NAME=prod-app HEALTH_URL=http://127.0.0.1:8000/login ./scripts/monitor_health.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

APP_NAME="${APP_NAME:-dev-app}"
URL="${HEALTH_URL:-http://127.0.0.1:8000/login}"
LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/health_error.log"
TIMEOUT_SEC=5
NOTIFY_TIMEOUT_SEC=10

mkdir -p "$LOG_DIR"

# ----- liveness probe ------------------------------------------------------
# GET request; -o /dev/null discards body; -w prints status code only.
# --max-time bounds the whole request. On connection failure curl exits
# non-zero and we substitute "000" for %{http_code}.
# (HEAD is unreliable here: /login is a GET-only route → returns 405 to HEAD
# even when the server is healthy.)
status="$(curl -s -o /dev/null \
    -w '%{http_code}' \
    --max-time "$TIMEOUT_SEC" \
    "$URL" 2>/dev/null)"
status="${status:-000}"

if [ "$status" = "200" ]; then
    exit 0
fi

# ----- failure path: log + notify -----------------------------------------
ts="$(date '+%Y-%m-%d %H:%M:%S')"
host="$(hostname)"
message="[$ts] $APP_NAME DOWN host=$host url=$URL status=$status"
echo "$message" >> "$LOG_FILE"

# Read LINE_WORKS_WEBHOOK_URL from .env without sourcing the whole file.
# Strips surrounding "..." or '...' if present.
webhook_url=""
if [ -f "$ENV_FILE" ]; then
    webhook_url="$(grep -E '^LINE_WORKS_WEBHOOK_URL=' "$ENV_FILE" \
        | head -n1 \
        | sed -e 's/^LINE_WORKS_WEBHOOK_URL=//' \
              -e 's/^"\(.*\)"$/\1/' \
              -e "s/^'\(.*\)'\$/\1/")"
fi

# Skip notification if URL not configured.
if [ -z "$webhook_url" ]; then
    exit 1
fi

# JSON-escape the message text (backslash first, then double-quote).
# Payload schema: legacy WorksMobile webhook (webhook.worksmobile.com) requires
# {"body":{"text":"..."}} — verified empirically against the configured URL.
# Other shapes ({"text":...}, {"type":"text","text":...}, etc.) all return
# HTTP 400 "missing parameter (body.text)". The newer worksapis.com bot API
# uses {"content":{"type":"text","text":"..."}} with OAuth — not used here.
text="${message//\\/\\\\}"
text="${text//\"/\\\"}"
payload="{\"body\":{\"text\":\"$text\"}}"

http_code="$(curl -s -o /dev/null \
    -w '%{http_code}' \
    -X POST \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    --max-time "$NOTIFY_TIMEOUT_SEC" \
    "$webhook_url" 2>/dev/null)"
http_code="${http_code:-000}"

# LINE WORKS Incoming Webhook returns 200 on success.
if [ "$http_code" != "200" ]; then
    echo "[$ts] NOTIFY_FAIL webhook_status=$http_code" >> "$LOG_FILE"
fi

exit 1
