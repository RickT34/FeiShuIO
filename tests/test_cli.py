import json

from feishu_io import cli


class FakeClient:
    calls = []

    def __init__(self, base_url=None, api_key=None, *, timeout=10.0):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def send_markdown(self, text, id):
        self.calls.append(("send", id, text))
        return {"ok": True, "id": id}

    def recv_unread_response(self, id, *, limit=100, ack=True):
        self.calls.append(("recv", id, limit, ack))
        return {"ok": True, "id": id, "messages": []}

    def ack_messages(self, id, message_ids):
        self.calls.append(("ack", id, message_ids))
        return {"ok": True, "id": id, "acked": len(message_ids)}

    def health(self):
        return {"ok": True}

    def ready(self):
        return {"ok": True, "checks": {}}

    def cleanup(self):
        return {"ok": True}


def test_cli_send_reads_text_argument(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--url", "http://x", "--key", "k", "send", "test", "**hi**"])

    assert exit_code == 0
    assert FakeClient.calls == [("send", "test", "**hi**")]
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_recv_no_ack(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--key", "k", "recv", "test", "--limit", "2", "--no-ack"])

    assert exit_code == 0
    assert FakeClient.calls == [("recv", "test", 2, False)]
    assert json.loads(capsys.readouterr().out)["messages"] == []


def test_cli_ack(monkeypatch, capsys):
    FakeClient.calls = []
    monkeypatch.setattr(cli, "FeishuIO", FakeClient)

    exit_code = cli.main(["--key", "k", "ack", "test", "1", "2"])

    assert exit_code == 0
    assert FakeClient.calls == [("ack", "test", [1, 2])]
    assert json.loads(capsys.readouterr().out)["acked"] == 2

