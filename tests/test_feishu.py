import json

from feishu_io.feishu import build_markdown_message_payload, build_reaction_payload


def test_build_markdown_message_payload_uses_string_content():
    payload = build_markdown_message_payload(chat_id="oc_1", text="**hello**")

    assert payload["receive_id"] == "oc_1"
    assert payload["msg_type"] == "interactive"
    assert isinstance(payload["content"], str)
    assert json.loads(payload["content"])["elements"][0]["content"] == "**hello**"


def test_build_reaction_payload():
    assert build_reaction_payload(emoji_type="OK") == {
        "reaction_type": {"emoji_type": "OK"}
    }
