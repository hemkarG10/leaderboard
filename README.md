# Leaderboard Service

Realtime per-game leaderboards: submit a score, read top N, read a user's rank
with neighbours. In-memory, single process (P0).

**Design:** [DESIGN.md](DESIGN.md) · **ADRs:** [DECISIONS.md](DECISIONS.md) ·
**Spec:** [SPEC.md](SPEC.md) · **Architecture:** [docs/architecture.md](docs/architecture.md)

## Review anchors

| Requirement | Where |
|---|---|
| Architecture / request lifecycle | [docs/architecture.md](docs/architecture.md) (Mermaid) |
| Validation & errors | pydantic `StrictInt` + ID regex; envelope in `app/main.py` |
| Tests | `pytest -q` — checklist, scale/race, metrics (`tests/`) |
| CI/CD | `.github/workflows/ci.yml` + `deploy.yml` → DigitalOcean |
| Setup / run / test | Quickstart below |

## Ranking rules (locked)

1. **A score update replaces that user's score for that game.** A different
   value (higher or lower) overwrites the stored score. Submitting the **same**
   score again is a no-op (score, rank, and tie order stay put).
2. **Higher score ranks better. Rank 1 is best.** Ranks are unique and
   1-indexed.
3. **Equal scores are ties.** Break ties by **earlier update first**
   (`achieved_seq`), then **`user_id` ascending**. The same order is used in
   Top X and surroundings.
4. **Each game has its own leaderboard.** No cross-game ranking.
5. **Surroundings** (default `window=1`) = this user + the one user immediately
   above + the one user immediately below.

Scores are **strict integers** in `[score_min, score_max]` (negatives allowed;
decimals rejected). IDs: `^[A-Za-z0-9_-]{1,64}$`.

| Read | Missing game behaviour |
|---|---|
| Top X | **200** empty `entries` |
| Surroundings | **404** `game_not_found` / `user_not_found` |

Swagger at `/docs` is for manual smoke checks. It does **not** replace tests.
Correctness: `tests/test_checklist_cases.py`. Scale / race / metrics:
`tests/test_scale_race_metrics.py`.

## Metrics (Prometheus)

`GET /metrics` — scrape with `curl -s localhost:8000/metrics`.

| Metric | Labels |
|---|---|
| `http_requests_total` | `method`, `route` (template), `status` |
| `http_request_duration_seconds` | same |
| `score_submits_total` | `result` = `ok` \| `rejected` |

Route templates only (e.g. `/v1/games/{game_id}/scores`) — never `user_id` /
`game_id` values.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)  
(Forward port **8000** in Cursor's Ports panel if the browser cannot connect.)

```bash
curl -s localhost:8000/healthz
curl -s -X POST localhost:8000/v1/games/maze/scores \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"alice","score":1500}'
curl -s 'localhost:8000/v1/games/maze/leaderboard?limit=10'
curl -s 'localhost:8000/v1/games/maze/users/alice?window=1'
pytest -q
```

## Deploy with OrbStack

Run this on your **Mac host** (OrbStack must be running). The Cursor remote
shell has no Docker daemon.

```bash
# In Terminal.app / iTerm, from this repo directory:
./scripts/deploy.sh
# equivalent: docker compose up -d --build
```

| URL | What |
| --- | --- |
| https://api.leaderboard.orb.local/docs | Swagger (OrbStack HTTPS) |
| https://leaderboard.local/docs | Same API |
| http://localhost:8000/docs | Published port |

```bash
curl -fsS https://api.leaderboard.orb.local/healthz
docker compose logs -f api
docker compose down
```

## GitHub CI/CD + DigitalOcean

**CI** (`.github/workflows/ci.yml`): on every push/PR — install deps, `pytest`,
Docker image build.

**CD** (`.github/workflows/deploy.yml`): on push to `main` — build image, push
to DigitalOcean Container Registry, update App Platform (secret
`DIGITALOCEAN_ACCESS_TOKEN`).

### One-time setup

```bash
# 1) GitHub
gh auth login
./scripts/bootstrap-github.sh

# 2) DigitalOcean API token → GitHub secret + local doctl
gh secret set DIGITALOCEAN_ACCESS_TOKEN   # paste DO personal access token
doctl auth init

# 3) Create the App Platform app (Dockerfile build, /healthz checks)
./scripts/do-create.sh

# 4) Watch CI
gh run watch
```

App spec: [`.do/app.yaml`](.do/app.yaml). After deploy:

```bash
doctl apps list
curl -fsS "https://<your-app-url>/healthz"
curl -fsS "https://<your-app-url>/docs"
```

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| POST | `/v1/games/{game_id}/scores` `{user_id, score}` | 201 + `Location` (first) / 200 `{…,rank,improved}` | 400, 409 |
| GET | `/v1/games/{game_id}/leaderboard?limit&offset` | 200 `{game_id,total,entries:[{rank,user_id,score}]}` | 400 |
| GET | `/v1/games/{game_id}/users/{user_id}?window` | 200 `{rank,score,total,above,below}` | 400, 404 |
| GET | `/healthz` · `/readyz` · `/metrics` · `/docs` | 200 | — |

Error envelope: `{"error":{"code","message","details","request_id"}}`.
`improved` is true only when the new score is strictly greater than the old one.

Defaults: `limit=10` (max 100), `window=1` (max 25). Over-max → **400** (not clamped).

## Built vs deferred

| Built | Deferred |
|---|---|
| Replace-on-update submit, top N, surroundings | Redis / multi-instance |
| Validation, coded errors, request IDs | Period boards, SSE, rate limits |
| Checklist cases 1–53 + concurrency | Anti-cheat |
| Prometheus outcomes + entry gauge | — |
| GitHub Actions CI (test + Docker) + DO App Platform CD | — |

## Failure modes / break points

Restart loses data. Capacity → 409. Bad input → 400 and **board unchanged**.
Breaks first on memory, then multi-instance split-brain, then hot-game
serialization — see DESIGN.md.
