"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import health, leaderboard
from app.config import Settings, get_settings, set_settings
from app.errors import AppError
from app.infra.store import InMemoryStore
from app.logging_setup import RequestContextMiddleware, configure_logging
from app.metrics import score_submit_rejected

logger = structlog.get_logger()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )


def _jsonable_details(details: Any) -> Any:
    """Make pydantic / handler details JSON-safe (no Exception, bytes, NaN, Inf)."""
    if isinstance(details, list):
        return [_jsonable_details(item) for item in details]
    if isinstance(details, dict):
        out: dict[str, Any] = {}
        for k, v in details.items():
            if isinstance(v, Exception):
                out[k] = str(v)
            else:
                out[k] = _jsonable_details(v)
        return out
    if isinstance(details, (bytes, bytearray)):
        try:
            return details.decode("utf-8", errors="replace")
        except Exception:
            return repr(details)
    if isinstance(details, float):
        if details != details or details in (float("inf"), float("-inf")):
            return str(details)
    return details


def _error_body(
    *, code: str, message: str, details: Any, request_id: str
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": _jsonable_details(details),
            "request_id": request_id,
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    set_settings(settings)
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = InMemoryStore(
            max_games=settings.max_games,
            max_users_per_game=settings.max_users_per_game,
        )
        app.state.settings = settings
        logger.info("store_ready", max_games=settings.max_games)
        yield

    app = FastAPI(
        title="Leaderboard Service",
        description=(
            "Per-game realtime leaderboards (in-memory, single worker).\n\n"
            "**Try it:** use **Authorize** only if `API_KEY` is set; otherwise "
            "call endpoints directly with **Try it out**.\n\n"
            "| Method | Path |\n|---|---|\n"
            "| POST | `/v1/games/{game_id}/scores` |\n"
            "| GET | `/v1/games` |\n"
            "| GET | `/v1/games/{game_id}/leaderboard` |\n"
            "| GET | `/v1/games/{game_id}/users/{user_id}` |\n"
            "| GET | `/v1/games/{game_id}/compare` |\n"
            "| GET | `/v1/leaderboard` (global sum) |\n"
            "| GET | `/v1/users/{user_id}` (profile across games) |\n"
            "| GET | `/healthz` · `/readyz` · `/metrics` |"
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "leaderboard",
                "description": (
                    "Submit scores, per-game and global boards, profiles, "
                    "game list, and head-to-head compare"
                ),
            },
            {"name": "ops", "description": "Health, readiness, and Prometheus metrics"},
        ],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        rid = request.headers.get("X-Request-ID", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=rid,
            ),
            headers={"X-Request-ID": rid} if rid else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Count body validation failures on the write path only.
        if request.method == "POST" and "/scores" in request.url.path:
            score_submit_rejected()
        rid = request.headers.get("X-Request-ID", "")
        return JSONResponse(
            status_code=400,
            content=_error_body(
                code="validation_failed",
                message="request validation failed",
                details=exc.errors(),
                request_id=rid,
            ),
            headers={"X-Request-ID": rid} if rid else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = request.headers.get("X-Request-ID", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code="http_error",
                message=str(exc.detail),
                details={},
                request_id=rid,
            ),
            headers={"X-Request-ID": rid} if rid else None,
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = request.headers.get("X-Request-ID", "")
        logger.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_error_body(
                code="internal_error",
                message="an unexpected error occurred",
                details={},
                request_id=rid,
            ),
            headers={"X-Request-ID": rid} if rid else None,
        )

    app.include_router(health.router)
    app.include_router(leaderboard.global_router)
    app.include_router(leaderboard.router)
    return app


app = create_app()
