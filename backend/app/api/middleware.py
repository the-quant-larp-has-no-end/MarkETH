"""The Origin check on state-changing requests (SPEC §3, Appendix A.2).

The session lives in a SameSite=Lax cookie; this check is the second line against a
foreign page making a signed-in browser act. A request with a method other than GET,
HEAD or OPTIONS must carry an `Origin` header listed in `ALLOWED_ORIGINS`, else 403.

Written as a pure ASGI middleware: it looks at the request's headers and either answers
itself or passes the three ASGI callables on untouched, so a streaming response (the
event stream) is never buffered.
"""

from typing import Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import error_response
from app.core.errors import ErrorCode

SAFE_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})

ORIGIN_MESSAGE: Final[str] = "This request did not come from the MarkETH app."


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self._app(scope, receive, send)
            return
        # The container is put on the application's state before the first request:
        # by `create_app` when one is passed in, by the lifespan otherwise.
        allowed = scope["app"].state.container.settings.app.allowed_origins
        origin = Headers(scope=scope).get("origin")
        if origin is None or origin.rstrip("/") not in {item.rstrip("/") for item in allowed}:
            response = error_response(403, ErrorCode.FORBIDDEN, ORIGIN_MESSAGE)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
