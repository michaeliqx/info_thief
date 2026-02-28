import asyncio

import httpx

from app.models import Settings
from app.server import app
from app.wecom import WecomCrypto


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


def test_wecom_url_verification_endpoint(monkeypatch) -> None:
    settings = Settings(
        timezone="Asia/Shanghai",
        wecom_enabled=True,
        wecom_corp_id="ww1234567890",
        wecom_agent_id="1000002",
        wecom_secret="secret_xxx",
        wecom_token="token_xxx",
        wecom_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    )
    crypto = WecomCrypto(settings.wecom_token, settings.wecom_encoding_aes_key, settings.wecom_corp_id)
    encrypted_xml = crypto.encrypt("verify-me", nonce="nonce-1", timestamp="1710000000")
    echostr = encrypted_xml.split("<Encrypt><![CDATA[", 1)[1].split("]]></Encrypt>", 1)[0]
    signature = encrypted_xml.split("<MsgSignature><![CDATA[", 1)[1].split("]]></MsgSignature>", 1)[0]

    monkeypatch.setattr("app.server.load_settings", lambda: settings)

    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(
                "/wecom/events",
                params={
                    "msg_signature": signature,
                    "timestamp": "1710000000",
                    "nonce": "nonce-1",
                    "echostr": echostr,
                },
            )

    resp = asyncio.run(_run())

    assert resp.status_code == 200
    assert resp.text == "verify-me"
