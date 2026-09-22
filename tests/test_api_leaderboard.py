"""API integration tests via httpx ASGITransport."""

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
        max_games=100,
        max_users_per_game=50,
        api_key="",
    )


@pytest_asyncio.fixture
async def client(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac


@pytest.mark.asyncio
async def test_healthz_readyz_metrics(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).json()["status"] == "ready"
    assert "http_requests_total" in (await client.get("/metrics")).text
    assert "score_submits_total" in (await client.get("/metrics")).text


@pytest.mark.asyncio
async def test_submit_top_and_user_flow(client: AsyncClient) -> None:
    r1 = await client.post(
        "/v1/games/maze/scores", json={"user_id": "alice", "score": 50}
    )
    assert r1.status_code == 201
    assert r1.headers["location"] == "/v1/games/maze/users/alice"

    await client.post("/v1/games/maze/scores", json={"user_id": "bob", "score": 90})
    r3 = await client.post(
        "/v1/games/maze/scores", json={"user_id": "alice", "score": 100}
    )
    assert r3.status_code == 200 and r3.json()["improved"] is True

    r4 = await client.post(
        "/v1/games/maze/scores", json={"user_id": "alice", "score": 100}
    )
    assert r4.json()["improved"] is False

    board = (await client.get("/v1/games/maze/leaderboard?limit=10")).json()
    assert board["entries"][0] == {"rank": 1, "user_id": "alice", "score": 100}

    ctx = (await client.get("/v1/games/maze/users/bob?window=1")).json()
    assert ctx["above"] == [{"rank": 1, "user_id": "alice", "score": 100}]
    assert ctx["below"] == []


@pytest.mark.asyncio
async def test_unknown_game_top_empty_surroundings_404(client: AsyncClient) -> None:
    board = await client.get("/v1/games/nope/leaderboard")
    assert board.status_code == 200
    assert board.json() == {"game_id": "nope", "total": 0, "entries": []}

    missing = await client.get("/v1/games/nope/users/ghost")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "game_not_found"


@pytest.mark.asyncio
async def test_validation_and_request_id(client: AsyncClient) -> None:
    res = await client.post(
        "/v1/games/maze/scores", json={"user_id": "u", "score": "100"}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_failed"
    assert "request_id" in res.json()["error"]

    echoed = await client.get("/healthz", headers={"X-Request-ID": "exam-req-1"})
    assert echoed.headers.get("x-request-id") == "exam-req-1"
