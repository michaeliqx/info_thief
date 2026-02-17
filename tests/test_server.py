import asyncio

import httpx

from app.server import app


def test_health_endpoint() -> None:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    resp = asyncio.run(_run())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "time" in body


def test_feishu_url_verification_endpoint() -> None:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/feishu/events",
                json={
                    "type": "url_verification",
                    "challenge": "challenge-token",
                },
            )

    resp = asyncio.run(_run())

    assert resp.status_code == 200
    assert resp.json()["challenge"] == "challenge-token"
