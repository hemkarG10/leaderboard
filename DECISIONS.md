# Architecture Decision Records

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
