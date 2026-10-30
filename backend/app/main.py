"""FastAPI application entrypoint, lifespan management, and health/readiness probes."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.deps import RuntimeContainer
from app.api.endpoints import router as api_router
from app.api.errors import register_exception_handlers
from app.api.lessons import router as lessons_router
from app.core.config import load_config
from app.core.hardening import (
    BodySizeLimitMiddleware,
    IpRateLimitMiddleware,
    RequestContextLogMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.logging import configure_logging, log_event
from app.db.migrator import MigrationRunner
from app.db.pool import AsyncDatabasePool
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.errors import ServiceError
from app.services.lesson_service import LessonService

logger = logging.getLogger(__name__)

_PURGE_INTERVAL_SECONDS = 300.0

_FALLBACK_SPA_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Fulwood Forge</title>
  </head>
  <body>
    <div id="root">Fulwood Forge</div>
  </body>
</html>
"""


def _allowed_origins() -> list[str]:
    """Return the explicit list of allowed CORS origins."""
    raw = os.environ.get("CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:5173", "http://127.0.0.1:5173"]


def _raise_unavailable(detail: str) -> None:
    raise ServiceError(
        detail,
        problem_type="urn:fulwoodforge:error:service-unavailable",
        title="Service Unavailable",
        status_code=503,
    )


async def _periodic_state_purge(app: FastAPI) -> None:
    """Periodically purge expired user_sessions and idempotency_keys rows."""
    while not getattr(app.state, "is_draining", False):
        await asyncio.sleep(_PURGE_INTERVAL_SECONDS)
        container: RuntimeContainer | None = getattr(app.state, "container", None)
        if container is None or getattr(app.state, "is_draining", False):
            continue
        try:
            keys_deleted = await container.auth_service.purge_expired_state()
            lessons_keys_deleted = (
                await container.lesson_service.purge_expired_idempotency_keys()
            )
            log_event(
                logger,
                "background_purge_completed",
                "Purged expired idempotency and session state",
                keys_deleted=keys_deleted + lessons_keys_deleted,
            )
        except (OSError, RuntimeError, ValueError, ServiceError) as exc:
            log_event(
                logger,
                "background_purge_failed",
                "Background state purge encountered an error",
                level=logging.WARNING,
                error_class=type(exc).__name__,
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage database pool startup, migrations, bootstrap admin, and graceful SIGTERM draining."""
    configure_logging()
    app.state.is_draining = False
    existing_container: RuntimeContainer | None = getattr(app.state, "container", None)
    owns_pool = False

    if existing_container is None:
        config = load_config()
        pool = AsyncDatabasePool(config)
        await pool.initialize(max_attempts=20, retry_delay_seconds=1.5)
        owns_pool = True

        migrator = MigrationRunner()
        async with pool.connection() as conn:
            await migrator.apply_pending(conn)

        auth_service = AuthService(pool=pool, config=config)
        admin_service = AdminService(pool=pool, config=config)
        lesson_service = LessonService(pool=pool, config=config)
        await admin_service.ensure_bootstrap_admin()

        app.state.container = RuntimeContainer(
            config=config,
            pool=pool,
            auth_service=auth_service,
            admin_service=admin_service,
            lesson_service=lesson_service,
        )
    else:
        await existing_container.admin_service.ensure_bootstrap_admin()

    purge_task = asyncio.create_task(_periodic_state_purge(app))
    log_event(logger, "application_started", "Fulwood Forge runtime started")
    try:
        yield
    finally:
        app.state.is_draining = True
        log_event(logger, "application_draining", "Fulwood Forge runtime draining")
        purge_task.cancel()
        with suppress(asyncio.CancelledError):
            await purge_task
        active_container: RuntimeContainer | None = getattr(
            app.state, "container", None
        )
        if owns_pool and active_container is not None:
            await active_container.pool.close()
        log_event(logger, "application_stopped", "Fulwood Forge runtime stopped")


app = FastAPI(
    title="Fulwood Forge API",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.state.is_draining = False

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID"],
    expose_headers=[
        "Content-Disposition",
        "X-Artifact-UUID",
        "X-Cache-Hit",
        "X-Generation-UUID",
        "X-Request-ID",
        "Retry-After",
    ],
)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(IpRateLimitMiddleware)
app.add_middleware(RequestContextLogMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router)
app.include_router(lessons_router)
app.include_router(admin_router)
app.include_router(api_router, prefix="/api")


@app.get("/health")
@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness probe returning 200 OK whenever the Uvicorn process is responsive."""
    return {"status": "live"}


def _is_pandoc_available() -> bool:
    """Return True if a Pandoc binary is available via PATH or pypandoc."""
    if shutil.which("pandoc") is not None:
        return True
    try:
        import pypandoc

        return Path(pypandoc.get_pandoc_path()).is_file()
    except (ImportError, OSError, RuntimeError):
        return False


@app.get("/health/ready")
async def health_ready(request: Request) -> dict[str, str]:
    """Readiness probe verifying non-draining state, MySQL pool connectivity, and Pandoc binary."""
    if getattr(request.app.state, "is_draining", False):
        _raise_unavailable("Application is draining for shutdown.")

    container: RuntimeContainer | None = getattr(request.app.state, "container", None)
    if container is None:
        _raise_unavailable("Application runtime container is not initialized.")
    assert container is not None

    db_ok = await container.pool.ping()
    if not db_ok:
        _raise_unavailable("Database connection pool is not ready.")

    if not _is_pandoc_available():
        _raise_unavailable("Pandoc binary is not available on PATH.")

    return {
        "status": "ready",
        "database": "connected",
        "pandoc": "available",
    }


_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="spa-assets")


def _serve_spa_index() -> Response:
    index_file = _DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse(content=_FALLBACK_SPA_HTML, status_code=200)


@app.get("/", include_in_schema=False)
@app.get("/login", include_in_schema=False)
@app.get("/register", include_in_schema=False)
@app.get("/admin", include_in_schema=False)
async def spa_routes() -> Response:
    """Serve the compiled React SPA shell for client-side navigation routes."""
    return _serve_spa_index()
