"""Request and response schemas for the auth endpoints.

``LoginRequest`` / ``RefreshRequest`` model the inbound JSON bodies;
``TokenPair`` / ``AccessToken`` model the outbound token payloads. The
``token_type`` field is present for OAuth2 compatibility even though the
only value Phase 1 ever emits is ``"bearer"``.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /auth/login``."""

    email: EmailStr
    # Security audit 2026-07: cap the unauthenticated input so a huge
    # body can't be shovelled through bcrypt (bcrypt itself only reads
    # the first 72 bytes, but the cap bounds the request cost).
    password: str = Field(min_length=1, max_length=1024)


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
