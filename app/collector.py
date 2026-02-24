from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from app.models import RawItem, SourceConfig

logger = logging.getLogger(__name__)

# 相对时间匹配：N小时前、N分钟前、昨天、N天前、今天
_RELATIVE_PATTERNS = [
    (re.compile(r"(\d+)\s*小时前"), lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r"(\d+)\s*分钟前"), lambda m: timedelta(minutes=int(m.group(1)))),
    (re.compile(r"(\d+)\s*天前"), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r"昨天"), lambda _: timedelta(days=1)),
    (re.compile(r"今天"), lambda _: timedelta(hours=0)),
]

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


def _parse_html_date(
    text: str,
    regex: Optional[str] = None,
    ref_time: Optional[datetime] = None,
) -> Optional[datetime]:
    """解析 HTML 中的日期文本，支持绝对日期和相对时间。"""
    if not text or not text.strip():
        return None
    text = text.strip()
    ref = ref_time or datetime.now(timezone.utc)

    # 相对时间
    for pattern, delta_fn in _RELATIVE_PATTERNS:
        m = pattern.search(text)
        if m:
            delta = delta_fn(m)
            return (ref - delta).astimezone(timezone.utc)

    # 用正则提取日期片段
    if regex:
        m = re.search(regex, text)
        if m:
            text = m.group(0)

    return _parse_datetime(text)


def _extract_date_from_element(
    elem,
    date_attr: Optional[str],
    date_regex: Optional[str],
    ref_time: datetime,
) -> Optional[datetime]:
    """从 DOM 元素提取发布时间。"""
    if date_attr and elem.get(date_attr):
        return _parse_datetime(elem.get(date_attr))
    text = elem.get_text(" ", strip=True)
    return _parse_html_date(text, regex=date_regex, ref_time=ref_time)


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


def _extract_page_links(
    source: SourceConfig, html: str, ref_time: datetime
) -> Iterable[tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    selector = source.article_selector or "article a, h2 a, h3 a, li a"
    compiled_pattern = re.compile(source.link_pattern) if source.link_pattern else None

    def _check_and_yield(a, container) -> Optional[tuple[str, str, Optional[datetime]]]:
        href = a.get("href")
        text = a.get_text(" ", strip=True)
        if not href or not text:
            return None
        lowered_text = text.strip().lower()
        if len(text.strip()) < 8:
            return None
        if any(noise in lowered_text for noise in _NOISE_TITLE_WORDS):
            return None
        if lowered_text.endswith("app"):
            return None
        if href.startswith(("#", "javascript:", "mailto:")):
            return None
        url = urljoin(source.url, href)
        if not url.startswith("http"):
            return None
        if compiled_pattern and not compiled_pattern.search(url):
            return None
        published = None
        if source.date_selector and container:
            date_elem = container.select_one(source.date_selector)
            if date_elem:
                published = _extract_date_from_element(
                    date_elem, source.date_attr, source.date_regex, ref_time
                )
        return (text, url, published)

    if source.item_container_selector and source.date_selector:
        for container in soup.select(source.item_container_selector):
            a = container.select_one(selector)
            if not a:
                continue
            result = _check_and_yield(a, container)
            if result:
                yield result
        return

    if source.date_selector:
        for a in soup.select(selector):
            container = a.parent
            while container and container.name:
                date_elem = container.select_one(source.date_selector)
                if date_elem:
                    break
                container = container.parent if hasattr(container, "parent") else None
            result = _check_and_yield(a, container)
            if result:
                yield result
        return

    for a in soup.select(selector):
        result = _check_and_yield(a, None)
        if result:
            yield result


def _collect_html(source: SourceConfig, client: httpx.Client) -> list[RawItem]:
    resp = client.get(source.url)
    resp.raise_for_status()
    now = datetime.now(timezone.utc)

    items: list[RawItem] = []
    seen_urls: set[str] = set()
    for title, url, published in _extract_page_links(source, resp.text, now):
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
                published_at=published,
                discovered_at=now,
                tags=source.tags,
            )
        )
        if len(items) >= 30:
            break
    return items


def collect_from_source(
    source: SourceConfig,
    timeout_seconds: int = 15,
    proxy: str | None = None,
) -> list[RawItem]:
    logger.info("Collecting source: %s", source.name)
    client_kwargs: dict = {"timeout": timeout_seconds, "follow_redirects": True}
    if proxy and proxy.strip():
        client_kwargs["proxy"] = proxy.strip()
    with httpx.Client(**client_kwargs) as client:
        if source.type == "rss":
            return _collect_rss(source, client)
        if source.type == "html":
            return _collect_html(source, client)
    raise ValueError(f"Unsupported source type: {source.type}")
