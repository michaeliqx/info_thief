from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from Crypto.Cipher import AES
from fastapi import BackgroundTasks

from app.models import DailyBrief, Settings
from app.publisher import render_markdown

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_CACHE_LOCK = threading.Lock()

_EVENT_CACHE: OrderedDict[str, None] = OrderedDict()
_EVENT_CACHE_LOCK = threading.Lock()
_EVENT_CACHE_MAX = 1000

_HELP_TEXT = (
    "可用命令：\n"
    "/run 或 运行日报：立即执行并回传当天完整日报\n"
    "/latest 或 最新日报：查看最近一次归档摘要\n"
    "/status 或 状态：查看服务状态\n"
    "/help 或 帮助：查看帮助"
)


def _token_cache_key(corp_id: str, base_url: str) -> str:
    return f"{base_url}|{corp_id}"


def _get_access_token(
    corp_id: str,
    secret: str,
    base_url: str,
    client: httpx.Client,
    now_fn: Callable[[], float] = time.time,
) -> str:
    if not corp_id or not secret:
        raise ValueError("Missing WeCom credentials")

    now = now_fn()
    cache_key = _token_cache_key(corp_id, base_url)

    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached is not None:
            token, expire_at = cached
            if expire_at - now >= 60:
                return token

    resp = client.get(
        f"{base_url}/cgi-bin/gettoken",
        params={"corpid": corp_id, "corpsecret": secret},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"WeCom auth failed with status={resp.status_code}")

    data = resp.json()
    if int(data.get("errcode", -1)) != 0:
        raise RuntimeError(f"WeCom auth failed: {data.get('errmsg', 'unknown error')}")

    token = str(data.get("access_token", "")).strip()
    if not token:
        raise RuntimeError("WeCom auth failed: missing access_token")

    expire_in = int(data.get("expires_in", 7200))
    expire_at = now + max(300, expire_in - 120)
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE[cache_key] = (token, expire_at)
    return token


def _clear_access_token_cache(corp_id: str, base_url: str) -> None:
    cache_key = _token_cache_key(corp_id, base_url)
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE.pop(cache_key, None)


def push_wecom_message(
    corp_id: str,
    secret: str,
    agent_id: str,
    to_user: str,
    content: str,
    msg_type: str = "text",
    base_url: str = "https://qyapi.weixin.qq.com",
    retries: tuple[int, ...] = (2, 5, 10),
    sleep_fn: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
) -> bool:
    if not to_user:
        raise ValueError("Missing WeCom to_user")

    payload: dict[str, object] = {
        "touser": to_user,
        "msgtype": msg_type,
        "agentid": int(agent_id),
        "safe": 0,
    }
    if msg_type == "markdown":
        payload["markdown"] = {"content": content}
    else:
        payload["msgtype"] = "text"
        payload["text"] = {"content": content}

    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=15)

    attempts = (0,) + retries
    try:
        for wait_seconds in attempts:
            if wait_seconds > 0:
                sleep_fn(wait_seconds)
            if _push_wecom_message_once(
                corp_id=corp_id,
                secret=secret,
                agent_id=agent_id,
                to_user=to_user,
                payload=payload,
                base_url=base_url,
                client=client,
            ):
                return True
        return False
    finally:
        if owned_client and client is not None:
            client.close()


def _push_wecom_message_once(
    corp_id: str,
    secret: str,
    agent_id: str,
    to_user: str,
    payload: dict[str, object],
    base_url: str,
    client: httpx.Client,
) -> bool:
    token = _get_access_token(corp_id, secret, base_url, client)
    resp = client.post(
        f"{base_url}/cgi-bin/message/send",
        params={"access_token": token},
        json=payload,
    )
    if resp.status_code != 200:
        _clear_access_token_cache(corp_id, base_url)
        logger.warning(
            "WeCom send failed: status=%s to_user=%s body=%s",
            resp.status_code,
            to_user,
            (resp.text or "")[:300],
        )
        return False

    data = resp.json()
    ok = int(data.get("errcode", -1)) == 0
    if not ok:
        _clear_access_token_cache(corp_id, base_url)
        logger.warning(
            "WeCom send failed: errcode=%s errmsg=%s to_user=%s agent_id=%s",
            data.get("errcode"),
            data.get("errmsg"),
            to_user,
            agent_id,
        )
    return ok


def split_text_for_wecom(content: str, max_chars: int = 3000) -> list[str]:
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


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    joined = "".join(sorted([token, timestamp, nonce, encrypted]))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("Invalid PKCS7 data")
    pad = data[-1]
    if pad < 1 or pad > 32:
        raise ValueError("Invalid PKCS7 padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise ValueError("Invalid PKCS7 padding")
    return data[:-pad]


def _pkcs7_pad(data: bytes) -> bytes:
    block_size = 32
    pad = block_size - (len(data) % block_size)
    if pad == 0:
        pad = block_size
    return data + bytes([pad]) * pad


class WecomCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str) -> None:
        if not token or not encoding_aes_key or not corp_id:
            raise ValueError("Missing WeCom crypto settings")
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.aes_key[:16]

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypted: str) -> None:
        expected = _sha1_signature(self.token, timestamp, nonce, encrypted)
        if expected != msg_signature:
            raise ValueError("Invalid WeCom signature")

    def decrypt(self, encrypted: str, msg_signature: str, timestamp: str, nonce: str) -> str:
        self.verify_signature(msg_signature, timestamp, nonce, encrypted)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        decoded = base64.b64decode(encrypted)
        plain = _pkcs7_unpad(cipher.decrypt(decoded))
        xml_len = struct.unpack(">I", plain[16:20])[0]
        xml_bytes = plain[20 : 20 + xml_len]
        receive_id = plain[20 + xml_len :].decode("utf-8")
        if receive_id != self.corp_id:
            raise ValueError("WeCom corp id mismatch")
        return xml_bytes.decode("utf-8")

    def encrypt(self, plain_text: str, nonce: str, timestamp: str | None = None) -> str:
        receive_id = self.corp_id.encode("utf-8")
        raw = (
            os.urandom(16)
            + struct.pack(">I", len(plain_text.encode("utf-8")))
            + plain_text.encode("utf-8")
            + receive_id
        )
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        encrypted = base64.b64encode(cipher.encrypt(_pkcs7_pad(raw))).decode("utf-8")
        ts = timestamp or str(int(time.time()))
        signature = _sha1_signature(self.token, ts, nonce, encrypted)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{ts}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )


def handle_wecom_url_verification(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
    settings: Settings,
) -> str:
    crypto = WecomCrypto(settings.wecom_token, settings.wecom_encoding_aes_key, settings.wecom_corp_id)
    return crypto.decrypt(echostr, msg_signature, timestamp, nonce)


def _parse_xml_text(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    parsed: dict[str, str] = {}
    for child in root:
        if child.tag:
            parsed[child.tag] = child.text or ""
    return parsed


def _extract_encrypted_from_callback(xml_text: str) -> str:
    payload = _parse_xml_text(xml_text)
    encrypted = payload.get("Encrypt", "").strip()
    if not encrypted:
        raise ValueError("Missing WeCom Encrypt field")
    return encrypted


def _send_reply(
    settings: Settings,
    to_user: str,
    content: str,
    msg_type: str = "text",
    send_message_fn: Callable[..., bool] = push_wecom_message,
) -> bool:
    try:
        return send_message_fn(
            corp_id=settings.wecom_corp_id,
            secret=settings.wecom_secret,
            agent_id=settings.wecom_agent_id,
            to_user=to_user,
            content=content,
            msg_type=msg_type,
            base_url=settings.wecom_base_url,
            retries=(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("WeCom reply exception: to_user=%s", to_user)
        return False


def _run_pipeline_and_reply(
    settings: Settings,
    to_user: str,
    send_message_fn: Callable[..., bool],
    run_pipeline_fn: Callable[..., DailyBrief],
) -> None:
    try:
        brief = run_pipeline_fn(push=False)
        content = render_markdown(brief)
        chunks = split_text_for_wecom(content)
        if not chunks:
            chunks = [f"执行完成：{brief.title}（无可用内容）"]

        total = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            header = f"[{brief.title}] 第{idx}/{total}段\n" if total > 1 else ""
            _send_reply(
                settings=settings,
                to_user=to_user,
                content=header + chunk,
                msg_type="markdown",
                send_message_fn=send_message_fn,
            )
    except Exception as exc:  # noqa: BLE001
        _send_reply(
            settings=settings,
            to_user=to_user,
            content=f"执行失败：{str(exc)[:500]}",
            send_message_fn=send_message_fn,
        )


def handle_wecom_event(
    body: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    settings: Settings,
    background_tasks: BackgroundTasks,
    send_message_fn: Callable[..., bool] = push_wecom_message,
    run_pipeline_fn: Callable[..., DailyBrief] | None = None,
) -> str:
    if run_pipeline_fn is None:
        from app.pipeline import run_daily_pipeline

        run_pipeline_fn = run_daily_pipeline

    crypto = WecomCrypto(settings.wecom_token, settings.wecom_encoding_aes_key, settings.wecom_corp_id)
    encrypted = _extract_encrypted_from_callback(body)
    plain_xml = crypto.decrypt(encrypted, msg_signature, timestamp, nonce)
    message = _parse_xml_text(plain_xml)

    event_id = str(message.get("MsgId") or message.get("FromUserName") + ":" + message.get("CreateTime", ""))
    if _seen_event(event_id):
        logger.debug("WeCom duplicate event ignored: %s", event_id)
        return "success"

    msg_type = message.get("MsgType", "").strip().lower()
    from_user = message.get("FromUserName", "").strip()
    if not from_user:
        return "success"

    if msg_type == "event":
        event = message.get("Event", "").strip().lower()
        if event == "enter_agent":
            _send_reply(
                settings=settings,
                to_user=from_user,
                content=_HELP_TEXT,
                send_message_fn=send_message_fn,
            )
        return "success"

    if msg_type != "text":
        return "success"

    text = (message.get("Content", "") or "").strip()
    command = _resolve_command(text)
    if command is None:
        _send_reply(
            settings=settings,
            to_user=from_user,
            content="未识别到指令。\n发送 /help 查看可用命令，或直接发送：运行日报 / 最新日报 / 状态",
            send_message_fn=send_message_fn,
        )
        return "success"

    logger.info(
        "WeCom command received: from_user=%s agent_id=%s command=%s",
        from_user,
        settings.wecom_agent_id,
        command,
    )

    if command == "help":
        _send_reply(
            settings=settings,
            to_user=from_user,
            content=_HELP_TEXT,
            send_message_fn=send_message_fn,
        )
        return "success"

    if command == "latest":
        _send_reply(
            settings=settings,
            to_user=from_user,
            content=_format_latest_summary(settings.archives_dir),
            send_message_fn=send_message_fn,
        )
        return "success"

    if command == "status":
        _send_reply(
            settings=settings,
            to_user=from_user,
            content=_format_status_summary(settings),
            send_message_fn=send_message_fn,
        )
        return "success"

    if command == "run":
        _send_reply(
            settings=settings,
            to_user=from_user,
            content="已收到 /run，开始执行，请稍候。",
            send_message_fn=send_message_fn,
        )
        background_tasks.add_task(
            _run_pipeline_and_reply,
            settings,
            from_user,
            send_message_fn,
            run_pipeline_fn,
        )
        return "success"

    return "success"
