"""User-management business logic. HTTP-agnostic; raises domain exceptions.

Admin-only in Phase 1 (see :mod:`app.api.users` for RBAC enforcement).
The Phase 1 invite flow takes an ``initial_password`` from the caller
and hashes it with :func:`app.core.security.hash_password`. Email-based
password reset is deferred to Phase 6.

Active-admin cap (C-2): the number of simultaneously-active admins is
capped at :attr:`app.config.Settings.max_active_admins` (default 3),
where an active admin is ``role == admin AND is_active``. The cap is
enforced when inviting a new admin and when promoting/reactivating a
user into the active-admin set; the last active admin can be neither
deactivated nor demoted. App-level rule only — no DB constraint, no
migration — and the app is single-tenant, so the cap is global. The
bootstrap ``scripts.seed_admin`` path does not pass through this module
and is intentionally exempt (it creates the very first admin).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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


class AdminLimitReached(Exception):
    """Raised when an invite/promotion would exceed the active-admin cap."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"Maximum of {limit} active admins reached")


class LastAdminProtected(Exception):
    """Raised when an update would remove the last remaining active admin."""

    def __init__(self) -> None:
        super().__init__("Cannot deactivate or demote the last active admin")


async def _count_active_admins(db: AsyncSession) -> int:
    """Count users that are currently active admins (role=admin AND is_active)."""
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.admin, User.is_active.is_(True))
    )
    return int((await db.execute(stmt)).scalar_one())


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
    """Create a new user record.

    Raises :class:`DuplicateEmail` when the email already exists, and
    :class:`AdminLimitReached` when ``role`` is admin and the active-admin
    cap is already met (invited users are always active, so a new admin is
    always a new *active* admin).
    """
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateEmail(email)
    if role == UserRole.admin:
        limit = get_settings().max_active_admins
        if await _count_active_admins(db) >= limit:
            raise AdminLimitReached(limit)
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

    Enforces the active-admin cap before mutating: promoting/reactivating
    a user into the active-admin set raises :class:`AdminLimitReached`
    when the cap is met, and demoting/deactivating the last active admin
    raises :class:`LastAdminProtected`.
    """
    user = await get_user(db, user_id)

    # Enforce the active-admin cap / last-admin protection BEFORE mutating.
    # Compare the target's active-admin status before and after; a ``None``
    # field value means "leave unchanged".
    old_active_admin = user.role == UserRole.admin and user.is_active
    new_role = fields.get("role")
    new_is_active = fields.get("is_active")
    eff_role = new_role if new_role is not None else user.role
    eff_active = new_is_active if new_is_active is not None else user.is_active
    new_active_admin = eff_role == UserRole.admin and eff_active

    if new_active_admin and not old_active_admin:
        # Joining the active-admin set (promotion or reactivation).
        limit = get_settings().max_active_admins
        if await _count_active_admins(db) >= limit:
            raise AdminLimitReached(limit)
    elif old_active_admin and not new_active_admin:
        # Leaving the active-admin set (demotion or deactivation). The
        # target is still counted, so a count of 1 means it is the last.
        if await _count_active_admins(db) <= 1:
            raise LastAdminProtected()

    for k, v in fields.items():
        if v is not None:
            setattr(user, k, v)
    await db.flush()
    return user
