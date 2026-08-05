from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, status

from message_io.config import Settings, get_settings


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    supplied_key = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        supplied_key = authorization[7:].strip()

    if not supplied_key or not hmac.compare_digest(supplied_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
        )

