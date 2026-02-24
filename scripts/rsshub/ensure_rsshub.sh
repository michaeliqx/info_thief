#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

RSSHUB_HOST="${RSSHUB_HOST:-0.0.0.0}"
RSSHUB_PORT="${RSSHUB_PORT:-1200}"
RSSHUB_HEALTH_HOST="${RSSHUB_HEALTH_HOST:-127.0.0.1}"
RSSHUB_BASE_URL="${RSSHUB_BASE_URL:-http://$RSSHUB_HEALTH_HOST:$RSSHUB_PORT}"
RSSHUB_START_SCRIPT="${RSSHUB_START_SCRIPT:-$PROJECT_DIR/scripts/rsshub/start_rsshub.sh}"
RSSHUB_WAIT_SECONDS="${RSSHUB_WAIT_SECONDS:-60}"
RSSHUB_LOG_FILE="${RSSHUB_LOG_FILE:-$PROJECT_DIR/logs/rsshub.log}"

health_check() {
  curl -fsS --max-time 2 "$RSSHUB_BASE_URL/" >/dev/null 2>&1
}

if health_check; then
  echo "[rsshub] already healthy: $RSSHUB_BASE_URL"
  exit 0
fi

mkdir -p "$(dirname "$RSSHUB_LOG_FILE")"
echo "[rsshub] not ready, starting in background: $RSSHUB_BASE_URL"
nohup env RSSHUB_HOST="$RSSHUB_HOST" RSSHUB_PORT="$RSSHUB_PORT" RSSHUB_LISTEN_INADDR_ANY=1 "$RSSHUB_START_SCRIPT" >>"$RSSHUB_LOG_FILE" 2>&1 &

for ((i = 1; i <= RSSHUB_WAIT_SECONDS; i++)); do
  if health_check; then
    echo "[rsshub] healthy after ${i}s: $RSSHUB_BASE_URL"
    exit 0
  fi
  sleep 1
done

echo "[rsshub] failed to start within ${RSSHUB_WAIT_SECONDS}s, check log: $RSSHUB_LOG_FILE" >&2
exit 1
