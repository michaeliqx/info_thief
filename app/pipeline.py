from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.classifier import classify_items
from app.collector import collect_from_source
from app.config import load_settings, load_sources
from app.deduper import dedupe_items
from app.feishu import push_feishu_text
from app.llm import FallbackLLMClient, LLMClient, OpenAILLMClient, VolcengineLLMClient
from app.models import BriefItem, DailyBrief, RankedItem, Settings, SourceConfig
from app.normalizer import normalize_items
from app.publisher import archive_brief, push_markdown, render_markdown, send_failure_alert
from app.ranker import rank_items, select_items_with_mix
from app.rsshub_bootstrap import ensure_rsshub_for_sources
from app.storage import StateStore

logger = logging.getLogger(__name__)


def _build_llm_client(settings: Settings, override: Optional[LLMClient] = None) -> LLMClient:
    if override is not None:
        return override
    if settings.llm_provider == "volcengine" and settings.ark_api_key:
        return VolcengineLLMClient(
            api_key=settings.ark_api_key,
            base_url=settings.volcengine_base_url,
            model=settings.llm_model,
        )
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAILLMClient(api_key=settings.openai_api_key, model=settings.llm_model)
    logger.warning("No valid LLM key found for provider=%s, fallback to heuristic summarizer", settings.llm_provider)
    return FallbackLLMClient()


def collect_all_sources(
    sources: list[SourceConfig],
    timeout_seconds: int,
    proxy: str | None = None,
) -> tuple[list, dict[str, str]]:
    raw_items = []
    source_errors: dict[str, str] = {}

    if not sources:
        return raw_items, source_errors

    max_workers = min(8, max(1, len(sources)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(collect_from_source, source, timeout_seconds, proxy): source.name
            for source in sources
        }
        for future in as_completed(future_map):
            source_name = future_map[future]
            try:
                items = future.result()
                raw_items.extend(items)
            except Exception as exc:  # noqa: BLE001
                source_errors[source_name] = str(exc)
                logger.warning("Source failed: %s | %s", source_name, exc)

    return raw_items, source_errors


def _build_source_hit_stats(
    raw_items: list,
    sources: list[SourceConfig],
    since: datetime,
    until: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    raw_counts = {source.name: 0 for source in sources}
    recent_counts = {source.name: 0 for source in sources}
    source_names = [source.name for source in sources]

    for item in raw_items:
        source_name = getattr(item, "source_name", "")
        if source_name not in raw_counts:
            raw_counts[source_name] = 0
            recent_counts[source_name] = 0
        raw_counts[source_name] += 1
        published_at = getattr(item, "published_at", None)
        if published_at and since <= published_at <= until:
            recent_counts[source_name] += 1

        # 聚合源拆分为“源名/发布方”后，同时回填到父源计数，避免父源日志误报 source_miss。
        for base_name in source_names:
            prefix = f"{base_name}/"
            if source_name.startswith(prefix):
                raw_counts[base_name] = raw_counts.get(base_name, 0) + 1
                if published_at and since <= published_at <= until:
                    recent_counts[base_name] = recent_counts.get(base_name, 0) + 1
                break

    return raw_counts, recent_counts


def _log_source_hit_stats(raw_counts: dict[str, int], recent_counts: dict[str, int]) -> None:
    for source_name in sorted(raw_counts):
        raw_count = raw_counts.get(source_name, 0)
        recent_count = recent_counts.get(source_name, 0)
        if raw_count == 0:
            logger.warning("source_miss | %s | raw=0 | within24h=0", source_name)
        else:
            logger.info("source_hit | %s | raw=%d | within24h=%d", source_name, raw_count, recent_count)


def _build_brief(
    selected_items: list[RankedItem],
    llm: LLMClient,
    run_time: datetime,
    tz_name: str,
) -> DailyBrief:
    brief_items: list[BriefItem] = []
    for item in selected_items:
        try:
            key_points = llm.summarize_item(item.title, item.content, item.source_name, item.url)
        except Exception:  # noqa: BLE001
            key_points = FallbackLLMClient().summarize_item(item.title, item.content, item.source_name, item.url)

        key_points = [p for p in key_points if p][:4]
        if len(key_points) < 2:
            key_points.append("建议阅读原文了解完整信息。")

        brief_items.append(
            BriefItem(
                perspective=item.perspective,
                title=item.title,
                key_points=key_points,
                source_name=item.source_name,
                url=item.url,
                score=item.score,
            )
        )

    titles = [item.title for item in brief_items]
    snippets = [f"{item.title} {'; '.join(item.key_points)}" for item in brief_items]

    try:
        intro = llm.compose_intro(titles)
    except Exception:  # noqa: BLE001
        intro = FallbackLLMClient().compose_intro(titles)

    try:
        observations = llm.compose_observations(snippets)
    except Exception:  # noqa: BLE001
        observations = FallbackLLMClient().compose_observations(snippets)

    local_date = run_time.astimezone(ZoneInfo(tz_name)).date()
    title = f"AI 每日情报 | {local_date.isoformat()}"

    return DailyBrief(
        date=local_date,
        title=title,
        intro=intro,
        items=brief_items,
        observations=observations[:2],
    )


def _to_wecom_content(markdown_content: str, max_chars: int = 3800) -> str:
    if len(markdown_content) <= max_chars:
        return markdown_content
    return markdown_content[: max_chars - 20] + "\n\n(内容过长，已截断)"


def _to_feishu_content(markdown_content: str, max_chars: int = 6000) -> str:
    if len(markdown_content) <= max_chars:
        return markdown_content
    return markdown_content[: max_chars - 20] + "\n\n(内容过长，已截断)"


def run_daily_pipeline(
    settings_path: str = "config/settings.yaml",
    sources_path: str = "config/sources.yaml",
    llm_client: Optional[LLMClient] = None,
    push: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> DailyBrief:
    settings = load_settings(settings_path)
    sources = load_sources(sources_path)
    ensure_rsshub_for_sources(sources)
    llm = _build_llm_client(settings, llm_client)

    store = StateStore(settings.db_path)
    store.init_db()

    metrics = {
        "source_count": len(sources),
        "raw_count": 0,
        "normalized_count": 0,
        "deduped_count": 0,
        "selected_count": 0,
        "source_errors": {},
    }
    push_enabled = settings.push_enabled if push is None else push
    metrics["push_enabled"] = push_enabled

    try:
        proxy = settings.http_proxy.strip() or None
        raw_items, source_errors = collect_all_sources(
            sources, settings.request_timeout_seconds, proxy=proxy
        )
        metrics["source_errors"] = source_errors
        metrics["raw_count"] = len(raw_items)

        run_time = now or datetime.now(timezone.utc)
        if run_time.tzinfo is None:
            run_time = run_time.replace(tzinfo=timezone.utc)
        since = run_time - timedelta(hours=24)

        raw_counts, recent_counts = _build_source_hit_stats(raw_items, sources, since=since, until=run_time)
        _log_source_hit_stats(raw_counts, recent_counts)
        metrics["source_raw_counts"] = raw_counts
        metrics["source_recent_24h_counts"] = recent_counts

        normalized = normalize_items(raw_items, since=since, until=run_time)
        metrics["normalized_count"] = len(normalized)

        seen_ids = store.load_seen_item_ids(days=7)
        metrics["seen_pool_count"] = len(seen_ids)
        metrics["seen_filtered_count"] = 0
        metrics["seen_filter_relaxed"] = False

        normalized_after_seen = [item for item in normalized if item.item_id not in seen_ids]
        metrics["seen_filtered_count"] = len(normalized) - len(normalized_after_seen)
        metrics["after_seen_count"] = len(normalized_after_seen)

        # 当天还没有真实成功推送时，避免“仅因调试/历史状态导致全量被 seen 拦截”而产出空报告。
        if push_enabled and normalized and not normalized_after_seen:
            has_recent_push = store.has_recent_successful_push_run(hours=24)
            if not has_recent_push:
                logger.warning(
                    "All normalized items filtered by seen cache (%d), relax once because no successful push in 24h",
                    len(normalized),
                )
                normalized_after_seen = normalized
                metrics["seen_filter_relaxed"] = True

        deduped = dedupe_items(normalized_after_seen)
        metrics["deduped_count"] = len(deduped)

        classified = classify_items(deduped, llm_client=llm, use_llm_fallback=False)
        ranked = rank_items(classified, now=run_time)
        selected = select_items_with_mix(
            ranked,
            item_min=settings.item_min,
            item_max=settings.item_max,
            mix_min_each=settings.mix_min_each,
            max_items_per_source=settings.max_items_per_source,
        )
        metrics["selected_count"] = len(selected)

        brief = _build_brief(selected, llm, run_time=run_time, tz_name=settings.timezone)
        archive_brief(brief, settings.archives_dir)

        markdown = render_markdown(brief)
        if push_enabled:
            push_attempted = False
            push_errors: list[str] = []

            if settings.wechat_webhook:
                push_attempted = True
                pushed = push_markdown(settings.wechat_webhook, _to_wecom_content(markdown))
                if not pushed:
                    push_errors.append("wecom")

            if settings.feishu_enabled and settings.feishu_push_targets:
                push_attempted = True
                feishu_content = _to_feishu_content(markdown)
                feishu_ok = True
                for target in settings.feishu_push_targets:
                    ok = push_feishu_text(
                        app_id=settings.feishu_app_id,
                        app_secret=settings.feishu_app_secret,
                        base_url=settings.feishu_base_url,
                        receive_id=target,
                        receive_id_type=settings.feishu_receive_id_type,
                        content=feishu_content,
                    )
                    if not ok:
                        feishu_ok = False
                if not feishu_ok:
                    push_errors.append("feishu")

            if not push_attempted:
                raise RuntimeError("Push is enabled but no channel configured")
            if push_errors:
                raise RuntimeError(f"Push failed: {', '.join(push_errors)}")

        if push_enabled:
            store.mark_seen([(item.item_id, item.canonical_url) for item in selected])
        else:
            logger.info("Skip mark_seen because push is disabled")
        store.log_run(status="success", metrics=metrics)
        return brief

    except Exception as exc:  # noqa: BLE001
        logger.exception("Daily pipeline failed")
        if push_enabled:
            try:
                if settings.wechat_webhook:
                    send_failure_alert(settings.wechat_webhook, str(exc))
                if settings.feishu_enabled and settings.feishu_push_targets:
                    alert_text = f"[AI日报告警] 当日任务失败：{str(exc)[:500]}"
                    for target in settings.feishu_push_targets:
                        push_feishu_text(
                            app_id=settings.feishu_app_id,
                            app_secret=settings.feishu_app_secret,
                            base_url=settings.feishu_base_url,
                            receive_id=target,
                            receive_id_type=settings.feishu_receive_id_type,
                            content=alert_text,
                            retries=(),
                        )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to send failure alert")
        store.log_run(status="failed", metrics=metrics, error_message=str(exc))
        raise
