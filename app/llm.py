from __future__ import annotations

import json
import logging
from typing import Any, Optional, Protocol

from openai import OpenAI

from app.models import Perspective

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def classify_perspective(self, title: str, content: str) -> Optional[Perspective]: ...

    def summarize_item_structured(
        self,
        title: str,
        content: str,
        source_name: str,
        url: str,
        perspective: Optional[Perspective] = None,
    ) -> dict[str, Any]: ...

    def summarize_item(self, title: str, content: str, source_name: str, url: str) -> list[str]: ...

    def compose_intro(self, titles: list[str]) -> str: ...

    def compose_observations(self, snippets: list[str]) -> list[str]: ...


def _extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    if isinstance(response, dict):
        output_text = response.get("output_text")
        if output_text:
            return str(output_text).strip()

    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output", [])

    texts: list[str] = []
    for item in output or []:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content", [])

        for part in content or []:
            part_type = getattr(part, "type", None)
            if part_type is None and isinstance(part, dict):
                part_type = part.get("type")

            if part_type in ("output_text", "text"):
                text = getattr(part, "text", None)
                if text is None and isinstance(part, dict):
                    text = part.get("text")
                if text:
                    texts.append(str(text).strip())

    return "\n".join([t for t in texts if t]).strip()


def _safe_load_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]
    if "```" in text:
        for segment in text.split("```"):
            cleaned = segment.strip()
            if not cleaned:
                continue
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            candidates.append(cleaned)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    return None


def _normalize_importance(value: Any, fallback: str = "medium") -> str:
    raw = str(value or "").strip().lower()
    if raw in {"high", "h", "重要", "高", "high_priority"}:
        return "high"
    if raw in {"low", "l", "低", "次要", "参考"}:
        return "low"
    if raw in {"medium", "m", "中", "关注"}:
        return "medium"
    return fallback


def _build_fallback_summary(
    title: str,
    content: str,
    source_name: str,
    url: str,
    perspective: Optional[Perspective] = None,
) -> dict[str, Any]:
    text = (content or title).strip()
    if not text:
        text = title
    snippet = text[:120]
    perspective_text = perspective.value if perspective is not None else "综合"

    importance = "medium"
    if any(keyword in f"{title} {content}" for keyword in ("发布", "开源", "融资", "突破", "重磅", "首个")):
        importance = "high"

    return {
        "importance": importance,
        "why_it_matters": f"该信息与{perspective_text}方向有关，可能影响后续选题优先级与资源投入。",
        "stance": "优先关注可验证的落地信号与业务指标，避免被单点叙事带节奏。",
        "points": [
            f"该信息由{source_name}发布，主题与 AI 发展相关。",
            f"核心内容：{snippet}",
            f"可通过原文进一步确认发布时间与细节：{url}",
        ],
    }


def _normalize_summary_payload(
    payload: dict[str, Any] | None,
    *,
    title: str,
    content: str,
    source_name: str,
    url: str,
    perspective: Optional[Perspective] = None,
) -> dict[str, Any]:
    fallback = _build_fallback_summary(title, content, source_name, url, perspective)
    data = payload or {}

    points_raw = data.get("points", [])
    points = [str(p).strip() for p in points_raw if str(p).strip()] if isinstance(points_raw, list) else []
    if not points:
        points = fallback["points"]

    return {
        "importance": _normalize_importance(data.get("importance"), fallback=fallback["importance"]),
        "why_it_matters": str(data.get("why_it_matters") or "").strip() or fallback["why_it_matters"],
        "stance": str(data.get("stance") or "").strip() or fallback["stance"],
        "points": points[:4],
    }


class FallbackLLMClient:
    def classify_perspective(self, title: str, content: str) -> Optional[Perspective]:
        _ = (title, content)
        return None

    def summarize_item_structured(
        self,
        title: str,
        content: str,
        source_name: str,
        url: str,
        perspective: Optional[Perspective] = None,
    ) -> dict[str, Any]:
        return _build_fallback_summary(title, content, source_name, url, perspective)

    def summarize_item(self, title: str, content: str, source_name: str, url: str) -> list[str]:
        summary = self.summarize_item_structured(title, content, source_name, url)
        return summary["points"]

    def compose_intro(self, titles: list[str]) -> str:
        top_titles = "；".join(titles[:3])
        return f"今日 AI 资讯覆盖产品、技术与行业动态。重点包括：{top_titles}。"

    def compose_observations(self, snippets: list[str]) -> list[str]:
        _ = snippets
        return [
            "模型发布与应用落地并行推进，产品化节奏持续加快。",
            "国内外厂商在成本、推理效率和场景深度上竞争明显。",
        ]


class OpenAILLMClient:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def classify_perspective(self, title: str, content: str) -> Optional[Perspective]:
        prompt = (
            "请只输出一个英文标签：product 或 technology 或 industry。"
            "\n标题:" + title + "\n内容:" + content[:1200]
        )
        raw = self._chat("你是AI资讯分类助手。", prompt).lower()
        if "product" in raw:
            return Perspective.PRODUCT
        if "technology" in raw:
            return Perspective.TECHNOLOGY
        if "industry" in raw:
            return Perspective.INDUSTRY
        return None

    def summarize_item_structured(
        self,
        title: str,
        content: str,
        source_name: str,
        url: str,
        perspective: Optional[Perspective] = None,
    ) -> dict[str, Any]:
        perspective_hint = perspective.value if perspective is not None else "mixed"
        prompt = (
            "请将以下资讯总结为严格 JSON，不要输出任何额外文本。\n"
            "返回格式："
            "{\"importance\":\"high|medium|low\","
            "\"why_it_matters\":\"一句话说明为什么重要\","
            "\"stance\":\"一句话给出立场判断\","
            "\"points\":[\"要点1\",\"要点2\"]}\n"
            "要求：\n"
            "1) points 输出 2-4 条中文短句；\n"
            "2) why_it_matters 强调业务/产品/行业意义；\n"
            "3) stance 体现编辑判断，不要中性复述；\n"
            "4) importance 用 high/medium/low。\n"
            f"\n视角提示:{perspective_hint}\n标题:{title}\n来源:{source_name}\n链接:{url}\n内容:{content[:4000]}"
        )
        raw = self._chat("你是严谨的科技编辑。", prompt)
        data = _safe_load_json(raw)
        if data is None:
            logger.warning("OpenAI summary JSON parse failed, fallback text output")
            data = {"points": [raw[:120], "建议阅读原文确认关键细节。"]}
        return _normalize_summary_payload(
            data,
            title=title,
            content=content,
            source_name=source_name,
            url=url,
            perspective=perspective,
        )

    def summarize_item(self, title: str, content: str, source_name: str, url: str) -> list[str]:
        summary = self.summarize_item_structured(title, content, source_name, url)
        return summary["points"]

    def compose_intro(self, titles: list[str]) -> str:
        prompt = "请基于这些标题写3-5句中文日报导语：\n" + "\n".join(titles[:12])
        return self._chat("你是AI日报主编。", prompt)

    def compose_observations(self, snippets: list[str]) -> list[str]:
        prompt = (
            "请基于以下信息给出1-2条跨来源观察，返回JSON: {\"observations\":[\"...\"]}\n"
            + "\n".join(snippets[:20])
        )
        raw = self._chat("你是行业分析师。", prompt)
        data = _safe_load_json(raw)
        if data is not None:
            obs = [str(i).strip() for i in data.get("observations", []) if str(i).strip()]
            if obs:
                return obs[:2]
        return ["今日信息显示模型能力迭代与应用落地持续共振。"]


class VolcengineLLMClient:
    def __init__(self, api_key: str, base_url: str, model: str = "doubao-seed-1-8-251228") -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def _respond(self, system_prompt: str, user_prompt: str) -> str:
        merged_prompt = f"{system_prompt}\n\n{user_prompt}"
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": merged_prompt,
                        }
                    ],
                }
            ],
        )
        text = _extract_response_text(response)
        return text.strip()

    def ping(self) -> str:
        return self._respond("你是助手。", "请只回复：连接成功")

    def classify_perspective(self, title: str, content: str) -> Optional[Perspective]:
        prompt = (
            "请只输出一个英文标签：product 或 technology 或 industry。"
            "\n标题:" + title + "\n内容:" + content[:1200]
        )
        raw = self._respond("你是AI资讯分类助手。", prompt).lower()
        if "product" in raw:
            return Perspective.PRODUCT
        if "technology" in raw:
            return Perspective.TECHNOLOGY
        if "industry" in raw:
            return Perspective.INDUSTRY
        return None

    def summarize_item_structured(
        self,
        title: str,
        content: str,
        source_name: str,
        url: str,
        perspective: Optional[Perspective] = None,
    ) -> dict[str, Any]:
        perspective_hint = perspective.value if perspective is not None else "mixed"
        prompt = (
            "请将以下资讯总结为严格 JSON，不要输出任何额外文本。\n"
            "返回格式："
            "{\"importance\":\"high|medium|low\","
            "\"why_it_matters\":\"一句话说明为什么重要\","
            "\"stance\":\"一句话给出立场判断\","
            "\"points\":[\"要点1\",\"要点2\"]}\n"
            "要求：\n"
            "1) points 输出 2-4 条中文短句；\n"
            "2) why_it_matters 强调业务/产品/行业意义；\n"
            "3) stance 体现编辑判断，不要中性复述；\n"
            "4) importance 用 high/medium/low。\n"
            f"\n视角提示:{perspective_hint}\n标题:{title}\n来源:{source_name}\n链接:{url}\n内容:{content[:4000]}"
        )
        raw = self._respond("你是严谨的科技编辑。", prompt)
        data = _safe_load_json(raw)
        if data is None:
            logger.warning("Volcengine summary JSON parse failed, fallback text output")
            data = {"points": [raw[:120], "建议阅读原文确认关键细节。"]}
        return _normalize_summary_payload(
            data,
            title=title,
            content=content,
            source_name=source_name,
            url=url,
            perspective=perspective,
        )

    def summarize_item(self, title: str, content: str, source_name: str, url: str) -> list[str]:
        summary = self.summarize_item_structured(title, content, source_name, url)
        return summary["points"]

    def compose_intro(self, titles: list[str]) -> str:
        prompt = "请基于这些标题写3-5句中文日报导语：\n" + "\n".join(titles[:12])
        return self._respond("你是AI日报主编。", prompt)

    def compose_observations(self, snippets: list[str]) -> list[str]:
        prompt = (
            "请基于以下信息给出1-2条跨来源观察，返回JSON: {\"observations\":[\"...\"]}\n"
            + "\n".join(snippets[:20])
        )
        raw = self._respond("你是行业分析师。", prompt)
        data = _safe_load_json(raw)
        if data is not None:
            obs = [str(i).strip() for i in data.get("observations", []) if str(i).strip()]
            if obs:
                return obs[:2]
        logger.warning("Volcengine observations JSON parse failed")
        return ["今日信息显示模型能力迭代与应用落地持续共振。"]
