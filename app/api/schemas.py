"""Request / response models. Scores are StrictInt — no strings, bools, floats."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.config import Settings, get_settings


def _id_pattern(settings: Settings | None = None) -> re.Pattern[str]:
    s = settings or get_settings()
    return re.compile(rf"^[A-Za-z0-9_-]{{1,{s.id_max_len}}}$")


def validate_id(value: str, *, field_name: str = "id") -> str:
    if not _id_pattern().match(value):
        raise ValueError(
            f"{field_name} must match ^[A-Za-z0-9_-]{{1,{get_settings().id_max_len}}}$"
        )
    return value


class SubmitScoreRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"user_id": "alice", "score": 1500}],
        },
    )

    user_id: str = Field(..., min_length=1, examples=["alice"])
    score: StrictInt = Field(..., examples=[1500])

    @field_validator("user_id")
    @classmethod
    def _user_id(cls, v: str) -> str:
        return validate_id(v, field_name="user_id")

    @field_validator("score")
    @classmethod
    def _score_bounds(cls, v: int) -> int:
        s = get_settings()
        if v < s.score_min or v > s.score_max:
            raise ValueError(
                f"score must be between {s.score_min} and {s.score_max}"
            )
        return v


class RankedEntryOut(BaseModel):
    rank: int
    user_id: str
    score: int


class GlobalRankedEntryOut(BaseModel):
    rank: int
    user_id: str
    score: int
    games_played: int


class SubmitScoreResponse(BaseModel):
    game_id: str
    user_id: str
    score: int
    rank: int
    improved: bool


class LeaderboardResponse(BaseModel):
    game_id: str
    total: int
    entries: list[RankedEntryOut]


class GlobalLeaderboardResponse(BaseModel):
    """Cross-game ranking: score is the sum of the user's scores in every game."""

    total: int
    entries: list[GlobalRankedEntryOut]


class UserGameStandingOut(BaseModel):
    game_id: str
    rank: int
    score: int
    total: int


class UserProfileResponse(BaseModel):
    user_id: str
    games_played: int
    total_score: int
    games: list[UserGameStandingOut]


class GameSummaryOut(BaseModel):
    game_id: str
    players: int
    top_score: int


class GameListResponse(BaseModel):
    games: list[GameSummaryOut]


class CompareUserOut(BaseModel):
    user_id: str
    rank: int
    score: int


class CompareResponse(BaseModel):
    game_id: str
    leader: str
    score_gap: int
    users: list[CompareUserOut]


class UserContextResponse(BaseModel):
    game_id: str
    user_id: str
    rank: int
    score: int
    total: int
    above: list[RankedEntryOut]
    below: list[RankedEntryOut]
