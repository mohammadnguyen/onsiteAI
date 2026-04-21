"""Task 3: smoke-test the User model against a real Postgres schema."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import LanguageCode, User, UserRole


@pytest.mark.asyncio
async def test_user_roundtrip(db_session):
    """Insert a User, flush, reload, and assert field round-trip + defaults."""
    user = User(
        user_id=uuid.uuid4(),
        full_name="Test Admin",
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="placeholder",
        role=UserRole.admin,
    )
    db_session.add(user)
    await db_session.flush()

    loaded = (
        await db_session.execute(select(User).where(User.user_id == user.user_id))
    ).scalar_one()

    assert loaded.full_name == "Test Admin"
    assert loaded.role == UserRole.admin
    assert loaded.language_preference == LanguageCode.en
    assert loaded.is_active is True
    assert loaded.created_at is not None
    assert loaded.updated_at is not None
