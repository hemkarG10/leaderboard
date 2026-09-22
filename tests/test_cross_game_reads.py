"""Cross-game reads: global board, user profile, game list, compare."""

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


# --- 1. Global leaderboard ----------------------------------------------------


@pytest.mark.asyncio
async def test_global_empty(client: AsyncClient) -> None:
    r = await client.get("/v1/leaderboard")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "entries": []}


@pytest.mark.asyncio
async def test_global_sum_and_games_played(client: AsyncClient) -> None:
    # Alice 1500 maze + 1000 raid = 2500; Bob 2000 maze only.
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1500})
    await client.post("/v1/games/maze/scores", json={"user_id": "bob", "score": 2000})
    await client.post("/v1/games/raid/scores", json={"user_id": "alice", "score": 1000})

    board = (await client.get("/v1/leaderboard")).json()
    assert board["total"] == 2
    assert board["entries"] == [
        {"rank": 1, "user_id": "alice", "score": 2500, "games_played": 2},
        {"rank": 2, "user_id": "bob", "score": 2000, "games_played": 1},
    ]


@pytest.mark.asyncio
async def test_global_tie_on_sum(client: AsyncClient) -> None:
    await client.post("/v1/games/a/scores", json={"user_id": "alice", "score": 100})
    await client.post("/v1/games/b/scores", json={"user_id": "bob", "score": 100})

    board = (await client.get("/v1/leaderboard")).json()
    assert board["entries"][0]["user_id"] == "alice"
    assert board["entries"][1]["user_id"] == "bob"


@pytest.mark.asyncio
async def test_global_limit_over_max_400(client: AsyncClient) -> None:
    bad = await client.get("/v1/leaderboard?limit=101")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_failed"


# --- 2. User profile across games --------------------------------------------


@pytest.mark.asyncio
async def test_user_profile_across_games(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1500})
    await client.post("/v1/games/maze/scores", json={"user_id": "bob", "score": 100})
    await client.post("/v1/games/raid/scores", json={"user_id": "alice", "score": 1000})

    # Fill raid so alice is not alone (rank still 1).
    await client.post("/v1/games/raid/scores", json={"user_id": "carol", "score": 50})

    r = await client.get("/v1/users/alice")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "alice"
    assert body["games_played"] == 2
    assert body["total_score"] == 2500
    assert body["games"] == [
        {"game_id": "maze", "rank": 1, "score": 1500, "total": 2},
        {"game_id": "raid", "rank": 1, "score": 1000, "total": 2},
    ]


@pytest.mark.asyncio
async def test_user_profile_unknown_404(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1})
    r = await client.get("/v1/users/ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


@pytest.mark.asyncio
async def test_user_profile_empty_store_404(client: AsyncClient) -> None:
    r = await client.get("/v1/users/alice")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


# --- 3. Game list -------------------------------------------------------------


@pytest.mark.asyncio
async def test_game_list_empty(client: AsyncClient) -> None:
    r = await client.get("/v1/games")
    assert r.status_code == 200
    assert r.json() == {"games": []}


@pytest.mark.asyncio
async def test_game_list_one_game(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1500})
    await client.post("/v1/games/maze/scores", json={"user_id": "bob", "score": 100})
    await client.post("/v1/games/raid/scores", json={"user_id": "carol", "score": 50})

    r = await client.get("/v1/games")
    assert r.json() == {
        "games": [
            {"game_id": "maze", "players": 2, "top_score": 1500},
            {"game_id": "raid", "players": 1, "top_score": 50},
        ]
    }


# --- 4. Compare ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_two_players(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1500})
    await client.post("/v1/games/maze/scores", json={"user_id": "bob", "score": 1300})
    await client.post("/v1/games/maze/scores", json={"user_id": "carol", "score": 1400})

    r = await client.get("/v1/games/maze/compare", params={"user_a": "alice", "user_b": "bob"})
    assert r.status_code == 200
    assert r.json() == {
        "game_id": "maze",
        "leader": "alice",
        "score_gap": 200,
        "users": [
            {"user_id": "alice", "rank": 1, "score": 1500},
            {"user_id": "bob", "rank": 3, "score": 1300},
        ],
    }


@pytest.mark.asyncio
async def test_compare_same_user(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1500})
    r = await client.get(
        "/v1/games/maze/compare", params={"user_a": "alice", "user_b": "alice"}
    )
    assert r.json() == {
        "game_id": "maze",
        "leader": "alice",
        "score_gap": 0,
        "users": [{"user_id": "alice", "rank": 1, "score": 1500}],
    }


@pytest.mark.asyncio
async def test_compare_missing_game(client: AsyncClient) -> None:
    r = await client.get(
        "/v1/games/nope/compare", params={"user_a": "alice", "user_b": "bob"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "game_not_found"


@pytest.mark.asyncio
async def test_compare_missing_user(client: AsyncClient) -> None:
    await client.post("/v1/games/maze/scores", json={"user_id": "alice", "score": 1})
    r = await client.get(
        "/v1/games/maze/compare", params={"user_a": "alice", "user_b": "ghost"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"
