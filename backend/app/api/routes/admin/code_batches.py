"""Code batches: create, list, revoke (SPEC §2, §10, Appendix A.6)."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import AdminUser, CodeServiceDep
from app.schemas.admin.code_batches import (
    CodeBatchCreatedResponse,
    CodeBatchListResponse,
    CreateCodeBatchRequest,
    RevokeCodesRequest,
    RevokeCodesResponse,
)

router = APIRouter(prefix="/code-batches", tags=["admin-code-batches"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a batch; the plain codes are returned this once",
)
async def create_code_batch(
    body: CreateCodeBatchRequest, admin: AdminUser, codes: CodeServiceDep
) -> CodeBatchCreatedResponse:
    return await codes.create_batch(admin, body)


@router.get("", summary="Batches with their redeemed and revoked counts")
async def list_code_batches(codes: CodeServiceDep) -> CodeBatchListResponse:
    return await codes.list_batches()


@router.post("/{batch_id}/revoke", summary="Revoke unredeemed codes of a batch")
async def revoke_codes(
    batch_id: UUID, body: RevokeCodesRequest, admin: AdminUser, codes: CodeServiceDep
) -> RevokeCodesResponse:
    return await codes.revoke(admin, batch_id, body)
