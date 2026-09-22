"""structlog setup + request-id + Prometheus HTTP middleware."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.metrics import observe_request, route_template


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign request_id, access-log, and HTTP Prometheus metrics."""

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Still count the request if something escapes handlers.
            elapsed = time.perf_counter() - started
            observe_request(
                method=request.method,
                route=route_template(request),
                status=500,
                seconds=elapsed,
            )
            raise

        elapsed = time.perf_counter() - started
        route = route_template(request)
        observe_request(
            method=request.method,
            route=route,
            status=response.status_code,
            seconds=elapsed,
        )

        response.headers["X-Request-ID"] = request_id
        structlog.get_logger().info(
            "request",
            method=request.method,
            path=request.url.path,
            route=route,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        return response
