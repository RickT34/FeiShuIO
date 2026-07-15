import sys

import pytest

from feishu_io import server
from feishu_io import server_cli


def test_server_cli_passes_runtime_options_to_uvicorn(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "feishu-io-server",
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
            ("feishu_io.server:app",),
            {
                "host": "127.0.0.1",
                "port": 8765,
                "log_level": "warning",
                "reload": False,
            },
        )
    ]


def test_client_only_install_explains_missing_server_dependencies(monkeypatch):
    real_import = __import__

    def import_without_server_dependencies(name, *args, **kwargs):
        if name == "feishu_io.server":
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_server_dependencies)

    with pytest.raises(SystemExit, match=r"pip install 'feishu-io\[server\]'"):
        server_cli.main()
