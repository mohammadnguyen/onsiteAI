"""Admin-only user management endpoints.

Phase 1 invite flow: an admin supplies ``initial_password`` directly in
the request body. Email-based password reset lands in Phase 6.

All routes require ``role == admin``. A contributor calling any of
these routes gets a 403 from :func:`app.deps.require_admin`. A
deactivated user's token is rejected earlier by :func:`get_current_user`
(401), so ``PATCH /users/{id}`` with ``is_active=False`` effectively
logs the target out — even though their JWT has not expired.

Active-admin cap (C-2): inviting/promoting beyond
:attr:`app.config.Settings.max_active_admins` active admins, or
deactivating/demoting the last active admin, returns ``409 Conflict``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models.user import User
from app.schemas.user import UserInvite, UserPublic, UserUpdate
from app.services import users as svc

router = APIRouter(tags=["users"])


@router.get(
    "",
    response_model=list[UserPublic],
    status_code=status.HTTP_200_OK,
)
async def list_users_endpoint(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    """Return every user. Admin only."""
    return await svc.list_users(db)


@router.post(
    "/invite",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user_endpoint(
    body: UserInvite,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create a user from an admin-supplied ``initial_password``.

    Phase 1 has no email delivery; the admin is expected to communicate
    the password to the invitee out of band. Phase 6 will add a proper
    magic-link / reset-password flow.

    Returns 201 with the new :class:`UserPublic`. A pre-existing user
    with the same email produces a 409; inviting an admin beyond the
    active-admin cap also produces a 409.
    """
    try:
        return await svc.invite_user(
            db,
            full_name=body.full_name,
            email=body.email,
            role=body.role,
            initial_password=body.initial_password,
            language_preference=body.language_preference,
        )
    except svc.DuplicateEmail as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        # Lost the email pre-check race to a concurrent invite — the DB
        # UNIQUE backstop fired; surface a clean 409, not a 500 (audit R30).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from exc
    except svc.AdminLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def update_user_endpoint(
    user_id: uuid.UUID,
    body: UserUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Partially update a user. Admin only.

    Any subset of ``full_name``, ``role``, ``is_active``, and
    ``language_preference`` may be supplied. Setting ``is_active=False``
    forces the target's existing access tokens to 401 on the next auth
    check — :func:`app.deps.get_current_user` rejects deactivated users.

    Returns 409 when promoting beyond the active-admin cap or when the
    change would remove the last active admin.
    """
    try:
        return await svc.update_user(
            db,
            user_id,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
            language_preference=body.language_preference,
        )
    except svc.UserNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
    except (svc.AdminLimitReached, svc.LastAdminProtected) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
