# Spec

**Design:** [DESIGN.md](DESIGN.md) · **ADRs:** [DECISIONS.md](DECISIONS.md) ·
**Spec:** this file · **Architecture:** [docs/architecture.md](docs/architecture.md)

## Goal
Realtime per-game leaderboard HTTP API matching the locked ranking rules in
[README.md](README.md) and [DESIGN.md](DESIGN.md).

## Semantics (summary)
- Different score → replace; equal score → no-op.
- Order: `(-score, achieved_seq, user_id)` (score desc, then earlier seq, then
  `user_id` asc).
- Top X on unknown/empty game → **200** empty `entries`. Surroundings miss →
  **404**.
- Surroundings default: one above / one below (`window=1`).
- First submit for a user → **201** + `Location`; later → **200**.
- `improved` is true only when the new score is strictly greater.
- **Global score** = sum of current scores on every game; recompute by walking
  `by_user` (no second SortedList).

## API surface
| Method | Path | Success |
|---|---|---|
| POST | `/v1/games/{game_id}/scores` | 201 / 200 `{…,rank,improved}` |
| GET | `/v1/games` | 200 `{games:[{game_id,players,top_score}]}` |
| GET | `/v1/games/{game_id}/leaderboard?limit&offset` | 200 `{game_id,total,entries}` |
| GET | `/v1/games/{game_id}/users/{user_id}?window` | 200 `{rank,score,total,above,below}` |
| GET | `/v1/games/{game_id}/compare?user_a&user_b` | 200 `{leader,score_gap,users}` |
| GET | `/v1/leaderboard?limit&offset` | 200 `{total,entries[+games_played]}` |
| GET | `/v1/users/{user_id}` | 200 `{games_played,total_score,games}` |
| GET | `/healthz` · `/readyz` · `/metrics` · `/docs` | 200 |

Scores: `StrictInt` in `[score_min, score_max]`. IDs:
`^[A-Za-z0-9_-]{1,id_max_len}$`. Over-max `limit`/`window` → **400**.

## Acceptance
- [x] Cases 1–53 green in `tests/test_checklist_cases.py`
- [x] Domain + concurrency modules green
  (`test_leaderboard_domain.py`, `test_concurrency.py`, `test_api_leaderboard.py`)
- [x] Scale / race / metrics coverage in `tests/test_scale_race_metrics.py`
- [x] Cross-game reads in `tests/test_cross_game_reads.py` (global, profile,
  game list, compare)
- [x] `/docs` available for manual smoke (1, 4, 17, 24, 25, 34)
- [x] Prometheus: `http_requests_total`, `http_request_duration_seconds`,
  `score_submits_total{result=ok|rejected}` on `GET /metrics`
