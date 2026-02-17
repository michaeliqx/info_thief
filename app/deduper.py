from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

from app.models import NormalizedItem


def _content_fingerprint(content: str) -> str:
    payload = content[:800].strip().lower().encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def _normalize_title(title: str) -> str:
    lowered = title.lower()
    lowered = lowered.replace("人工智能", "ai")
    lowered = lowered.replace("大模型", "模型")
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"[^\w\u4e00-\u9fff]", "", lowered)
    return lowered


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def dedupe_items(items: list[NormalizedItem], title_similarity_threshold: float = 0.92) -> list[NormalizedItem]:
    # Higher source weight first, then newer discovery time.
    sorted_items = sorted(items, key=lambda x: (x.source_weight, x.discovered_at), reverse=True)

    selected: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()

    for item in sorted_items:
        if item.canonical_url in seen_urls:
            continue

        fp = _content_fingerprint(item.content)
        if item.content and fp in seen_fingerprints:
            continue

        duplicate_by_title = False
        for kept in selected:
            if _title_similarity(item.title, kept.title) >= title_similarity_threshold:
                duplicate_by_title = True
                break
        if duplicate_by_title:
            continue

        selected.append(item)
        seen_urls.add(item.canonical_url)
        if item.content:
            seen_fingerprints.add(fp)

    return selected
