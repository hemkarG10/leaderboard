"""Prometheus metrics — bounded labels only (never game_id / user_id)."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "route", "status"],
)

SCORE_SUBMITS = Counter(
    "score_submits_total",
    "Score submit attempts by result",
    ["result"],  # ok | rejected
)


def observe_request(*, method: str, route: str, status: int, seconds: float) -> None:
    status_label = str(status)
    HTTP_REQUESTS.labels(method=method, route=route, status=status_label).inc()
    HTTP_DURATION.labels(method=method, route=route, status=status_label).observe(
        seconds
    )


def score_submit_ok() -> None:
    SCORE_SUBMITS.labels(result="ok").inc()


def score_submit_rejected() -> None:
    SCORE_SUBMITS.labels(result="rejected").inc()


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def route_template(request) -> str:
    """Prefer the FastAPI/Starlette route template; never raw path params."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) if route is not None else None
    if isinstance(path, str) and path:
        return path
    # Unmatched / early 404 — keep cardinality bounded.
    return "unmatched"
