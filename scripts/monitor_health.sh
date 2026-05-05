#!/usr/bin/env bash
# scripts/monitor_health.sh — liveness probe for dev-app (Phase G-2).
#
# Hits http://127.0.0.1:8000/login. On any non-200 response (including
# connection refused), appends a one-line record to ~/logs/health_error.log.
# Stays silent when healthy so cron output stays clean.
#
# Usage:
#     ./scripts/monitor_health.sh
#     # cron: */5 * * * * /home/ubuntu/dev-app/scripts/monitor_health.sh

set -u

URL="${HEALTH_URL:-http://127.0.0.1:8000/login}"
LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/health_error.log"
TIMEOUT_SEC=5

mkdir -p "$LOG_DIR"

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

if [ "$status" != "200" ]; then
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] DOWN url=$URL status=$status" >> "$LOG_FILE"
    exit 1
fi

exit 0
