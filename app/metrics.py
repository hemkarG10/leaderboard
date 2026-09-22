"""Prometheus metrics. Labels never include game_id or user_id."""

from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

SUBMISSIONS = Counter(
    "leaderboard_submissions_total",
    "Score submit outcomes",
    ["outcome"],  # improved | not_improved | rejected
)

ENTRIES = Gauge(
    "leaderboard_entries",
    "Total entries across all games (no game label)",
)


def set_entries_gauge(n: int) -> None:
    ENTRIES.set(n)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
