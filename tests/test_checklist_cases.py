"""Checklist cases 1–53 — HTTP integration (+ concurrency) per README rules.

Ranking rules locked in README / DESIGN.md:
- Different score replaces; equal is a no-op.
- Higher score → better rank; rank 1 is best.
- Ties: earlier achieved_seq, then user_id.
- Per-game boards.
- Surroundings (window=1): user + one above + one below.
"""

from __future__ import annotations

import asyncio
import json

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
        max_users_per_game=10_000,
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


async def _submit(client: AsyncClient, game: str, user: str, score: int):
    return await client.post(
        f"/v1/games/{game}/scores", json={"user_id": user, "score": score}
    )


async def _top(client: AsyncClient, game: str, **params):
    return await client.get(f"/v1/games/{game}/leaderboard", params=params)


async def _around(client: AsyncClient, game: str, user: str, window: int = 1):
    return await client.get(
        f"/v1/games/{game}/users/{user}", params={"window": window}
    )


# ── Submit score (1–13) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_01_new_user_new_game(client) -> None:
    c, _ = client
    r = await _submit(c, "g1", "alice", 100)
    assert r.status_code == 201
    body = r.json()
    assert body["score"] == 100 and body["rank"] == 1 and body["improved"] is True


@pytest.mark.asyncio
async def test_02_second_user_lower_score(client) -> None:
    c, _ = client
    await _submit(c, "g2", "alice", 100)
    r = await _submit(c, "g2", "bob", 50)
    assert r.status_code == 201 and r.json()["rank"] == 2
    top = (await _top(c, "g2", limit=10)).json()["entries"]
    assert [e["user_id"] for e in top] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_03_second_user_higher_score(client) -> None:
    c, _ = client
    await _submit(c, "g3", "alice", 100)
    r = await _submit(c, "g3", "bob", 200)
    assert r.json()["rank"] == 1
    top = (await _top(c, "g3", limit=10)).json()["entries"]
    assert [e["user_id"] for e in top] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_04_same_user_higher_score(client) -> None:
    c, _ = client
    await _submit(c, "g4", "alice", 100)
    await _submit(c, "g4", "bob", 150)
    r = await _submit(c, "g4", "alice", 200)
    assert r.status_code == 200
    assert r.json()["score"] == 200 and r.json()["rank"] == 1
    assert r.json()["improved"] is True


@pytest.mark.asyncio
async def test_05_same_user_lower_score_replaces(client) -> None:
    c, _ = client
    await _submit(c, "g5", "alice", 200)
    await _submit(c, "g5", "bob", 100)
    r = await _submit(c, "g5", "alice", 50)
    assert r.status_code == 200
    assert r.json()["score"] == 50 and r.json()["rank"] == 2
    assert r.json()["improved"] is False
    top = (await _top(c, "g5", limit=10)).json()["entries"]
    assert [e["user_id"] for e in top] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_06_same_score_noop_stable_ties(client) -> None:
    c, _ = client
    await _submit(c, "g6", "alice", 100)
    await _submit(c, "g6", "bob", 100)
    before = (await _top(c, "g6", limit=10)).json()["entries"]
    r = await _submit(c, "g6", "alice", 100)
    assert r.status_code == 200
    assert r.json()["score"] == 100 and r.json()["rank"] == 1
    after = (await _top(c, "g6", limit=10)).json()["entries"]
    assert before == after == [
        {"rank": 1, "user_id": "alice", "score": 100},
        {"rank": 2, "user_id": "bob", "score": 100},
    ]


@pytest.mark.asyncio
async def test_07_same_user_two_games(client) -> None:
    c, _ = client
    await _submit(c, "chess", "alice", 10)
    await _submit(c, "poker", "alice", 99)
    a = (await _around(c, "chess", "alice")).json()
    b = (await _around(c, "poker", "alice")).json()
    assert a["score"] == 10 and a["rank"] == 1
    assert b["score"] == 99 and b["rank"] == 1


@pytest.mark.asyncio
async def test_08_two_users_two_games_isolated(client) -> None:
    c, _ = client
    await _submit(c, "A", "u1", 10)
    await _submit(c, "B", "u2", 99)
    top_a = (await _top(c, "A", limit=10)).json()
    top_b = (await _top(c, "B", limit=10)).json()
    assert [e["user_id"] for e in top_a["entries"]] == ["u1"]
    assert [e["user_id"] for e in top_b["entries"]] == ["u2"]


@pytest.mark.asyncio
async def test_09_score_zero_accepted(client) -> None:
    c, _ = client
    r = await _submit(c, "g9", "alice", 0)
    assert r.status_code == 201 and r.json()["score"] == 0 and r.json()["rank"] == 1


@pytest.mark.asyncio
async def test_10_negative_score_ranked_below(client) -> None:
    c, _ = client
    await _submit(c, "g10", "pos", 5)
    r = await _submit(c, "g10", "neg", -3)
    assert r.status_code == 201 and r.json()["rank"] == 2
    top = (await _top(c, "g10", limit=10)).json()["entries"]
    assert top[0]["user_id"] == "pos" and top[1]["score"] == -3


@pytest.mark.asyncio
async def test_11_largest_allowed_score(client, settings: Settings) -> None:
    c, _ = client
    r = await _submit(c, "g11", "max", settings.score_max)
    assert r.status_code == 201
    assert r.json()["score"] == settings.score_max and r.json()["rank"] == 1


@pytest.mark.asyncio
async def test_12_decimal_score_rejected(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g12/scores", json={"user_id": "alice", "score": 1.5}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"
    top = (await _top(c, "g12", limit=10)).json()
    assert top["total"] == 0


@pytest.mark.asyncio
async def test_13_many_users_out_of_order(client) -> None:
    c, _ = client
    # Out-of-order submits; final order by score then tie-break.
    await _submit(c, "g13", "c", 20)
    await _submit(c, "g13", "a", 50)
    await _submit(c, "g13", "b", 50)  # tie with a; a earlier → ranks higher
    await _submit(c, "g13", "d", 10)
    ids = [e["user_id"] for e in (await _top(c, "g13", limit=10)).json()["entries"]]
    assert ids == ["a", "b", "c", "d"]


# ── Top X (14–23) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_14_top_empty_game_success(client) -> None:
    c, _ = client
    r = await _top(c, "empty_game", limit=10)
    assert r.status_code == 200
    assert r.json() == {"game_id": "empty_game", "total": 0, "entries": []}


@pytest.mark.asyncio
async def test_15_top_10_with_3_users(client) -> None:
    c, _ = client
    await _submit(c, "g15", "a", 1)
    await _submit(c, "g15", "b", 3)
    await _submit(c, "g15", "c", 2)
    entries = (await _top(c, "g15", limit=10)).json()["entries"]
    assert [e["user_id"] for e in entries] == ["b", "c", "a"]
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_16_top_10_exactly_10(client) -> None:
    c, _ = client
    for i in range(10):
        await _submit(c, "g16", f"u{i}", i)
    entries = (await _top(c, "g16", limit=10)).json()["entries"]
    assert len(entries) == 10
    assert [e["rank"] for e in entries] == list(range(1, 11))
    assert entries[0]["user_id"] == "u9"


@pytest.mark.asyncio
async def test_17_top_10_of_25(client) -> None:
    c, _ = client
    for i in range(25):
        await _submit(c, "g17", f"u{i:02d}", i)
    data = (await _top(c, "g17", limit=10)).json()
    assert data["total"] == 25
    assert len(data["entries"]) == 10
    assert data["entries"][-1]["rank"] == 10
    assert all(e["rank"] != 11 for e in data["entries"])


@pytest.mark.asyncio
async def test_18_top_1(client) -> None:
    c, _ = client
    await _submit(c, "g18", "a", 1)
    await _submit(c, "g18", "b", 9)
    entries = (await _top(c, "g18", limit=1)).json()["entries"]
    assert entries == [{"rank": 1, "user_id": "b", "score": 9}]


@pytest.mark.asyncio
async def test_19_top_100(client) -> None:
    c, _ = client
    for i in range(120):
        await _submit(c, "g19", f"u{i}", i)
    data = (await _top(c, "g19", limit=100)).json()
    assert len(data["entries"]) == 100
    scores = [e["score"] for e in data["entries"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_20_default_limit_is_10(client) -> None:
    c, _ = client
    for i in range(15):
        await _submit(c, "g20", f"u{i}", i)
    data = (await _top(c, "g20")).json()  # omit limit
    assert len(data["entries"]) == 10
    assert data["entries"][0]["rank"] == 1


@pytest.mark.asyncio
async def test_21_top_after_score_update(client) -> None:
    c, _ = client
    await _submit(c, "g21", "a", 10)
    await _submit(c, "g21", "b", 20)
    await _submit(c, "g21", "a", 30)
    ids = [e["user_id"] for e in (await _top(c, "g21", limit=10)).json()["entries"]]
    assert ids == ["a", "b"]


@pytest.mark.asyncio
async def test_22_tied_scores_in_top(client) -> None:
    c, _ = client
    await _submit(c, "g22", "zed", 50)
    await _submit(c, "g22", "ann", 50)
    entries = (await _top(c, "g22", limit=10)).json()["entries"]
    # Earlier update (zed) ranks above ann despite user_id order.
    assert [e["user_id"] for e in entries] == ["zed", "ann"]


@pytest.mark.asyncio
async def test_23_each_row_has_rank_user_score(client) -> None:
    c, _ = client
    await _submit(c, "g23", "a", 7)
    e = (await _top(c, "g23", limit=10)).json()["entries"][0]
    assert set(e.keys()) == {"rank", "user_id", "score"}
    assert e["rank"] == 1


# ── Surroundings (24–33) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_24_middle_surroundings(client) -> None:
    c, _ = client
    await _submit(c, "g24", "a", 300)
    await _submit(c, "g24", "b", 200)
    await _submit(c, "g24", "c", 100)
    body = (await _around(c, "g24", "b", window=1)).json()
    assert body["rank"] == 2
    assert body["above"] == [{"rank": 1, "user_id": "a", "score": 300}]
    assert body["below"] == [{"rank": 3, "user_id": "c", "score": 100}]


@pytest.mark.asyncio
async def test_25_rank1_no_above(client) -> None:
    c, _ = client
    await _submit(c, "g25", "a", 300)
    await _submit(c, "g25", "b", 200)
    body = (await _around(c, "g25", "a", window=1)).json()
    assert body["rank"] == 1 and body["above"] == []
    assert body["below"][0]["user_id"] == "b"


@pytest.mark.asyncio
async def test_26_last_no_below(client) -> None:
    c, _ = client
    await _submit(c, "g26", "a", 300)
    await _submit(c, "g26", "b", 200)
    body = (await _around(c, "g26", "b", window=1)).json()
    assert body["below"] == []
    assert body["above"][0]["user_id"] == "a"


@pytest.mark.asyncio
async def test_27_only_one_user(client) -> None:
    c, _ = client
    await _submit(c, "g27", "solo", 1)
    body = (await _around(c, "g27", "solo", window=1)).json()
    assert body["rank"] == 1 and body["above"] == [] and body["below"] == []


@pytest.mark.asyncio
async def test_28_user_on_other_game_not_found(client) -> None:
    c, _ = client
    await _submit(c, "other", "alice", 10)
    await _submit(c, "this", "bob", 1)
    r = await _around(c, "this", "alice")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


@pytest.mark.asyncio
async def test_29_unknown_user(client) -> None:
    c, _ = client
    await _submit(c, "g29", "alice", 1)
    r = await _around(c, "g29", "ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


@pytest.mark.asyncio
async def test_30_unknown_game_surroundings_404(client) -> None:
    c, _ = client
    r = await _around(c, "no_such_game", "anyone")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "game_not_found"


@pytest.mark.asyncio
async def test_31_score_change_updates_neighbours(client) -> None:
    c, _ = client
    await _submit(c, "g31", "a", 300)
    await _submit(c, "g31", "b", 200)
    await _submit(c, "g31", "c", 100)
    before = (await _around(c, "g31", "b", window=1)).json()
    assert before["rank"] == 2
    await _submit(c, "g31", "b", 400)
    after = (await _around(c, "g31", "b", window=1)).json()
    assert after["rank"] == 1 and after["above"] == []
    assert after["below"][0]["user_id"] == "a"


@pytest.mark.asyncio
async def test_32_tie_neighbours_follow_tiebreak(client) -> None:
    c, _ = client
    await _submit(c, "g32", "first", 100)
    await _submit(c, "g32", "second", 100)
    await _submit(c, "g32", "third", 100)
    body = (await _around(c, "g32", "second", window=1)).json()
    assert body["above"][0]["user_id"] == "first"
    assert body["below"][0]["user_id"] == "third"


@pytest.mark.asyncio
async def test_33_rank_matches_top_list(client) -> None:
    c, _ = client
    for uid, sc in (("a", 10), ("b", 30), ("c", 20)):
        await _submit(c, "g33", uid, sc)
    top = (await _top(c, "g33", limit=10)).json()["entries"]
    for row in top:
        ctx = (await _around(c, "g33", row["user_id"], window=1)).json()
        assert ctx["rank"] == row["rank"]
        assert ctx["score"] == row["score"]


# ── Invalid input (34–50) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_34_missing_user_id(client) -> None:
    c, _ = client
    r = await c.post("/v1/games/g34/scores", json={"score": 1})
    assert r.status_code == 400
    assert (await _top(c, "g34")).json()["total"] == 0


@pytest.mark.asyncio
async def test_35_empty_user_id(client) -> None:
    c, _ = client
    r = await c.post("/v1/games/g35/scores", json={"user_id": "", "score": 1})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_36_spaces_only_user_id(client) -> None:
    c, _ = client
    r = await c.post("/v1/games/g36/scores", json={"user_id": "   ", "score": 1})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_37_user_id_too_long(client, settings: Settings) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g37/scores",
        json={"user_id": "x" * (settings.id_max_len + 1), "score": 1},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_38_missing_score(client) -> None:
    c, _ = client
    r = await c.post("/v1/games/g38/scores", json={"user_id": "alice"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_39_score_as_text(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g39/scores", json={"user_id": "alice", "score": "abc"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_40_nan_rejected(client) -> None:
    c, _ = client
    # Non-finite values must not be accepted as scores.
    r = await c.post(
        "/v1/games/g40/scores",
        content='{"user_id":"alice","score":null}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"

    r2 = await c.post(
        "/v1/games/g40/scores",
        content='{"user_id":"alice","score":1e400}',
        headers={"Content-Type": "application/json"},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_41_wrong_json_types(client) -> None:
    c, _ = client
    r1 = await c.post(
        "/v1/games/g41/scores", json={"user_id": 123, "score": 1}
    )
    r2 = await c.post(
        "/v1/games/g41/scores", json={"user_id": "alice", "score": True}
    )
    assert r1.status_code == 400 and r2.status_code == 400


@pytest.mark.asyncio
async def test_42_empty_body(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g42/scores",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_43_malformed_json(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g43/scores",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_44_empty_game_id(client) -> None:
    c, _ = client
    r = await c.post("/v1/games//scores", json={"user_id": "a", "score": 1})
    assert r.status_code in (400, 404, 405)


@pytest.mark.asyncio
async def test_45_game_id_with_spaces(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/bad%20id/scores", json={"user_id": "a", "score": 1}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_46_limit_zero(client) -> None:
    c, _ = client
    await _submit(c, "g46", "a", 1)
    r = await _top(c, "g46", limit=0)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_47_negative_limit(client) -> None:
    c, _ = client
    await _submit(c, "g47", "a", 1)
    r = await _top(c, "g47", limit=-1)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_48_limit_above_max_rejected(client) -> None:
    c, _ = client
    await _submit(c, "g48", "a", 1)
    r = await _top(c, "g48", limit=101)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_49_non_numeric_limit(client) -> None:
    c, _ = client
    await _submit(c, "g49", "a", 1)
    r = await c.get("/v1/games/g49/leaderboard?limit=abc")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_50_wrong_content_type(client) -> None:
    c, _ = client
    r = await c.post(
        "/v1/games/g50/scores",
        content='{"user_id":"alice","score":1}',
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code in (400, 415)


@pytest.mark.asyncio
async def test_failed_request_leaves_board_unchanged(client) -> None:
    c, _ = client
    await _submit(c, "g_bad", "alice", 42)
    before = (await _top(c, "g_bad", limit=10)).json()
    bad = await c.post(
        "/v1/games/g_bad/scores", json={"user_id": "alice", "score": "nope"}
    )
    assert bad.status_code == 400
    after = (await _top(c, "g_bad", limit=10)).json()
    assert before == after


# ── Load / concurrency (51–53) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_51_concurrent_updates_same_user(client) -> None:
    c, app = client
    scores = [1, 50, 20, 80, 40, 90, 10, 70]

    async def one(score: int) -> None:
        r = await _submit(c, "race51", "solo", score)
        assert r.status_code in (200, 201)

    await asyncio.gather(*(one(s) for s in scores))
    top = (await _top(c, "race51", limit=10)).json()
    assert top["total"] == 1
    assert top["entries"][0]["user_id"] == "solo"
    # Last write wins among concurrent tasks — score must be one of the submitted.
    assert top["entries"][0]["score"] in scores
    assert app.state.store._boards["race51"].invariant_holds()


@pytest.mark.asyncio
async def test_52_many_users_concurrent_same_game(client) -> None:
    c, app = client
    users = [f"u{i}" for i in range(40)]

    async def one(uid: str, score: int) -> None:
        r = await _submit(c, "race52", uid, score)
        assert r.status_code in (200, 201)

    await asyncio.gather(*(one(u, int(u[1:]) * 3) for u in users))
    data = (await _top(c, "race52", limit=100)).json()
    assert data["total"] == 40
    assert len(data["entries"]) == 40
    scores = [e["score"] for e in data["entries"]]
    assert scores == sorted(scores, reverse=True)
    assert {e["user_id"] for e in data["entries"]} == set(users)
    assert app.state.store._boards["race52"].invariant_holds()


@pytest.mark.asyncio
async def test_53_read_top_while_submits(client) -> None:
    c, app = client
    # Seed so reads always see a non-empty consistent board.
    for i in range(10):
        await _submit(c, "race53", f"seed{i}", i)

    stop = asyncio.Event()
    snapshots: list[list[str]] = []

    async def writer() -> None:
        n = 0
        while not stop.is_set():
            await _submit(c, "race53", f"w{n % 15}", n)
            n += 1
            await asyncio.sleep(0)

    async def reader() -> None:
        for _ in range(40):
            data = (await _top(c, "race53", limit=20)).json()
            ids = [e["user_id"] for e in data["entries"]]
            assert len(ids) == len(set(ids)), "duplicate user in snapshot"
            scores = [e["score"] for e in data["entries"]]
            assert scores == sorted(scores, reverse=True)
            snapshots.append(ids)
            await asyncio.sleep(0)

    w = asyncio.create_task(writer())
    await reader()
    stop.set()
    await w
    assert snapshots
    assert app.state.store._boards["race53"].invariant_holds()
