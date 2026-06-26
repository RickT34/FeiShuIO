from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import lark_oapi as lark
from lark_oapi.core.json import JSON

from feishu_io.config import Settings, get_settings
from feishu_io.events import extract_text_message
from feishu_io.feishu import FeishuAppClient
from feishu_io.handlers import handle_incoming_message_sync
from feishu_io.store import MessageStore

logger = logging.getLogger(__name__)


def handle_lark_message_event(event, *, store: MessageStore, client: FeishuAppClient) -> None:
    try:
        payload = JSON.unmarshal(JSON.marshal(event), dict)
        message = extract_text_message(payload)
        if not message:
            logger.warning("received unsupported Feishu message event")
            return
        handle_incoming_message_sync(message=message, store=store, client=client)
    except Exception:
        logger.exception("failed to process Feishu message event")


def build_event_handler(
    *,
    settings: Settings,
    store: MessageStore,
    client: FeishuAppClient,
):
    def on_message(event) -> None:
        handle_lark_message_event(event, store=store, client=client)

    return (
        lark.EventDispatcherHandler.builder(
            settings.feishu_event_encrypt_key or "",
            settings.feishu_event_verify_token or "",
        )
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )


class ListenerService:
    def __init__(
        self,
        *,
        settings_factory: Callable[[], Settings] = get_settings,
    ) -> None:
        self._settings_factory = settings_factory
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ws_client: Any | None = None

    def start(self) -> threading.Thread:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._thread

            self._thread = threading.Thread(
                target=self.run,
                name="feishu-ws-listener",
                daemon=True,
            )
            self._thread.start()
            return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        ws_client = self._ws_client
        for method_name in ("stop", "close"):
            method = getattr(ws_client, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    logger.exception("failed to %s Feishu ws client", method_name)
                break

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def run(self) -> None:
        try:
            self._run_once()
        except Exception:
            logger.exception("Feishu ws listener stopped unexpectedly")

    def _run_once(self) -> None:
        settings = self._settings_factory()
        store = MessageStore(settings.db_path)
        client = FeishuAppClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        handler = build_event_handler(settings=settings, store=store, client=client)
        self._ws_client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=handler,
        )
        self._ws_client.start()


listener_service = ListenerService()


def run_listener() -> None:
    ListenerService().run()


def start_listener_thread() -> threading.Thread:
    return listener_service.start()


def stop_listener_thread(timeout: float = 5.0) -> None:
    listener_service.stop(timeout=timeout)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_listener()


if __name__ == "__main__":
    main()
