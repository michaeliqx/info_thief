"""Collector 单元测试，含 HTML 源发布时间解析。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.collector import (
    _extract_date_from_element,
    _parse_html_date,
    collect_from_source,
)
from app.models import SourceConfig


def test_parse_html_date_absolute() -> None:
    """解析绝对日期 YYYY-MM-DD HH:MM。"""
    ref = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)
    result = _parse_html_date("8858 点击 2026-02-24 10:05", regex=r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", ref_time=ref)
    assert result is not None
    assert result.year == 2026
    assert result.month == 2
    assert result.day == 24
    assert result.hour == 10
    assert result.minute == 5


def test_parse_html_date_relative_hours() -> None:
    """解析相对时间 N小时前。"""
    ref = datetime(2026, 2, 24, 14, 0, 0, tzinfo=timezone.utc)
    result = _parse_html_date("3小时前", ref_time=ref)
    assert result is not None
    assert result.hour == 11
    assert result.day == 24


def test_parse_html_date_relative_yesterday() -> None:
    """解析相对时间 昨天。"""
    ref = datetime(2026, 2, 24, 10, 0, 0, tzinfo=timezone.utc)
    result = _parse_html_date("昨天", ref_time=ref)
    assert result is not None
    assert result.day == 23
    assert result.month == 2


def test_parse_html_date_relative_days() -> None:
    """解析相对时间 N天前。"""
    ref = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)
    result = _parse_html_date("2天前", ref_time=ref)
    assert result is not None
    assert result.day == 22


def test_parse_html_date_iso() -> None:
    """解析 ISO 格式日期。"""
    result = _parse_html_date("2026-02-24T08:00:00+08:00")
    assert result is not None
    assert result.year == 2026
    assert result.month == 2
    assert result.day == 24


def test_parse_html_date_chinese_month_day_time() -> None:
    """解析中文月日时间格式。"""
    ref = datetime(2026, 2, 24, 10, 0, 0, tzinfo=timezone.utc)
    result = _parse_html_date("发布时间：02月13日 13:27", ref_time=ref)
    assert result is not None
    assert result.year == 2026
    assert result.month == 2
    assert result.day == 13
    assert result.hour == 13
    assert result.minute == 27


def test_parse_html_date_chinese_ymd_hour_only() -> None:
    """解析中文年月日+小时（无分钟）格式。"""
    result = _parse_html_date("2026年02月05日 19点")
    assert result is not None
    assert result.year == 2026
    assert result.month == 2
    assert result.day == 5
    assert result.hour == 19
    assert result.minute == 0


def test_parse_html_date_empty() -> None:
    """空字符串返回 None。"""
    assert _parse_html_date("") is None
    assert _parse_html_date("   ") is None


def test_collect_html_with_date_from_mock() -> None:
    """使用 mock HTML 测试带日期解析的采集。"""
    html = """
    <html>
    <body>
    <div>
        <a href="/newDetail.html?newId=123" style="font-size: 18px">测试AI新闻标题</a>
        <div style="text-align: right; font-size:14px;color: #A7A7A7;">100 点击 2026-02-24 09:00</div>
    </div>
    <div>
        <a href="/newDetail.html?newId=456" style="font-size: 18px">另一条AI大模型新闻</a>
        <div style="text-align: right; font-size:14px;color: #A7A7A7;">200 点击 2026-02-23 18:30</div>
    </div>
    </body>
    </html>
    """
    source = SourceConfig(
        name="test_source",
        type="html",
        url="https://example.com/",
        article_selector="a[href*='newDetail']",
        link_pattern=r"newDetail\.html\?newId=",
        date_selector="div[style*='text-align: right']",
        date_regex=r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}",
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None

        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 2
    assert items[0].title == "测试AI新闻标题"
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026
    assert items[0].published_at.month == 2
    assert items[0].published_at.day == 24
    assert items[0].published_at.hour == 9

    assert items[1].title == "另一条AI大模型新闻"
    assert items[1].published_at is not None
    assert items[1].published_at.day == 23
    assert items[1].published_at.hour == 18


def test_collect_html_without_date_config_drop_when_article_has_no_date() -> None:
    """无日期配置且文章页也无日期时，条目会被丢弃。"""
    html = """
    <html><body>
    <a href="https://example.com/p/123.html">智东西AI新闻测试</a>
    </body></html>
    """
    source = SourceConfig(
        name="test_no_date",
        type="html",
        url="https://example.com/",
        article_selector="a[href*='/p/']",
        link_pattern=r"example\.com/p/",
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None

        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 0


def test_collect_html_fallback_article_page_date() -> None:
    """列表页无日期时，回源文章页提取发布时间。"""
    list_html = """
    <html><body>
    <a href="https://example.com/p/123.html">智东西AI新闻测试</a>
    </body></html>
    """
    article_html = """
    <html><head>
      <meta property="article:published_time" content="2026-02-24T09:30:00+08:00" />
    </head><body>正文</body></html>
    """
    source = SourceConfig(
        name="test_fallback_article_date",
        type="html",
        url="https://example.com/",
        article_selector="a[href*='/p/']",
        link_pattern=r"example\.com/p/",
    )

    with patch("app.collector.httpx.Client") as mock_client:
        client = mock_client.return_value.__enter__.return_value

        class _Resp:
            def __init__(self, text: str) -> None:
                self.text = text

            @staticmethod
            def raise_for_status() -> None:
                return None

        client.get.side_effect = [_Resp(list_html), _Resp(article_html)]
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026
    assert items[0].published_at.month == 2
    assert items[0].published_at.day == 24
