#!/bin/bash
# Supervisor/cron 启动脚本：加载 .env，确保 RSSHub 可用后执行单次日报任务
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
source .venv/bin/activate
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

"$PROJECT_DIR/scripts/rsshub/ensure_rsshub.sh"
exec python -m app.run_daily
