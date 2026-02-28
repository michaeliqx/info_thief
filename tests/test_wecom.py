from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from app.models import BriefItem, DailyBrief, Perspective, Settings
from app.wecom import (
    WecomCrypto,
    handle_wecom_event,
    handle_wecom_url_verification,
    push_wecom_message,
    split_text_for_wecom,
)


@dataclass
class _Resp:
    status_code: int
    payload: dict

    def json(self) -> dict:
        return self.payload

    @property
    def text(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


class _FakeClient:
    def __init__(self, token_responses: list[_Resp], msg_responses: list[_Resp]) -> None:
        self.token_responses = token_responses
        self.msg_responses = msg_responses
        self.token_calls = 0
        self.msg_calls = 0

    def get(self, url: str, **kwargs) -> _Resp:
        if url.endswith("/cgi-bin/gettoken"):
            self.token_calls += 1
            idx = min(self.token_calls - 1, len(self.token_responses) - 1)
            return self.token_responses[idx]
        raise AssertionError(f"unexpected url: {url}")

    def post(self, url: str, **kwargs) -> _Resp:
        if url.endswith("/cgi-bin/message/send"):
            self.msg_calls += 1
            idx = min(self.msg_calls - 1, len(self.msg_responses) - 1)
            return self.msg_responses[idx]
        raise AssertionError(f"unexpected url: {url}")

    def close(self) -> None:
        return None


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.tasks.append((func, args, kwargs))


def _settings() -> Settings:
    return Settings(
        timezone="Asia/Shanghai",
        schedule_time="09:30",
        wecom_enabled=True,
        wecom_corp_id="ww1234567890",
        wecom_agent_id="1000002",
        wecom_secret="secret_xxx",
        wecom_token="token_xxx",
        wecom_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    )


def _encrypt_message(xml_text: str, settings: Settings, nonce: str = "nonce-1", timestamp: str = "1710000000") -> tuple[str, str]:
    crypto = WecomCrypto(settings.wecom_token, settings.wecom_encoding_aes_key, settings.wecom_corp_id)
    encrypted_xml = crypto.encrypt(xml_text, nonce=nonce, timestamp=timestamp)
    encrypted = encrypted_xml.split("<Encrypt><![CDATA[", 1)[1].split("]]></Encrypt>", 1)[0]
    signature = encrypted_xml.split("<MsgSignature><![CDATA[", 1)[1].split("]]></MsgSignature>", 1)[0]
    return encrypted, signature


def test_push_wecom_message_retry_until_success() -> None:
    client = _FakeClient(
        token_responses=[
            _Resp(status_code=200, payload={"errcode": 0, "access_token": "t1", "expires_in": 7200}),
            _Resp(status_code=200, payload={"errcode": 0, "access_token": "t2", "expires_in": 7200}),
        ],
        msg_responses=[
            _Resp(status_code=200, payload={"errcode": 40014, "errmsg": "invalid access token"}),
            _Resp(status_code=200, payload={"errcode": 0, "errmsg": "ok"}),
        ],
    )

    sleeps: list[float] = []
    ok = push_wecom_message(
        corp_id="ww1234567890",
        secret="secret_xxx",
        agent_id="1000002",
        to_user="zhangsan",
        content="hello",
        retries=(1,),
        sleep_fn=lambda sec: sleeps.append(sec),
        client=client,
    )

    assert ok is True
    assert client.token_calls >= 1
    assert client.msg_calls == 2
    assert sleeps == [1]


def test_split_text_for_wecom_splits_long_content() -> None:
    short = "短内容"
    assert split_text_for_wecom(short) == [short]

    long_content = "a" * 1000 + "\n" + "b" * 1000 + "\n" + "c" * 1000
    chunks = split_text_for_wecom(long_content, max_chars=1500)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 1600 for chunk in chunks)


def test_handle_wecom_url_verification() -> None:
    settings = _settings()
    echostr, signature = _encrypt_message("hello-wecom", settings)

    plain = handle_wecom_url_verification(
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-1",
        echostr=echostr,
        settings=settings,
    )

    assert plain == "hello-wecom"


def test_handle_wecom_event_help_command() -> None:
    settings = _settings()
    tasks = _FakeBackgroundTasks()
    sent: list[str] = []
    plain_xml = """
    <xml>
      <ToUserName><![CDATA[ww1234567890]]></ToUserName>
      <FromUserName><![CDATA[zhangsan]]></FromUserName>
      <CreateTime>1710000000</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[/help]]></Content>
      <MsgId>10001</MsgId>
      <AgentID>1000002</AgentID>
    </xml>
    """.strip()
    encrypted, signature = _encrypt_message(plain_xml, settings)
    body = f"<xml><ToUserName><![CDATA[{settings.wecom_corp_id}]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

    result = handle_wecom_event(
        body=body,
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-1",
        settings=settings,
        background_tasks=tasks,
        send_message_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
    )

    assert result == "success"
    assert len(sent) == 1
    assert "/run" in sent[0]
    assert tasks.tasks == []


def test_handle_wecom_event_run_command_adds_background_task() -> None:
    settings = _settings()
    tasks = _FakeBackgroundTasks()
    sent: list[str] = []
    plain_xml = """
    <xml>
      <ToUserName><![CDATA[ww1234567890]]></ToUserName>
      <FromUserName><![CDATA[zhangsan]]></FromUserName>
      <CreateTime>1710000000</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[/run]]></Content>
      <MsgId>10002</MsgId>
      <AgentID>1000002</AgentID>
    </xml>
    """.strip()
    encrypted, signature = _encrypt_message(plain_xml, settings)
    body = f"<xml><ToUserName><![CDATA[{settings.wecom_corp_id}]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

    result = handle_wecom_event(
        body=body,
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-1",
        settings=settings,
        background_tasks=tasks,
        send_message_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
        run_pipeline_fn=lambda **kwargs: DailyBrief(
            date=date.today(),
            title="test",
            intro="intro",
            items=[
                BriefItem(
                    perspective=Perspective.PRODUCT,
                    title="item",
                    key_points=["k1", "k2"],
                    source_name="s",
                    url="https://example.com",
                    score=1.0,
                )
            ],
            observations=["obs"],
        ),
    )

    assert result == "success"
    assert len(sent) == 1
    assert "开始执行" in sent[0]
    assert len(tasks.tasks) == 1

    task_func, task_args, task_kwargs = tasks.tasks[0]
    task_func(*task_args, **task_kwargs)
    assert any("# test" in msg or "AI 每日情报" in msg for msg in sent[1:])
