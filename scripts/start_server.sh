#!/bin/bash
# Supervisor 启动脚本：加载 .env 后启动 HTTP 服务（适用于不支持 env_file 的 Supervisor 版本）
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
exec python -m app.server --host 0.0.0.0 --port 8000

