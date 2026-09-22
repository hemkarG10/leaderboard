# Architecture Decision Records

**Design:** [DESIGN.md](DESIGN.md) · **ADRs:** this file ·
**Spec:** [SPEC.md](SPEC.md) · **Architecture:** [docs/architecture.md](docs/architecture.md)

## ADR-001: Replace on different score; equal is a no-op

**Status:** Accepted (supersedes earlier “best-score only” draft)

**Context:** The product checklist requires that a score update **replaces** the
user's score for that game (including a lower score), while an equal resubmit
must not reshuffle ties.

**Decision:**
- `new_score != old_score` → replace entry and assign a new `achieved_seq`.
- `new_score == old_score` → no-op (keep score, rank, and seq).
- Response field `improved` is `true` only when `new_score > old_score`.

**Consequences:** Lower scores drop rank intentionally. Equal retries are
idempotent for free. Documented in README § Ranking rules.

---

## ADR-002: Deterministic tie-break via server sequence

**Status:** Accepted

**Context:** Equal scores need a stable total order so ranks are unique.

**Decision:** Order by `(-score, achieved_seq, user_id)`. `achieved_seq` is a
monotonic counter in `InMemoryStore`, assigned when a score is created or
replaced (not on equal no-ops). Never client time.

**Consequences:** Earlier achievement ranks higher; then `user_id`. Same order
in Top X and surroundings.

---

## ADR-003: In-memory SortedList + dict (P0)

**Status:** Accepted

**Context:** Need O(log n) submit / rank without Redis in CI.

**Decision:** Per game: `by_user` + `SortedList` of `(-score, seq, user_id)`.
Dependency: `sortedcontainers`.

**Consequences:** Single process / single uvicorn worker. Restart loses data.

---

## ADR-004: Single-event-loop concurrency model

**Status:** Accepted

**Context:** Read-modify-write on two structures must not interleave.

**Decision:** Synchronous store mutations (no `await`). One uvicorn worker.
Adding `await` or a thread pool requires a per-game `asyncio.Lock`, never held
across network I/O.

---

## ADR-005: Empty Top X vs 404 on surroundings

**Status:** Accepted

**Context:** Checklist case 14 wants Top X on an empty/unknown game to succeed
with an empty list; surroundings for unknown game/user should surface clearly.

**Decision:**
- `GET …/leaderboard` → 200 `{total:0, entries:[]}` when the game has no board.
- `GET …/users/{id}` → 404 `game_not_found` or `user_not_found`.

**Consequences:** Typos on surroundings still 404; Top X stays safe for UIs that
poll before the first submit.

---

## ADR-006: Global + profile reads recompute from `by_user`

**Status:** Accepted

**Context:** Scores on different games are not the same unit, but the product
wants a cross-game board and profile without a second index to keep in sync.

**Decision:**
- A user's **global score** is the **sum of their current score on every game**.
- Tie-break: earliest `achieved_seq` (`min` across games), then `user_id`.
- `GET /v1/leaderboard` entries include `games_played`. Same `limit` rules as
  per-game Top X (default 10, max 100, over → 400). Empty → 200 empty list.
- `GET /v1/users/{user_id}` returns standings via `rank_of` per board (sorted by
  `game_id`); unknown user → 404.
- `GET /v1/games` lists `{game_id, players, top_score}` sorted by `game_id`.
- `GET /v1/games/{id}/compare` uses two `rank_of` calls; same user → one entry
  and `score_gap: 0`.
- Recompute on each request by walking boards. **Do not** keep a second
  SortedList unless reads get slow.

**Consequences:** O(entries) scan per global/profile request (fine for P0). A
materialized global index can wait until a second store (Redis) exists.
