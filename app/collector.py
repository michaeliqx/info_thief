from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from app.models import RawItem, SourceConfig

logger = logging.getLogger(__name__)

_NOISE_TITLE_WORDS = {
    "登录",
    "注册",
    "关于",
    "联系我们",
    "订阅",
    "隐私",
    "条款",
    "下载",
    "交流群",
    "公众号",
    " app",
    "app ",
    "learn more",
    "more",
}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dtparser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _collect_rss(source: SourceConfig, client: httpx.Client) -> list[RawItem]:
    resp = client.get(source.url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[RawItem] = []
    now = datetime.now(timezone.utc)
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        content = ""
        if entry.get("summary"):
            content = str(entry.get("summary"))
        elif entry.get("content"):
            content = str(entry.get("content"))

        published = None
        for key in ("published", "updated", "pubDate"):
            published = _parse_datetime(entry.get(key))
            if published:
                break

        items.append(
            RawItem(
                source_name=source.name,
                source_weight=source.weight,
                url=link,
                title=title,
                content=content,
                published_at=published,
                discovered_at=now,
                tags=source.tags,
            )
        )
    return items


def _extract_page_links(source: SourceConfig, html: str) -> Iterable[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    selector = source.article_selector or "article a, h2 a, h3 a, li a"
    compiled_pattern = re.compile(source.link_pattern) if source.link_pattern else None
    for a in soup.select(selector):
        href = a.get("href")
        text = a.get_text(" ", strip=True)
        if not href or not text:
            continue
        lowered_text = text.strip().lower()
        if len(text.strip()) < 8:
            continue
        if any(noise in lowered_text for noise in _NOISE_TITLE_WORDS):
            continue
        if lowered_text.endswith("app"):
            continue
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(source.url, href)
        if not url.startswith("http"):
            continue
        if compiled_pattern and not compiled_pattern.search(url):
            continue
        yield text, url


def _collect_html(source: SourceConfig, client: httpx.Client) -> list[RawItem]:
    resp = client.get(source.url)
    resp.raise_for_status()
    now = datetime.now(timezone.utc)

    items: list[RawItem] = []
    seen_urls: set[str] = set()
    for title, url in _extract_page_links(source, resp.text):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append(
            RawItem(
                source_name=source.name,
                source_weight=source.weight,
                url=url,
                title=title,
                content="",
                published_at=None,
                discovered_at=now,
                tags=source.tags,
            )
        )
        if len(items) >= 30:
            break
    return items


def collect_from_source(source: SourceConfig, timeout_seconds: int = 15) -> list[RawItem]:
    logger.info("Collecting source: %s", source.name)
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        if source.type == "rss":
            return _collect_rss(source, client)
        if source.type == "html":
            return _collect_html(source, client)
    raise ValueError(f"Unsupported source type: {source.type}")
