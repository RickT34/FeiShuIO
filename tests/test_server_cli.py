import sys

import pytest

from message_io import server
from message_io import server_cli


def test_server_cli_passes_runtime_options_to_uvicorn(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "message-io-server",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--log-level",
            "warning",
        ],
    )
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    server.main()

    assert calls == [
        (
            ("message_io.server:app",),
            {
                "host": "127.0.0.1",
                "port": 8765,
                "log_level": "warning",
                "reload": False,
            },
        )
    ]


def test_server_cli_reads_runtime_options_from_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MESSAGE_IO_API_KEY=test-key",
                "FEISHU_APP_ID=cli_test",
                "FEISHU_APP_SECRET=test-secret",
                "MESSAGE_IO_HOST=127.0.0.1",
                "MESSAGE_IO_PORT=18000",
                "MESSAGE_IO_LOG_LEVEL=warning",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MESSAGE_IO_HOST", raising=False)
    monkeypatch.delenv("MESSAGE_IO_PORT", raising=False)
    monkeypatch.delenv("MESSAGE_IO_LOG_LEVEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["message-io-server"])
    calls = []
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    server.get_settings.cache_clear()

    try:
        server.main()
    finally:
        server.get_settings.cache_clear()

    assert calls == [
        (
            ("message_io.server:app",),
            {
                "host": "127.0.0.1",
                "port": 18000,
                "log_level": "warning",
                "reload": False,
            },
        )
    ]


def test_client_only_install_explains_missing_server_dependencies(monkeypatch):
    real_import = __import__

    def import_without_server_dependencies(name, *args, **kwargs):
        if name == "message_io.server":
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_server_dependencies)

    with pytest.raises(SystemExit, match=r"pip install 'message-io\[server\]'"):
        server_cli.main()
