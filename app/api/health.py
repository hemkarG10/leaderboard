"""Liveness, readiness, and Prometheus scrape endpoints."""

from fastapi import APIRouter, Response

from app.metrics import metrics_payload

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict[str, str]:
    # In-memory store is always ready once the process is up.
    return {"status": "ready"}


@router.get("/metrics")
def metrics() -> Response:
    body, content_type = metrics_payload()
    return Response(content=body, media_type=content_type)
