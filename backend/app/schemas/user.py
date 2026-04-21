"""Public-facing user schemas.

``UserPublic`` is the canonical shape returned by any endpoint that
surfaces a user to an API client. The password hash is deliberately NOT
a field on this schema, so it cannot leak through a response by
accident, even if the caller passes the whole ORM instance into
``.model_validate()``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr

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
