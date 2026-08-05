from __future__ import annotations

import re


ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def normalize_alias(alias: str) -> str:
    return alias.strip()


def parse_bind_command(text: str) -> str | None:
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != "/bind":
        return None
    alias = normalize_alias(parts[1])
    if not ALIAS_RE.fullmatch(alias):
        return None
    return alias
