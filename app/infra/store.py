"""Process-local store: game_id → Leaderboard + monotonic sequence.

Sync FastAPI routes run on a threadpool, so concurrent requests can overlap.
One `threading.Lock` serialises every read and write against the boards.
Never hold the lock across network I/O (there is none here).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.domain.leaderboard import Leaderboard, RankedEntry, SubmitResult
from app.errors import CapacityExceeded, GameNotFound, UserNotFound


@dataclass(frozen=True, slots=True)
class AroundResult:
    user: RankedEntry
    above: list[RankedEntry]
    below: list[RankedEntry]
    total: int


@dataclass(frozen=True, slots=True)
class GlobalRankedEntry:
    rank: int
    user_id: str
    score: int
    games_played: int


@dataclass(frozen=True, slots=True)
class UserGameStanding:
    game_id: str
    rank: int
    score: int
    total: int


@dataclass(frozen=True, slots=True)
class UserProfile:
    user_id: str
    games_played: int
    total_score: int
    games: list[UserGameStanding]


@dataclass(frozen=True, slots=True)
class GameSummary:
    game_id: str
    players: int
    top_score: int


@dataclass(frozen=True, slots=True)
class CompareResult:
    game_id: str
    leader: str
    score_gap: int
    users: list[RankedEntry]


class InMemoryStore:
    def __init__(self, *, max_games: int, max_users_per_game: int) -> None:
        self._boards: dict[str, Leaderboard] = {}
        self._seq = 0
        self._max_games = max_games
        self._max_users_per_game = max_users_per_game
        self._lock = threading.Lock()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def total_entries(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._boards.values())

    def submit(self, game_id: str, user_id: str, score: int) -> SubmitResult:
        with self._lock:
            board = self._boards.get(game_id)
            if board is None:
                if len(self._boards) >= self._max_games:
                    raise CapacityExceeded(
                        f"max_games ({self._max_games}) reached",
                        details={"resource": "games", "limit": self._max_games},
                    )
                board = Leaderboard()
                self._boards[game_id] = board

            existing = board.by_user.get(user_id)
            if existing is None and len(board) >= self._max_users_per_game:
                raise CapacityExceeded(
                    f"max_users_per_game ({self._max_users_per_game}) reached",
                    details={
                        "resource": "users_per_game",
                        "limit": self._max_users_per_game,
                        "game_id": game_id,
                    },
                )

            # Equal resubmit: keep achieved_seq; do not burn a sequence number.
            if existing is not None and existing.score == score:
                return board.submit(user_id, score, existing.achieved_seq)

            return board.submit(user_id, score, self._next_seq())

    def top(
        self, game_id: str, limit: int, offset: int
    ) -> tuple[int, list[RankedEntry]]:
        with self._lock:
            # Unknown / empty game → success with an empty board (not 404).
            board = self._boards.get(game_id)
            if board is None:
                return 0, []
            return len(board), board.top(limit, offset)

    def global_top(
        self, limit: int, offset: int
    ) -> tuple[int, list[GlobalRankedEntry]]:
        """Rank users by sum of current scores across all games.

        Walk each board's `by_user`. Tie-break: earliest `achieved_seq`, then
        `user_id`. No second SortedList — recompute on each request.
        """
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")

        with self._lock:
            # user_id → [score_sum, min_achieved_seq, games_played]
            totals: dict[str, list[int]] = {}
            for board in self._boards.values():
                for uid, entry in board.by_user.items():
                    agg = totals.get(uid)
                    if agg is None:
                        totals[uid] = [entry.score, entry.achieved_seq, 1]
                    else:
                        agg[0] += entry.score
                        agg[2] += 1
                        if entry.achieved_seq < agg[1]:
                            agg[1] = entry.achieved_seq

            ranked = sorted(
                (
                    (score, seq, uid, games)
                    for uid, (score, seq, games) in totals.items()
                ),
                key=lambda t: (-t[0], t[1], t[2]),
            )
            total = len(ranked)
            slice_ = ranked[offset : offset + limit]
            entries = [
                GlobalRankedEntry(
                    rank=offset + i + 1,
                    user_id=uid,
                    score=score,
                    games_played=games,
                )
                for i, (score, _seq, uid, games) in enumerate(slice_)
            ]
            return total, entries

    def user_profile(self, user_id: str) -> UserProfile:
        with self._lock:
            games: list[UserGameStanding] = []
            total_score = 0
            for game_id in sorted(self._boards):
                board = self._boards[game_id]
                ranked = board.rank_of(user_id)
                if ranked is None:
                    continue
                games.append(
                    UserGameStanding(
                        game_id=game_id,
                        rank=ranked.rank,
                        score=ranked.score,
                        total=len(board),
                    )
                )
                total_score += ranked.score

            if not games:
                raise UserNotFound(
                    f"user '{user_id}' not found in any game",
                    details={"user_id": user_id},
                )
            return UserProfile(
                user_id=user_id,
                games_played=len(games),
                total_score=total_score,
                games=games,
            )

    def list_games(self) -> list[GameSummary]:
        with self._lock:
            out: list[GameSummary] = []
            for game_id in sorted(self._boards):
                board = self._boards[game_id]
                if len(board) == 0:
                    continue
                top = board.ranked[0]
                out.append(
                    GameSummary(
                        game_id=game_id,
                        players=len(board),
                        top_score=-top[0],
                    )
                )
            return out

    def compare(
        self, game_id: str, user_a: str, user_b: str
    ) -> CompareResult:
        with self._lock:
            board = self._require_game(game_id)

            if user_a == user_b:
                ranked = board.rank_of(user_a)
                if ranked is None:
                    raise UserNotFound(
                        f"user '{user_a}' not found in game '{game_id}'",
                        details={"game_id": game_id, "user_id": user_a},
                    )
                return CompareResult(
                    game_id=game_id,
                    leader=user_a,
                    score_gap=0,
                    users=[ranked],
                )

            a = board.rank_of(user_a)
            if a is None:
                raise UserNotFound(
                    f"user '{user_a}' not found in game '{game_id}'",
                    details={"game_id": game_id, "user_id": user_a},
                )
            b = board.rank_of(user_b)
            if b is None:
                raise UserNotFound(
                    f"user '{user_b}' not found in game '{game_id}'",
                    details={"game_id": game_id, "user_id": user_b},
                )

            leader = user_a if a.rank < b.rank else user_b
            return CompareResult(
                game_id=game_id,
                leader=leader,
                score_gap=abs(a.score - b.score),
                users=[a, b],
            )

    def around(self, game_id: str, user_id: str, window: int) -> AroundResult:
        with self._lock:
            board = self._require_game(game_id)
            result = board.around(user_id, window)
            if result is None:
                raise UserNotFound(
                    f"user '{user_id}' not found in game '{game_id}'",
                    details={"game_id": game_id, "user_id": user_id},
                )
            user, above, below = result
            return AroundResult(
                user=user, above=above, below=below, total=len(board)
            )

    def _require_game(self, game_id: str) -> Leaderboard:
        board = self._boards.get(game_id)
        if board is None:
            raise GameNotFound(
                f"game '{game_id}' not found",
                details={"game_id": game_id},
            )
        return board
