from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models import ClassifiedItem, Perspective, RankedItem


def _calc_heat_score(text: str) -> float:
    lowered = text.lower()
    signal_words = ["发布", "开源", "融资", "上线", "breakthrough", "launch", "benchmark"]
    hits = sum(1 for w in signal_words if w in lowered)
    return min(2.0, hits * 0.4)


def _calc_tag_bonus(tags: list[str]) -> float:
    tag_set = {t.lower() for t in tags}
    bonus = 0.0
    # 显式最高优先级来源（例如用户指定的重点渠道）
    if "priority_top" in tag_set:
        bonus += 3.0
    # 优先个人/自媒体来源
    if "self_media" in tag_set:
        bonus += 1.0
    if "personal" in tag_set:
        bonus += 0.8
    if "creator" in tag_set:
        bonus += 0.4
    if "wechat" in tag_set and "official" not in tag_set:
        bonus += 0.2
    if "official" in tag_set:
        bonus -= 0.2
    return bonus


def _calc_recency_score(published_at: datetime | None, discovered_at: datetime, now: datetime) -> float:
    base_time = published_at or discovered_at
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - base_time).total_seconds() / 3600)
    # 24h 内线性衰减到 0.
    return max(0.0, 5.0 * (1 - min(age_hours, 24.0) / 24.0))


def rank_items(items: list[ClassifiedItem], now: datetime | None = None) -> list[RankedItem]:
    now = now or datetime.now(timezone.utc)
    ranked: list[RankedItem] = []

    for item in items:
        recency = _calc_recency_score(item.published_at, item.discovered_at, now)
        authority = item.source_weight * 2.5
        heat = _calc_heat_score(f"{item.title} {item.content}")
        tag_bonus = _calc_tag_bonus(item.tags)
        score = recency + authority + heat + tag_bonus

        ranked.append(
            RankedItem(
                **item.model_dump(),
                score=round(score, 4),
                rank_reason=(
                    f"recency={recency:.2f}, authority={authority:.2f}, "
                    f"heat={heat:.2f}, tag_bonus={tag_bonus:.2f}"
                ),
            )
        )

    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked


def select_items_with_mix(
    ranked_items: list[RankedItem],
    item_min: int,
    item_max: int,
    mix_min_each: int,
    max_items_per_source: Optional[int] = None,
) -> list[RankedItem]:
    if item_max < item_min:
        item_max = item_min

    grouped: dict[Perspective, list[RankedItem]] = {p: [] for p in Perspective}
    for item in ranked_items:
        grouped[item.perspective].append(item)

    selected: list[RankedItem] = []
    selected_ids: set[str] = set()
    source_counts: dict[str, int] = {}

    def _can_add(item: RankedItem, enforce_source_cap: bool = True) -> bool:
        if item.item_id in selected_ids:
            return False
        if not enforce_source_cap:
            return True
        if max_items_per_source is None or max_items_per_source <= 0:
            return True
        return source_counts.get(item.source_name, 0) < max_items_per_source

    def _add_item(item: RankedItem) -> None:
        selected.append(item)
        selected_ids.add(item.item_id)
        source_counts[item.source_name] = source_counts.get(item.source_name, 0) + 1

    # 保证三类最小配额
    for perspective in Perspective:
        added = 0
        for item in grouped[perspective]:
            if added >= mix_min_each:
                break
            if not _can_add(item, enforce_source_cap=True):
                continue
            _add_item(item)
            added += 1

    # 补齐到上限
    for item in ranked_items:
        if len(selected) >= item_max:
            break
        if not _can_add(item, enforce_source_cap=True):
            continue
        _add_item(item)

    # 若有足够候选，至少达到 item_min
    if len(selected) < item_min:
        for item in ranked_items:
            if len(selected) >= item_min:
                break
            if not _can_add(item, enforce_source_cap=True):
                continue
            _add_item(item)

    selected.sort(key=lambda x: x.score, reverse=True)
    return selected[:item_max]
