from __future__ import annotations

from typing import Optional

from app.llm import LLMClient
from app.models import ClassifiedItem, NormalizedItem, Perspective

_RULES = {
    Perspective.PRODUCT: ["发布", "上线", "产品", "应用", "agent", "app", "launch", "release"],
    Perspective.TECHNOLOGY: ["论文", "算法", "架构", "benchmark", "推理", "训练", "模型", "research"],
    Perspective.INDUSTRY: ["融资", "估值", "政策", "合作", "并购", "市场", "生态", "监管"],
}


def _rule_classify(text: str) -> Perspective | None:
    lowered = text.lower()
    scores: dict[Perspective, int] = {p: 0 for p in Perspective}
    for perspective, keywords in _RULES.items():
        for kw in keywords:
            if kw.lower() in lowered:
                scores[perspective] += 1

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top[0][1] == 0:
        return None
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def _tag_classify(tags: list[str]) -> Optional[Perspective]:
    lowered = {tag.lower() for tag in tags}
    if {"product", "application", "app"} & lowered:
        return Perspective.PRODUCT
    if {"technology", "research", "model"} & lowered:
        return Perspective.TECHNOLOGY
    if {"industry", "policy", "market"} & lowered:
        return Perspective.INDUSTRY
    return None


def classify_items(
    items: list[NormalizedItem],
    llm_client: Optional[LLMClient] = None,
    use_llm_fallback: bool = False,
) -> list[ClassifiedItem]:
    classified: list[ClassifiedItem] = []

    for item in items:
        text = f"{item.title} {item.content}"
        perspective = _rule_classify(text)
        source = "rule"

        if perspective is None:
            perspective = _tag_classify(item.tags)
            if perspective is not None:
                source = "rule"

        if perspective is None and llm_client is not None and use_llm_fallback:
            perspective = llm_client.classify_perspective(item.title, item.content)
            source = "llm"

        if perspective is None:
            perspective = Perspective.INDUSTRY
            source = "fallback"

        classified.append(
            ClassifiedItem(
                **item.model_dump(),
                perspective=perspective,
                classification_source=source,
            )
        )

    return classified
