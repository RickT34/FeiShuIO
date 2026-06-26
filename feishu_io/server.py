from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status

from feishu_io.auth import require_api_key
from feishu_io.config import Settings, get_settings
from feishu_io.events import extract_text_message
from feishu_io.feishu import FeishuAppClient, FeishuAppError
from feishu_io.handlers import handle_incoming_message
from feishu_io.listener import start_listener_thread, stop_listener_thread
from feishu_io.models import (
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
    if settings.enable_ws_listener:
        start_listener_thread()
    try:
        yield
    finally:
        if settings.enable_ws_listener:
            stop_listener_thread()


app = FastAPI(title="FeiShuIO", version="0.1.0", lifespan=lifespan)


@lru_cache
def get_store() -> MessageStore:
    return MessageStore(get_settings().db_path)


@lru_cache
def get_feishu_client() -> FeishuAppClient:
    settings = get_settings()
    return FeishuAppClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
    )


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


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
    if not store.resolve_alias(payload.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown id: {payload.id}; bind it in a Feishu group with /bind {payload.id}",
        )
    messages = store.pop_unread(payload.id, payload.limit)
    if settings.mark_read_reaction:
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
    )


@app.post("/feishu/events")
async def feishu_events(
    request: Request,
    store: MessageStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    client: FeishuAppClient = Depends(get_feishu_client),
) -> dict[str, Any]:
    payload = await request.json()

    if payload.get("type") == "url_verification":
        token = payload.get("token")
        if (
            settings.feishu_event_verify_token
            and token != settings.feishu_event_verify_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid Feishu verification token",
            )
        return {"challenge": payload.get("challenge")}

    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    token = payload.get("token") or header.get("token")
    if settings.feishu_event_verify_token and token != settings.feishu_event_verify_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Feishu verification token",
        )

    message = extract_text_message(payload)
    if message:
        return await handle_incoming_message(
            message=message,
            store=store,
            client=client,
        )

    return {"ok": True}


def main() -> None:
    uvicorn.run("feishu_io.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
