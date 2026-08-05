from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from lark_oapi.core.model import RawRequest

from message_io.auth import require_api_key
from message_io.config import Settings, get_settings
from message_io.domain import DeliveryReference, Destination, MessageContent
from message_io.listener import ListenerService, build_event_handler
from message_io.models import (
    AckMessagesRequest,
    AckMessagesResponse,
    ReceiveMessagesRequest,
    ReceiveMessagesResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from message_io.platforms.base import PlatformError
from message_io.platforms.feishu import FeishuAdapter
from message_io.platforms.registry import PlatformRegistry
from message_io.store import MessageStore


logger = logging.getLogger(__name__)


@lru_cache
def get_store() -> MessageStore:
    return MessageStore(get_settings().db_path)


@lru_cache
def get_platforms() -> PlatformRegistry:
    settings = get_settings()
    store = get_store()
    registry = PlatformRegistry()
    if not settings.feishu_enabled:
        return registry
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("enabled Feishu adapter has no credentials")
    adapter = FeishuAdapter(
        account_id=settings.feishu_account_id,
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        reaction_emoji=(
            settings.feishu_read_reaction_emoji
            if settings.feishu_mark_delivered_reaction
            else None
        ),
        retry_attempts=settings.feishu_retry_attempts,
        retry_base_delay=settings.feishu_retry_base_delay,
    )
    listener = None
    if settings.feishu_listener_enabled:
        listener = ListenerService(
            settings_factory=lambda: settings,
            store=store,
            adapter=adapter,
        )
    registry.register(adapter, listener=listener)
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    get_store().cleanup(
        delivered_retention_days=settings.delivered_retention_days,
        processed_retention_days=settings.processed_retention_days,
    )
    platforms = get_platforms()
    platforms.start_listeners()
    try:
        yield
    finally:
        platforms.stop_listeners()


app = FastAPI(title="MessageIO", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ready")
async def ready(
    store: MessageStore = Depends(get_store),
    platforms: PlatformRegistry = Depends(get_platforms),
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        checks["database"] = store.health_check()
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "checks": checks},
        ) from exc

    platform_statuses = platforms.status()
    unavailable = [
        name
        for name, platform_status in platform_statuses.items()
        if not platform_status.get("connected")
    ]
    checks["messaging"] = {
        "ok": not unavailable,
        "configured": len(platform_statuses),
        "connected": len(platform_statuses) - len(unavailable),
    }
    if unavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "checks": checks},
        )
    return {"ok": True, "checks": checks}


@app.post(
    "/messages/send",
    response_model=SendMessageResponse,
    dependencies=[Depends(require_api_key)],
)
async def send_message(
    payload: SendMessageRequest,
    store: MessageStore = Depends(get_store),
    platforms: PlatformRegistry = Depends(get_platforms),
) -> SendMessageResponse:
    destinations = _resolve_targets(store, payload.target)
    content = MessageContent(type=payload.content.type, text=payload.content.text)
    results = await asyncio.gather(
        *(
            _send_to_destination(platforms, destination, content)
            for destination in destinations
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
    failures = [result for result in results if isinstance(result, Exception)]
    sent = len(results) - len(failures)
    if failures:
        for destination, result in zip(destinations, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "failed to send platform message: %s/%s/%s",
                    destination.platform,
                    destination.account_id,
                    destination.conversation_id,
                    exc_info=(type(result), result, result.__traceback__),
                )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "target": payload.target,
                "sent": sent,
                "failed": len(failures),
            },
        )
    return SendMessageResponse(ok=True, target=payload.target, sent=sent)


@app.post(
    "/messages/receive",
    response_model=ReceiveMessagesResponse,
    dependencies=[Depends(require_api_key)],
)
async def receive_messages(
    payload: ReceiveMessagesRequest,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    platforms: PlatformRegistry = Depends(get_platforms),
) -> ReceiveMessagesResponse:
    _resolve_targets(store, payload.target)
    messages, lease_token = store.receive(
        payload.target,
        payload.limit,
        ack=payload.ack,
        lease_seconds=settings.message_lease_seconds,
    )
    if payload.ack:
        references = store.delivery_references(
            [int(message["message_id"]) for message in messages]
        )
        await _mark_delivered(platforms, references)
    return ReceiveMessagesResponse(
        ok=True,
        target=payload.target,
        messages=messages,
        ack_required=not payload.ack,
        lease_token=lease_token,
    )


@app.post(
    "/messages/acknowledge",
    response_model=AckMessagesResponse,
    dependencies=[Depends(require_api_key)],
)
async def acknowledge_messages(
    payload: AckMessagesRequest,
    store: MessageStore = Depends(get_store),
    platforms: PlatformRegistry = Depends(get_platforms),
) -> AckMessagesResponse:
    references = store.ack_messages(
        payload.target,
        payload.message_ids,
        lease_token=payload.lease_token,
    )
    await _mark_delivered(platforms, references)
    return AckMessagesResponse(
        ok=True, target=payload.target, acked=len(references)
    )


async def _mark_delivered(
    platforms: PlatformRegistry, references: list[DeliveryReference]
) -> None:
    for reference in references:
        if not reference.external_message_id:
            continue
        try:
            adapter = platforms.get(reference.platform, reference.account_id)
            await adapter.mark_delivered(
                external_message_id=reference.external_message_id
            )
        except (KeyError, PlatformError):
            logger.exception(
                "failed to mark platform message delivered: %s/%s",
                reference.platform,
                reference.account_id,
            )


async def _send_to_destination(
    platforms: PlatformRegistry,
    destination: Destination,
    content: MessageContent,
) -> None:
    adapter = platforms.get(destination.platform, destination.account_id)
    await adapter.send(destination=destination, content=content)


def _resolve_targets(store: MessageStore, target: str) -> list[Destination]:
    destinations = store.resolve_targets(target)
    if not destinations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown target: {target}; bind a conversation with /bind {target}",
        )
    return destinations


@app.post("/platforms/feishu/events")
async def feishu_events(
    request: Request,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    platforms: PlatformRegistry = Depends(get_platforms),
) -> Response:
    if not settings.feishu_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feishu platform is disabled",
        )
    if not settings.feishu_event_verify_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feishu HTTP callback is disabled because no verification token is configured",
        )
    adapter = platforms.get("feishu", settings.feishu_account_id)
    if not isinstance(adapter, FeishuAdapter):
        raise HTTPException(status_code=500, detail="invalid Feishu adapter")

    raw_request = RawRequest()
    raw_request.uri = request.url.path
    raw_request.body = await request.body()
    raw_request.headers = dict(request.headers)
    for header_name in (
        "X-Lark-Request-Timestamp",
        "X-Lark-Request-Nonce",
        "X-Lark-Signature",
        "X-Request-Id",
    ):
        value = request.headers.get(header_name)
        if value is not None:
            raw_request.headers[header_name] = value
    dispatcher = build_event_handler(settings=settings, store=store, adapter=adapter)
    raw_response = await asyncio.to_thread(dispatcher.do, raw_request)
    return Response(
        content=raw_response.content or b"",
        status_code=raw_response.status_code or status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers=raw_response.headers,
    )


@app.post("/maintenance/cleanup", dependencies=[Depends(require_api_key)])
async def cleanup(
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return {
        "ok": True,
        **store.cleanup(
            delivered_retention_days=settings.delivered_retention_days,
            processed_retention_days=settings.processed_retention_days,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="message-io-server",
        description="Run the MessageIO REST service and configured platform listeners.",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    settings: Settings | None = None
    if args.host is None or args.port is None or args.log_level is None:
        settings = get_settings()
    uvicorn.run(
        "message_io.server:app",
        host=args.host if args.host is not None else settings.host,
        port=args.port if args.port is not None else settings.port,
        log_level=(args.log_level if args.log_level is not None else settings.log_level),
        reload=False,
    )


if __name__ == "__main__":
    main()
