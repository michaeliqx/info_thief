from app.models import SourceConfig
from app.rsshub_bootstrap import ensure_rsshub_for_sources


def test_ensure_rsshub_for_local_source(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr("app.rsshub_bootstrap.Path.exists", lambda _self: True)

    def _fake_run(cmd, check, env):
        calls.append((cmd, check, env.get("RSSHUB_HOST"), env.get("RSSHUB_PORT")))
        return 0

    monkeypatch.setattr("app.rsshub_bootstrap.subprocess.run", _fake_run)

    sources = [
        SourceConfig(name="s1", type="rss", url="http://0.0.0.0:1200/huxiu/search/AI"),
    ]
    ensure_rsshub_for_sources(sources)

    assert len(calls) == 1
    assert calls[0][0] == ["scripts/rsshub/ensure_rsshub.sh"]
    assert calls[0][1] is True
    assert calls[0][2] == "0.0.0.0"
    assert calls[0][3] == "1200"


def test_ensure_rsshub_for_non_local_source(monkeypatch) -> None:
    called = False

    def _fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("app.rsshub_bootstrap.subprocess.run", _fake_run)

    sources = [
        SourceConfig(name="s1", type="rss", url="https://example.com/rss.xml"),
    ]
    ensure_rsshub_for_sources(sources)

    assert called is False
