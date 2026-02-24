from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.llm import FallbackLLMClient
from app.models import RawItem
from app.pipeline import run_daily_pipeline


def test_pipeline_generates_brief_and_archives(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.yaml"
    sources_path = tmp_path / "sources.yaml"
    db_path = tmp_path / "state.db"
    archives_dir = tmp_path / "archives"

    settings = {
        "timezone": "Asia/Shanghai",
        "schedule_time": "09:30",
        "collector_trigger_time": "09:20",
        "item_min": 8,
        "item_max": 12,
        "mix_min_each": 2,
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "wechat_webhook": "",
        "openai_api_key": "",
        "request_timeout_seconds": 3,
        "db_path": str(db_path),
        "archives_dir": str(archives_dir),
        "log_level": "INFO",
    }
    sources = {"sources": [{"name": "dummy", "type": "rss", "url": "https://example.com/rss", "enabled": True}]}

    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    sources_path.write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")

    now = datetime.now(timezone.utc)
    raw_items = []
    for idx in range(12):
        if idx % 3 == 0:
            title = f"公司发布 AI 应用 {idx}"
        elif idx % 3 == 1:
            title = f"新模型架构与推理优化 {idx}"
        else:
            title = f"AI 公司融资与行业合作 {idx}"

        raw_items.append(
            RawItem(
                source_name="dummy",
                source_weight=1.2,
                url=f"https://example.com/{idx}",
                title=title,
                content=title + " 详细内容",
                published_at=now - timedelta(hours=idx),
                discovered_at=now,
                tags=["ai"],
            )
        )

    def _fake_collect_all_sources(_sources, _timeout, proxy=None):
        return raw_items, {}

    monkeypatch.setattr("app.pipeline.collect_all_sources", _fake_collect_all_sources)

    brief = run_daily_pipeline(
        settings_path=str(settings_path),
        sources_path=str(sources_path),
        llm_client=FallbackLLMClient(),
        push=False,
        now=now,
    )

    assert 8 <= len(brief.items) <= 12
    assert (archives_dir / f"{brief.date.isoformat()}.md").exists()
    assert (archives_dir / f"{brief.date.isoformat()}.json").exists()
    assert Path(db_path).exists()


def test_pipeline_now_none_keeps_recent_discovered_items(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.yaml"
    sources_path = tmp_path / "sources.yaml"
    db_path = tmp_path / "state.db"
    archives_dir = tmp_path / "archives"

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
        "openai_api_key": "",
        "request_timeout_seconds": 3,
        "db_path": str(db_path),
        "archives_dir": str(archives_dir),
        "log_level": "INFO",
    }
    sources = {"sources": [{"name": "dummy", "type": "rss", "url": "https://example.com/rss", "enabled": True}]}

    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    sources_path.write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")

    def _fake_collect_all_sources(_sources, _timeout, proxy=None):
        now = datetime.now(timezone.utc)
        item = RawItem(
            source_name="dummy",
            source_weight=1.2,
            url="https://example.com/recent",
            title="公司发布 AI 应用",
            content="这是刚刚发现的 AI 资讯",
            published_at=None,
            discovered_at=now,
            tags=["ai", "product"],
        )
        return [item], {}

    monkeypatch.setattr("app.pipeline.collect_all_sources", _fake_collect_all_sources)

    brief = run_daily_pipeline(
        settings_path=str(settings_path),
        sources_path=str(sources_path),
        llm_client=FallbackLLMClient(),
        push=False,
        now=None,
    )

    assert len(brief.items) >= 1


def test_pipeline_pushes_to_feishu_targets(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.yaml"
    sources_path = tmp_path / "sources.yaml"
    db_path = tmp_path / "state.db"
    archives_dir = tmp_path / "archives"

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
        "push_enabled": True,
        "wechat_webhook": "",
        "feishu_enabled": True,
        "feishu_app_id": "cli_xxx",
        "feishu_app_secret": "secret_xxx",
        "feishu_push_targets": ["oc_test_group"],
        "feishu_receive_id_type": "chat_id",
        "openai_api_key": "",
        "request_timeout_seconds": 3,
        "db_path": str(db_path),
        "archives_dir": str(archives_dir),
        "log_level": "INFO",
    }
    sources = {"sources": [{"name": "dummy", "type": "rss", "url": "https://example.com/rss", "enabled": True}]}

    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    sources_path.write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")

    now = datetime.now(timezone.utc)

    def _fake_collect_all_sources(_sources, _timeout, proxy=None):
        item = RawItem(
            source_name="dummy",
            source_weight=1.2,
            url="https://example.com/recent",
            title="公司发布 AI 应用",
            content="这是刚刚发现的 AI 资讯",
            published_at=None,
            discovered_at=now,
            tags=["ai", "product"],
        )
        return [item], {}

    push_calls: list[dict] = []

    def _fake_push_feishu_text(**kwargs):
        push_calls.append(kwargs)
        return True

    monkeypatch.setattr("app.pipeline.collect_all_sources", _fake_collect_all_sources)
    monkeypatch.setattr("app.pipeline.push_feishu_text", _fake_push_feishu_text)

    run_daily_pipeline(
        settings_path=str(settings_path),
        sources_path=str(sources_path),
        llm_client=FallbackLLMClient(),
        push=None,
        now=now,
    )

    assert len(push_calls) == 1
    assert push_calls[0]["receive_id"] == "oc_test_group"
