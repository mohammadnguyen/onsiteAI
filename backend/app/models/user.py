"""User account model.

V1 supports two roles (admin, contributor) and two UI languages (en, zh).
Email is stored as entered (case-sensitive). Phase 6 may add case-folded
lookup; until then, admins should invite with the email they want stored.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import UUID, Boolean
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    """Allowed roles on a SiteTracker user account."""

    admin = "admin"
    contributor = "contributor"


class LanguageCode(str, enum.Enum):
    """Allowed UI language preferences."""

    en = "en"
    zh = "zh"


class User(Base, TimestampMixin):
    """A human user of the SiteTracker application."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Email is stored as entered; case-folded lookup is a Phase 6 concern.
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SqlaEnum(UserRole, name="user_role", native_enum=True, create_type=True),
        nullable=False,
    )
    language_preference: Mapped[LanguageCode] = mapped_column(
        SqlaEnum(
            LanguageCode, name="language_code", native_enum=True, create_type=True
        ),
        nullable=False,
        default=LanguageCode.en,
        server_default=LanguageCode.en.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
