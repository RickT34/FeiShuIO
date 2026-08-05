import json

import httpx
import pytest

from message_io.domain import Destination, MessageContent
from message_io.platforms.feishu import (
    FeishuAdapter,
    FeishuError,
    build_message_payload,
    build_reaction_payload,
    extract_message,
)


def adapter(**overrides):
    values = {
        "account_id": "default",
        "app_id": "cli_test",
        "app_secret": "secret",
        "reaction_emoji": "Get",
        "retry_attempts": 2,
        "retry_base_delay": 0,
    }
    values.update(overrides)
    return FeishuAdapter(**values)


def test_markdown_is_adapted_to_feishu_interactive_message():
    payload = build_message_payload(
        conversation_id="oc_1",
        content=MessageContent(type="markdown", text="**hello**"),
        idempotency_key="request-1",
    )

    assert payload["receive_id"] == "oc_1"
    assert payload["msg_type"] == "interactive"
    assert json.loads(payload["content"])["elements"][0]["content"] == "**hello**"
    assert payload["uuid"] == "request-1"


def test_plain_text_is_adapted_without_markdown_card():
    payload = build_message_payload(
        conversation_id="oc_1", content=MessageContent(type="text", text="hello")
    )

    assert payload["msg_type"] == "text"
    assert json.loads(payload["content"]) == {"text": "hello"}


def test_feishu_event_is_normalized_at_adapter_boundary():
    message = extract_message(
        {
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
            }
        },
        account_id="bot-a",
    )

    assert message is not None
    assert message.destination == Destination("feishu", "bot-a", "oc_1")
    assert message.sender.id == "ou_1"
    assert message.content == MessageContent(type="text", text="hello")


def test_unknown_feishu_content_becomes_common_unknown_message():
    message = extract_message(
        {
            "event": {
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "sticker",
                    "content": "{}",
                }
            }
        },
        account_id="default",
    )

    assert message.content == MessageContent(type="unknown", text="[unknown]")


@pytest.mark.asyncio
async def test_retry_safe_request_retries_transient_failure(monkeypatch):
    calls = 0

    async def fake_post(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://open.feishu.cn/test")
        if calls == 1:
            return httpx.Response(500, request=request, text="down")
        return httpx.Response(200, request=request, json={"code": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = await adapter()._post_json(
        "https://open.feishu.cn/test", retry_safe=True
    )

    assert response.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_ambiguous_write_is_not_retried(monkeypatch):
    calls = 0

    async def fake_post(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://open.feishu.cn/test")
        return httpx.Response(500, request=request, text="down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(FeishuError, match="Feishu HTTP 500"):
        await adapter(retry_attempts=3)._post_json("https://open.feishu.cn/test")
    assert calls == 1


@pytest.mark.asyncio
async def test_unsupported_common_outbound_type_fails_at_adapter(monkeypatch):
    client = adapter()
    client._tenant_access_token = "token"
    client._token_expires_at = 10**20

    with pytest.raises(FeishuError, match="cannot send content type"):
        await client.send(
            destination=Destination("feishu", "default", "oc_1"),
            content=MessageContent(type="image", text="[image]"),
        )


def test_reaction_payload_is_provider_private():
    assert build_reaction_payload(emoji_type="Get") == {
        "reaction_type": {"emoji_type": "Get"}
    }
