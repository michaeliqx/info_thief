#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${1:-http://0.0.0.0:1200}"
shift || true

python3 "$SCRIPT_DIR/test_rsshub.py" "$BASE_URL" "$@"
