from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from app.models import DailyBrief


def _perspective_label(value: str) -> str:
    mapping = {
        "product": "产品视角",
        "technology": "技术视角",
        "industry": "行业视角",
    }
    return mapping.get(value, "综合视角")


def _importance_label(value: str) -> str:
    mapping = {
        "high": "重要",
        "medium": "关注",
        "low": "速览",
    }
    return mapping.get(value, "关注")


def _key_point_limit(value: str) -> int:
    mapping = {
        "high": 4,
        "medium": 3,
        "low": 2,
    }
    return mapping.get(value, 3)


def render_markdown(brief: DailyBrief) -> str:
    lines: list[str] = []
    lines.append(f"# {brief.title}")
    lines.append("")
    lines.append(f"**导语**：{brief.intro}")
    lines.append("")

    for idx, item in enumerate(brief.items, start=1):
        perspective_text = _perspective_label(item.perspective.value)
        importance_text = _importance_label(item.importance)
        lines.append(f"## {idx}、【{importance_text}】【{perspective_text}】{item.title}")
        lines.append(f"- 来源：{item.source_name}")
        lines.append(f"- 原文链接：{item.url}")
        lines.append("- 关键信息：")
        for point in item.key_points[: _key_point_limit(item.importance)]:
            lines.append(f"  - {point}")
        insight = item.insight.strip() or "该信息可能影响后续选题优先级与资源投入，建议结合业务目标跟踪。"
        lines.append(f"- insight：{insight}")
        lines.append("")

    lines.append("## 跨条观察")
    for obs in brief.observations:
        lines.append(f"- {obs}")

    return "\n".join(lines).strip()


def _build_wecom_payload(content: str) -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }


def push_markdown(
    webhook: str,
    content: str,
    retries: tuple[int, ...] = (30, 90, 180),
    sleep_fn: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
) -> bool:
    if not webhook:
        raise ValueError("Missing wechat webhook URL")

    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=15)

    attempts = (0,) + retries
    try:
        for wait_seconds in attempts:
            if wait_seconds > 0:
                sleep_fn(wait_seconds)

            resp = client.post(webhook, json=_build_wecom_payload(content))
            ok = resp.status_code == 200
            if ok:
                data = resp.json()
                if int(data.get("errcode", -1)) == 0:
                    return True
        return False
    finally:
        if owned_client and client is not None:
            client.close()


def send_failure_alert(webhook: str, error_message: str, client: httpx.Client | None = None) -> bool:
    if not webhook:
        return False

    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=15)

    payload = {
        "msgtype": "text",
        "text": {
            "content": f"[AI日报告警] 当日任务失败：{error_message[:500]}",
        },
    }

    try:
        resp = client.post(webhook, json=payload)
        return resp.status_code == 200 and int(resp.json().get("errcode", -1)) == 0
    finally:
        if owned_client and client is not None:
            client.close()


def archive_brief(brief: DailyBrief, archives_dir: str) -> tuple[str, str]:
    Path(archives_dir).mkdir(parents=True, exist_ok=True)
    date_str = brief.date.isoformat()
    md_path = Path(archives_dir) / f"{date_str}.md"
    json_path = Path(archives_dir) / f"{date_str}.json"

    markdown = render_markdown(brief)
    md_path.write_text(markdown, encoding="utf-8")

    payload = brief.model_dump(mode="json")
    payload["generated_at"] = datetime.utcnow().isoformat()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return str(md_path), str(json_path)
