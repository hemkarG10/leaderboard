"""Scale, race, fault-tolerance, and metrics cases (review checklist)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.conftest import metric_sum_by_name, metric_value


async def _submit(client: AsyncClient, game: str, user: str, score: int):
    return await client.post(
        f"/v1/games/{game}/scores", json={"user_id": user, "score": score}
    )


async def _top(client: AsyncClient, game: str, limit: int = 100):
    return await client.get(f"/v1/games/{game}/leaderboard", params={"limit": limit})


async def _around(client: AsyncClient, game: str, user: str, window: int = 1):
    return await client.get(
        f"/v1/games/{game}/users/{user}", params={"window": window}
    )


@pytest.fixture
def big_settings() -> Settings:
    return Settings(
        score_min=-1_000_000_000,
        score_max=1_000_000_000,
        top_default=10,
        top_max=100,
        window_default=1,
        window_max=25,
        id_max_len=64,
        max_games=200,
        max_users_per_game=50_000,
        api_key="",
    )


@pytest.fixture
async def big_client(big_settings: Settings):
    app = create_app(big_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac, app


# ── Scale ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scale_01_top100_of_10000(big_client) -> None:
    c, app = big_client
    store = app.state.store
    for i in range(10_000):
        store.submit("big", f"u{i}", i)
    res = await _top(c, "big", 100)
    assert res.status_code == 200
    entries = res.json()["entries"]
    assert len(entries) == 100
    assert [e["rank"] for e in entries] == list(range(1, 101))
    assert entries[0]["score"] == 9999
    assert entries[-1]["score"] == 9900
    scores = [e["score"] for e in entries]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_scale_02_top100_with_3_users(big_client) -> None:
    c, _ = big_client
    for uid, sc in (("a", 1), ("b", 3), ("c", 2)):
        assert (await _submit(c, "g", uid, sc)).status_code in (200, 201)
    entries = (await _top(c, "g", 100)).json()["entries"]
    assert len(entries) == 3
    assert [e["user_id"] for e in entries] == ["b", "c", "a"]


@pytest.mark.asyncio
async def test_scale_03_repeated_updates_one_row_per_user(big_client) -> None:
    c, app = big_client
    for round_ in range(5):
        for i in range(1000):
            app.state.store.submit("g", f"u{i}", round_ * 1000 + i)
    data = (await _top(c, "g", 100)).json()
    assert data["total"] == 1000
    assert len(app.state.store._boards["g"].by_user) == 1000
    assert app.state.store._boards["g"].invariant_holds()


@pytest.mark.asyncio
async def test_scale_04_games_isolated(big_client) -> None:
    c, app = big_client
    for g in range(50):
        for i in range(1000):
            app.state.store.submit(f"game{g}", f"u{i}", i + g)
    for g in range(50):
        data = (await _top(c, f"game{g}", 100)).json()
        assert data["total"] == 1000
        assert all(e["score"] >= 900 + g for e in data["entries"])


@pytest.mark.asyncio
async def test_scale_05_surroundings_middle_of_10000(big_client) -> None:
    c, app = big_client
    for i in range(10_000):
        app.state.store.submit("g", f"u{i}", i)
    # user with score 5000 is near the middle of descending ranks
    body = (await _around(c, "g", "u5000", window=1)).json()
    assert body["rank"] == 5000  # scores 9999..5000 → rank 5000 for score 5000
    assert body["above"][0]["user_id"] == "u5001"
    assert body["below"][0]["user_id"] == "u4999"
    # Cross-check against top page that includes this rank via offset
    page = (
        await c.get(
            "/v1/games/g/leaderboard",
            params={"limit": 1, "offset": body["rank"] - 1},
        )
    ).json()["entries"][0]
    assert page["user_id"] == "u5000" and page["rank"] == body["rank"]


@pytest.mark.asyncio
async def test_scale_06_top_while_submits(big_client) -> None:
    c, app = big_client
    for i in range(200):
        app.state.store.submit("g", f"seed{i}", i)

    stop = asyncio.Event()

    async def writer() -> None:
        n = 0
        while not stop.is_set():
            await _submit(c, "g", f"w{n % 80}", n)
            n += 1
            await asyncio.sleep(0)

    async def reader() -> None:
        for _ in range(30):
            data = (await _top(c, "g", 100)).json()
            ids = [e["user_id"] for e in data["entries"]]
            assert len(ids) == len(set(ids))
            scores = [e["score"] for e in data["entries"]]
            assert scores == sorted(scores, reverse=True)
            await asyncio.sleep(0)

    w = asyncio.create_task(writer())
    await reader()
    stop.set()
    await w
    assert app.state.store._boards["g"].invariant_holds()


@pytest.mark.asyncio
async def test_scale_07_limit_above_max_rejected(big_client) -> None:
    c, _ = big_client
    await _submit(c, "g", "a", 1)
    res = await _top(c, "g", 101)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_failed"


# ── Race conditions ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_race_08_parallel_same_user(big_client) -> None:
    c, app = big_client
    scores = list(range(50, 100))

    async def one(score: int) -> None:
        r = await _submit(c, "race", "solo", score)
        assert r.status_code in (200, 201)

    await asyncio.gather(*(one(s) for s in scores))
    data = (await _top(c, "race", 100)).json()
    assert data["total"] == 1
    assert data["entries"][0]["score"] in scores
    assert app.state.store._boards["race"].invariant_holds()


@pytest.mark.asyncio
async def test_race_09_100_users_parallel(big_client) -> None:
    c, app = big_client

    async def one(i: int) -> None:
        r = await _submit(c, "race", f"u{i}", i * 10)
        assert r.status_code in (200, 201)

    await asyncio.gather(*(one(i) for i in range(100)))
    data = (await _top(c, "race", 100)).json()
    assert data["total"] == 100
    assert {e["user_id"] for e in data["entries"]} == {f"u{i}" for i in range(100)}


@pytest.mark.asyncio
async def test_race_10_same_user_two_games(big_client) -> None:
    c, _ = big_client

    async def a() -> None:
        assert (await _submit(c, "A", "u", 11)).status_code in (200, 201)

    async def b() -> None:
        assert (await _submit(c, "B", "u", 22)).status_code in (200, 201)

    await asyncio.gather(a(), b())
    assert (await _around(c, "A", "u")).json()["score"] == 11
    assert (await _around(c, "B", "u")).json()["score"] == 22


@pytest.mark.asyncio
async def test_race_11_readers_and_writers(big_client) -> None:
    c, app = big_client
    for i in range(50):
        app.state.store.submit("g", f"s{i}", i)

    async def writer() -> None:
        for n in range(100):
            await _submit(c, "g", f"w{n % 40}", n)

    async def reader() -> None:
        for _ in range(40):
            top = (await _top(c, "g", 100)).json()["entries"]
            ids = [e["user_id"] for e in top]
            assert len(ids) == len(set(ids))
            assert [e["rank"] for e in top] == list(range(1, len(top) + 1))
            scores = [e["score"] for e in top]
            assert scores == sorted(scores, reverse=True)
            if top:
                mid = top[len(top) // 2]["user_id"]
                ctx = (await _around(c, "g", mid, 1)).json()
                assert ctx["rank"] >= 1

    await asyncio.gather(writer(), reader())
    assert app.state.store._boards["g"].invariant_holds()


@pytest.mark.asyncio
async def test_race_12_ordered_second_wins(big_client) -> None:
    c, _ = big_client
    r1 = await _submit(c, "g", "u", 10)
    assert r1.status_code == 201
    r2 = await _submit(c, "g", "u", 99)
    assert r2.status_code == 200
    assert (await _around(c, "g", "u")).json()["score"] == 99


@pytest.mark.asyncio
async def test_race_13_rejected_with_valid_parallel(big_client) -> None:
    c, _ = big_client
    await _submit(c, "g", "u", 50)

    async def bad() -> None:
        r = await c.post(
            "/v1/games/g/scores", json={"user_id": "u", "score": "nope"}
        )
        assert r.status_code == 400

    async def good() -> None:
        r = await _submit(c, "g", "u", 60)
        assert r.status_code == 200

    await asyncio.gather(bad(), good())
    assert (await _around(c, "g", "u")).json()["score"] in (50, 60)
    # Never the invalid payload
    assert isinstance((await _around(c, "g", "u")).json()["score"], int)


# ── Fault tolerance ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fault_14_malformed_then_valid(big_client) -> None:
    c, _ = big_client
    bad = await c.post(
        "/v1/games/g/scores",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert bad.status_code == 400
    ok = await _submit(c, "g", "alice", 7)
    assert ok.status_code == 201
    assert (await _around(c, "g", "alice")).json()["score"] == 7


@pytest.mark.asyncio
async def test_fault_15_invalid_keeps_old_score(big_client) -> None:
    c, _ = big_client
    await _submit(c, "g", "alice", 42)
    before = (await _around(c, "g", "alice")).json()
    bad = await c.post(
        "/v1/games/g/scores", json={"user_id": "alice", "score": "x"}
    )
    assert bad.status_code == 400
    after = (await _around(c, "g", "alice")).json()
    assert after["score"] == before["score"] == 42
    assert after["rank"] == before["rank"]


@pytest.mark.asyncio
async def test_fault_16_restart_clears_memory(big_settings: Settings) -> None:
    app1 = create_app(big_settings)
    async with app1.router.lifespan_context(app1):
        async with AsyncClient(
            transport=ASGITransport(app=app1), base_url="http://test"
        ) as c:
            await _submit(c, "g", "alice", 1)
            assert (await _top(c, "g")).json()["total"] == 1

    # New process / app instance → empty board; health still ok.
    app2 = create_app(big_settings)
    async with app2.router.lifespan_context(app2):
        async with AsyncClient(
            transport=ASGITransport(app=app2), base_url="http://test"
        ) as c:
            assert (await c.get("/healthz")).json() == {"status": "ok"}
            assert (await _top(c, "g")).json()["total"] == 0


@pytest.mark.asyncio
async def test_fault_20_health_when_up(big_client) -> None:
    c, _ = big_client
    assert (await c.get("/healthz")).status_code == 200
    assert (await c.get("/readyz")).status_code == 200


# Cases 17–19, 21 require an external store (Redis) — deferred; see DESIGN.md.


# ── Metrics ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_22_fresh_names(big_client) -> None:
    c, _ = big_client
    text = (await c.get("/metrics")).text
    assert (await c.get("/metrics")).status_code == 200
    assert "http_requests_total" in text
    assert "http_request_duration_seconds" in text
    assert "score_submits_total" in text


@pytest.mark.asyncio
async def test_metrics_23_successful_submit(big_client) -> None:
    c, _ = big_client
    before = (await c.get("/metrics")).text
    ok_before = metric_value(before, "score_submits_total", {"result": "ok"})
    await _submit(c, "g", "alice", 1)
    after = (await c.get("/metrics")).text
    assert metric_value(after, "score_submits_total", {"result": "ok"}) == ok_before + 1
    # Route template — not a raw game id
    assert 'route="/v1/games/{game_id}/scores"' in after
    assert 'game_id="g"' not in after


@pytest.mark.asyncio
async def test_metrics_24_invalid_submit(big_client) -> None:
    c, _ = big_client
    before = (await c.get("/metrics")).text
    rej_before = metric_value(before, "score_submits_total", {"result": "rejected"})
    ok_before = metric_value(before, "score_submits_total", {"result": "ok"})
    await c.post("/v1/games/g/scores", json={"user_id": "a", "score": "bad"})
    after = (await c.get("/metrics")).text
    assert (
        metric_value(after, "score_submits_total", {"result": "rejected"})
        == rej_before + 1
    )
    assert metric_value(after, "score_submits_total", {"result": "ok"}) == ok_before


@pytest.mark.asyncio
async def test_metrics_25_404_surroundings(big_client) -> None:
    c, _ = big_client
    before = (await c.get("/metrics")).text
    req_before = metric_sum_by_name(before, "http_requests_total")
    ok_before = metric_value(before, "score_submits_total", {"result": "ok"})
    res = await _around(c, "missing", "nobody")
    assert res.status_code == 404
    after = (await c.get("/metrics")).text
    assert metric_sum_by_name(after, "http_requests_total") >= req_before + 1
    assert 'status="404"' in after
    assert metric_value(after, "score_submits_total", {"result": "ok"}) == ok_before


@pytest.mark.asyncio
async def test_metrics_26_twenty_mixed_requests(big_client) -> None:
    c, _ = big_client
    before = metric_sum_by_name((await c.get("/metrics")).text, "http_requests_total")
    # 20 application requests (not counting the metrics scrapes themselves carefully:
    # we measure delta of non-metrics routes by issuing 20 calls then scraping once).
    for i in range(10):
        await _submit(c, "g", f"u{i}", i)
    for _ in range(5):
        await _top(c, "g", 10)
    for _ in range(5):
        await c.get("/healthz")
    after = (await c.get("/metrics")).text
    # +20 app calls + previous metrics GETs already in `before`; delta includes
    # the final /metrics GET itself (+1). Accept >= 20.
    delta = metric_sum_by_name(after, "http_requests_total") - before
    assert delta >= 20
    assert "http_request_duration_seconds_count" in after


@pytest.mark.asyncio
async def test_metrics_27_labels_do_not_include_user_ids(big_client) -> None:
    c, _ = big_client
    for i in range(30):
        await _submit(c, "g", f"user_{i}_unique", i)
    text = (await c.get("/metrics")).text
    our_lines = [
        ln
        for ln in text.splitlines()
        if ln.startswith("http_requests_total{")
        or ln.startswith("http_request_duration_seconds_")
        or ln.startswith("score_submits_total{")
    ]
    blob = "\n".join(our_lines)
    assert 'user_id="' not in blob
    assert 'game_id="' not in blob
    assert "user_0_unique" not in blob
    assert "/v1/games/{game_id}/scores" in blob
