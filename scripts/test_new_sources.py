#!/usr/bin/env python3
"""测试新接入的 4 个消息源：AI科技大本营、蔡荔谈AI、Andy730、Xsignal"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_settings, load_sources
from app.collector import collect_from_source
from app.normalizer import normalize_items


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    settings = load_settings("config/settings.yaml")
    all_sources = load_sources("config/sources.yaml")
    proxy = (settings.http_proxy or "").strip() or None
    target_names = {
        "AI科技大本营(搜狗微信检索)",
        "蔡荔谈AI(搜狗微信检索)",
        "Andy730(搜狗微信检索)",
        "Xsignal(虎嗅专栏)",
    }
    sources = [s for s in all_sources if s.name in target_names]
    if not sources:
        print("未找到目标源，检查 sources.yaml")
        return 1

    print("=" * 60)
    print("采集 4 个新源")
    print("=" * 60)

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)  # 放宽到 7 天用于校验
    all_items = []

    for source in sources:
        try:
            items = collect_from_source(source, timeout_seconds=25, proxy=proxy)
            all_items.extend(items)
            print(f"\n[{source.name}] 原始抓取: {len(items)} 条")
            for i, item in enumerate(items[:5], 1):
                pub = item.published_at.strftime("%Y-%m-%d %H:%M") if item.published_at else "N/A"
                in_window = "24h内" if (item.published_at and since <= item.published_at <= now) else "超24h"
                print(f"  {i}. [{in_window}] {item.title[:50]}...")
                print(f"     URL: {item.url}")
        except Exception as e:
            print(f"\n[{source.name}] 采集失败: {e}")

    normalized_24h = normalize_items(all_items, since=since, until=now)
    normalized_7d = normalize_items(all_items, since=since_7d, until=now)
    print("\n" + "=" * 60)
    print(f"24 小时内通过过滤: {len(normalized_24h)} 条")
    print(f"7 天内通过过滤: {len(normalized_7d)} 条")
    print("=" * 60)

    # 用户提供的校验：标题关键词
    expected = [
        ("蔡荔谈AI", "Claude Code", "Cloudflare 工程师用了 9 个月总结出的 Claude Code 工作流"),
        ("AI科技大本营", "OpenClaw", "OpenClaw失控删光200+邮件"),
        ("Andy730", "AGI演进", "Anthropic CEO访谈：AGI演进本质与终局展望"),
    ]

    print("\n校验用户提供的近期推送是否在抓取结果中:")
    for source_hint, kw, desc in expected:
        found = False
        for item in all_items:
            if kw in item.title or kw in (item.content or ""):
                print(f"  [匹配] {desc}")
                print(f"    source: {item.source_name} | title: {item.title[:60]}...")
                print(f"    url: {item.url[:80]}...")
                found = True
                break
        if not found:
            print(f"  [未找到] {desc} (关键词: {kw})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
