from __future__ import annotations

import json
import logging
import os
import re
import time as pytime
from html import unescape
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from app.env_utils import load_local_env
from app.models import RawItem, SourceConfig

logger = logging.getLogger(__name__)

# 相对时间匹配：N小时前、N分钟前、昨天、N天前、今天
_RELATIVE_PATTERNS = [
    (re.compile(r"(\d+)\s*小时前"), lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r"(\d+)\s*分钟前"), lambda m: timedelta(minutes=int(m.group(1)))),
    (re.compile(r"(\d+)\s*天前"), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r"刚刚"), lambda _: timedelta(minutes=0)),
    (re.compile(r"昨天"), lambda _: timedelta(days=1)),
    (re.compile(r"今天"), lambda _: timedelta(hours=0)),
]

_DATE_SNIPPET_PATTERNS = [
    re.compile(
        r"\d{4}[年/\-\.]\d{1,2}[月/\-\.]\d{1,2}(?:日|号)?(?:\s+\d{1,2}(?:[:：]\d{1,2}|点(?:\d{1,2})?))?"
    ),
    re.compile(r"\d{1,2}月\d{1,2}日(?:\s+\d{1,2}(?:[:：]\d{1,2}|点(?:\d{1,2})?))?"),
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"),
    re.compile(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2})?",
        flags=re.IGNORECASE,
    ),
]

_CHINESE_YMD_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*[年/\-\.]\s*(?P<month>\d{1,2})\s*[月/\-\.]\s*(?P<day>\d{1,2})\s*(?:日|号)?"
    r"(?:\s*(?P<hour>\d{1,2})(?:\s*[:：点时]\s*(?P<minute>\d{1,2}))?)?"
)
_CHINESE_MD_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"
    r"(?:\s*(?P<hour>\d{1,2})(?:\s*[:：点时]\s*(?P<minute>\d{1,2}))?)?"
)
_JSONLD_DATE_KEYS = {"datepublished", "datecreated", "datemodified", "uploaddate"}

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

_WECHAT_MP_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://mp.weixin.qq.com/",
}

_SOGOU_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://weixin.sogou.com/",
}

_SOGOU_REDIRECT_CHUNK_PATTERN = re.compile(r"url \+= '([^']+)';")
_WECHAT_VAR_PATTERNS = {
    "biz": re.compile(r'var\s+biz\s*=\s*"([A-Za-z0-9_=]+)"'),
    "mid": re.compile(r'var\s+mid\s*=\s*"([0-9]+)"'),
    "idx": re.compile(r'var\s+idx\s*=\s*"([0-9]+)"'),
    "sn": re.compile(r'var\s+sn\s*=\s*"([A-Za-z0-9]+)"'),
}

_PUBLISHER_PATTERNS = [
    re.compile(r"本文来自微信公众号[:：]\s*([^\s，。；;、\"“”'’<]{2,40})"),
    re.compile(r"作者\s*[：:]\s*[\"“]?([A-Za-z0-9_\-\u4e00-\u9fff·]{2,40})"),
    re.compile(r"作者\s*\"([A-Za-z0-9_\-\u4e00-\u9fff·]{2,40})\""),
    re.compile(r"来源\s*[：:]\s*([A-Za-z0-9_\-\u4e00-\u9fff·]{2,40})"),
]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dtparser.parse(value, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _parse_chinese_datetime(value: str, ref_time: datetime) -> datetime | None:
    text = value.strip()
    match = _CHINESE_YMD_PATTERN.search(text)
    if match:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = int(match.group("hour") or 0)
        minute = int(match.group("minute") or 0)
        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None

    match = _CHINESE_MD_PATTERN.search(text)
    if match:
        year = ref_time.year
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = int(match.group("hour") or 0)
        minute = int(match.group("minute") or 0)
        try:
            parsed = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            if parsed - ref_time > timedelta(days=2):
                parsed = parsed.replace(year=year - 1)
            return parsed
        except ValueError:
            return None
    return None


def _parse_unix_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not re.fullmatch(r"\d{10}|\d{13}", text):
        return None
    try:
        timestamp = int(text)
    except ValueError:
        return None
    if len(text) == 13:
        timestamp //= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _extract_date_snippet(text: str, regex: Optional[str] = None) -> str | None:
    if regex:
        match = re.search(regex, text)
        if match:
            return match.group(0).strip()
    for pattern in _DATE_SNIPPET_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def _parse_html_date(
    text: str,
    regex: Optional[str] = None,
    ref_time: Optional[datetime] = None,
    allow_relative: bool = True,
) -> Optional[datetime]:
    """解析 HTML 中的日期文本，支持绝对日期和相对时间。"""
    if not text or not text.strip():
        return None
    text = text.strip()
    ref = ref_time or datetime.now(timezone.utc)

    # 相对时间
    if allow_relative and len(text) <= 80:
        for pattern, delta_fn in _RELATIVE_PATTERNS:
            m = pattern.search(text)
            if m:
                delta = delta_fn(m)
                return (ref - delta).astimezone(timezone.utc)

    snippet = _extract_date_snippet(text, regex=regex)
    if not snippet:
        return None

    ts = _parse_unix_timestamp(snippet)
    if ts:
        return ts

    parsed = _parse_chinese_datetime(snippet, ref)
    if parsed:
        return parsed
    return _parse_datetime(snippet)


def _extract_date_from_element(
    elem,
    date_attr: Optional[str],
    date_regex: Optional[str],
    ref_time: datetime,
) -> Optional[datetime]:
    """从 DOM 元素提取发布时间。"""
    if date_attr and elem.get(date_attr):
        return _parse_html_date(str(elem.get(date_attr)), ref_time=ref_time)
    text = elem.get_text(" ", strip=True)
    return _parse_html_date(text, regex=date_regex, ref_time=ref_time)


def _extract_date_from_json_ld(payload, ref_time: datetime) -> datetime | None:
    if isinstance(payload, list):
        for item in payload:
            parsed = _extract_date_from_json_ld(item, ref_time)
            if parsed:
                return parsed
        return None
    if not isinstance(payload, dict):
        return None

    for key, value in payload.items():
        lowered_key = key.lower()
        if lowered_key in _JSONLD_DATE_KEYS and isinstance(value, str):
            parsed = _parse_html_date(value, ref_time=ref_time)
            if parsed:
                return parsed
        parsed = _extract_date_from_json_ld(value, ref_time)
        if parsed:
            return parsed
    return None


def _extract_article_published_at(html: str, ref_time: datetime) -> datetime | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    meta_selectors = [
        ("meta[property='article:published_time']", "content"),
        ("meta[property='article:modified_time']", "content"),
        ("meta[name='pubdate']", "content"),
        ("meta[name='publishdate']", "content"),
        ("meta[name='publish-date']", "content"),
        ("meta[name='date']", "content"),
        ("meta[itemprop='datePublished']", "content"),
        ("meta[itemprop='dateCreated']", "content"),
        ("meta[itemprop='dateModified']", "content"),
    ]
    for selector, attr in meta_selectors:
        for elem in soup.select(selector):
            value = (elem.get(attr) or "").strip()
            if value:
                candidates.append(value)

    for elem in soup.select("time"):
        value = (elem.get("datetime") or elem.get_text(" ", strip=True) or "").strip()
        if value:
            candidates.append(value)

    for value in candidates:
        parsed = _parse_html_date(value, ref_time=ref_time)
        if parsed:
            return parsed

    for script in soup.select("script[type='application/ld+json']"):
        payload = (script.string or script.get_text() or "").strip()
        if not payload:
            continue
        try:
            parsed = _extract_date_from_json_ld(json.loads(payload), ref_time)
            if parsed:
                return parsed
        except json.JSONDecodeError:
            continue

    body_text = soup.get_text(" ", strip=True)
    for marker in ("发布时间", "发布于", "发表于", "更新于", "日期"):
        idx = body_text.find(marker)
        if idx >= 0:
            snippet = body_text[idx : idx + 120]
            parsed = _parse_html_date(snippet, ref_time=ref_time)
            if parsed:
                return parsed

    return _parse_html_date(body_text[:4000], ref_time=ref_time, allow_relative=False)


def _matches_required_keywords(source: SourceConfig, *parts: str) -> bool:
    required = [kw.strip() for kw in source.required_keywords_any if kw and kw.strip()]
    if not required:
        return True
    haystack = " ".join(parts).lower()
    return any(kw.lower() in haystack for kw in required)


def _matches_required_author_keywords(source: SourceConfig, author: str) -> bool:
    required = [kw.strip() for kw in source.required_author_keywords_any if kw and kw.strip()]
    if not required:
        return True
    author_lower = author.lower()
    return any(kw.lower() in author_lower for kw in required)


def _extract_publisher_from_text(text: str) -> str:
    payload = unescape(text or "")
    for pattern in _PUBLISHER_PATTERNS:
        match = pattern.search(payload)
        if not match:
            continue
        publisher = match.group(1).strip().strip(".,;:，。；：\"'“”’")
        if 2 <= len(publisher) <= 40:
            return publisher
    return ""


def _extract_sogou_redirect_url(html: str) -> str | None:
    chunks = _SOGOU_REDIRECT_CHUNK_PATTERN.findall(html)
    if not chunks:
        return None
    url = "".join(chunks).replace("\n", "").replace(" ", "").replace("@", "")
    return url if url.startswith("http") else None


def _to_canonical_wechat_article_url(client: httpx.Client, url: str) -> str:
    if "mp.weixin.qq.com/s?" not in url:
        return url
    try:
        resp = client.get(url, headers=_WECHAT_MP_MOBILE_HEADERS)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return url

    text = resp.text
    values: dict[str, str] = {}
    for key, pattern in _WECHAT_VAR_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            return str(resp.url)
        values[key] = match.group(1)
    return (
        f"https://mp.weixin.qq.com/s?__biz={values['biz']}"
        f"&mid={values['mid']}&idx={values['idx']}&sn={values['sn']}#rd"
    )


def _resolve_item_url(source: SourceConfig, client: httpx.Client, url: str) -> str:
    if not source.resolve_sogou_redirect:
        return url
    if "weixin.sogou.com/link?" not in url:
        return url
    try:
        resp = client.get(url, headers=_SOGOU_BROWSER_HEADERS)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return url

    resolved = _extract_sogou_redirect_url(resp.text)
    if not resolved:
        return url
    return _to_canonical_wechat_article_url(client, resolved)


def _fetch_article_published_at(client: httpx.Client, url: str, ref_time: datetime) -> datetime | None:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    return _extract_article_published_at(resp.text, ref_time)


def _fetch_article_publisher(client: httpx.Client, url: str) -> str:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    return _extract_publisher_from_text(text[:8000])


def _extract_rss_published_at(entry, content: str, client: httpx.Client, ref_time: datetime) -> datetime | None:
    for key in ("published", "updated", "pubDate", "dc_date", "date"):
        parsed = _parse_html_date(str(entry.get(key) or ""), ref_time=ref_time)
        if parsed:
            return parsed

    for key in ("published_parsed", "updated_parsed"):
        parsed_struct = entry.get(key)
        if isinstance(parsed_struct, pytime.struct_time):
            try:
                return datetime(*parsed_struct[:6], tzinfo=timezone.utc)
            except ValueError:
                continue

    for candidate in (entry.get("summary"), content, entry.get("title")):
        parsed = _parse_html_date(str(candidate or ""), ref_time=ref_time)
        if parsed:
            return parsed

    link = (entry.get("link") or "").strip()
    if link:
        return _fetch_article_published_at(client, link, ref_time)
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
            chunks = entry.get("content")
            if isinstance(chunks, list):
                content = " ".join(str(chunk.get("value", "")) for chunk in chunks if isinstance(chunk, dict))
            else:
                content = str(chunks)
        author = str(entry.get("author") or "")
        if not _matches_required_keywords(source, title, content, author):
            logger.debug("Drop rss item not matching source keywords: %s | %s", source.name, title)
            continue
        if not _matches_required_author_keywords(source, author):
            logger.debug("Drop rss item not matching author keywords: %s | %s | author=%s", source.name, title, author)
            continue

        published = _extract_rss_published_at(entry, content, client, now)
        if not published:
            logger.debug("Drop rss item without published_at: %s | %s", source.name, link)
            continue

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


def _collect_wechat_profile(source: SourceConfig, client: httpx.Client) -> list[RawItem]:
    load_local_env()
    biz = (source.wechat_biz or "").strip()
    if not biz:
        match = re.search(r"__biz=([A-Za-z0-9_=]+)", source.url)
        if match:
            biz = match.group(1)
    if not biz:
        raise ValueError(f"wechat_profile source missing wechat_biz: {source.name}")

    cookie = os.getenv("WECHAT_COOKIE", "").strip()
    if not cookie:
        logger.warning("Skip wechat_profile source without WECHAT_COOKIE: %s", source.name)
        return []

    headers = dict(_WECHAT_MP_MOBILE_HEADERS)
    headers["Cookie"] = cookie

    base_params = {
        "action": "getmsg",
        "__biz": biz,
        "f": "json",
        "offset": "0",
        "count": "10",
        "is_ok": "1",
        "scene": "124",
        "x5": "0",
    }

    resp = client.get("https://mp.weixin.qq.com/mp/profile_ext", params=base_params, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    ret = int(payload.get("ret", payload.get("base_resp", {}).get("ret", -1)))
    if ret != 0:
        logger.warning("wechat_profile getmsg failed: %s | ret=%s, fallback to public sources", source.name, ret)
        return []

    general_list = payload.get("general_msg_list")
    if isinstance(general_list, str):
        try:
            general_list = json.loads(general_list)
        except json.JSONDecodeError:
            logger.warning("wechat_profile general_msg_list is invalid JSON: %s", source.name)
            return []
    if not isinstance(general_list, dict):
        return []

    now = datetime.now(timezone.utc)
    seen_links: set[str] = set()
    items: list[RawItem] = []
    for msg in general_list.get("list", []):
        if not isinstance(msg, dict):
            continue
        comm_info = msg.get("comm_msg_info") or {}
        timestamp = comm_info.get("datetime")
        if not isinstance(timestamp, int):
            continue
        published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        app_msg_info = msg.get("app_msg_ext_info")
        if not isinstance(app_msg_info, dict):
            continue
        candidates = [app_msg_info] + [i for i in app_msg_info.get("multi_app_msg_item_list", []) if isinstance(i, dict)]
        for entry in candidates:
            title = str(entry.get("title") or "").strip()
            content_url = str(entry.get("content_url") or "").strip()
            digest = str(entry.get("digest") or "").strip()
            if not title or not content_url:
                continue
            if not _matches_required_keywords(source, title, digest):
                continue
            final_url = urljoin("https://mp.weixin.qq.com", content_url)
            if final_url in seen_links:
                continue
            seen_links.add(final_url)

            items.append(
                RawItem(
                    source_name=source.name,
                    source_weight=source.weight,
                    url=final_url,
                    title=title,
                    content=digest,
                    published_at=published_at,
                    discovered_at=now,
                    tags=source.tags,
                )
            )
            if len(items) >= 30:
                return items
    return items


def _extract_nearby_date(anchor, ref_time: datetime) -> datetime | None:
    node = anchor
    for _ in range(5):
        node = getattr(node, "parent", None)
        if node is None or not getattr(node, "get_text", None):
            break
        text = node.get_text(" ", strip=True)
        # 邻近节点常包含标题文案（如“刚刚，XXX”），这里禁用相对时间避免误判旧文为新文。
        parsed = _parse_html_date(text, ref_time=ref_time, allow_relative=False)
        if parsed:
            return parsed
    return None


def _extract_page_links(
    source: SourceConfig, html: str, ref_time: datetime
) -> Iterable[tuple[str, str, Optional[datetime], str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    selector = source.article_selector or "article a, h2 a, h3 a, li a"
    compiled_pattern = re.compile(source.link_pattern) if source.link_pattern else None

    def _pick_nearest_date_elem(container, anchor):
        if not source.date_selector:
            return None
        try:
            candidates = list(container.select(source.date_selector))
        except Exception:  # noqa: BLE001
            return None
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        ordered_nodes = [node for node in container.descendants if getattr(node, "name", None)]
        if not ordered_nodes:
            return candidates[0]
        index_by_id = {id(node): idx for idx, node in enumerate(ordered_nodes)}
        anchor_idx = index_by_id.get(id(anchor))
        if anchor_idx is None:
            return candidates[0]

        def _distance_key(elem):
            elem_idx = index_by_id.get(id(elem), anchor_idx)
            delta = elem_idx - anchor_idx
            # 距离相同时优先选择链接后方（通常与发布时间布局更一致）
            return (abs(delta), 0 if delta >= 0 else 1)

        return min(
            candidates,
            key=_distance_key,
        )

    def _check_and_yield(a, container) -> Optional[tuple[str, str, Optional[datetime], str, str]]:
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

        container_text = text
        author = ""
        if container and getattr(container, "get_text", None):
            container_text = container.get_text(" ", strip=True)
            if source.author_selector:
                author_elem = container.select_one(source.author_selector)
                if author_elem:
                    author = author_elem.get_text(" ", strip=True)
        published = None
        if source.date_selector and container:
            date_elem = _pick_nearest_date_elem(container, a)
            if date_elem:
                published = _extract_date_from_element(
                    date_elem, source.date_attr, source.date_regex, ref_time
                )
        if not published:
            published = _extract_nearby_date(a, ref_time)
        return (text, url, published, container_text, author)

    if source.item_container_selector and source.date_selector:
        for container in soup.select(source.item_container_selector):
            for a in container.select(selector):
                result = _check_and_yield(a, container)
                if result:
                    yield result
                    break
        return

    if source.date_selector:
        for a in soup.select(selector):
            container = a.parent
            depth = 0
            while container and container.name and depth < 8:
                if container.name in {"body", "html"}:
                    break
                date_elem = container.select_one(source.date_selector)
                if date_elem:
                    break
                container = container.parent if hasattr(container, "parent") else None
                depth += 1
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
    for title, url, published, context_text, author in _extract_page_links(source, resp.text, now):
        if not _matches_required_keywords(source, title, context_text, author, url):
            logger.debug("Drop html item not matching source keywords: %s | %s", source.name, title)
            continue
        if not _matches_required_author_keywords(source, author):
            logger.debug("Drop html item not matching author keywords: %s | %s | author=%s", source.name, title, author)
            continue

        final_url = _resolve_item_url(source, client, url)
        if final_url in seen_urls:
            continue
        seen_urls.add(final_url)
        if not published:
            published = _fetch_article_published_at(client, final_url, now)
        if not published:
            logger.debug("Drop html item without published_at: %s | %s", source.name, final_url)
            continue

        source_name = source.name
        if source.split_source_by_publisher:
            publisher = _extract_publisher_from_text(f"{title} {context_text}")
            if not publisher:
                publisher = _fetch_article_publisher(client, final_url)
            if publisher:
                source_name = f"{source.name}/{publisher}"

        items.append(
            RawItem(
                source_name=source_name,
                source_weight=source.weight,
                url=final_url,
                title=title,
                content=context_text,
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
        if source.type == "wechat_profile":
            return _collect_wechat_profile(source, client)
    raise ValueError(f"Unsupported source type: {source.type}")
