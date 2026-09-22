"""Leaderboard HTTP routes — translate HTTP ⇄ domain only."""

from __future__ import annotations

import secrets

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from app.api.schemas import (
    CompareResponse,
    CompareUserOut,
    GameListResponse,
    GameSummaryOut,
    GlobalLeaderboardResponse,
    GlobalRankedEntryOut,
    LeaderboardResponse,
    RankedEntryOut,
    SubmitScoreRequest,
    SubmitScoreResponse,
    UserContextResponse,
    UserGameStandingOut,
    UserProfileResponse,
    validate_id,
)
from app.config import Settings, get_settings
from app.errors import AppError, Unauthorized, ValidationFailed
from app.infra.store import InMemoryStore
from app.metrics import score_submit_ok, score_submit_rejected

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/games", tags=["leaderboard"])
global_router = APIRouter(prefix="/v1", tags=["leaderboard"])


def get_store(request: Request) -> InMemoryStore:
    return request.app.state.store


def get_app_settings() -> Settings:
    return get_settings()


def _path_id(value: str, name: str) -> str:
    try:
        return validate_id(value, field_name=name)
    except ValueError as exc:
        raise ValidationFailed(str(exc), details=[{"loc": [name], "msg": str(exc)}]) from exc


def _limit_or_default(limit: int | None, settings: Settings) -> int:
    if limit is None:
        return settings.top_default
    if limit > settings.top_max:
        raise ValidationFailed(
            f"limit must be <= {settings.top_max}",
            details=[{"loc": ["query", "limit"], "msg": f"max is {settings.top_max}"}],
        )
    return limit


# --- Global / cross-game reads -------------------------------------------------


@global_router.get(
    "/leaderboard",
    response_model=GlobalLeaderboardResponse,
    summary="Global top N (sum of scores across all games)",
)
def get_global_leaderboard(
    limit: int | None = Query(default=None, ge=1, examples=[10]),
    offset: int = Query(default=0, ge=0, examples=[0]),
    store: InMemoryStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> GlobalLeaderboardResponse:
    limit = _limit_or_default(limit, settings)
    total, entries = store.global_top(limit, offset)
    return GlobalLeaderboardResponse(
        total=total,
        entries=[
            GlobalRankedEntryOut(
                rank=e.rank,
                user_id=e.user_id,
                score=e.score,
                games_played=e.games_played,
            )
            for e in entries
        ],
    )


@global_router.get(
    "/users/{user_id}",
    response_model=UserProfileResponse,
    summary="One player across all games",
    responses={404: {"description": "user_not_found"}},
)
def get_user_profile(
    user_id: str = Path(..., examples=["alice"]),
    store: InMemoryStore = Depends(get_store),
) -> UserProfileResponse:
    user_id = _path_id(user_id, "user_id")
    profile = store.user_profile(user_id)
    return UserProfileResponse(
        user_id=profile.user_id,
        games_played=profile.games_played,
        total_score=profile.total_score,
        games=[
            UserGameStandingOut(
                game_id=g.game_id,
                rank=g.rank,
                score=g.score,
                total=g.total,
            )
            for g in profile.games
        ],
    )


# --- Per-game reads / writes --------------------------------------------------


@router.get(
    "",
    response_model=GameListResponse,
    summary="List games with players and top score",
)
def list_games(
    store: InMemoryStore = Depends(get_store),
) -> GameListResponse:
    return GameListResponse(
        games=[
            GameSummaryOut(
                game_id=g.game_id,
                players=g.players,
                top_score=g.top_score,
            )
            for g in store.list_games()
        ]
    )


@router.post(
    "/{game_id}/scores",
    response_model=SubmitScoreResponse,
    status_code=200,
    summary="Submit a score",
    responses={
        201: {"description": "First score for this user (Location header set)"},
        200: {"description": "Existing user updated or unchanged"},
        400: {"description": "validation_failed"},
        409: {"description": "capacity_exceeded"},
    },
)
def submit_score(
    request: Request,
    body: SubmitScoreRequest,
    game_id: str = Path(..., examples=["maze"]),
    store: InMemoryStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> SubmitScoreResponse | JSONResponse:
    try:
        game_id = _path_id(game_id, "game_id")

        if settings.api_key:
            provided = request.headers.get("X-API-Key", "")
            expected = settings.api_key
            # compare_digest requires equal length; reject early otherwise.
            if len(provided) != len(expected) or not secrets.compare_digest(
                provided, expected
            ):
                raise Unauthorized(
                    "invalid or missing API key",
                    details=[
                        {"loc": ["header", "X-API-Key"], "msg": "unauthorized"}
                    ],
                )

        result = store.submit(game_id, body.user_id, body.score)
    except AppError:
        score_submit_rejected()
        raise
    except Exception:
        score_submit_rejected()
        raise

    score_submit_ok()

    logger.info(
        "score_submitted",
        game_id=game_id,
        user_id=body.user_id,
        score=result.entry.score,
        improved=result.improved,
        rank=result.rank,
    )

    payload = SubmitScoreResponse(
        game_id=game_id,
        user_id=body.user_id,
        score=result.entry.score,
        rank=result.rank,
        improved=result.improved,
    )
    if result.created:
        headers = {"Location": f"/v1/games/{game_id}/users/{body.user_id}"}
        return JSONResponse(
            status_code=201,
            content=payload.model_dump(),
            headers=headers,
        )
    return payload


@router.get(
    "/{game_id}/leaderboard",
    response_model=LeaderboardResponse,
    summary="Top N leaderboard",
)
def get_leaderboard(
    game_id: str = Path(..., examples=["maze"]),
    limit: int | None = Query(default=None, ge=1, examples=[10]),
    offset: int = Query(default=0, ge=0, examples=[0]),
    store: InMemoryStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> LeaderboardResponse:
    game_id = _path_id(game_id, "game_id")
    limit = _limit_or_default(limit, settings)

    total, entries = store.top(game_id, limit, offset)
    return LeaderboardResponse(
        game_id=game_id,
        total=total,
        entries=[
            RankedEntryOut(rank=e.rank, user_id=e.user_id, score=e.score)
            for e in entries
        ],
    )


@router.get(
    "/{game_id}/compare",
    response_model=CompareResponse,
    summary="Compare two players on one game",
    responses={
        404: {"description": "game_not_found / user_not_found"},
        400: {"description": "validation_failed"},
    },
)
def compare_users(
    game_id: str = Path(..., examples=["maze"]),
    user_a: str = Query(..., examples=["alice"]),
    user_b: str = Query(..., examples=["bob"]),
    store: InMemoryStore = Depends(get_store),
) -> CompareResponse:
    game_id = _path_id(game_id, "game_id")
    user_a = _path_id(user_a, "user_a")
    user_b = _path_id(user_b, "user_b")

    result = store.compare(game_id, user_a, user_b)
    return CompareResponse(
        game_id=result.game_id,
        leader=result.leader,
        score_gap=result.score_gap,
        users=[
            CompareUserOut(user_id=u.user_id, rank=u.rank, score=u.score)
            for u in result.users
        ],
    )


@router.get(
    "/{game_id}/users/{user_id}",
    response_model=UserContextResponse,
    summary="User rank and neighbours",
)
def get_user_context(
    game_id: str = Path(..., examples=["maze"]),
    user_id: str = Path(..., examples=["alice"]),
    window: int | None = Query(default=None, ge=0, examples=[2]),
    store: InMemoryStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> UserContextResponse:
    game_id = _path_id(game_id, "game_id")
    user_id = _path_id(user_id, "user_id")

    if window is None:
        window = settings.window_default
    if window > settings.window_max:
        raise ValidationFailed(
            f"window must be <= {settings.window_max}",
            details=[
                {"loc": ["query", "window"], "msg": f"max is {settings.window_max}"}
            ],
        )

    result = store.around(game_id, user_id, window)
    return UserContextResponse(
        game_id=game_id,
        user_id=result.user.user_id,
        rank=result.user.rank,
        score=result.user.score,
        total=result.total,
        above=[
            RankedEntryOut(rank=e.rank, user_id=e.user_id, score=e.score)
            for e in result.above
        ],
        below=[
            RankedEntryOut(rank=e.rank, user_id=e.user_id, score=e.score)
            for e in result.below
        ],
    )
