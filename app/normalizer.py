from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import NormalizedItem, RawItem

AI_KEYWORDS = {
    "ai",
    "aigc",
    "llm",
    "agent",
    "模型",
    "大模型",
    "多模态",
    "生成式",
    "人工智能",
    "机器学习",
    "深度学习",
    "推理",
    "token",
    "openai",
    "anthropic",
    "deepmind",
    "gpt",
    "claude",
    "gemini",
}

_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "from",
    "source",
}


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_language(text: str) -> str:
    if not text:
        return "unknown"
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_chars = len(re.findall(r"[A-Za-z]", text))
    if zh_chars > 0 and en_chars == 0:
        return "zh"
    if en_chars > 0 and zh_chars == 0:
        return "en"
    if zh_chars > 0 and en_chars > 0:
        return "mixed"
    return "unknown"


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _TRACKING_QUERY_KEYS]
    normalized_query = urlencode(sorted(query))
    cleaned = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), normalized_query, ""))
    return cleaned


def is_ai_related(title: str, content: str, tags: list[str]) -> bool:
    merged = f"{title} {content}".lower()
    if any(keyword in merged for keyword in AI_KEYWORDS):
        return True

    tag_set = {tag.lower() for tag in tags}
    if "ai" in tag_set:
        return True
    if {"technology", "research", "official"} & tag_set and len(title) >= 10:
        return True
    return False


def is_within_window(target: datetime, since: datetime, until: datetime) -> bool:
    return since <= target <= until


def make_item_id(canonical_url: str, title: str) -> str:
    payload = f"{canonical_url}|{title.strip().lower()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_items(raw_items: list[RawItem], since: datetime, until: datetime) -> list[NormalizedItem]:
    normalized: list[NormalizedItem] = []

    for item in raw_items:
        title = clean_text(item.title)
        content = clean_text(item.content)
        if not title:
            continue
        if not is_ai_related(title, content, item.tags):
            continue

        if item.published_at is None:
            continue
        if not is_within_window(item.published_at, since, until):
            continue

        canonical_url = canonicalize_url(item.url)
        item_id = make_item_id(canonical_url, title)
        language = detect_language(f"{title} {content}")
        normalized.append(
            NormalizedItem(
                item_id=item_id,
                source_name=item.source_name,
                source_weight=item.source_weight,
                url=item.url,
                canonical_url=canonical_url,
                title=title,
                content=content[:5000],
                published_at=item.published_at,
                discovered_at=item.discovered_at,
                language=language,
                tags=item.tags,
            )
        )
    return normalized
