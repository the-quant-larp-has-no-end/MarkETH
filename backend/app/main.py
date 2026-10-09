"""The FastAPI application (SPEC §9, §10).

    uv run uvicorn app.main:create_app --factory

`create_app()` builds the application without touching settings or any data store, so
the OpenAPI document can be exported anywhere (`scripts/export_openapi.py`). Settings
are read and the `Container` is built when the server starts the application's
lifespan. Tests pass their own container instead and manage it themselves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api.errors import install_error_handlers
from app.api.middleware import OriginCheckMiddleware
from app.api.router import API_PREFIX, api_router
from app.container import Container
from app.core.config import get_settings
from app.core.logging import configure_logging

API_TITLE: Final[str] = "MarkETH API"
API_VERSION: Final[str] = "0.1.0"


def create_app(container: Container | None = None) -> FastAPI:
    """Build the API. With `container` given it is used as is and never started or
    stopped here; without one, the lifespan builds, starts and stops its own."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            yield
            return
        settings = get_settings()
        configure_logging(settings.app.log_level)
        own = Container.build(settings)
        app.state.container = own
        await own.start()
        try:
            yield
        finally:
            await own.stop()

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    if container is not None:
        app.state.container = container
    app.add_middleware(OriginCheckMiddleware)
    install_error_handlers(app)
    app.include_router(api_router)
    return app


def _operation_id(route: APIRoute) -> str:
    """The route function's name: short, stable operation ids for the generated client."""
    return route.name
