import json

import httpx
import pytest

from feishu_io.feishu import FeishuAppClient, FeishuAppError
from feishu_io.feishu import build_markdown_message_payload, build_reaction_payload


def test_build_markdown_message_payload_uses_string_content():
    payload = build_markdown_message_payload(
        chat_id="oc_1",
        text="**hello**",
        idempotency_key="request-1",
    )

    assert payload["receive_id"] == "oc_1"
    assert payload["msg_type"] == "interactive"
    assert isinstance(payload["content"], str)
    assert json.loads(payload["content"])["elements"][0]["content"] == "**hello**"
    assert payload["uuid"] == "request-1"


def test_build_reaction_payload():
    assert build_reaction_payload(emoji_type="Get") == {
        "reaction_type": {"emoji_type": "Get"}
    }


@pytest.mark.asyncio
async def test_post_json_retries_transient_http_errors(monkeypatch):
    calls = 0

    async def fake_sleep(_delay):
        return None

    async def fake_post(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://open.feishu.cn/test")
        if calls == 1:
            return httpx.Response(500, request=request, text="temporarily down")
        return httpx.Response(200, request=request, json={"code": 0})

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = FeishuAppClient(
        app_id="cli_test",
        app_secret="secret",
        retry_attempts=2,
        retry_base_delay=0,
    )

    response = await client._post_json(
        "https://open.feishu.cn/test", retry_safe=True
    )

    assert response.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_post_json_does_not_retry_ambiguous_posts(monkeypatch):
    calls = 0

    async def fake_post(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://open.feishu.cn/test")
        return httpx.Response(500, request=request, text="temporarily down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = FeishuAppClient(
        app_id="cli_test",
        app_secret="secret",
        retry_attempts=3,
        retry_base_delay=0,
    )

    with pytest.raises(FeishuAppError, match="Feishu HTTP 500"):
        await client._post_json("https://open.feishu.cn/test")

    assert calls == 1


@pytest.mark.asyncio
async def test_send_markdown_uses_one_idempotency_key_for_retries(monkeypatch):
    requests = []
    client = FeishuAppClient(
        app_id="cli_test",
        app_secret="secret",
        retry_attempts=2,
        retry_base_delay=0,
    )
    client._tenant_access_token = "token"
    client._token_expires_at = 10**20

    async def fake_post(self, url, **kwargs):
        requests.append(kwargs["json"])
        request = httpx.Request("POST", url)
        if len(requests) == 1:
            return httpx.Response(500, request=request, text="temporarily down")
        return httpx.Response(200, request=request, json={"code": 0, "msg": "ok"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await client.send_markdown(chat_id="oc_1", text="hello")

    assert len(requests) == 2
    assert len(requests[0]["uuid"]) == 32
    assert requests[0]["uuid"] == requests[1]["uuid"]


@pytest.mark.asyncio
async def test_post_json_wraps_request_errors(monkeypatch):
    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", "https://open.feishu.cn/test")
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = FeishuAppClient(
        app_id="cli_test",
        app_secret="secret",
        retry_attempts=1,
        retry_base_delay=0,
    )

    with pytest.raises(FeishuAppError, match="Feishu request failed"):
        await client._post_json("https://open.feishu.cn/test")
