"""Shared fixtures and metric helpers for integration tests."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        score_min=-1_000_000_000,
        score_max=1_000_000_000,
        top_default=10,
        top_max=100,
        window_default=1,
        window_max=25,
        id_max_len=64,
        max_games=10_000,
        max_users_per_game=50_000,
        api_key="",
    )


@pytest_asyncio.fixture
async def client(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac, app


def metric_value(text: str, name: str, labels: dict[str, str] | None = None) -> float:
    """Parse a counter/gauge sample from Prometheus text exposition."""
    total = 0.0
    found = False
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        if labels is None:
            if "{" in line.split(None, 1)[0]:
                continue
            total += float(line.rsplit(None, 1)[-1])
            found = True
            continue
        if "{" not in line:
            continue
        label_blob = line[line.index("{") + 1 : line.index("}")]
        if all(f'{k}="{v}"' in label_blob for k, v in labels.items()):
            total += float(line.rsplit(None, 1)[-1])
            found = True
    return total if found else 0.0


def metric_sum_by_name(text: str, name: str) -> float:
    total = 0.0
    prefix = name + "{"
    bare = name + " "
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(prefix) or line.startswith(bare):
            total += float(line.rsplit(None, 1)[-1])
    return total
