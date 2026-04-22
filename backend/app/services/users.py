"""User-management business logic. HTTP-agnostic; raises domain exceptions.

Admin-only in Phase 1 (see :mod:`app.api.users` for RBAC enforcement).
The Phase 1 invite flow takes an ``initial_password`` from the caller
and hashes it with :func:`app.core.security.hash_password`. Email-based
password reset is deferred to Phase 6.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import LanguageCode, User, UserRole


class UserNotFound(Exception):
    """Raised by :func:`get_user` / :func:`update_user` when the id is unknown."""

    def __init__(self, user_id: uuid.UUID):
        self.user_id = user_id
        super().__init__(f"User {user_id} not found")


class DuplicateEmail(Exception):
    """Raised by :func:`invite_user` when the email already exists."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"User with email {email!r} already exists")


async def list_users(db: AsyncSession) -> list[User]:
    """Return every user, newest first."""
    q = select(User).order_by(User.created_at.desc())
    return list((await db.execute(q)).scalars().all())


async def invite_user(
    db: AsyncSession,
    *,
    full_name: str,
    email: str,
    role: UserRole,
    initial_password: str,
    language_preference: LanguageCode = LanguageCode.en,
) -> User:
    """Create a new user record. Raises :class:`DuplicateEmail` on clash."""
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateEmail(email)
    user = User(
        user_id=uuid.uuid4(),
        full_name=full_name,
        email=email,
        password_hash=hash_password(initial_password),
        role=role,
        language_preference=language_preference,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Load a user by id or raise :class:`UserNotFound`."""
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFound(user_id)
    return user


async def update_user(
    db: AsyncSession, user_id: uuid.UUID, **fields
) -> User:
    """Apply any non-``None`` values from ``fields`` to the target user.

    ``None`` values in ``fields`` mean "leave the existing value alone",
    matching the partial-PATCH semantics of ``UserUpdate``. Unknown
    keyword arguments will raise ``AttributeError`` via ``setattr`` —
    callers are expected to pass only model-backed field names.
    """
    user = await get_user(db, user_id)
    for k, v in fields.items():
        if v is not None:
            setattr(user, k, v)
    await db.flush()
    return user
