"""One error shape for every non-2xx response: `{code, message, details}` (SPEC §10, A.2).

Services raise `AppError` subclasses; everything else that can go wrong on the way in
or out (validation, an unknown route, a bug) is translated here into the same shape, so
a client only ever parses one kind of error.

One translation is a rule of the spec rather than a courtesy: with Postgres down,
"everything returns 503" (SPEC §9). No service catches a lost connection (it has to
leave the unit of work anyway); `_database_error` recognises it here, once. A lock that
was not granted within `POSTGRES_LOCK_TIMEOUT_MS` takes the same road and is 503 too.
"""

import logging
from collections.abc import Mapping
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode, ServiceUnavailableError
from app.db import database
from app.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)

VALIDATION_MESSAGE: Final[str] = "The request is not valid."
INTERNAL_MESSAGE: Final[str] = "Something went wrong."

_HTTP_STATUS_CODES: Final[Mapping[int, ErrorCode]] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    # The path exists but not with this method: to a client, no such operation.
    405: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}
"""Codes for errors the framework raises itself (an unknown route, a wrong method)."""


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """The JSON response of an error. `details` may hold schema objects; they are
    serialised the way the API serialises them, amounts as strings."""
    body = ErrorResponse(code=code, message=message, details=dict(details or {}))
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status_code, headers=dict(headers or {})
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    for family in database.ERROR_FAMILIES:
        app.add_exception_handler(family, _database_error)
    app.add_exception_handler(Exception, _unexpected_error)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """422 with one entry per failed field: where (`loc`), what (`msg`), which rule (`type`).

    The rejected input is not echoed back, and neither is anything pydantic attaches
    that might not be JSON.
    """
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(422, ErrorCode.VALIDATION_ERROR, VALIDATION_MESSAGE, {"errors": errors})


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    fallback = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_ERROR
    code = _HTTP_STATUS_CODES.get(exc.status_code, fallback)
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _database_error(request: Request, exc: Exception) -> JSONResponse:
    """503 when Postgres is unreachable or did not grant a lock in time (SPEC §9); every
    other such error is a bug.

    A bug is raised again, so it takes the same road as any unexpected error: logged
    with its traceback and answered with 500 by `_unexpected_error`.
    """
    if not database.is_unavailable(exc):
        raise exc
    logger.warning("Postgres could not serve %s %s: %r", request.method, request.url.path, exc)
    return error_response(
        ServiceUnavailableError.status_code,
        ServiceUnavailableError.code,
        ServiceUnavailableError.default_message,
    )


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """500 for anything nobody foresaw. The traceback goes to the log, never to the client."""
    logger.error("Unhandled error in %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, ErrorCode.INTERNAL_ERROR, INTERNAL_MESSAGE)
