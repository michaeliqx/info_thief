import yaml

from app.config import load_sources


def test_load_sources_resolves_inline_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RSSHUB_BASE_URL", "http://127.0.0.1:1200")
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "s1",
                        "type": "rss",
                        "url": "${RSSHUB_BASE_URL}/huxiu/search/AI",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    sources = load_sources(str(sources_path))
    assert sources[0].url == "http://127.0.0.1:1200/huxiu/search/AI"


def test_load_sources_resolves_env_default_value(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RSSHUB_BASE_URL", raising=False)
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "s1",
                        "type": "rss",
                        "url": "${RSSHUB_BASE_URL:-http://0.0.0.0:1200}/huxiu/search/AI",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    sources = load_sources(str(sources_path))
    assert sources[0].url == "http://0.0.0.0:1200/huxiu/search/AI"


def test_load_sources_reads_local_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RSSHUB_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("RSSHUB_BASE_URL=http://127.0.0.1:1200\n", encoding="utf-8")

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    sources_path = config_dir / "sources.yaml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "s1",
                        "type": "rss",
                        "url": "${RSSHUB_BASE_URL}/huxiu/search/AI",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    sources = load_sources(str(sources_path))
    assert sources[0].url == "http://127.0.0.1:1200/huxiu/search/AI"


def test_new_sources_ai_camp_caili_andy_xsignal(tmp_path, monkeypatch) -> None:
    """验证 AI科技大本营、蔡荔谈AI、Andy730、Xsignal 四个新源配置可正确加载"""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    sources_path = config_dir / "sources.yaml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "AI科技大本营(搜狗微信检索)",
                        "type": "html",
                        "url": "https://weixin.sogou.com/weixin?type=2&query=AI%E7%A7%91%E6%8A%80%E5%A4%A7%E6%9C%AC%E8%90%A5",
                        "required_author_keywords_any": ["AI科技大本营"],
                        "enabled": True,
                    },
                    {
                        "name": "蔡荔谈AI(搜狗微信检索)",
                        "type": "html",
                        "url": "https://weixin.sogou.com/weixin?type=2&query=%E8%94%A1%E8%8D%94%E8%B0%88AI",
                        "required_author_keywords_any": ["蔡荔谈AI"],
                        "enabled": True,
                    },
                    {
                        "name": "Andy730(搜狗微信检索)",
                        "type": "html",
                        "url": "https://weixin.sogou.com/weixin?type=2&query=Andy730",
                        "required_author_keywords_any": ["Andy730"],
                        "enabled": True,
                    },
                    {
                        "name": "Xsignal(虎嗅专栏)",
                        "type": "rss",
                        "url": "${RSSHUB_BASE_URL:-http://0.0.0.0:1200}/huxiu/member/12888029/article",
                        "enabled": True,
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    sources = load_sources(str(sources_path))
    names = {s.name for s in sources}
    assert "AI科技大本营(搜狗微信检索)" in names
    assert "蔡荔谈AI(搜狗微信检索)" in names
    assert "Andy730(搜狗微信检索)" in names
    assert "Xsignal(虎嗅专栏)" in names
