#!/bin/bash
# Supervisor 启动脚本：加载 .env 后启动定时调度器（适用于不支持 env_file 的 Supervisor 版本）
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
exec python -m app.scheduler
