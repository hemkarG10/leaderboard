"""Concurrency: many concurrent submits across few users."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_concurrent_submits_keep_max_when_racing_upwards() -> None:
    """Per-user sequential ascending submits, many users in parallel.

    Under replace semantics, only sequential-per-user ascending writes
    guarantee the final score equals max. Cross-user concurrency still stresses
    the single-event-loop store.
    """
    settings = Settings(max_games=10, max_users_per_game=100)
    app = create_app(settings)
    transport = ASGITransport(app=app)

    users = [f"u{i}" for i in range(50)]

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:

            async def user_flow(user_id: str) -> None:
                uid_num = int(user_id[1:])
                for j in range(10):
                    res = await client.post(
                        "/v1/games/race/scores",
                        json={"user_id": user_id, "score": uid_num * 10 + j},
                    )
                    assert res.status_code in (200, 201), res.text

            await asyncio.gather(*(user_flow(u) for u in users))

            board = await client.get(
                "/v1/games/race/leaderboard?limit=100&offset=0"
            )
            assert board.status_code == 200
            data = board.json()
            assert app.state.store._boards["race"].invariant_holds()

    assert data["total"] == 50
    by_user = {e["user_id"]: e["score"] for e in data["entries"]}
    for u in users:
        uid_num = int(u[1:])
        expected = max(uid_num * 10 + j for j in range(10))
        assert by_user[u] == expected
    scores = [e["score"] for e in data["entries"]]
    assert scores == sorted(scores, reverse=True)
    assert [e["rank"] for e in data["entries"]] == list(range(1, 51))
