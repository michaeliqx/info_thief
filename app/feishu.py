from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from fastapi import BackgroundTasks

from app.models import DailyBrief, Settings
from app.publisher import render_markdown

logger = logging.getLogger(__name__)

_MENTION_TAG_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_CACHE_LOCK = threading.Lock()

_EVENT_CACHE: OrderedDict[str, None] = OrderedDict()
_EVENT_CACHE_LOCK = threading.Lock()
_EVENT_CACHE_MAX = 1000

_HELP_TEXT = (
    "可用命令（支持带/和不带/）：\n"
    "/run 或 运行日报：立即执行并回传当天完整日报\n"
    "/latest 或 最新日报：查看最近一次归档摘要\n"
    "/status 或 状态：查看服务状态与会话ID\n"
    "/help 或 帮助：查看帮助\n"
    "\n"
    "示例：\n"
    "- 运行日报\n"
    "- 最新日报\n"
    "- 状态"
)


def _token_cache_key(app_id: str, base_url: str) -> str:
    return f"{base_url}|{app_id}"


def _get_tenant_access_token(
    app_id: str,
    app_secret: str,
    base_url: str,
    client: httpx.Client,
    now_fn: Callable[[], float] = time.time,
) -> str:
    now = now_fn()
    cache_key = _token_cache_key(app_id, base_url)

    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached is not None:
            token, expire_at = cached
            if expire_at - now >= 60:
                return token

    resp = client.post(
        f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Feishu auth failed with status={resp.status_code}")

    data = resp.json()
    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"Feishu auth failed: {data.get('msg', 'unknown error')}")

    token = str(data.get("tenant_access_token", "")).strip()
    if not token:
        raise RuntimeError("Feishu auth failed: missing tenant_access_token")

    expire_in = int(data.get("expire", 7200))
    expire_at = now + max(300, expire_in - 120)
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE[cache_key] = (token, expire_at)
    return token


def _clear_tenant_access_token_cache(app_id: str, base_url: str) -> None:
    cache_key = _token_cache_key(app_id, base_url)
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE.pop(cache_key, None)


def push_feishu_text(
    app_id: str,
    app_secret: str,
    base_url: str,
    receive_id: str,
    content: str,
    receive_id_type: str = "chat_id",
    retries: tuple[int, ...] = (2, 5, 10),
    sleep_fn: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
) -> bool:
    if not app_id or not app_secret:
        raise ValueError("Missing Feishu app credentials")
    if not receive_id:
        raise ValueError("Missing Feishu receive_id")

    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=15)

    attempts = (0,) + retries
    try:
        for wait_seconds in attempts:
            if wait_seconds > 0:
                sleep_fn(wait_seconds)
            if _send_feishu_text_once(
                app_id=app_id,
                app_secret=app_secret,
                base_url=base_url,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
                content=content,
                client=client,
            ):
                return True
        return False
    finally:
        if owned_client and client is not None:
            client.close()


def _send_feishu_text_once(
    app_id: str,
    app_secret: str,
    base_url: str,
    receive_id: str,
    receive_id_type: str,
    content: str,
    client: httpx.Client,
) -> bool:
    token = _get_tenant_access_token(app_id, app_secret, base_url, client)
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False),
    }
    resp = client.post(
        f"{base_url}/open-apis/im/v1/messages",
        params={"receive_id_type": receive_id_type},
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    if resp.status_code != 200:
        body_preview = (resp.text or "")[:300]
        logger.warning(
            "Feishu send failed: status=%s receive_id_type=%s receive_id=%s body=%s",
            resp.status_code,
            receive_id_type,
            receive_id,
            body_preview,
        )
        _clear_tenant_access_token_cache(app_id, base_url)
        return False

    data = resp.json()
    ok = int(data.get("code", -1)) == 0
    if not ok:
        logger.warning(
            "Feishu send failed: code=%s msg=%s receive_id=%s",
            data.get("code"),
            data.get("msg"),
            receive_id,
        )
        _clear_tenant_access_token_cache(app_id, base_url)
    return ok


def _extract_post_text(content_json: dict) -> str:
    def _extract(content: dict) -> str:
        title = str(content.get("title", "")).strip()
        blocks = content.get("content", [])
        parts: list[str] = [title] if title else []
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, list):
                    continue
                for element in block:
                    if not isinstance(element, dict):
                        continue
                    tag = element.get("tag")
                    if tag in {"text", "a"}:
                        value = str(element.get("text", "")).strip()
                        if value:
                            parts.append(value)
                    elif tag == "at":
                        name = str(element.get("user_name", "用户")).strip()
                        parts.append(f"@{name}")
        return " ".join(p for p in parts if p).strip()

    if "content" in content_json:
        text = _extract(content_json)
        if text:
            return text

    for lang_key in ("zh_cn", "en_us", "ja_jp"):
        lang_content = content_json.get(lang_key)
        if isinstance(lang_content, dict):
            text = _extract(lang_content)
            if text:
                return text
    return ""


def _extract_message_text(message: dict) -> str:
    msg_type = str(message.get("message_type", ""))
    raw_content = message.get("content", "")
    if not isinstance(raw_content, str):
        return ""

    if msg_type == "text":
        try:
            return str(json.loads(raw_content).get("text", "")).strip()
        except json.JSONDecodeError:
            return raw_content.strip()

    if msg_type == "post":
        try:
            content_json = json.loads(raw_content)
            if isinstance(content_json, dict):
                return _extract_post_text(content_json)
        except json.JSONDecodeError:
            return raw_content.strip()
    return ""


def _strip_mentions(text: str) -> str:
    cleaned = _MENTION_TAG_RE.sub("", text)
    return " ".join(cleaned.split())


def _resolve_command(text: str) -> str | None:
    if not text:
        return None
    normalized = text.strip().lower()
    token = normalized.split()[0] if normalized else ""

    run_tokens = {"/run", "run", "运行日报", "执行日报", "生成日报"}
    latest_tokens = {"/latest", "latest", "最新日报", "查看日报", "日报"}
    status_tokens = {"/status", "status", "状态", "运行状态", "健康检查"}
    help_tokens = {"/help", "help", "帮助", "菜单", "命令"}

    if token in run_tokens:
        return "run"
    if token in latest_tokens:
        return "latest"
    if token in status_tokens:
        return "status"
    if token in help_tokens:
        return "help"
    if normalized in run_tokens:
        return "run"
    if normalized in latest_tokens:
        return "latest"
    if normalized in status_tokens:
        return "status"
    if normalized in help_tokens:
        return "help"
    if token.startswith("/"):
        return "help"
    return None


def _split_text_for_feishu(content: str, max_chars: int = 3000) -> list[str]:
    text = content.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = remaining.rfind("\n", 0, max_chars)
        if split_at < max_chars // 3:
            split_at = max_chars
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _seen_event(event_id: str) -> bool:
    if not event_id:
        return False
    with _EVENT_CACHE_LOCK:
        if event_id in _EVENT_CACHE:
            return True
        _EVENT_CACHE[event_id] = None
        while len(_EVENT_CACHE) > _EVENT_CACHE_MAX:
            _EVENT_CACHE.popitem(last=False)
    return False


def _format_latest_summary(archives_dir: str) -> str:
    files = sorted(Path(archives_dir).glob("*.json"), reverse=True)
    if not files:
        return "还没有归档记录，请先执行 /run。"

    latest_file = files[0]
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    title = payload.get("title", latest_file.stem)
    lines = [f"最近一次归档：{title}", f"条目数：{len(items)}"]
    for idx, item in enumerate(items[:3], start=1):
        lines.append(f"{idx}. {item.get('title', '未命名')}")
    return "\n".join(lines)


def _format_status_summary(settings: Settings) -> str:
    db_path = Path(settings.db_path)
    latest_archive = sorted(Path(settings.archives_dir).glob("*.json"), reverse=True)
    archive_name = latest_archive[0].name if latest_archive else "无"

    run_status = "无运行记录"
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT run_at, status, error_message FROM run_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                run_at = row[0]
                status = row[1]
                run_status = f"{status} @ {run_at}"
                if row[2]:
                    run_status += f" | error={row[2][:120]}"

    now = datetime.now(timezone.utc).isoformat()
    return (
        f"服务状态：ok\n"
        f"当前时间(UTC)：{now}\n"
        f"最近归档：{archive_name}\n"
        f"最近任务：{run_status}"
    )


def _send_reply(
    settings: Settings,
    chat_id: str,
    content: str,
    sender_open_id: str = "",
    chat_type: str = "",
    send_text_fn: Callable[..., bool] = push_feishu_text,
) -> bool:
    # In p2p chat, open_id is more stable than chat_id for bot replies.
    targets: list[tuple[str, str]] = []
    if chat_type == "p2p" and sender_open_id:
        targets.append(("open_id", sender_open_id))
    targets.append((settings.feishu_receive_id_type, chat_id))
    if sender_open_id and ("open_id", sender_open_id) not in targets:
        targets.append(("open_id", sender_open_id))

    for receive_id_type, receive_id in targets:
        try:
            ok = send_text_fn(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
                base_url=settings.feishu_base_url,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
                content=content,
                retries=(),
            )
            if ok:
                return True
            logger.warning(
                "Feishu reply attempt failed: receive_id_type=%s receive_id=%s",
                receive_id_type,
                receive_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Feishu reply exception: receive_id_type=%s receive_id=%s",
                receive_id_type,
                receive_id,
            )

    logger.error(
        "Feishu reply failed on all targets, chat_id=%s sender_open_id=%s",
        chat_id,
        sender_open_id,
    )
    return False


def _run_pipeline_and_reply(
    settings: Settings,
    chat_id: str,
    sender_open_id: str,
    chat_type: str,
    send_text_fn: Callable[..., bool],
    run_pipeline_fn: Callable[..., DailyBrief],
) -> None:
    try:
        brief = run_pipeline_fn(push=False)
        markdown_content = render_markdown(brief)
        chunks = _split_text_for_feishu(markdown_content)
        if not chunks:
            chunks = [f"执行完成：{brief.title}（无可用内容）"]

        total = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            header = f"[{brief.title}] 第{idx}/{total}段\n" if total > 1 else ""
            _send_reply(
                settings,
                chat_id,
                header + chunk,
                sender_open_id=sender_open_id,
                chat_type=chat_type,
                send_text_fn=send_text_fn,
            )
    except Exception as exc:  # noqa: BLE001
        _send_reply(
            settings,
            chat_id,
            f"执行失败：{str(exc)[:500]}",
            sender_open_id=sender_open_id,
            chat_type=chat_type,
            send_text_fn=send_text_fn,
        )


def handle_feishu_event(
    payload: dict,
    settings: Settings,
    background_tasks: BackgroundTasks,
    send_text_fn: Callable[..., bool] = push_feishu_text,
    run_pipeline_fn: Callable[..., DailyBrief] | None = None,
) -> dict:
    if run_pipeline_fn is None:
        from app.pipeline import run_daily_pipeline

        run_pipeline_fn = run_daily_pipeline
    event_type = payload.get("type")
    if event_type == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    expected_token = settings.feishu_verification_token.strip()
    if expected_token:
        incoming_token = payload.get("token") or payload.get("header", {}).get("token")
        if incoming_token != expected_token:
            logger.warning("Feishu token mismatch, reject event")
            return {"ok": False, "error": "invalid token"}

    header = payload.get("header", {})
    if header.get("event_type") != "im.message.receive_v1":
        return {"ok": True}

    event_id = str(header.get("event_id", ""))
    if _seen_event(event_id):
        logger.debug("Feishu duplicate event ignored: %s", event_id)
        return {"ok": True, "duplicate": True}

    event = payload.get("event", {})
    sender = event.get("sender", {}).get("sender_id", {})
    sender_id = str(sender.get("open_id") or sender.get("user_id") or sender.get("union_id") or "")
    allow_from = settings.feishu_allow_from
    if allow_from and sender_id not in allow_from:
        logger.info("Feishu sender not in allowlist, ignored: %s", sender_id)
        return {"ok": True}

    message = event.get("message", {})
    chat_id = str(message.get("chat_id", "")).strip()
    if not chat_id:
        return {"ok": True}

    chat_type = str(message.get("chat_type", ""))
    raw_content = str(message.get("content", ""))
    if chat_type == "group" and settings.feishu_require_mention:
        mentions = message.get("mentions") or []
        if not mentions and "<at" not in raw_content:
            logger.debug("Feishu group message ignored due to require mention")
            return {"ok": True}

    text = _strip_mentions(_extract_message_text(message))
    command = _resolve_command(text)
    if command is None:
        logger.debug("Feishu message ignored (no command): chat_id=%s text=%s", chat_id, text[:80])
        if chat_type == "p2p" and text:
            _send_reply(
                settings,
                chat_id,
                "未识别到指令。\n发送 /help 查看可用命令，或直接发送：运行日报 / 最新日报 / 状态",
                sender_open_id=sender_id,
                chat_type=chat_type,
                send_text_fn=send_text_fn,
            )
        return {"ok": True}

    logger.info(
        "Feishu command received: event_id=%s chat_id=%s sender=%s command=%s",
        event_id,
        chat_id,
        sender_id,
        command,
    )

    if command == "help":
        _send_reply(
            settings,
            chat_id,
            _HELP_TEXT,
            sender_open_id=sender_id,
            chat_type=chat_type,
            send_text_fn=send_text_fn,
        )
        return {"ok": True}

    if command == "latest":
        _send_reply(
            settings,
            chat_id,
            _format_latest_summary(settings.archives_dir),
            sender_open_id=sender_id,
            chat_type=chat_type,
            send_text_fn=send_text_fn,
        )
        return {"ok": True}

    if command == "status":
        status_text = _format_status_summary(settings) + f"\n当前会话ID：{chat_id}"
        _send_reply(
            settings,
            chat_id,
            status_text,
            sender_open_id=sender_id,
            chat_type=chat_type,
            send_text_fn=send_text_fn,
        )
        return {"ok": True}

    if command == "run":
        _send_reply(
            settings,
            chat_id,
            "已收到 /run，开始执行，请稍候。",
            sender_open_id=sender_id,
            chat_type=chat_type,
            send_text_fn=send_text_fn,
        )
        background_tasks.add_task(
            _run_pipeline_and_reply,
            settings,
            chat_id,
            sender_id,
            chat_type,
            send_text_fn,
            run_pipeline_fn,
        )
        return {"ok": True}

    return {"ok": True}
