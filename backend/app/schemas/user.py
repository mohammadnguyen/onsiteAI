"""Public-facing user schemas.

``UserPublic`` is the canonical shape returned by any endpoint that
surfaces a user to an API client. The password hash is deliberately NOT
a field on this schema, so it cannot leak through a response by
accident, even if the caller passes the whole ORM instance into
``.model_validate()``.

``UserInvite`` / ``UserUpdate`` are the request bodies for the Phase 1
admin-only invite and update flows. Phase 1's invite flow takes
``initial_password`` directly; email-based reset is a Phase 6 concern.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import LanguageCode, UserRole


class UserPublic(BaseModel):
    """Serialised view of a ``User`` safe to return over the wire."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    full_name: str
    email: EmailStr
    role: UserRole
    language_preference: LanguageCode
    is_active: bool


class UserInvite(BaseModel):
    """Request body for ``POST /users/invite`` (admin only).

    The admin supplies ``initial_password`` directly in Phase 1.
    No password-strength rules are applied here — that lands in
    Phase 6 alongside email-based reset.
    """

    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    role: UserRole
    initial_password: str = Field(min_length=1, max_length=255)
    language_preference: LanguageCode = LanguageCode.en


class UserUpdate(BaseModel):
    """Request body for ``PATCH /users/{user_id}`` (admin only).

    All fields are optional; only those supplied are mutated. ``None``
    means "do not change". Setting ``is_active=False`` deactivates the
    user; their existing access tokens immediately stop working because
    ``get_current_user`` requires ``is_active``.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None
    language_preference: LanguageCode | None = None
