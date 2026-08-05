import pytest

from message_io.platforms.registry import PlatformRegistry


class Adapter:
    def __init__(self, platform, account_id):
        self.platform = platform
        self.account_id = account_id


class Listener:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def stop(self, timeout=5.0):
        self.events.append("stop")

    def status(self):
        return {"running": True, "connected": True}


def test_registry_selects_adapter_by_platform_and_account():
    registry = PlatformRegistry()
    primary = Adapter("feishu", "primary")
    secondary = Adapter("feishu", "secondary")
    registry.register(primary)
    registry.register(secondary)

    assert registry.get("feishu", "primary") is primary
    assert registry.get("feishu", "secondary") is secondary
    with pytest.raises(KeyError, match="slack/workspace"):
        registry.get("slack", "workspace")


def test_registry_owns_listener_lifecycle_and_status():
    registry = PlatformRegistry()
    listener = Listener()
    registry.register(Adapter("feishu", "default"), listener=listener)

    registry.start_listeners()
    status = registry.status()
    registry.stop_listeners()

    assert listener.events == ["start", "stop"]
    assert status == {"feishu/default": {"running": True, "connected": True}}


def test_registry_rejects_duplicate_adapter_key():
    registry = PlatformRegistry()
    registry.register(Adapter("feishu", "default"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Adapter("feishu", "default"))
