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

from feishu_io.auth import require_api_key
from feishu_io.config import Settings, get_settings
from feishu_io.feishu import FeishuAppClient, FeishuAppError
from feishu_io.listener import (
    build_event_handler,
    listener_status,
    start_listener_thread,
    stop_listener_thread,
)
from feishu_io.models import (
    AckMessagesRequest,
    AckMessagesResponse,
    RecvUnreadRequest,
    RecvUnreadResponse,
    SendMarkdownRequest,
    SendMarkdownResponse,
)
from feishu_io.store import MessageStore


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    MessageStore(settings.db_path).cleanup(
        delivered_retention_days=settings.delivered_retention_days,
        processed_retention_days=settings.processed_retention_days,
    )
    if settings.enable_ws_listener:
        start_listener_thread()
    try:
        yield
    finally:
        if settings.enable_ws_listener:
            stop_listener_thread()


app = FastAPI(title="FeiShuIO", version="0.3.0", lifespan=lifespan)


@lru_cache
def get_store() -> MessageStore:
    return MessageStore(get_settings().db_path)


@lru_cache
def get_feishu_client() -> FeishuAppClient:
    settings = get_settings()
    return FeishuAppClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        retry_attempts=settings.feishu_retry_attempts,
        retry_base_delay=settings.feishu_retry_base_delay,
    )


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ready")
async def ready(
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
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

    if settings.enable_ws_listener:
        status_payload = listener_status()
        checks["listener"] = status_payload
        if not status_payload["connected"]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"ok": False, "checks": checks},
            )

    return {"ok": True, "checks": checks}


@app.post(
    "/send_markdown",
    response_model=SendMarkdownResponse,
    dependencies=[Depends(require_api_key)],
)
async def send_markdown(
    payload: SendMarkdownRequest,
    client: FeishuAppClient = Depends(get_feishu_client),
    store: MessageStore = Depends(get_store),
) -> SendMarkdownResponse:
    chat_id = store.resolve_alias(payload.id)
    if not chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown id: {payload.id}; bind it in a Feishu group with /bind {payload.id}",
        )

    try:
        data = await client.send_markdown(chat_id=chat_id, text=payload.text)
    except FeishuAppError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return SendMarkdownResponse(
        ok=True,
        id=payload.id,
        feishu_code=data.get("code"),
        message=data.get("msg"),
    )


@app.post(
    "/recv_unread",
    response_model=RecvUnreadResponse,
    dependencies=[Depends(require_api_key)],
)
async def recv_unread(
    payload: RecvUnreadRequest,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    client: FeishuAppClient = Depends(get_feishu_client),
) -> RecvUnreadResponse:
    chat_id = store.resolve_alias(payload.id)
    if not chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown id: {payload.id}; bind it in a Feishu group with /bind {payload.id}",
        )
    messages = store.pop_unread(
        chat_id,
        payload.limit,
        ack=payload.ack,
        lease_seconds=settings.message_lease_seconds,
    )
    for message in messages:
        message["id"] = payload.id
    if payload.ack and settings.mark_read_reaction:
        for message in messages:
            external_message_id = message.get("external_message_id")
            if not external_message_id:
                continue
            try:
                await client.add_reaction(
                    message_id=external_message_id,
                    emoji_type=settings.read_reaction_emoji,
                )
            except FeishuAppError:
                logger.exception(
                    "failed to add read reaction to Feishu message %s",
                    external_message_id,
                )

    return RecvUnreadResponse(
        ok=True,
        id=payload.id,
        messages=messages,
        ack_required=not payload.ack,
    )


@app.post(
    "/ack_messages",
    response_model=AckMessagesResponse,
    dependencies=[Depends(require_api_key)],
)
async def ack_messages(
    payload: AckMessagesRequest,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    client: FeishuAppClient = Depends(get_feishu_client),
) -> AckMessagesResponse:
    chat_id = store.resolve_alias(payload.id)
    if not chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown id: {payload.id}; bind it in a Feishu group with /bind {payload.id}",
        )

    acked_messages = store.ack_messages(
        chat_id,
        payload.message_ids,
        lease_token=payload.lease_token,
    )
    if settings.mark_read_reaction:
        for message in acked_messages:
            external_message_id = message.get("external_message_id")
            if not external_message_id:
                continue
            try:
                await client.add_reaction(
                    message_id=external_message_id,
                    emoji_type=settings.read_reaction_emoji,
                )
            except FeishuAppError:
                logger.exception(
                    "failed to add read reaction to leased message %s",
                    external_message_id,
                )

    return AckMessagesResponse(ok=True, id=payload.id, acked=len(acked_messages))


@app.post("/maintenance/cleanup", dependencies=[Depends(require_api_key)])
async def cleanup(
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    deleted = store.cleanup(
        delivered_retention_days=settings.delivered_retention_days,
        processed_retention_days=settings.processed_retention_days,
    )
    return {"ok": True, **deleted}


@app.post("/feishu/events")
async def feishu_events(
    request: Request,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    client: FeishuAppClient = Depends(get_feishu_client),
) -> Response:
    if not settings.feishu_event_verify_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP event callback is disabled because no verification token is configured",
        )

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

    dispatcher = build_event_handler(settings=settings, store=store, client=client)
    raw_response = await asyncio.to_thread(dispatcher.do, raw_request)
    return Response(
        content=raw_response.content or b"",
        status_code=raw_response.status_code or status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers=raw_response.headers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="feishu-io-server",
        description="Run the persistent FeiShuIO REST and Feishu listener service.",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    settings: Settings | None = None
    if args.host is None or args.port is None or args.log_level is None:
        settings = get_settings()
    uvicorn.run(
        "feishu_io.server:app",
        host=args.host if args.host is not None else settings.host,
        port=args.port if args.port is not None else settings.port,
        log_level=(
            args.log_level if args.log_level is not None else settings.log_level
        ),
        reload=False,
    )


if __name__ == "__main__":
    main()
