import json

import pytest

from feishu_io import cli


class FakeClient:
    calls = []
    recv_responses = []

    def __init__(
        self, base_url=None, api_key=None, *, timeout=10.0, config_path=None
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.config_path = config_path

    def send_markdown(self, text, id):
        self.calls.append(("send", id, text))
        return {"ok": True, "id": id}

    def recv_unread_response(self, id, *, limit=100, ack=True):
        self.calls.append(("recv", id, limit, ack))
        if self.recv_responses:
            return self.recv_responses.pop(0)
        return {"ok": True, "id": id, "messages": []}

    def ack_messages(self, id, message_ids, *, lease_token):
        self.calls.append(("ack", id, message_ids, lease_token))
        return {"ok": True, "id": id, "acked": len(message_ids)}

    def health(self):
        return {"ok": True}

    def ready(self):
        return {"ok": True, "checks": {}}

    def cleanup(self):
        return {
            "ok": True,
            "delivered_messages_deleted": 2,
            "processed_messages_deleted": 1,
        }


def test_cli_send_reads_text_argument(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--url", "http://x", "--key", "k", "send", "test", "**hi**"])

    assert exit_code == 0
    assert FakeClient.calls == [("send", "test", "**hi**")]
    output = capsys.readouterr().out
    assert output == '{"ok":true}\n'


def test_cli_recv_no_ack(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--key", "k", "recv", "test", "--limit", "2", "--no-ack"])

    assert exit_code == 0
    assert FakeClient.calls == [("recv", "test", 2, False)]
    assert json.loads(capsys.readouterr().out)["messages"] == []


def test_cli_recv_prints_only_agent_fields_and_hoists_lease_token(
    monkeypatch, capsys
):
    FakeClient.calls = []
    FakeClient.recv_responses = [
        {
            "ok": True,
            "id": "test",
            "ack_required": True,
            "messages": [
                {
                    "message_id": 7,
                    "external_message_id": "om_internal",
                    "id": "test",
                    "sender_id": "ou_internal",
                    "sender_name": "Rick",
                    "message_type": "text",
                    "text": "continue",
                    "raw": {"large": "payload"},
                    "created_at": "2026-07-20 10:00:00",
                    "lease_token": "a" * 32,
                }
            ],
        }
    ]
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--key", "k", "recv", "test", "--no-ack"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "messages": [
            {
                "message_id": 7,
                "sender_name": "Rick",
                "text": "continue",
                "created_at": "2026-07-20 10:00:00",
            }
        ],
        "lease_token": "a" * 32,
    }


def test_cli_full_preserves_complete_server_response(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.recv_responses = [
        {
            "ok": True,
            "id": "test",
            "messages": [{"message_id": 1, "raw": {"event": "complete"}}],
            "ack_required": False,
        }
    ]
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--key", "k", "--full", "recv", "test"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "id": "test",
        "messages": [{"message_id": 1, "raw": {"event": "complete"}}],
        "ack_required": False,
    }


def test_cli_recv_keeps_non_text_type(monkeypatch, capsys):
    FakeClient.recv_responses = [
        {
            "messages": [
                {
                    "message_id": 8,
                    "message_type": "image",
                    "text": "[image]",
                }
            ]
        }
    ]
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    assert cli.main(["--key", "k", "recv", "test"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "messages": [
            {"message_id": 8, "text": "[image]", "message_type": "image"}
        ]
    }


@pytest.mark.parametrize("command", ["health", "ready"])
def test_cli_compacts_service_status(command, monkeypatch, capsys):
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    assert cli.main(["--key", "k", command]) == 0

    assert capsys.readouterr().out == '{"ok":true}\n'


def test_cli_cleanup_keeps_only_deleted_counts(monkeypatch, capsys):
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    assert cli.main(["--key", "k", "cleanup"]) == 0

    assert capsys.readouterr().out == (
        '{"delivered_messages_deleted":2,"processed_messages_deleted":1}\n'
    )


def test_cli_ack(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    lease_token = "a" * 32
    exit_code = cli.main(
        ["--key", "k", "ack", "test", lease_token, "1", "2"]
    )

    assert exit_code == 0
    assert FakeClient.calls == [("ack", "test", [1, 2], lease_token)]
    assert capsys.readouterr().out == '{"acked":2}\n'


def test_cli_configure_then_uses_saved_config(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "client.json"
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    configured = cli.main(
        [
            "--config",
            str(config_path),
            "configure",
            "https://feishu.example.com/",
            "--api-key",
            "secret",
        ]
    )
    sent = cli.main(
        ["--config", str(config_path), "send", "test", "hello"]
    )

    assert configured == 0
    assert sent == 0
    assert FakeClient.calls[-1] == ("send", "test", "hello")
    assert json.loads(config_path.read_text()) == {
        "url": "https://feishu.example.com",
        "api_key": "secret",
    }
    assert "secret" not in capsys.readouterr().out


def test_cli_recv_waits_until_messages_arrive(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.recv_responses = [
        {"ok": True, "id": "test", "messages": []},
        {"ok": True, "id": "test", "messages": [{"message_id": 1}]},
    ]
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)
    monkeypatch.setattr(cli.time, "monotonic", iter([0.0, 0.1]).__next__)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = cli.main(
        ["--key", "k", "recv", "test", "--wait", "10", "--interval", "1"]
    )

    assert exit_code == 0
    assert FakeClient.calls == [
        ("recv", "test", 100, True),
        ("recv", "test", 100, True),
    ]
    assert json.loads(capsys.readouterr().out)["messages"][0]["message_id"] == 1


def test_cli_recv_wait_returns_empty_response_at_deadline(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.recv_responses = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)
    monkeypatch.setattr(cli.time, "monotonic", iter([0.0, 1.0]).__next__)
    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("unexpected sleep")),
    )

    exit_code = cli.main(["--key", "k", "recv", "test", "--wait", "1"])

    assert exit_code == 0
    assert FakeClient.calls == [("recv", "test", 100, True)]
    assert json.loads(capsys.readouterr().out)["messages"] == []


def test_cli_rejects_invalid_wait_interval_before_receiving(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(
        ["--key", "k", "recv", "test", "--wait", "1", "--interval", "0"]
    )

    assert exit_code == 1
    assert FakeClient.calls == []
    assert "--interval must be greater than 0" in capsys.readouterr().err
