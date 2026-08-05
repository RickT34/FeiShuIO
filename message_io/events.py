from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

CommandName = Literal["help", "bind", "current", "list", "unbind", "invalid"]


@dataclass(frozen=True)
class UserCommand:
    name: CommandName
    alias: str | None = None


def normalize_alias(alias: str) -> str:
    return alias.strip()


def parse_user_command(text: str) -> UserCommand | None:
    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0]
    if command == "/help":
        return UserCommand("help" if len(parts) == 1 else "invalid")
    if command == "/bind":
        if len(parts) == 1:
            return UserCommand("current")
        if len(parts) == 2:
            alias = normalize_alias(parts[1])
            if ALIAS_RE.fullmatch(alias):
                return UserCommand("bind", alias)
        return UserCommand("invalid")
    if command == "/binds":
        return UserCommand("list" if len(parts) == 1 else "invalid")
    if command == "/unbind":
        return UserCommand("unbind" if len(parts) == 1 else "invalid")
    return None
