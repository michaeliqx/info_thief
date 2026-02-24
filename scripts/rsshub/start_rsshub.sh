#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RSSHUB_DIR="${RSSHUB_DIR:-$PROJECT_DIR/.rsshub/RSSHub_stable}"
RSSHUB_HOST="${RSSHUB_HOST:-0.0.0.0}"
RSSHUB_PORT="${RSSHUB_PORT:-1200}"
RSSHUB_LISTEN_INADDR_ANY="${RSSHUB_LISTEN_INADDR_ANY:-1}"

"$SCRIPT_DIR/setup_rsshub.sh"

cd "$RSSHUB_DIR"
echo "[rsshub] starting on $RSSHUB_HOST:$RSSHUB_PORT"
exec env PORT="$RSSHUB_PORT" HOST="$RSSHUB_HOST" LISTEN_INADDR_ANY="$RSSHUB_LISTEN_INADDR_ANY" NODE_ENV=production npm start
