"""CLI: seed the initial admin user + builder categories.

Usage from the ``backend/`` directory::

    uv run python -m scripts.seed_admin \
        --email admin@example.com --password admin --name "Admin"

Idempotent: re-running resets the admin's password/name/role to the
supplied values and re-runs :func:`seed_builder_categories`, which is
itself idempotent. Intended for human dev bootstrap; the test suite
uses its own fixtures.

Optional flag ``--seed-suppliers`` (Capture Hardening Patch CHP-6)
additionally seeds the AU residential starter supplier list via
:func:`seed_suppliers`. Off by default — explicit opt-in so the
seed never runs against the live operative DB unless the operator
asks for it. The seed itself is non-destructive: it only INSERTS
missing rows and never overwrites existing supplier rows.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.core.seed import seed_builder_categories, seed_suppliers
from app.database import get_sessionmaker
from app.models.user import LanguageCode, User, UserRole


async def _seed_admin(db: AsyncSession, email: str, password: str, name: str) -> User:
    """Insert-or-update the admin row keyed on ``email``."""
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        existing.full_name = name
        existing.password_hash = hash_password(password)
        existing.role = UserRole.admin
        existing.is_active = True
        return existing
    user = User(
        user_id=uuid.uuid4(),
        full_name=name,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.admin,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _main(
    email: str, password: str, name: str, *, with_suppliers: bool = False
) -> None:
    Session = get_sessionmaker()
    async with Session() as db:
        await _seed_admin(db, email, password, name)
        await seed_builder_categories(db)
        if with_suppliers:
            await seed_suppliers(db)
        await db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed an admin user + builder categories (and optionally suppliers)."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Admin")
    parser.add_argument(
        "--seed-suppliers",
        action="store_true",
        help=(
            "Additionally seed the AU residential starter supplier list. "
            "Off by default — explicit opt-in so the seed never runs "
            "against the live DB unless the operator asks for it. The "
            "seed is non-destructive: only INSERTS missing suppliers."
        ),
    )
    args = parser.parse_args()
    asyncio.run(
        _main(
            args.email,
            args.password,
            args.name,
            with_suppliers=args.seed_suppliers,
        )
    )


if __name__ == "__main__":
    main()
