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

from app.core.security import hash_password, verify_password_async
from app.models.user import User

# Security audit 2026-07: a constant decoy hash. When the email is
# unknown or the account is inactive we still run a bcrypt verify
# against this decoy so the reject path takes the SAME time as a real
# password check — otherwise the timing difference (immediate return vs
# tens of ms of bcrypt) enumerates which emails have active accounts,
# defeating the uniform "Invalid email or password" message.
_DECOY_HASH = hash_password("timing-attack-decoy-not-a-real-password")


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> User | None:
    """Return the matching ``User`` if credentials are valid, else ``None``."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        # Constant-time reject: spend the same bcrypt cost as a real
        # verify so latency doesn't leak account existence.
        await verify_password_async(password, _DECOY_HASH)
        return None
    if not await verify_password_async(password, user.password_hash):
        return None
    return user
