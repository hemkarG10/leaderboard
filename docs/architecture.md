# Architecture

See also [DESIGN.md](../DESIGN.md) and [DECISIONS.md](../DECISIONS.md).

```mermaid
flowchart LR
    C[Client] -->|HTTPS| MW[Middleware<br/>request_id · access log]
    MW --> R[api/leaderboard.py]
    R -->|Depends| S[infra/store.py<br/>InMemoryStore]
    S --> D[domain/leaderboard.py<br/>dict + SortedList]
    R -.->|AppError| EH[Error envelope]
    EH --> C
    R -->|200/201| C
    P[Prometheus] -->|scrape| M["/metrics"]
    Probe[Liveness] --> H["/healthz"]
```

## Layers
| Layer | Module | Rule |
|---|---|---|
| API | `app/api/` | HTTP only; pydantic in, JSON out |
| Infra | `app/infra/store.py` | Lifetime, caps, sequence counter |
| Domain | `app/domain/leaderboard.py` | Pure; stdlib + sortedcontainers |

## Process model
One uvicorn worker. State lives on `app.state.store`. Multiple workers would
split the board (each process has its own memory).
