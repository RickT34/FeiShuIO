from __future__ import annotations

import logging
import threading
import time
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
        self._stop_event = threading.Event()
        self._ws_client: Any | None = None
        self._last_started_at: float | None = None
        self._last_stopped_at: float | None = None
        self._last_error: str | None = None

    def start(self) -> threading.Thread:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._thread

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.run,
                name="feishu-ws-listener",
                daemon=True,
            )
            self._thread.start()
            return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
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
        delay: float | None = None
        while not self._stop_event.is_set():
            settings = self._settings_factory()
            if delay is None:
                delay = settings.listener_retry_base_delay
            try:
                self._last_started_at = time.time()
                self._last_error = None
                self._run_once(settings)
                self._last_stopped_at = time.time()
                if not self._stop_event.is_set():
                    logger.warning("Feishu ws listener stopped; restarting")
            except Exception as exc:
                self._last_stopped_at = time.time()
                self._last_error = str(exc)
                logger.exception("Feishu ws listener stopped unexpectedly")

            if self._stop_event.is_set():
                break

            wait_seconds = min(delay, settings.listener_retry_max_delay)
            logger.info("restarting Feishu ws listener in %.1fs", wait_seconds)
            self._stop_event.wait(wait_seconds)
            delay = min(delay * 2, settings.listener_retry_max_delay)
        self._ws_client = None

    def _run_once(self, settings: Settings | None = None) -> None:
        settings = settings or self._settings_factory()
        store = MessageStore(settings.db_path)
        client = FeishuAppClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            retry_attempts=settings.feishu_retry_attempts,
            retry_base_delay=settings.feishu_retry_base_delay,
        )
        handler = build_event_handler(settings=settings, store=store, client=client)
        self._ws_client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=handler,
        )
        self._ws_client.start()

    def status(self) -> dict[str, Any]:
        thread = self._thread
        return {
            "running": bool(thread and thread.is_alive()),
            "last_started_at": self._last_started_at,
            "last_stopped_at": self._last_stopped_at,
            "last_error": self._last_error,
        }


listener_service = ListenerService()


def run_listener() -> None:
    ListenerService().run()


def start_listener_thread() -> threading.Thread:
    return listener_service.start()


def stop_listener_thread(timeout: float = 5.0) -> None:
    listener_service.stop(timeout=timeout)


def listener_status() -> dict[str, Any]:
    return listener_service.status()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_listener()


if __name__ == "__main__":
    main()
