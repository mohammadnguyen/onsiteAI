"""Category management routes.

* ``GET /categories`` — any authenticated caller; returns the active
  category list ordered by ``display_order``. Admins can pass
  ``?include_inactive=true`` to retrieve archived rows as well.
* ``POST /categories`` — admin-only; creates a new category. Collides with
  409 on a duplicate ``category_name``.
* ``PATCH /categories/{category_id}`` — admin-only; partial update. Returns
  404 on a missing id and 409 on a name collision with another row.

We follow the Task 5 convention: inline :class:`fastapi.HTTPException`
calls rather than domain exceptions converted at the boundary.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.category import Category
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryPublic, CategoryUpdate

router = APIRouter(tags=["categories"])


@router.get(
    "",
    response_model=list[CategoryPublic],
    status_code=status.HTTP_200_OK,
)
async def list_categories(
    include_inactive: bool = Query(
        default=False,
        description=(
            "Admin-only toggle: include archived (is_active=false) rows in "
            "the response. Default False — the V1 dashboard hides them."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Category]:
    """List categories ordered by ``display_order``."""
    stmt = select(Category).order_by(Category.display_order)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "",
    response_model=CategoryPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    body: CategoryCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Category:
    """Create a category (admin only)."""
    # Check for a duplicate name up-front; we can't rely on the DB's
    # UNIQUE-constraint error surfacing cleanly because this session is
    # wrapped in an outer SAVEPOINT by the test harness and a failed
    # INSERT poisons the transaction.
    existing = (
        await db.execute(select(Category).where(Category.category_name == body.category_name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category with that name already exists",
        )
    cat = Category(
        category_name=body.category_name,
        display_order=body.display_order,
        is_active=body.is_active,
    )
    db.add(cat)
    await db.flush()
    return cat


@router.patch(
    "/{category_id}",
    response_model=CategoryPublic,
    status_code=status.HTTP_200_OK,
)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Category:
    """Partially update a category (admin only)."""
    cat = await db.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    if body.category_name is not None and body.category_name != cat.category_name:
        clash = (
            await db.execute(
                select(Category).where(
                    Category.category_name == body.category_name,
                    Category.category_id != category_id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Category with that name already exists",
            )
        cat.category_name = body.category_name
    if body.display_order is not None:
        cat.display_order = body.display_order
    if body.is_active is not None:
        cat.is_active = body.is_active
    await db.flush()
    return cat
