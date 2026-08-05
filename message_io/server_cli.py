from __future__ import annotations

from collections.abc import Callable


SERVER_MODULES = {"fastapi", "lark_oapi", "pydantic_settings", "uvicorn"}


def _load_entrypoint(module_name: str, function_name: str) -> Callable[[], None]:
    try:
        module = __import__(module_name, fromlist=[function_name])
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".", 1)[0] in SERVER_MODULES:
            raise SystemExit(
                "MessageIO server dependencies are not installed. "
                "Install them with: pip install 'message-io[server]'"
            ) from exc
        raise
    return getattr(module, function_name)


def main() -> None:
    _load_entrypoint("message_io.server", "main")()


def listener_main() -> None:
    _load_entrypoint("message_io.listener", "main")()
