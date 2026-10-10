"""Code batches: create, list, open the codes again, revoke (SPEC §2, §10, Appendix A.6)."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import AdminUser, CodeServiceDep
from app.schemas.admin.code_batches import (
    CodeBatchCodesResponse,
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
    summary="Create a batch and get its codes",
)
async def create_code_batch(
    body: CreateCodeBatchRequest, admin: AdminUser, codes: CodeServiceDep
) -> CodeBatchCreatedResponse:
    return await codes.create_batch(admin, body)


@router.get("", summary="Batches with their redeemed and revoked counts")
async def list_code_batches(codes: CodeServiceDep) -> CodeBatchListResponse:
    return await codes.list_batches()


@router.get(
    "/{batch_id}/codes",
    summary="The codes of a batch that can still be redeemed; every read is audited",
)
async def list_batch_codes(
    batch_id: UUID, admin: AdminUser, codes: CodeServiceDep, response: Response
) -> CodeBatchCodesResponse:
    # The answer holds codes that are worth credits: no browser or proxy keeps a copy.
    response.headers["Cache-Control"] = "no-store"
    return await codes.batch_codes(admin, batch_id)


@router.post("/{batch_id}/revoke", summary="Revoke unredeemed codes of a batch")
async def revoke_codes(
    batch_id: UUID, body: RevokeCodesRequest, admin: AdminUser, codes: CodeServiceDep
) -> RevokeCodesResponse:
    return await codes.revoke(admin, batch_id, body)
