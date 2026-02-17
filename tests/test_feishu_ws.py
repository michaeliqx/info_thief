from types import SimpleNamespace

import yaml

from app.feishu_ws import FeishuLongConnectionGateway


def _make_settings_yaml(path) -> None:
    settings = {
        "timezone": "Asia/Shanghai",
        "schedule_time": "09:30",
        "collector_trigger_time": "09:20",
        "item_min": 1,
        "item_max": 2,
        "mix_min_each": 1,
        "llm_provider": "volcengine",
        "llm_model": "doubao-seed-1-8-251228",
        "volcengine_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "ark_api_key": "",
        "push_enabled": False,
        "wechat_webhook": "",
        "feishu_enabled": True,
        "feishu_app_id": "cli_xxx",
        "feishu_app_secret": "sec_xxx",
        "feishu_verification_token": "token_xxx",
        "feishu_connection_mode": "websocket",
        "openai_api_key": "",
        "request_timeout_seconds": 3,
        "db_path": "data/state.db",
        "archives_dir": "archives",
        "log_level": "INFO",
    }
    path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")


def test_build_message_payload_from_ws_event(tmp_path) -> None:
    settings_path = tmp_path / "settings.yaml"
    _make_settings_yaml(settings_path)

    gateway = FeishuLongConnectionGateway(settings_path=str(settings_path))
    data = SimpleNamespace(
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1", user_id=None, union_id=None)),
            message=SimpleNamespace(
                message_id="om_xxx",
                chat_id="oc_xxx",
                chat_type="p2p",
                message_type="text",
                content='{"text":"/status"}',
                mentions=[],
            ),
        )
    )

    payload = gateway._build_message_payload(data)

    assert payload["header"]["event_type"] == "im.message.receive_v1"
    assert payload["header"]["event_id"] == "om_xxx"
    assert payload["event"]["sender"]["sender_id"]["open_id"] == "ou_1"
    assert payload["event"]["message"]["chat_id"] == "oc_xxx"
    assert payload["event"]["message"]["content"] == '{"text":"/status"}'


def test_on_message_sync_calls_handler(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.yaml"
    _make_settings_yaml(settings_path)
    gateway = FeishuLongConnectionGateway(settings_path=str(settings_path))

    seen: list[dict] = []

    def _fake_handle(payload, settings, background_tasks):  # noqa: ANN001
        seen.append(payload)
        return {"ok": True}

    monkeypatch.setattr("app.feishu_ws.handle_feishu_event", _fake_handle)

    data = SimpleNamespace(
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1", user_id=None, union_id=None)),
            message=SimpleNamespace(
                message_id="om_xxx",
                chat_id="oc_xxx",
                chat_type="p2p",
                message_type="text",
                content='{"text":"/help"}',
                mentions=[],
            ),
        )
    )
    gateway._on_message_sync(data)

    assert len(seen) == 1
    assert seen[0]["event"]["message"]["chat_id"] == "oc_xxx"
