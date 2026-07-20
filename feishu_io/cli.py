from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from feishu_io.client import DEFAULT_BASE_URL, FeishuIO, FeishuIOError
from feishu_io.client_config import (
    client_config_path,
    load_client_config,
    save_client_config,
)


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _compact_messages(data: dict[str, Any]) -> dict[str, Any]:
    messages = []
    lease_tokens: set[str] = set()
    for message in data.get("messages") or []:
        compact = {
            key: message[key]
            for key in ("message_id", "sender_name", "text", "created_at")
            if message.get(key) is not None
        }
        if message.get("message_type") not in (None, "text"):
            compact["message_type"] = message["message_type"]
        if message.get("lease_token"):
            lease_tokens.add(message["lease_token"])
        messages.append(compact)

    result: dict[str, Any] = {"messages": messages}
    if len(lease_tokens) == 1:
        result["lease_token"] = lease_tokens.pop()
    return result


def _compact_response(command: str, data: dict[str, Any]) -> dict[str, Any]:
    if command == "recv":
        return _compact_messages(data)
    if command == "ack":
        return {"acked": data.get("acked", 0)}
    if command == "cleanup":
        return {
            key: value
            for key, value in data.items()
            if key.endswith("_deleted")
        }
    if command in {"send", "health", "ready"}:
        return {"ok": bool(data.get("ok"))}
    return data


def _print_response(command: str, data: dict[str, Any], *, full: bool) -> None:
    _print_json(data if full else _compact_response(command, data))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feishu-ioctl",
        description="Small client for a running FeiShuIO service.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help=f"FeiShuIO base URL. Defaults to FEISHU_IO_URL or {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="API key. Defaults to FEISHU_IO_API_KEY.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Client config path. Defaults to FEISHU_IO_CONFIG or "
            "~/.config/feishu-io/client.json."
        ),
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the complete server response instead of the compact Agent view.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    send = subparsers.add_parser("send", help="Send markdown to a bound group id.")
    send.add_argument("id")
    send.add_argument(
        "text",
        nargs="?",
        help="Markdown text. If omitted or '-', read from stdin.",
    )

    recv = subparsers.add_parser("recv", help="Receive unread messages.")
    recv.add_argument("id")
    recv.add_argument("--limit", type=int, default=100)
    recv.add_argument(
        "--no-ack",
        action="store_true",
        help="Lease messages instead of immediately marking them delivered.",
    )
    recv.add_argument(
        "--wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Wait up to this many seconds for at least one message.",
    )
    recv.add_argument(
        "--interval",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Polling interval used with --wait (default: 2).",
    )

    ack = subparsers.add_parser("ack", help="Acknowledge leased message ids.")
    ack.add_argument("id")
    ack.add_argument("lease_token", help="Lease token returned by recv --no-ack.")
    ack.add_argument("message_ids", nargs="+", type=int)

    subparsers.add_parser("health", help="Call /health.")
    subparsers.add_parser("ready", help="Call /ready.")
    subparsers.add_parser("cleanup", help="Run maintenance cleanup.")

    configure = subparsers.add_parser(
        "configure", help="Save the server URL and API key for later commands."
    )
    configure.add_argument("server_url")
    key_input = configure.add_mutually_exclusive_group()
    key_input.add_argument("--api-key", dest="configured_key")
    key_input.add_argument(
        "--key-stdin",
        action="store_true",
        help="Read the API key from stdin instead of the command line.",
    )
    subparsers.add_parser("config", help="Show the active client config without the key.")

    return parser


def _recv_with_wait(client: FeishuIO, args: argparse.Namespace) -> dict[str, Any]:
    if args.wait < 0:
        raise ValueError("--wait must be at least 0")
    if args.interval <= 0:
        raise ValueError("--interval must be greater than 0")

    deadline = time.monotonic() + args.wait
    while True:
        response = client.recv_unread_response(
            args.id,
            limit=args.limit,
            ack=not args.no_ack,
        )
        if response.get("messages") or args.wait == 0:
            return response
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return response
        time.sleep(min(args.interval, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "configure":
            configured_key = args.configured_key
            if args.key_stdin:
                configured_key = sys.stdin.read().strip()
            configured_key = configured_key or os.getenv("FEISHU_IO_API_KEY")
            if not configured_key:
                raise ValueError(
                    "API key is required; use --api-key, --key-stdin, or FEISHU_IO_API_KEY"
                )
            path = save_client_config(
                url=args.server_url,
                api_key=configured_key,
                path=args.config,
            )
            _print_json(
                {
                    "ok": True,
                    "path": str(path),
                    "url": load_client_config(path).url,
                }
            )
            return 0
        if args.command == "config":
            stored = load_client_config(args.config)
            _print_json(
                {
                    "path": str(client_config_path(args.config)),
                    "url": (
                        args.url
                        or os.getenv("FEISHU_IO_URL")
                        or stored.url
                        or DEFAULT_BASE_URL
                    ),
                    "api_key_configured": bool(
                        args.key or os.getenv("FEISHU_IO_API_KEY") or stored.api_key
                    ),
                }
            )
            return 0

        client = FeishuIO(
            args.url,
            args.key,
            timeout=args.timeout,
            config_path=args.config,
        )
        if args.command == "send":
            text = args.text
            if text is None or text == "-":
                text = sys.stdin.read()
            _print_response("send", client.send_markdown(text, args.id), full=args.full)
        elif args.command == "recv":
            _print_response("recv", _recv_with_wait(client, args), full=args.full)
        elif args.command == "ack":
            _print_response(
                "ack",
                client.ack_messages(
                    args.id,
                    args.message_ids,
                    lease_token=args.lease_token,
                ),
                full=args.full,
            )
        elif args.command == "health":
            _print_response("health", client.health(), full=args.full)
        elif args.command == "ready":
            _print_response("ready", client.ready(), full=args.full)
        elif args.command == "cleanup":
            _print_response("cleanup", client.cleanup(), full=args.full)
        else:
            parser.error(f"unknown command: {args.command}")
    except (FeishuIOError, ValueError) as exc:
        print(f"feishu-ioctl: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
