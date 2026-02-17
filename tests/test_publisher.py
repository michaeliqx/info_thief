from dataclasses import dataclass

from app.publisher import push_markdown


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
