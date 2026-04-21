"""Authentication service layer.

Exposes :func:`authenticate_user` which wraps the database lookup and
bcrypt password verification together. Returns the :class:`User` on
success and ``None`` on any failure condition (wrong email, wrong
password, deactivated account). The route handler does not distinguish
between these cases in its response — same 401 for all — so nothing
leaks through error messages about which part of the credential pair
was wrong.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> User | None:
    """Return the matching ``User`` if credentials are valid, else ``None``."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
