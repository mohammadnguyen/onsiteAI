"""SQLAlchemy ORM models.

Later tasks add concrete models (``user``, ``job``, ``category``) which must be
imported here (and in ``alembic/env.py``) so that :class:`Base.metadata` knows
about them for autogenerate.
"""

from app.models.base import Base

__all__ = ["Base"]
