#!/bin/bash
# 测试需代理的 news.google.com 源是否可检索
# 使用前请确保：1) .env 中 HTTP_PROXY 已配置  2) 代理服务已启动
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
echo "HTTP_PROXY=${HTTP_PROXY:-未设置}"
echo "---"
python3 -c "
from app.collector import collect_from_source
from app.config import load_sources
import os
sources = load_sources()
proxy = os.getenv('HTTP_PROXY') or ''
target_names = [
    '数字生命卡兹克(知乎专栏主源)', '数字生命卡兹克(腾讯新闻镜像)', '数字生命卡兹克(虎嗅转载)', '数字生命卡兹克(51CTO转载)',
    'MindCode(公众号公开转载)', 'AGENT橘(公众号公开转载)', 'Founder Park(公众号公开转载)',
    '刘小排r(公众号公开转载)', '42章经(公众号公开转载)', '歸藏(公众号公开转载)'
]
ok, fail = 0, 0
for s in sources:
    if s.name in target_names:
        try:
            items = collect_from_source(s, 30, proxy=proxy or None)
            print('OK', s.name, '->', len(items), 'items')
            ok += 1
        except Exception as e:
            print('FAIL', s.name, '->', str(e)[:70])
            fail += 1
print('---')
print('成功:', ok, '失败:', fail)
"
