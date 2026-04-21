"""Request and response schemas for the auth endpoints.

``LoginRequest`` / ``RefreshRequest`` model the inbound JSON bodies;
``TokenPair`` / ``AccessToken`` model the outbound token payloads. The
``token_type`` field is present for OAuth2 compatibility even though the
only value Phase 1 ever emits is ``"bearer"``.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /auth/login``."""

    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """Access + refresh pair returned on successful login."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Body of ``POST /auth/refresh``."""

    refresh_token: str


class AccessToken(BaseModel):
    """Single-token response returned by ``POST /auth/refresh``."""

    access_token: str
    token_type: str = "bearer"
