"""Authentication routes: login, me, refresh, logout.

Phase 1 is deliberately minimal:

* Login issues an access+refresh pair signed with HS256.
* ``/auth/me`` returns the decoded caller as :class:`UserPublic`.
* ``/auth/refresh`` accepts ONLY refresh tokens (type-discriminated)
  and emits a fresh access token. Refresh-token rotation is deferred.
* ``/auth/logout`` is stateless for now — the server doesn't blacklist
  the presented token. Phase 6 hardening will add a ``jti`` blacklist
  check both here and in :func:`app.deps.get_current_user`.

All four routes are grouped under the ``auth`` OpenAPI tag.
"""

from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.rate_limit import auth_rate_limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    AccessToken,
    LoginRequest,
    RefreshRequest,
    TokenPair,
)
from app.schemas.user import UserPublic
from app.services.auth import authenticate_user

router = APIRouter(tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_auth_rate_limit(key: str) -> None:
    """Raise 429 if ``key`` exceeds the configured per-minute auth cap (E2)."""
    limit = get_settings().auth_rate_limit_per_minute
    if not auth_rate_limiter.hit_and_check(key, limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a minute and try again.",
        )


@router.post(
    "/login",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
)
async def login(
    request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)
) -> TokenPair:
    """Exchange email + password for an access/refresh token pair."""
    _enforce_auth_rate_limit(f"login:{_client_ip(request)}:{body.email.strip().lower()}")
    user = await authenticate_user(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = {"sub": str(user.user_id)}
    return TokenPair(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
        token_type="bearer",
    )


@router.get(
    "/me",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    """Return the user identified by the bearer access token."""
    return UserPublic.model_validate(current_user)


@router.post(
    "/refresh",
    response_model=AccessToken,
    status_code=status.HTTP_200_OK,
)
async def refresh(
    request: Request, body: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> AccessToken:
    """Issue a new access token given a valid, unexpired refresh token."""
    _enforce_auth_rate_limit(f"refresh:{_client_ip(request)}")
    try:
        payload = decode_token(body.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive or missing user",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AccessToken(
        access_token=create_access_token({"sub": str(user.user_id)}),
        token_type="bearer",
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(current_user: User = Depends(get_current_user)) -> Response:
    """Stateless logout: the server does not (yet) revoke anything.

    Phase 6 will add a ``jti`` blacklist check here so tokens issued
    before logout stop being accepted for the remainder of their TTL.
    Until then, clients simply discard their stored tokens.
    """
    return Response(status_code=status.HTTP_204_NO_CONTENT)
