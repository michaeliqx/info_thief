"""Collector 单元测试，含 HTML 源发布时间解析。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

from app.collector import (
    _extract_sogou_redirect_url,
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


def test_parse_html_date_unix_timestamp() -> None:
    """解析 10 位 Unix 时间戳（用于 timeConvert）。"""
    result = _parse_html_date("timeConvert('1771988408')", regex=r"(?<=timeConvert\(')\d{10}(?='\))")
    assert result is not None
    expected = datetime.fromtimestamp(1771988408, tz=timezone.utc)
    assert result == expected


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


def test_collect_html_date_selector_prefers_nearest_date() -> None:
    """同一容器有多个时间标签时，优先绑定离链接最近的时间。"""
    html = """
    <html><body>
      <div class="list">
        <div class="card"><a href="/a/1">第一条AI新闻标题内容</a></div>
        <div class="meta"><time datetime="2026-02-24T09:00:00+08:00"></time></div>
        <div class="card"><a href="/a/2">第二条AI新闻标题内容</a></div>
        <div class="meta"><time datetime="2026-01-25T08:30:00+08:00"></time></div>
      </div>
    </body></html>
    """
    source = SourceConfig(
        name="test_nearest_date",
        type="html",
        url="https://example.com/",
        article_selector="div.card a",
        link_pattern=r"/a/",
        date_selector="time",
        date_attr="datetime",
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 2
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026
    assert items[0].published_at.month == 2
    assert items[0].published_at.day == 24
    assert items[1].published_at is not None
    assert items[1].published_at.year == 2026
    assert items[1].published_at.month == 1
    assert items[1].published_at.day == 25


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


def test_collect_html_container_fallback_to_next_valid_anchor() -> None:
    """容器内第一个链接无标题时，应继续尝试后续链接。"""
    html = """
    <html><body>
      <div class="item">
        <a href="/p/1"><img src="/x.png" /></a>
        <a href="/p/1">OpenAI 发布新模型能力更新</a>
        <span class="time">2026-02-25 10:00</span>
      </div>
    </body></html>
    """
    source = SourceConfig(
        name="test_container_next_anchor",
        type="html",
        url="https://example.com/",
        article_selector="a[href*='/p/']",
        link_pattern=r"/p/[0-9]+",
        item_container_selector="div.item",
        date_selector="span.time",
        date_regex=r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}",
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "OpenAI 发布新模型能力更新"
    assert items[0].url == "https://example.com/p/1"


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


def test_collect_html_fallback_article_page_ignores_relative_words_in_body() -> None:
    """文章正文里的“今天”不应被误判为发布时间。"""
    list_html = """
    <html><body>
    <a href="https://example.com/p/123.html">智东西AI新闻测试</a>
    </body></html>
    """
    article_html = """
    <html><body>
      今天我们继续讨论一个旧议题，这里没有发布时间元数据。
    </body></html>
    """
    source = SourceConfig(
        name="test_article_today_word",
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

    assert items == []


def test_collect_html_split_source_by_publisher() -> None:
    """聚合源可按正文发布方拆分 source_name。"""
    html = """
    <html><body>
    <article class="news-item">
      <a href="/newDetail.html?newId=101">Anthropic一条推文，引发了全球AI圈的群嘲</a>
      <div class="summary">本文来自微信公众号： 数字生命卡兹克 ，作 者：数字生命卡兹克</div>
      <div style="text-align: right;">2026-02-25 11:20</div>
    </article>
    </body></html>
    """
    source = SourceConfig(
        name="AITNT AI资讯(聚合源)",
        type="html",
        url="https://www.aitntnews.com/",
        article_selector="a[href*='newDetail']",
        link_pattern=r"newDetail\.html\?newId=",
        item_container_selector="article.news-item",
        date_selector="div[style*='text-align: right']",
        date_regex=r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}",
        split_source_by_publisher=True,
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].source_name == "AITNT AI资讯(聚合源)/数字生命卡兹克"


def test_collect_rss_filters_by_required_keywords_any() -> None:
    """搜索型 RSS 源可通过关键词约束避免误命中。"""
    rss = """
    <rss version="2.0">
      <channel>
        <title>test</title>
        <item>
          <title>无关条目</title>
          <link>https://example.com/1</link>
          <description>本文来自微信公众号：其他号</description>
          <pubDate>Wed, 25 Feb 2026 01:00:00 GMT</pubDate>
        </item>
        <item>
          <title>目标条目</title>
          <link>https://example.com/2</link>
          <description>本文来自微信公众号：目标号</description>
          <pubDate>Wed, 25 Feb 2026 02:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    source = SourceConfig(
        name="test_rss_keywords",
        type="rss",
        url="https://example.com/rss.xml",
        required_keywords_any=["目标号"],
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = rss
        mock_resp.raise_for_status = lambda: None
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "目标条目"
    assert items[0].url == "https://example.com/2"


def test_collect_rss_filters_by_required_author_keywords_any() -> None:
    """RSS 源可按作者关键词过滤为指定账号。"""
    rss = """
    <rss version="2.0">
      <channel>
        <title>test</title>
        <item>
          <title>条目A</title>
          <link>https://example.com/a</link>
          <author>其他作者</author>
          <description>本文来自微信公众号：目标号</description>
          <pubDate>Wed, 25 Feb 2026 01:00:00 GMT</pubDate>
        </item>
        <item>
          <title>条目B</title>
          <link>https://example.com/b</link>
          <author>目标号©</author>
          <description>本文来自微信公众号：目标号</description>
          <pubDate>Wed, 25 Feb 2026 02:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    source = SourceConfig(
        name="test_rss_author_keywords",
        type="rss",
        url="https://example.com/rss.xml",
        required_author_keywords_any=["目标号"],
    )

    with patch("app.collector.httpx.Client") as mock_client:
        mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
        mock_resp.text = rss
        mock_resp.raise_for_status = lambda: None
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "条目B"
    assert items[0].url == "https://example.com/b"


def test_extract_sogou_redirect_url() -> None:
    """可从搜狗跳转页脚本还原真实文章 URL。"""
    html = """
    <script>
      var url = '';
      url += 'https://mp.';
      url += 'weixin.qq.c';
      url += 'om/s?src=11';
      url += '&timestamp=1771991109';
      window.location.replace(url)
    </script>
    """
    resolved = _extract_sogou_redirect_url(html)
    assert resolved == "https://mp.weixin.qq.com/s?src=11&timestamp=1771991109"


def test_collect_html_with_sogou_redirect_and_author_filter() -> None:
    """搜狗源可按公众号名过滤，并把 /link 还原为稳定微信链接。"""
    list_html = """
    <html><body>
      <ul class="news-list">
        <li>
          <h3><a href="/link?url=abc123">目标文章标题 AI 进展</a></h3>
          <div class="s-p">
            <span class="all-time-y2">数字生命卡兹克</span>
            <span class="s2"><script>document.write(timeConvert('1771988408'))</script></span>
          </div>
        </li>
        <li>
          <h3><a href="/link?url=xyz789">无关文章标题</a></h3>
          <div class="s-p">
            <span class="all-time-y2">其他公众号</span>
            <span class="s2"><script>document.write(timeConvert('1771988408'))</script></span>
          </div>
        </li>
      </ul>
    </body></html>
    """
    sogou_jump_html = """
    <script>
      var url = '';
      url += 'https://mp.';
      url += 'weixin.qq.com';
      url += '/s?src=11';
      window.location.replace(url)
    </script>
    """
    wechat_article_html = """
    <script>
      var biz = "MzIyMzA5NjEyMA==" || "";
      var mid = "2647680087" || "";
      var idx = "1" || "";
      var sn = "04eb19f9821183082a1ce82c828912fb" || "";
    </script>
    """
    source = SourceConfig(
        name="test_sogou_wechat",
        type="html",
        url="https://weixin.sogou.com/weixin?type=2&query=%E6%95%B0%E5%AD%97",
        article_selector="ul.news-list li h3 a",
        link_pattern=r"/link\?url=",
        item_container_selector="ul.news-list li",
        date_selector="span.s2 script",
        date_regex=r"(?<=timeConvert\(')\d{10}(?='\))",
        author_selector="span.all-time-y2",
        required_author_keywords_any=["数字生命卡兹克"],
        resolve_sogou_redirect=True,
    )

    with patch("app.collector.httpx.Client") as mock_client:
        client = mock_client.return_value.__enter__.return_value

        class _Resp:
            def __init__(self, text: str) -> None:
                self.text = text
                self.url = "https://example.com"

            @staticmethod
            def raise_for_status() -> None:
                return None

        class _RespUrl(_Resp):
            def __init__(self, text: str, url: str) -> None:
                super().__init__(text)
                self.url = url

        client.get.side_effect = [
            _Resp(list_html),
            _Resp(sogou_jump_html),
            _RespUrl(wechat_article_html, "https://mp.weixin.qq.com/s?src=11"),
        ]
        items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "目标文章标题 AI 进展"
    assert items[0].url == (
        "https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA=="
        "&mid=2647680087&idx=1&sn=04eb19f9821183082a1ce82c828912fb#rd"
    )
    assert "数字生命卡兹克" in items[0].content


def test_collect_wechat_profile_with_cookie() -> None:
    """wechat_profile 源在提供 Cookie 时可解析列表返回条目。"""
    source = SourceConfig(
        name="test_wechat_profile",
        type="wechat_profile",
        url="https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=MzAxNTYwMzcyNw==#wechat_redirect",
        wechat_biz="MzAxNTYwMzcyNw==",
        required_keywords_any=["MindCode"],
    )
    payload = {
        "ret": 0,
        "general_msg_list": {
            "list": [
                {
                    "comm_msg_info": {"datetime": 1771246005},
                    "app_msg_ext_info": {
                        "title": "马年开局:做骑马的人,别做被AI牵的马 | MindCode 周报No.61",
                        "content_url": "/s/FZ7L0b1frNHNL1PJ6eBcNQ",
                        "digest": "MindCode 最新周报",
                    },
                }
            ]
        },
    }

    with patch("app.collector.httpx.Client") as mock_client:
        client = mock_client.return_value.__enter__.return_value

        class _Resp:
            text = ""

            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json():
                return payload

        client.get.return_value = _Resp()

        with patch.dict(os.environ, {"WECHAT_COOKIE": "mock-cookie"}, clear=False):
            items = collect_from_source(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title.startswith("马年开局")
    assert items[0].url == "https://mp.weixin.qq.com/s/FZ7L0b1frNHNL1PJ6eBcNQ"
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026


def test_collect_html_nearby_date_ignores_relative_words_in_title() -> None:
    """标题中的“刚刚”不应被当作发布时间。"""
    list_html = """
    <html><body>
      <div><a href="https://example.com/p/1">刚刚，春节杀手锏“源神”登场！</a></div>
    </body></html>
    """
    article_html = "<html><body>正文无日期</body></html>"
    source = SourceConfig(
        name="test_nearby_relative_title",
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

    assert items == []
