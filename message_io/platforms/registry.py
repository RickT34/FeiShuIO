from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from message_io.platforms.base import PlatformAdapter, PlatformListener


@dataclass(frozen=True)
class PlatformRuntime:
    adapter: PlatformAdapter
    listener: PlatformListener | None = None


class PlatformRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[tuple[str, str], PlatformRuntime] = {}

    def register(
        self,
        adapter: PlatformAdapter,
        *,
        listener: PlatformListener | None = None,
    ) -> None:
        key = (adapter.platform, adapter.account_id)
        if key in self._runtimes:
            raise ValueError(f"platform adapter already registered: {key[0]}/{key[1]}")
        self._runtimes[key] = PlatformRuntime(adapter=adapter, listener=listener)

    def get(self, platform: str, account_id: str) -> PlatformAdapter:
        runtime = self._runtimes.get((platform, account_id))
        if runtime is None:
            raise KeyError(f"platform adapter is not configured: {platform}/{account_id}")
        return runtime.adapter

    def start_listeners(self) -> None:
        for runtime in self._runtimes.values():
            if runtime.listener is not None:
                runtime.listener.start()

    def stop_listeners(self) -> None:
        for runtime in reversed(list(self._runtimes.values())):
            if runtime.listener is not None:
                runtime.listener.stop()

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            f"{platform}/{account_id}": (
                runtime.listener.status()
                if runtime.listener is not None
                else {"running": False, "connected": True, "mode": "outbound-only"}
            )
            for (platform, account_id), runtime in self._runtimes.items()
        }
