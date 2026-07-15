from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ClientConfig:
    url: str | None = None
    api_key: str | None = None


def normalize_server_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("server URL must be an absolute http:// or https:// URL")
    if parsed.query or parsed.fragment:
        raise ValueError("server URL must not contain a query string or fragment")
    return normalized


def client_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()

    configured = os.getenv("FEISHU_IO_CONFIG")
    if configured:
        return Path(configured).expanduser()

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "feishu-io" / "client.json"
    return Path.home() / ".config" / "feishu-io" / "client.json"


def load_client_config(
    path: str | os.PathLike[str] | None = None,
) -> ClientConfig:
    config_path = client_config_path(path)
    if not config_path.exists():
        return ClientConfig()

    try:
        payload: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read client config {config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"client config {config_path} must contain a JSON object")

    url = payload.get("url")
    api_key = payload.get("api_key")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"client config {config_path} has an invalid url")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError(f"client config {config_path} has an invalid api_key")
    return ClientConfig(url=url or None, api_key=api_key or None)


def save_client_config(
    *,
    url: str,
    api_key: str,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    config_path = client_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"url": normalize_server_url(url), "api_key": api_key}

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary.name, 0o600)
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(config_path)
        os.chmod(config_path, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return config_path
