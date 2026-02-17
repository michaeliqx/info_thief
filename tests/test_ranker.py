from datetime import datetime, timedelta, timezone

from app.models import ClassifiedItem, Perspective
from app.ranker import rank_items, select_items_with_mix


def _item(idx: int, perspective: Perspective) -> ClassifiedItem:
    now = datetime.now(timezone.utc)
    return ClassifiedItem(
        item_id=f"id-{idx}",
        source_name="s",
        source_weight=1.0,
        url=f"https://x.com/{idx}",
        canonical_url=f"https://x.com/{idx}",
        title=f"AI 新闻 {idx}",
        content="发布 模型",
        published_at=now - timedelta(hours=idx),
        discovered_at=now,
        language="zh",
        tags=[],
        perspective=perspective,
        classification_source="rule",
    )


def test_select_items_with_mix() -> None:
    candidates = []
    for i in range(3):
        candidates.append(_item(i, Perspective.PRODUCT))
        candidates.append(_item(i + 10, Perspective.TECHNOLOGY))
        candidates.append(_item(i + 20, Perspective.INDUSTRY))

    ranked = rank_items(candidates)
    selected = select_items_with_mix(ranked, item_min=8, item_max=8, mix_min_each=2)

    assert len(selected) == 8
    counts = {p: 0 for p in Perspective}
    for item in selected:
        counts[item.perspective] += 1
    assert counts[Perspective.PRODUCT] >= 2
    assert counts[Perspective.TECHNOLOGY] >= 2
    assert counts[Perspective.INDUSTRY] >= 2


def test_select_items_with_source_cap() -> None:
    now = datetime.now(timezone.utc)
    candidates: list[ClassifiedItem] = []
    # Source A has many high-score items.
    for i in range(9):
        candidates.append(
            ClassifiedItem(
                item_id=f"a-{i}",
                source_name="source-a",
                source_weight=1.5,
                url=f"https://a.com/{i}",
                canonical_url=f"https://a.com/{i}",
                title=f"AI 发布 {i}",
                content="发布 模型",
                published_at=now - timedelta(hours=i),
                discovered_at=now,
                language="zh",
                tags=["ai"],
                perspective=[Perspective.PRODUCT, Perspective.TECHNOLOGY, Perspective.INDUSTRY][i % 3],
                classification_source="rule",
            )
        )
    # Source B/C provide enough diversity.
    for i in range(6):
        source = "source-b" if i < 3 else "source-c"
        candidates.append(
            ClassifiedItem(
                item_id=f"x-{i}",
                source_name=source,
                source_weight=1.0,
                url=f"https://x.com/{i}",
                canonical_url=f"https://x.com/{i}",
                title=f"AI 动态 {i}",
                content="发布 模型",
                published_at=now - timedelta(hours=2 + i),
                discovered_at=now,
                language="zh",
                tags=["ai"],
                perspective=[Perspective.PRODUCT, Perspective.TECHNOLOGY, Perspective.INDUSTRY][i % 3],
                classification_source="rule",
            )
        )

    ranked = rank_items(candidates, now=now)
    selected = select_items_with_mix(
        ranked,
        item_min=6,
        item_max=6,
        mix_min_each=2,
        max_items_per_source=2,
    )

    source_a_count = sum(1 for item in selected if item.source_name == "source-a")
    assert source_a_count <= 2


def test_self_media_tag_bonus_prioritizes_personal_source() -> None:
    now = datetime.now(timezone.utc)
    personal = ClassifiedItem(
        item_id="p-1",
        source_name="personal",
        source_weight=1.0,
        url="https://p.com/1",
        canonical_url="https://p.com/1",
        title="AI 应用上线",
        content="发布 模型",
        published_at=now - timedelta(hours=1),
        discovered_at=now,
        language="zh",
        tags=["ai", "self_media", "personal"],
        perspective=Perspective.PRODUCT,
        classification_source="rule",
    )
    official = ClassifiedItem(
        item_id="o-1",
        source_name="official",
        source_weight=1.0,
        url="https://o.com/1",
        canonical_url="https://o.com/1",
        title="AI 应用上线",
        content="发布 模型",
        published_at=now - timedelta(hours=1),
        discovered_at=now,
        language="zh",
        tags=["ai", "official"],
        perspective=Perspective.PRODUCT,
        classification_source="rule",
    )

    ranked = rank_items([official, personal], now=now)
    assert ranked[0].source_name == "personal"


def test_priority_top_tag_has_highest_priority() -> None:
    now = datetime.now(timezone.utc)
    top = ClassifiedItem(
        item_id="t-1",
        source_name="top-source",
        source_weight=1.0,
        url="https://t.com/1",
        canonical_url="https://t.com/1",
        title="AI 进展",
        content="发布 模型",
        published_at=now - timedelta(hours=1),
        discovered_at=now,
        language="zh",
        tags=["ai", "priority_top"],
        perspective=Perspective.INDUSTRY,
        classification_source="rule",
    )
    normal = ClassifiedItem(
        item_id="n-1",
        source_name="normal-source",
        source_weight=1.4,
        url="https://n.com/1",
        canonical_url="https://n.com/1",
        title="AI 进展",
        content="发布 模型",
        published_at=now - timedelta(hours=1),
        discovered_at=now,
        language="zh",
        tags=["ai"],
        perspective=Perspective.INDUSTRY,
        classification_source="rule",
    )

    ranked = rank_items([normal, top], now=now)
    assert ranked[0].source_name == "top-source"
