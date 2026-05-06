#!/usr/bin/env bash
# scripts/monitor_health.sh — liveness probe for dev-app (Phase G-2).
#
# Hits http://127.0.0.1:8000/login. On any non-200 response (including
# connection refused), appends a one-line record to ~/logs/health_error.log
# AND posts a notification to LINE WORKS Incoming Webhook (if configured).
# Stays silent when healthy so cron output stays clean.
#
# Webhook URL is read from $PROJECT_ROOT/.env at runtime:
#     LINE_WORKS_WEBHOOK_URL=https://...
# If the variable is unset, notification is skipped (down event still logged).
#
# Usage:
#     ./scripts/monitor_health.sh
#     # cron: */5 * * * * /home/ubuntu/dev-app/scripts/monitor_health.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

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
message="[$ts] dev-app DOWN host=$host url=$URL status=$status"
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
text="${message//\\/\\\\}"
text="${text//\"/\\\"}"
payload="{\"content\":{\"type\":\"text\",\"text\":\"$text\"}}"

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
