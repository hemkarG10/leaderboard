"""Application settings — every limit lives here, nowhere as a literal."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

_override: "Settings | None" = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "leaderboard"
    score_min: int = -1_000_000_000
    score_max: int = 1_000_000_000
    top_default: int = 10
    top_max: int = 100
    window_default: int = 1
    window_max: int = 25
    id_max_len: int = 64
    # Caps sized for a single basic-xxs process (~1M entries total).
    max_games: int = 100
    max_users_per_game: int = 10_000
    request_body_max_bytes: int = 65_536
    # P1: leave empty to disable write auth
    api_key: str = ""


def get_settings() -> Settings:
    if _override is not None:
        return _override
    return Settings()


def set_settings(settings: Settings) -> None:
    """Used by create_app / tests so validators see the active Settings."""
    global _override
    _override = settings
