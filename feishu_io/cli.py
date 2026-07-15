from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from feishu_io.client import DEFAULT_BASE_URL, FeishuIO, FeishuIOError


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


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
    parser.add_argument("--timeout", type=float, default=10.0)

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

    ack = subparsers.add_parser("ack", help="Acknowledge leased message ids.")
    ack.add_argument("id")
    ack.add_argument("lease_token", help="Lease token returned by recv --no-ack.")
    ack.add_argument("message_ids", nargs="+", type=int)

    subparsers.add_parser("health", help="Call /health.")
    subparsers.add_parser("ready", help="Call /ready.")
    subparsers.add_parser("cleanup", help="Run maintenance cleanup.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        client = FeishuIO(args.url, args.key, timeout=args.timeout)
        if args.command == "send":
            text = args.text
            if text is None or text == "-":
                text = sys.stdin.read()
            _print_json(client.send_markdown(text, args.id))
        elif args.command == "recv":
            _print_json(
                client.recv_unread_response(
                    args.id,
                    limit=args.limit,
                    ack=not args.no_ack,
                )
            )
        elif args.command == "ack":
            _print_json(
                client.ack_messages(
                    args.id,
                    args.message_ids,
                    lease_token=args.lease_token,
                )
            )
        elif args.command == "health":
            _print_json(client.health())
        elif args.command == "ready":
            _print_json(client.ready())
        elif args.command == "cleanup":
            _print_json(client.cleanup())
        else:
            parser.error(f"unknown command: {args.command}")
    except (FeishuIOError, ValueError) as exc:
        print(f"feishu-ioctl: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
