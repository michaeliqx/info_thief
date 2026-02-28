from dataclasses import dataclass
from datetime import date

from app.models import BriefItem, DailyBrief, Perspective
from app.publisher import push_markdown, render_markdown


@dataclass
class _Resp:
    status_code: int
    errcode: int

    def json(self) -> dict:
        return {"errcode": self.errcode}


class _FakeClient:
    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = responses
        self.calls = 0

    def post(self, _url: str, json: dict) -> _Resp:  # noqa: A002
        self.calls += 1
        assert "msgtype" in json
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]

    def close(self) -> None:
        return None


def test_push_markdown_retry_until_success() -> None:
    client = _FakeClient(
        [
            _Resp(status_code=500, errcode=-1),
            _Resp(status_code=200, errcode=1),
            _Resp(status_code=200, errcode=0),
        ]
    )

    sleeps: list[float] = []
    ok = push_markdown(
        webhook="https://example.com/webhook",
        content="hello",
        retries=(1, 2),
        sleep_fn=lambda x: sleeps.append(x),
        client=client,
    )

    assert ok is True
    assert client.calls == 3
    assert sleeps == [1, 2]


def test_render_markdown_uses_insight() -> None:
    brief = DailyBrief(
        date=date.today(),
        title="AI 每日情报 | 测试",
        intro="今日导语",
        items=[
            BriefItem(
                perspective=Perspective.PRODUCT,
                title="测试条目",
                key_points=["要点1", "要点2"],
                source_name="测试源",
                url="https://example.com",
                score=1.0,
                insight="该技术可降低推理成本，值得关注落地进展。",
            )
        ],
        observations=["跨条观察"],
    )
    md = render_markdown(brief)
    assert "insight" in md
    assert "该技术可降低推理成本" in md
    assert "意义" not in md
    assert "立场观点" not in md
