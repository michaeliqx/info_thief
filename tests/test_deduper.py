from datetime import datetime, timezone

from app.deduper import dedupe_items
from app.models import NormalizedItem


def _item(item_id: str, url: str, title: str, content: str) -> NormalizedItem:
    now = datetime.now(timezone.utc)
    return NormalizedItem(
        item_id=item_id,
        source_name="test",
        source_weight=1.0,
        url=url,
        canonical_url=url,
        title=title,
        content=content,
        published_at=now,
        discovered_at=now,
        language="zh",
        tags=[],
    )


def test_dedupe_url_and_title_similarity() -> None:
    a = _item("1", "https://a.com/post", "AI 模型发布", "内容A")
    b = _item("2", "https://a.com/post", "AI 模型发布 重复", "内容B")
    c = _item("3", "https://b.com/post", "人工智能模型发布", "内容C")

    out = dedupe_items([a, b, c], title_similarity_threshold=0.8)
    assert len(out) == 1
    assert "模型发布" in out[0].title
