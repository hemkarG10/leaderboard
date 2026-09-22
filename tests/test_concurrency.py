"""Concurrency: store lock under threadpool + multi-user races."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.infra.store import InMemoryStore
from app.main import create_app


@pytest.mark.asyncio
async def test_concurrent_same_user_different_scores_one_row() -> None:
    """Overlapping posts for one user must leave a single row + invariant."""
    settings = Settings(max_games=10, max_users_per_game=100)
    app = create_app(settings)
    transport = ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            scores = list(range(1, 51))

            async def post(score: int) -> None:
                res = await client.post(
                    "/v1/games/lock/scores",
                    json={"user_id": "alice", "score": score},
                )
                assert res.status_code in (200, 201), res.text

            await asyncio.gather(*(post(s) for s in scores))

            board = app.state.store._boards["lock"]
            assert len(board) == 1
            assert board.invariant_holds()
            assert "alice" in board.by_user
            # Final score is one of the submitted values (last writer under lock).
            assert board.by_user["alice"].score in scores

            top = await client.get("/v1/games/lock/leaderboard?limit=10")
            assert top.json()["total"] == 1


def test_equal_resubmit_does_not_burn_seq() -> None:
    store = InMemoryStore(max_games=10, max_users_per_game=100)
    store.submit("g", "alice", 100)
    seq_after_create = store._seq
    store.submit("g", "alice", 100)  # equal no-op
    assert store._seq == seq_after_create
    store.submit("g", "alice", 200)  # different → new seq
    assert store._seq == seq_after_create + 1
    assert store._boards["g"].by_user["alice"].achieved_seq == store._seq


@pytest.mark.asyncio
async def test_concurrent_submits_keep_max_when_racing_upwards() -> None:
    """Per-user sequential ascending submits, many users in parallel.

    Under replace semantics, only sequential-per-user ascending writes
    guarantee the final score equals max. Cross-user concurrency still stresses
    the locked store.
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
