# SPEC

## Goal
Realtime per-game leaderboard HTTP API matching the locked ranking rules in
[README.md](README.md) and [DESIGN.md](DESIGN.md).

## Semantics (summary)
- Different score → replace; equal score → no-op.
- Order: score desc, then `achieved_seq` asc, then `user_id` asc.
- Top X on unknown game → empty 200. Surroundings miss → 404.
- Surroundings default: one above / one below (`window=1`).

## Acceptance
- [ ] Cases 1–53 green in `tests/test_checklist_cases.py`
- [ ] Domain + concurrency modules green
- [ ] `/docs` available for manual smoke (1, 4, 17, 24, 25, 34)
