"""User management: list, suspend, reinstate, set a role, adjust a balance (SPEC §3, §10,
Appendix A.2, A.3). Every change is audit-logged."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from app.api.deps import AdminUser, AdminUserServiceDep
from app.schemas.admin.users import (
    AdjustBalanceRequest,
    AdminUserItem,
    AdminUserListQuery,
    AdminUserListResponse,
    BalanceAdjustmentResponse,
    ReinstateUserRequest,
    SetUserRoleRequest,
    SuspendUserRequest,
)

router = APIRouter(prefix="/users", tags=["admin-users"])

IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID the client makes up per adjustment and repeats on every retry of it. "
            "The same key returns the same adjustment; it is never booked twice."
        ),
    ),
]


@router.get("", summary="Users with their role, status and balance, newest first")
async def list_users(
    query: Annotated[AdminUserListQuery, Query()], users: AdminUserServiceDep
) -> AdminUserListResponse:
    return await users.list_users(query)


@router.post("/{user_id}/suspend", summary="Suspend a user's account")
async def suspend_user(
    user_id: UUID, body: SuspendUserRequest, admin: AdminUser, users: AdminUserServiceDep
) -> AdminUserItem:
    return await users.suspend(admin, user_id, body)


@router.post("/{user_id}/reinstate", summary="Lift the suspension of a user's account")
async def reinstate_user(
    user_id: UUID, body: ReinstateUserRequest, admin: AdminUser, users: AdminUserServiceDep
) -> AdminUserItem:
    return await users.reinstate(admin, user_id, body)


@router.post("/{user_id}/role", summary="Set a user's role")
async def set_user_role(
    user_id: UUID, body: SetUserRoleRequest, admin: AdminUser, users: AdminUserServiceDep
) -> AdminUserItem:
    return await users.set_role(admin, user_id, body)


@router.post(
    "/{user_id}/adjustments",
    status_code=status.HTTP_201_CREATED,
    summary="Credit or debit a user's balance, idempotently",
)
async def adjust_user_balance(
    user_id: UUID,
    body: AdjustBalanceRequest,
    idempotency_key: IdempotencyKey,
    admin: AdminUser,
    users: AdminUserServiceDep,
) -> BalanceAdjustmentResponse:
    return await users.adjust_balance(admin, user_id, idempotency_key, body)
