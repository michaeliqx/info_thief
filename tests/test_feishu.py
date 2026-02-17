import json
from dataclasses import dataclass
from datetime import date

from app.feishu import handle_feishu_event, push_feishu_text
from app.models import BriefItem, DailyBrief, Perspective, Settings


@dataclass
class _Resp:
    status_code: int
    payload: dict

    def json(self) -> dict:
        return self.payload


class _FakeClient:
    def __init__(self, auth_responses: list[_Resp], msg_responses: list[_Resp]) -> None:
        self.auth_responses = auth_responses
        self.msg_responses = msg_responses
        self.auth_calls = 0
        self.msg_calls = 0

    def post(self, url: str, **kwargs) -> _Resp:
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            self.auth_calls += 1
            idx = min(self.auth_calls - 1, len(self.auth_responses) - 1)
            return self.auth_responses[idx]

        if url.endswith("/im/v1/messages"):
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
        feishu_enabled=True,
        feishu_app_id="cli_xxx",
        feishu_app_secret="secret_xxx",
        feishu_verification_token="token_xxx",
        feishu_push_targets=[],
    )


def test_push_feishu_text_retry_until_success() -> None:
    client = _FakeClient(
        auth_responses=[
            _Resp(status_code=200, payload={"code": 0, "tenant_access_token": "t1", "expire": 7200}),
            _Resp(status_code=200, payload={"code": 0, "tenant_access_token": "t2", "expire": 7200}),
        ],
        msg_responses=[
            _Resp(status_code=200, payload={"code": 1, "msg": "temporary fail"}),
            _Resp(status_code=200, payload={"code": 0, "msg": "ok"}),
        ],
    )

    sleeps: list[float] = []
    ok = push_feishu_text(
        app_id="cli_xxx",
        app_secret="secret_xxx",
        base_url="https://open.feishu.cn",
        receive_id="oc_xxx",
        content="hello",
        retries=(1,),
        sleep_fn=lambda sec: sleeps.append(sec),
        client=client,
    )

    assert ok is True
    assert client.msg_calls == 2
    assert client.auth_calls >= 1
    assert sleeps == [1]


def test_handle_feishu_event_help_command() -> None:
    sent: list[str] = []
    settings = _settings()
    tasks = _FakeBackgroundTasks()

    payload = {
        "header": {
            "event_id": "evt-help",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "/help"}, ensure_ascii=False),
            },
        },
    }

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
    )

    assert result["ok"] is True
    assert len(sent) == 1
    assert "/run" in sent[0]
    assert len(tasks.tasks) == 0


def test_handle_feishu_event_run_command_adds_background_task() -> None:
    sent: list[str] = []
    settings = _settings()
    tasks = _FakeBackgroundTasks()

    payload = {
        "header": {
            "event_id": "evt-run",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "/run"}, ensure_ascii=False),
            },
        },
    }

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
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

    assert result["ok"] is True
    assert len(sent) == 1
    assert "开始执行" in sent[0]
    assert len(tasks.tasks) == 1

    task_func, task_args, task_kwargs = tasks.tasks[0]
    task_func(*task_args, **task_kwargs)
    assert len(sent) >= 2
    assert any("AI 每日情报" in msg or "# test" in msg for msg in sent[1:])


def test_handle_feishu_event_group_requires_mention() -> None:
    sent: list[str] = []
    settings = _settings()
    tasks = _FakeBackgroundTasks()

    payload = {
        "header": {
            "event_id": "evt-group-ignore",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "/help"}, ensure_ascii=False),
                "mentions": [],
            },
        },
    }

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
    )

    assert result["ok"] is True
    assert sent == []


def test_handle_feishu_event_fallback_to_open_id_when_chat_id_reply_fails() -> None:
    settings = _settings()
    tasks = _FakeBackgroundTasks()
    calls: list[tuple[str, str]] = []

    payload = {
        "header": {
            "event_id": "evt-fallback",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "/help"}, ensure_ascii=False),
            },
        },
    }

    def _fake_send(**kwargs):
        calls.append((kwargs["receive_id_type"], kwargs["receive_id"]))
        # open_id succeeds immediately in p2p mode.
        return kwargs["receive_id_type"] == "open_id"

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=_fake_send,
    )

    assert result["ok"] is True
    assert calls[0] == ("open_id", "ou_user_1")


def test_handle_feishu_event_natural_language_command() -> None:
    sent: list[str] = []
    settings = _settings()
    tasks = _FakeBackgroundTasks()

    payload = {
        "header": {
            "event_id": "evt-natural",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "状态"}, ensure_ascii=False),
            },
        },
    }

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
    )

    assert result["ok"] is True
    assert len(sent) == 1
    assert "服务状态" in sent[0]


def test_handle_feishu_event_unknown_text_returns_hint_in_p2p() -> None:
    sent: list[str] = []
    settings = _settings()
    tasks = _FakeBackgroundTasks()

    payload = {
        "header": {
            "event_id": "evt-hint",
            "event_type": "im.message.receive_v1",
            "token": "token_xxx",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "chat_id": "oc_group_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "你好"}, ensure_ascii=False),
            },
        },
    }

    result = handle_feishu_event(
        payload=payload,
        settings=settings,
        background_tasks=tasks,
        send_text_fn=lambda **kwargs: sent.append(kwargs["content"]) or True,
    )

    assert result["ok"] is True
    assert len(sent) == 1
    assert "未识别到指令" in sent[0]
