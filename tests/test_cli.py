import json

from message_io import cli


class FakeClient:
    calls = []
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    def send(self, target, text, *, content_type="markdown"):
        self.calls.append(("send", target, content_type, text))
        return {"ok": True, "target": target, "sent": 1}

    def receive_response(self, target, *, limit=100, ack=True):
        self.calls.append(("receive", target, limit, ack))
        if self.responses:
            return self.responses.pop(0)
        return {"ok": True, "target": target, "messages": []}

    def acknowledge(self, target, message_ids, *, lease_token):
        self.calls.append(("ack", target, message_ids, lease_token))
        return {"ok": True, "target": target, "acked": len(message_ids)}

    def health(self):
        return {"ok": True}

    def ready(self):
        return {"ok": True, "checks": {}}

    def cleanup(self):
        return {"ok": True, "delivered_messages_deleted": 2}


def test_send_supports_common_content_type(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "MessageIO", FakeClient)

    assert cli.main(["--key", "k", "send", "ops", "hello", "--type", "text"]) == 0
    assert FakeClient.calls == [("send", "ops", "text", "hello")]
    assert capsys.readouterr().out == '{"ok":true,"sent":1}\n'


def test_receive_preserves_common_shape_and_hoists_lease(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.responses = [
        {
            "messages": [
                {
                    "message_id": 7,
                    "sender": {"id": "u1", "name": "Rick"},
                    "content": {"type": "text", "text": "continue"},
                    "received_at": "2026-08-05 10:00:00",
                }
            ],
            "lease_token": "a" * 32,
        }
    ]
    monkeypatch.setattr(cli, "MessageIO", FakeClient)

    assert cli.main(["--key", "k", "recv", "ops", "--no-ack"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "messages": [
            {
                "message_id": 7,
                "sender": {"id": "u1", "name": "Rick"},
                "content": {"type": "text", "text": "continue"},
                "received_at": "2026-08-05 10:00:00",
            }
        ],
        "lease_token": "a" * 32,
    }


def test_ack_outputs_count(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "MessageIO", FakeClient)

    assert cli.main(["--key", "k", "ack", "ops", "a" * 32, "1", "2"]) == 0
    assert FakeClient.calls == [("ack", "ops", [1, 2], "a" * 32)]
    assert capsys.readouterr().out == '{"acked":2}\n'


def test_receive_waits_until_a_message_arrives(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.responses = [
        {"messages": []},
        {"messages": [{"message_id": 1, "sender": {}, "content": {"type": "text", "text": "go"}, "received_at": "now"}]},
    ]
    monkeypatch.setattr(cli, "MessageIO", FakeClient)
    monkeypatch.setattr(cli.time, "monotonic", iter([0.0, 0.1]).__next__)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    assert cli.main(["--key", "k", "recv", "ops", "--wait", "10"]) == 0
    assert len(FakeClient.calls) == 2
    assert json.loads(capsys.readouterr().out)["messages"][0]["message_id"] == 1
