from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from message_io.client import DEFAULT_BASE_URL, MessageIO, MessageIOError
from message_io.client_config import client_config_path, load_client_config, save_client_config


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _compact_response(command: str, data: dict[str, Any]) -> dict[str, Any]:
    if command == "recv":
        response = {"messages": data.get("messages") or []}
        if data.get("lease_token"):
            response["lease_token"] = data["lease_token"]
        return response
    if command == "ack":
        return {"acked": data.get("acked", 0)}
    if command == "cleanup":
        return {key: value for key, value in data.items() if key.endswith("_deleted")}
    if command == "send":
        return {"ok": bool(data.get("ok")), "sent": int(data.get("sent", 0))}
    if command in {"health", "ready"}:
        return {"ok": bool(data.get("ok"))}
    return data


def _print_response(command: str, data: dict[str, Any], *, full: bool) -> None:
    _print_json(data if full else _compact_response(command, data))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="message-ioctl",
        description="Small client for a running MessageIO service.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help=f"MessageIO base URL. Defaults to MESSAGE_IO_URL or {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--key", default=None, help="API key. Defaults to MESSAGE_IO_API_KEY."
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Client config path. Defaults to MESSAGE_IO_CONFIG or "
            "~/.config/message-io/client.json."
        ),
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the complete platform-neutral server response.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    send = subparsers.add_parser("send", help="Send content to a bound target.")
    send.add_argument("target")
    send.add_argument("text", nargs="?", help="Text; if omitted or '-', read stdin.")
    send.add_argument(
        "--type", dest="content_type", choices=("text", "markdown"), default="markdown"
    )

    recv = subparsers.add_parser("recv", help="Receive unread messages.")
    recv.add_argument("target")
    recv.add_argument("--limit", type=int, default=100)
    recv.add_argument("--no-ack", action="store_true")
    recv.add_argument("--wait", type=float, default=0.0, metavar="SECONDS")
    recv.add_argument("--interval", type=float, default=2.0, metavar="SECONDS")

    ack = subparsers.add_parser("ack", help="Acknowledge leased message ids.")
    ack.add_argument("target")
    ack.add_argument("lease_token")
    ack.add_argument("message_ids", nargs="+", type=int)

    subparsers.add_parser("health")
    subparsers.add_parser("ready")
    subparsers.add_parser("cleanup")
    configure = subparsers.add_parser("configure")
    configure.add_argument("server_url")
    key_input = configure.add_mutually_exclusive_group()
    key_input.add_argument("--api-key", dest="configured_key")
    key_input.add_argument("--key-stdin", action="store_true")
    subparsers.add_parser("config")
    return parser


def _recv_with_wait(client: MessageIO, args: argparse.Namespace) -> dict[str, Any]:
    if args.wait < 0:
        raise ValueError("--wait must be at least 0")
    if args.interval <= 0:
        raise ValueError("--interval must be greater than 0")
    deadline = time.monotonic() + args.wait
    while True:
        response = client.receive_response(
            args.target, limit=args.limit, ack=not args.no_ack
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
            configured_key = configured_key or os.getenv("MESSAGE_IO_API_KEY")
            if not configured_key:
                raise ValueError(
                    "API key is required; use --api-key, --key-stdin, or MESSAGE_IO_API_KEY"
                )
            path = save_client_config(
                url=args.server_url, api_key=configured_key, path=args.config
            )
            _print_json({"ok": True, "path": str(path), "url": load_client_config(path).url})
            return 0
        if args.command == "config":
            stored = load_client_config(args.config)
            _print_json(
                {
                    "path": str(client_config_path(args.config)),
                    "url": args.url
                    or os.getenv("MESSAGE_IO_URL")
                    or stored.url
                    or DEFAULT_BASE_URL,
                    "api_key_configured": bool(
                        args.key or os.getenv("MESSAGE_IO_API_KEY") or stored.api_key
                    ),
                }
            )
            return 0

        client = MessageIO(
            args.url, args.key, timeout=args.timeout, config_path=args.config
        )
        if args.command == "send":
            text = args.text
            if text is None or text == "-":
                text = sys.stdin.read()
            _print_response(
                "send",
                client.send(args.target, text, content_type=args.content_type),
                full=args.full,
            )
        elif args.command == "recv":
            _print_response("recv", _recv_with_wait(client, args), full=args.full)
        elif args.command == "ack":
            _print_response(
                "ack",
                client.acknowledge(
                    args.target, args.message_ids, lease_token=args.lease_token
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
    except (MessageIOError, ValueError) as exc:
        print(f"message-ioctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
