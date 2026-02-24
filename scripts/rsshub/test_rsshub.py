#!/usr/bin/env python3
from __future__ import annotations

import sys
import urllib.parse
import xml.etree.ElementTree as ET

import httpx


def _default_paths() -> list[str]:
    keywords = [
        "数字生命卡兹克",
        "数字生命卡兹克 知乎",
        "数字生命卡兹克 腾讯",
        "MindCode 公众号",
        "AGENT橘 OpenClaw",
        "Founder Park",
        "刘小排r 53AI",
        "42章经 AI",
        "歸藏 AI",
    ]
    return [f"/huxiu/search/{urllib.parse.quote(k)}" for k in keywords]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://0.0.0.0:1200"
    paths = sys.argv[2:] if len(sys.argv) > 2 else _default_paths()

    ok = 0
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for path in paths:
            url = f"{base.rstrip('/')}{path}"
            try:
                resp = client.get(url)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR] {path} request failed: {exc}")
                continue

            if resp.status_code != 200:
                print(f"[ERR] {path} status={resp.status_code}")
                continue

            if "<rss" not in resp.text[:1000].lower():
                print(f"[ERR] {path} non-rss response")
                continue

            try:
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                title = (channel.findtext("title") if channel is not None else "") or ""
                items = root.findall("./channel/item")
                latest = items[0].findtext("pubDate") if items else ""
                print(f"[OK] {path} items={len(items)} latest={latest} title={title[:40]}")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR] {path} parse error: {exc}")

    print(f"[SUMMARY] ok={ok} total={len(paths)}")
    return 0 if ok == len(paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
