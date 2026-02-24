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
